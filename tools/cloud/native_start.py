"""检查或显式启动固定 Android 应用；不安装、不启动 ADB、不配置或推进对局。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shutil
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from royale_phase2.versioning import verify_manifest
from tools.cloud.native_identity import Journal, SERIAL, digest, reference, write

PACKAGE = 'nullsroyale.rel.free'
ACTIVITY = PACKAGE + '/com.supercell.clashroyale.GameApp'
PROBE_PORT = 26789


def check_script():
    # pidof exit 1 means not running, including normal asynchronous startup.
    return f'''set -eu
uid="$(id -u)"
printf 'UID=%s\\n' "$uid"
[ "$uid" = 0 ] || exit 31
printf 'BOOT=%s\\n' "$(getprop sys.boot_completed)"
printf 'APK_BEGIN\\n'
pm path {PACKAGE}
printf 'APK_END\\n'
pid="$(pidof {PACKAGE} || true)"
case "$pid" in *[!0-9]*) exit 32;; esac
printf 'PID=%s\\n' "$pid"
'''


def parse_check(text):
    if not text.strip():
        raise ValueError('Empty Android shell evidence; device/root status is unknown. '
                         'Check the VM and ADB transport before retrying')
    uid = re.findall(r'^UID=(\d+)$', text, re.MULTILINE)
    boot = re.findall(r'^BOOT=(.*)$', text, re.MULTILINE)
    pid = re.findall(r'^PID=(\d*)$', text, re.MULTILINE)
    if len(uid) != 1:
        raise ValueError('Incomplete Android shell evidence: missing or repeated UID; root status is unknown')
    if uid != ['0']:
        raise ValueError('An already-rooted Android shell is required')
    if boot != ['1']:
        raise ValueError('Android sys.boot_completed must be 1')
    if len(pid) != 1 or (pid[0] and int(pid[0]) <= 0):
        raise ValueError('Expected zero or one positive application PID')
    apks = re.findall(r'^APK_BEGIN\n(.*?)^APK_END$', text, re.MULTILINE | re.DOTALL)
    if len(apks) != 1:
        raise ValueError('Installed package evidence is missing')
    paths = apks[0].splitlines()
    if not paths or any(not re.fullmatch(r'package:/data/app/[A-Za-z0-9_./~+=@%-]+\.apk', p) for p in paths):
        raise ValueError('The fixed application is not installed under /data/app')
    return {'pid': int(pid[0]) if pid[0] else None, 'installed_apks': paths,
            'boot_completed': True, 'root_uid': 0}


def start_script():
    return f'''set -eu
[ "$(id -u)" = 0 ] || exit 31
svc power stayon true
am start --user 0 -n {ACTIVITY}
'''


def immersive_state_script():
    return '''set -eu
uid="$(id -u)"
printf 'UID=%s\\n' "$uid"
[ "$uid" = 0 ] || exit 31
printf 'SDK=%s\\n' "$(getprop ro.build.version.sdk)"
printf 'IMMERSIVE_VALUE=%s\\n' "$(settings --user 0 get secure immersive_mode_confirmations)"
'''


def parse_immersive_state(text):
    uid = re.findall(r'^UID=(\d+)$', text, re.MULTILINE)
    sdk = re.findall(r'^SDK=(\d+)$', text, re.MULTILINE)
    value = re.findall(r'^IMMERSIVE_VALUE=(.*)$', text, re.MULTILINE)
    if uid != ['0'] or sdk != ['31'] or len(value) != 1:
        raise ValueError('Immersive confirmation supports only a rooted Android 12/API 31 user-0 shell')
    return value[0]


def confirm_immersive_setting(journal, shell, report):
    # Android 12's WindowManager observes this setting and asynchronously hides
    # its system tutorial. Readback proves persistence, not focus or readiness.
    evidence = {'status': 'checking', 'required_android_api': 31, 'user': 0,
                'setting': 'secure immersive_mode_confirmations',
                'prompt_dismissal_verified': False, 'window_focus_verified': False,
                'limitation': 'Setting readback does not verify asynchronous prompt removal, application focus or native readiness.'}
    report['immersive_confirmation'] = evidence
    before = parse_immersive_state(journal.device('check-immersive', shell, immersive_state_script()))
    evidence['before'] = before
    if before == 'confirmed':
        evidence.update(status='already_confirmed', after=before, setting_changed=False)
        return
    journal.device('confirm-immersive', shell, '''set -eu
[ "$(id -u)" = 0 ] || exit 31
[ "$(getprop ro.build.version.sdk)" = 31 ] || exit 33
settings --user 0 put secure immersive_mode_confirmations confirmed
''')
    after = parse_immersive_state(journal.device('verify-immersive', shell, immersive_state_script()))
    evidence['after'] = after
    if after != 'confirmed':
        evidence['status'] = 'readback_failed'
        raise RuntimeError('Android immersive confirmation setting did not persist as confirmed')
    evidence.update(status='confirmed', setting_changed=True)


def probe_status(folder, report, factory=None):
    """One status request under the same exclusive lease as native training."""
    from royale_phase2.probe import ProbeClient
    from royale_phase3.common import native_lock
    request = folder / 'probe-status.request.txt'
    response = folder / 'probe-status.response.json'
    errors = folder / 'probe-status.stderr.log'
    request.write_text('session-v1\nstatus\n', encoding='utf-8')
    record = {'command': ['native-probe', '127.0.0.1', str(PROBE_PORT), 'status'],
              'stdin': reference(request), 'started_utc': datetime.now(timezone.utc).isoformat(),
              'scope': 'status only; no configure/reset/step', 'exit_code': -1}
    client = None
    try:
        with native_lock():
            client = (factory or ProbeClient)(host='127.0.0.1', port=PROBE_PORT, timeout=3)
            try:
                report['native_probe_rpc_calls'] += 1
                status = client.status()
                if not isinstance(status, dict) or status.get('ok') is not True:
                    raise ValueError('Native status must be an object with ok=true')
                write(response, status)
                errors.write_bytes(b'')
                record['exit_code'] = 0
                report.update(probe_reachable=True, probe_status=status)
            finally:
                client.close()
    except Exception as exc:
        write(response, None)
        errors.write_text(f'{type(exc).__name__}: {exc}', encoding='utf-8')
        raise
    finally:
        record.update(stdout=reference(response), stderr=reference(errors))
        report['probe_command'] = record


def check_or_start(manifest, output, *, serial, adb, adb_port=5037, root_shell='adbd',
                   start=False, forward=False, probe=False, confirm_immersive=False, polls=6, poll_interval=2,
                   runner=None, sleeper=time.sleep, probe_factory=None):
    if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
        raise ValueError('Explicit safe Android serial is required')
    if type(adb_port) is not int or not 1 <= adb_port <= 65535 or root_shell not in ('adbd', 'su0'):
        raise ValueError('Invalid explicit ADB endpoint or root shell')
    if type(polls) is not int or not 1 <= polls <= 30 or not 0 <= poll_interval <= 5:
        raise ValueError('PID polling is bounded to 1..30 attempts and 0..5 seconds')
    if runner is not None and probe and probe_factory is None:
        raise ValueError('Interface fixtures cannot access the real probe')
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'cloud-native-start.v1', 'status': 'unavailable',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'device_serial': serial,
              'evidence_scope': 'interface_only' if runner is not None else 'actual_adb_shell',
              'requested': {'start': start, 'forward': forward, 'probe_status': probe,
                            'confirm_immersive': confirm_immersive},
              'native_battle_execution_verified': False, 'native_identity_verified': False,
              'native_probe_rpc_calls': 0, 'start_commands': 0, 'commands': [],
              'limitations': ['A PID or status response does not verify a battle or loaded resource identity.',
                              'Use native_identity.py to verify actual loaded APK, libraries and update resources.']}
    write(folder / 'report.json', report)
    journal = None
    try:
        version = verify_manifest(manifest)
        if (version['manifest'].get('client_package') != PACKAGE
                or version['manifest'].get('ticks_per_second') != 20):
            raise ValueError('This helper supports only the pinned 20 Hz nullsroyale.rel.free application')
        path = Path(adb)
        if not path.is_absolute():
            path = local(adb) if '/' in str(adb) or '\\' in str(adb) else Path(shutil.which(str(adb)) or '')
        if not path.is_file():
            raise FileNotFoundError('Explicit ADB executable is unavailable')
        report.update(manifest=reference(manifest), version_fingerprint=version['fingerprint'],
                      adb_executable={'path': str(path), 'sha256': digest(path.read_bytes())})
        journal = Journal(folder, str(path), serial, adb_port, runner)
        client = journal.run('adb-version', [str(path), 'version'])
        protocol = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', client)
        if not protocol:
            raise ValueError('Cannot determine explicit ADB client protocol version')
        journal.client_version = int(protocol[1])
        if journal.device('get-state', ['get-state']).strip() != 'device':
            raise RuntimeError('Specified serial is not a ready Android device')
        shell = ['shell', '-T', *(['su', '0'] if root_shell == 'su0' else []), 'sh', '-s']
        found = parse_check(journal.device('check-app', shell, check_script()))
        report.update(process=found, checks_passed=True, already_running=found['pid'] is not None,
                      start_command_accepted=False)
        if confirm_immersive:
            confirm_immersive_setting(journal, shell, report)
        if start and found['pid'] is None:
            # Exactly one launch attempt. PID absence is handled by bounded polling.
            report['start_commands'] = 1
            accepted = journal.device('start-app', shell, start_script(), timeout=60)
            # am can exit 0 while writing "Error type 3" / "Error:" to stderr.
            # Preserve the journal and reject that launch before polling for a PID.
            launch_stderr = local(journal.records[-1]['stderr']['path']).read_text(encoding='utf-8')
            if re.search(r'^(?:Error(?: type \d+|:)|Exception|Security exception:)',
                         accepted + '\n' + launch_stderr, re.MULTILINE):
                raise RuntimeError('Android am start reported an error; see saved command output')
            report['start_command_accepted'] = True
            report['pid_poll_attempts'] = 0
            for index in range(polls):
                if index:
                    sleeper(poll_interval)
                found = parse_check(journal.device(f'poll-app-{index + 1:02d}', shell, check_script()))
                report.update(process=found, pid_poll_attempts=index + 1)
                if found['pid'] is not None:
                    break
        report['operation_status'] = ('running' if found['pid'] is not None else
                                      'start_pending' if report['start_command_accepted'] else 'not_running')
        if forward:
            rows = [line.split() for line in journal.device('list-forwards', ['forward', '--list']).splitlines()]
            occupied = [row for row in rows if len(row) == 3 and row[1] == f'tcp:{PROBE_PORT}']
            if occupied:
                if occupied != [[serial, f'tcp:{PROBE_PORT}', f'tcp:{PROBE_PORT}']]:
                    raise RuntimeError('Local native probe forward is occupied; no existing mapping was changed')
                report['forward'] = 'already_present'
            else:
                journal.device('forward-probe', ['forward', '--no-rebind', f'tcp:{PROBE_PORT}', f'tcp:{PROBE_PORT}'])
                report['forward'] = 'created'
        if probe:
            probe_status(folder, report, probe_factory)
        report['status'] = 'interface_only' if runner is not None else report['operation_status']
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}')
    finally:
        report['commands'] = journal.records if journal else []
        write(folder / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('check', 'start'), default='check')
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--adb-port', type=int, default=5037)
    parser.add_argument('--root-shell', choices=('adbd', 'su0'), default='adbd')
    parser.add_argument('--output', required=True, help='新的仓库内证据目录')
    parser.add_argument('--forward', action='store_true', help='显式建立26789转发；不替换已有转发')
    parser.add_argument('--probe-status', action='store_true', help='显式请求一次status；不推进对局')
    parser.add_argument('--confirm-immersive', action='store_true',
                        help='显式确认Android12/API31用户0的系统沉浸模式提示；仅保存设置，不保证焦点或原生就绪')
    parser.add_argument('--pid-polls', type=int, default=6)
    parser.add_argument('--poll-interval', type=float, default=2)
    args = parser.parse_args(argv)
    report = check_or_start(args.manifest, args.output, serial=args.serial, adb=args.adb,
        adb_port=args.adb_port, root_shell=args.root_shell, start=args.command == 'start',
        forward=args.forward, probe=args.probe_status, confirm_immersive=args.confirm_immersive,
        polls=args.pid_polls, poll_interval=args.poll_interval)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'running' else 2


if __name__ == '__main__':
    raise SystemExit(main())

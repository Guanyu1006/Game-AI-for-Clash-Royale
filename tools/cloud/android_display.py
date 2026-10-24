"""为已启动的 rooted Redroid 设置可恢复的显示兼容选项。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import shlex
import signal
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from tools.cloud.native_identity import Journal, SERIAL, digest, reference, write


def discovery_script():
    return '''set -eu
uid="$(id -u)"
printf 'UID=%s\\n' "$uid"
[ "$uid" = 0 ] || exit 31
printf 'BOOT=%s\\n' "$(getprop sys.boot_completed)"
printf 'SDK=%s\\n' "$(getprop ro.build.version.sdk)"
printf 'HARDWARE=%s\\n' "$(getprop ro.hardware)"
printf 'BOOT_HARDWARE=%s\\n' "$(getprop ro.boot.hardware)"
printf 'FREEZER_VALUE=%s\\n' "$(settings get global cached_apps_freezer)"
printf 'SCREEN_TIMEOUT_VALUE=%s\\n' "$(settings --user 0 get system screen_off_timeout)"
printf 'STAY_ON_VALUE=%s\\n' "$(settings get global stay_on_while_plugged_in)"
'''


def parse_discovery(text):
    values = {}
    for key in ('UID', 'BOOT', 'SDK', 'HARDWARE', 'BOOT_HARDWARE',
                'FREEZER_VALUE', 'SCREEN_TIMEOUT_VALUE', 'STAY_ON_VALUE'):
        matches = re.findall(r'^'+key+r'=(.*)$', text, re.MULTILINE)
        if len(matches) != 1:
            raise ValueError('Android display evidence missing or repeated: '+key)
        values[key] = matches[0]
    if values['UID'] != '0':
        raise ValueError('显示兼容配置需要选定 guest 的 root shell')
    if values['BOOT'] != '1':
        raise ValueError('Android 尚未完成启动；请等待 sys.boot_completed=1 后再配置显示')
    if not re.fullmatch(r'\d+', values['SDK']) or int(values['SDK']) < 31:
        raise ValueError('显示兼容配置仅支持 Android API >= 31')
    if 'redroid' not in (values['HARDWARE'], values['BOOT_HARDWARE']):
        raise ValueError('显示兼容配置仅支持明确识别的现有 Redroid guest')
    return dict(root_uid=0, boot_completed=True, sdk=int(values['SDK']),
                hardware=values['HARDWARE'], boot_hardware=values['BOOT_HARDWARE'],
                cached_apps_freezer=values['FREEZER_VALUE'],
                screen_off_timeout=values['SCREEN_TIMEOUT_VALUE'],
                stay_on_while_plugged_in=values['STAY_ON_VALUE'])


def _restore_setting(namespace, key, value):
    if value == 'null':
        return f'settings delete {namespace} {key}'
    return f'settings put {namespace} {key} {shlex.quote(value)}'


def prepare_display(adb, adb_port, serial, root_shell, output, *, runner=None):
    """Configure an existing guest; never start ADB, a VM, an app or native RPC.

    ``runner`` follows native_identity.Journal's (label, argv, script, timeout)
    fixture interface. Its results are explicitly marked interface-only.
    """
    if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
        raise ValueError('Explicit safe Android serial is required')
    if type(adb_port) is not int or not 1 <= adb_port <= 65535 or root_shell not in ('su0', 'adbd'):
        raise ValueError('Invalid explicit ADB endpoint or root shell')
    folder = local(output); folder.mkdir(parents=True, exist_ok=False)
    report = dict(schema='cloud-android-display.v1', status='running',
        created_utc=datetime.now(timezone.utc).isoformat(), device_serial=serial,
        adb_port=adb_port, root_shell=root_shell,
        evidence_scope='interface_only' if runner is not None else 'actual_selected_guest_settings',
        commands=[], mutation_attempts=[], restored=False, native_probe_rpc_calls=0,
        adb_server_started=False, vm_started=False, app_started=False, system_restarted=False,
        freezer_setting_verified=False, freezer_runtime_effect_verified=False,
        rendering_verified=False, future_uptime_verified=False,
        scope='Selected rooted Redroid guest settings and immediate readback only; no battle, rendering or future stability guarantee')
    write(folder/'report.json', report)
    journal = None
    try:
        adb_path = local(adb)
        if not adb_path.is_file():
            raise FileNotFoundError('Explicit existing ADB executable is unavailable')
        report['adb_executable'] = dict(path=str(adb_path), sha256=digest(adb_path.read_bytes()))
        journal = Journal(folder, str(adb_path), serial, adb_port, runner)
        client = journal.run('adb-version', [str(adb_path), 'version'])
        protocol = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', client)
        if not protocol:
            raise ValueError('Cannot determine existing ADB client protocol version')
        journal.client_version = int(protocol[1])
        if journal.device('get-state', ['get-state']).strip() != 'device':
            raise RuntimeError('Specified serial is not a ready Android device')
        shell = ['shell', '-T', *(['su', '0'] if root_shell == 'su0' else []), 'sh', '-s']
        before = parse_discovery(journal.device('read-display-before', shell, discovery_script()))
        report['before'] = before
        report['old_settings'] = [
            dict(namespace='global', key='cached_apps_freezer', value=before['cached_apps_freezer'], modified=True),
            dict(namespace='global', key='stay_on_while_plugged_in', value=before['stay_on_while_plugged_in'], modified=True),
            dict(namespace='system', user=0, key='screen_off_timeout', value=before['screen_off_timeout'], modified=False)]
        restore_lines = ['set -eu', '[ "$(id -u)" = 0 ] || exit 31',
            _restore_setting('global', 'cached_apps_freezer', before['cached_apps_freezer']),
            _restore_setting('global', 'stay_on_while_plugged_in', before['stay_on_while_plugged_in'])]
        restore_script = '\n'.join(restore_lines)+'\n'
        (folder/'restore-guest-settings.stdin.sh').write_text(restore_script, encoding='utf-8')
        argv = [str(adb_path), '-H', '127.0.0.1', '-P', str(adb_port), '-s', serial, *shell]
        restore_command = ' '.join(shlex.quote(value) for value in argv)+' < '+shlex.quote(
            str(folder/'restore-guest-settings.stdin.sh'))
        report['restore'] = dict(explicit_action_required=True, executed=False,
            command=restore_command, argv=argv, stdin=reference(folder/'restore-guest-settings.stdin.sh'),
            screen_off_timeout_changed=False,
            scope='Restores saved guest setting values; does not put the screen to sleep or restart any process')
        write(folder/'report.json', report)  # Persist prior values before the first mutation.
        for label, command in (
            ('disable-cached-apps-freezer', 'settings put global cached_apps_freezer disabled'),
            ('keep-display-on', 'svc power stayon true'),
            ('wake-display', 'input keyevent KEYCODE_WAKEUP')):
            attempt = dict(operation=label, command=command, status='running', retry=False)
            report['mutation_attempts'].append(attempt); write(folder/'report.json', report)
            try:
                journal.device(label, shell, 'set -eu\n[ "$(id -u)" = 0 ] || exit 31\n'+command+'\n')
                attempt['status'] = 'command_completed'
            except BaseException:
                attempt['status'] = 'failed_or_unknown_outcome'
                raise
            finally:
                write(folder/'report.json', report)
        after = parse_discovery(journal.device('read-display-after', shell, discovery_script()))
        report['after'] = after
        if after['cached_apps_freezer'] != 'disabled':
            raise RuntimeError('cached_apps_freezer readback is not disabled; configuration unverified')
        if not re.fullmatch(r'\d+', after['stay_on_while_plugged_in']) or int(after['stay_on_while_plugged_in']) == 0:
            raise RuntimeError('stay_on_while_plugged_in readback does not confirm stayon')
        report['freezer_setting_verified'] = True
        diagnostic = journal.device('activity-settings-diagnostic', shell,
            'set -eu\n[ "$(id -u)" = 0 ] || exit 31\ndumpsys activity settings\n', timeout=60)
        report['activity_settings_diagnostic'] = dict(recorded=True,
            evidence=journal.records[-1]['stdout'],
            freezer_lines=[line.strip() for line in diagnostic.splitlines() if 'freez' in line.casefold()],
            scope='One dumpsys snapshot only; not proof that every process is unfrozen or future uptime is safe')
        report['status'] = 'prepared'
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error=f'{type(exc).__name__}: {exc}')
        if isinstance(exc, KeyboardInterrupt):
            raise
    finally:
        report['commands'] = journal.records if journal is not None else []
        report['finished_utc'] = datetime.now(timezone.utc).isoformat()
        write(folder/'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=['prepare'], default='prepare')
    parser.add_argument('--adb', default='.cache/native-tools/bin/adb')
    parser.add_argument('--adb-port', type=int, default=5038)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--root-shell', choices=['su0', 'adbd'], default='su0')
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    report = prepare_display(args.adb, args.adb_port, args.serial, args.root_shell, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'prepared' else 2


if __name__ == '__main__':
    raise SystemExit(main())

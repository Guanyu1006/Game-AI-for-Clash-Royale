"""串行准备已有 Android：ADB、启动、内容就绪、当前身份和连接。"""
import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import signal
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase3.common import configure, local, native_lock, write, append


def prepare(args):
    stability_seconds = getattr(args, 'stability_seconds', 60)
    if type(stability_seconds) not in (int, float) or not 0 <= stability_seconds <= 180:
        raise ValueError('stability seconds must be 0..180')
    folder = local(args.output); folder.mkdir(parents=True, exist_ok=False)
    report = dict(schema='cloud-native-prepare.v1', status='running',
                  started=datetime.now(timezone.utc).isoformat(), stages=[],
                  native_battle_execution_verified=False, app_restart=False,
                  restart_app_requested=bool(args.restart_app),
                  display_compat_requested=bool(getattr(args, 'display_compat', False)))
    def save(): write(folder/'report.json', report)
    def call(label, argv, timeout=180):
        print(json.dumps({'stage': label, 'status': 'running'}, ensure_ascii=False), flush=True)
        stage = {'stage': label, 'command': argv, 'status': 'running', 'returncode': None}
        report['stages'].append(stage); save()
        started = time.monotonic(); deadline = started+timeout; heartbeat = started+10
        with (folder/(label+'.stdout.log')).open('wb') as stdout, (folder/(label+'.stderr.log')).open('wb') as stderr:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr, start_new_session=True)
            try:
                while process.poll() is None:
                    stamp = time.monotonic()
                    if stamp >= deadline: raise TimeoutError(f'{label} stage budget exceeded')
                    if stamp >= heartbeat:
                        stage['elapsed_seconds'] = stamp-started; save()
                        print(json.dumps({'stage': label, 'status': 'running', 'elapsed_seconds': round(stamp-started)}), flush=True)
                        heartbeat = stamp+10
                    time.sleep(.2)
            except BaseException:
                if process.poll() is None:
                    os.killpg(process.pid, signal.SIGTERM)
                    try: process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        os.killpg(process.pid, signal.SIGKILL); process.wait(timeout=5)
                stage['status'] = 'interrupted_or_timed_out'
                raise
            finally:
                stage.update(returncode=process.returncode, elapsed_seconds=time.monotonic()-started)
                save()
        if process.returncode:
            stage['status'] = 'failed'
            message = f'{label} exit {process.returncode}; see saved stdout/stderr'
            if label == 'game-start':
                child_path = folder/'start/report.json'
                detail = {'report_path': child_path.relative_to(ROOT).as_posix(),
                          'diagnostic_status': 'missing'}
                try:
                    if child_path.is_file():
                        if child_path.stat().st_size > 2*1024*1024:
                            raise ValueError('Child start report exceeds the 2 MiB diagnostic budget')
                        child = json.loads(child_path.read_text(encoding='utf-8-sig'))
                        if not isinstance(child, dict):
                            raise ValueError('Child start report must be a JSON object')
                        detail.update(diagnostic_status='available', error=child.get('error'),
                                      operation_status=child.get('operation_status'),
                                      start_command_accepted=child.get('start_command_accepted'))
                        if child.get('error'):
                            message += '; child error: ' + str(child['error'])
                except Exception as exc:
                    # Best-effort diagnostics must never mask the original
                    # nonzero stage exit or cause a second application start.
                    detail.update(diagnostic_status='unreadable', diagnostic_error=f'{type(exc).__name__}: {exc}')
                stage['detail'] = detail
            save()
            raise RuntimeError(message)
        stage['status'] = 'completed'; save()
        return (folder/(label+'.stdout.log')).read_bytes()
    adb = [str(local(args.adb)), '-H', '127.0.0.1', '-P', str(args.adb_port)]
    python = [sys.executable, '-B']
    common = ['--manifest', args.manifest, '--adb', args.adb, '--serial', args.serial,
              '--adb-port', str(args.adb_port), '--root-shell', args.root_shell]
    def runtime_stability():
        from royale_phase2.probe import ProbeClient
        from tools.cloud.native_identity import process_start, section
        from tools.cloud.native_start import PACKAGE
        started = time.monotonic()
        summary = dict(status='running', requested_seconds=stability_seconds,
                       started_utc=datetime.now(timezone.utc).isoformat(), checks_attempted=0,
                       checks_passed=0, poll_interval_seconds=5, adb_timeout_seconds=30,
                       probe_timeout_seconds=5, stability_verified=False,
                       scope='Finite process/content availability checks; no battle/renderer acceptance or future uptime guarantee')
        stage = dict(stage='runtime-stability', status='running')
        report['runtime_stability'] = summary; report['stages'].append(stage); save()
        print(json.dumps({'stage': 'runtime-stability', 'status': 'running',
                          'requested_seconds': stability_seconds}), flush=True)
        try:
            # Read at most the bound plus one byte, even if a replaced receipt
            # is much larger. The full identity verification ran immediately
            # before this final liveness gate.
            with (folder/'identity.json').open('rb') as stream:
                data = stream.read(2*1024*1024+1)
            if len(data) > 2*1024*1024:
                raise ValueError('Identity receipt exceeds the 2 MiB stability budget')
            identity = json.loads(data)
            if not isinstance(identity, dict):
                raise ValueError('Identity receipt must be a JSON object')
            expected = {key: identity.get(key) for key in ('pid', 'process_start_ticks')}
            if any(type(value) is not int or value <= 0 for value in expected.values()):
                raise ValueError('Identity receipt lacks a positive pid/process_start_ticks')
            summary['expected_identity'] = expected
            # PACKAGE is the fixed reviewed client name, never supplied by HTTP
            # or concatenated from a remote response. No app/control mutation.
            script = f'''set -eu
uid="$(id -u)"
printf 'UID=%s\\n' "$uid"
[ "$uid" = 0 ] || exit 31
pid="$(pidof {PACKAGE} || true)"
printf 'PID=%s\\n' "$pid"
case "$pid" in ''|*[!0-9]*) exit 32;; esac
printf 'STAT_BEGIN\\n'
cat "/proc/$pid/stat"
printf '\\nSTAT_END\\n'
'''
            shell = adb+['-s', args.serial, 'shell', '-T',
                         *(['su', '0'] if args.root_shell == 'su0' else []), 'sh', '-s']
            def current_identity(detail):
                result = subprocess.run(shell, input=script.encode('utf-8'), cwd=ROOT,
                                        capture_output=True, timeout=30, check=False)
                detail['adb_returncode'] = result.returncode
                raw = result.stdout
                detail['adb_response_bytes'] = len(raw)
                if len(raw) > 16*1024:
                    raise ValueError('Runtime identity response exceeds the 16 KiB budget')
                text = raw.decode('utf-8', errors='strict').replace('\r\n', '\n')
                if not text.strip():
                    raise RuntimeError('Runtime stability identity read returned empty output; transport/application availability unknown')
                pids = re.findall(r'^PID=(\d+)$', text, re.MULTILINE)
                if len(pids) == 1:
                    detail['current_identity'] = {'pid': int(pids[0]), 'process_start_ticks': None}
                if result.returncode != 0:
                    raise RuntimeError(f'Runtime stability identity read exit {result.returncode}; application may have exited')
                if not re.search(r'^UID=0$', text, re.MULTILINE) or len(pids) != 1:
                    raise ValueError('Runtime stability requires root and exactly one current application PID')
                stat = section(text, 'STAT')
                if stat.split(maxsplit=1)[0] != pids[0]:
                    raise ValueError('Runtime stability pidof and proc stat PID differ')
                current = detail['current_identity']
                current['process_start_ticks'] = process_start(stat)
                current['process_state'] = stat[stat.rfind(')')+1:].split()[0]
                if any(current[key] != expected[key] for key in expected):
                    raise RuntimeError('Runtime identity changed after verification; refusing stale connection')
                if current['process_state'] in ('Z', 'X', 'x'):
                    raise RuntimeError('Verified Android application is no longer alive')
                return current
            while True:
                row = dict(at=time.time(), checked_utc=datetime.now(timezone.utc).isoformat(),
                           elapsed_seconds=time.monotonic()-started, expected_identity=expected,
                           current_identity=None, probe_status=None, status='checking')
                summary['checks_attempted'] += 1
                client = None
                try:
                    current_identity(row)
                    client = ProbeClient(timeout=5)
                    status = client.status(); row['probe_status'] = status
                    if not isinstance(status, dict) or status.get('ok') is not True or \
                            status.get('coldReady') is not True or status.get('contentReady') is not True:
                        raise RuntimeError('Runtime content readiness regressed after identity verification')
                    if time.monotonic()-started >= stability_seconds:
                        # A slow status request could return from a replacement
                        # process. Bracket the final status with the same PID /
                        # start-time check before publishing a connection.
                        final = row['final_identity_check'] = {'current_identity': None}
                        summary['final_current_identity'] = current_identity(final)
                    row['status'] = 'passed'; summary['checks_passed'] += 1
                except BaseException as exc:
                    row.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                               error=f'{type(exc).__name__}: {exc}')
                    if isinstance(exc, subprocess.TimeoutExpired): row['adb_timed_out'] = True
                    raise
                finally:
                    if client is not None: client.close()
                    row['completed_elapsed_seconds'] = time.monotonic()-started
                    append(folder/'stability.jsonl', row); save()
                if 'final_identity_check' in row: break
                remaining = max(0, stability_seconds-(time.monotonic()-started))
                print(json.dumps({'stage': 'runtime-stability', 'status': 'waiting',
                                  'elapsed_seconds': round(time.monotonic()-started, 1)}), flush=True)
                time.sleep(min(5, remaining))
            summary.update(status='verified_window' if stability_seconds > 0 else 'instantaneous_only',
                           stability_verified=stability_seconds > 0)
            if stability_seconds == 0:
                summary['scope'] = 'Immediate final identity/content check only; stability window disabled; no future uptime guarantee'
            stage['status'] = 'completed'
        except BaseException as exc:
            summary.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                           error=f'{type(exc).__name__}: {exc}')
            stage['status'] = summary['status']
            raise
        finally:
            summary.update(elapsed_seconds=time.monotonic()-started,
                           finished_utc=datetime.now(timezone.utc).isoformat())
            stage['elapsed_seconds'] = summary['elapsed_seconds']; save()
    save()
    try:
        with native_lock():
            # This pinned ADB treats -H 127.0.0.1 and tcp:127.0.0.1 -L as a
            # remote server and refuses bootstrap. Use its default localhost
            # binding (-a is absent), with the requested port explicit. Device
            # clients below retain -H/-P; the project wrapper clears server-socket overrides.
            call('adb-server', [str(local(args.adb)), '-P', str(args.adb_port), 'start-server'], 30)
            deadline = time.monotonic()+args.boot_timeout
            def boot_read(operation, argv, cap, poll):
                remaining = deadline-time.monotonic()
                if remaining <= 0:
                    raise TimeoutError('Android boot budget exceeded')
                timeout = min(cap, remaining)
                item = {'operation': operation, 'timeout_seconds': timeout,
                        'returncode': None, 'timed_out': False, 'stdout': '', 'stderr': ''}
                def decoded(value):
                    return value.decode(errors='replace') if isinstance(value, bytes) else str(value or '')
                try:
                    result = subprocess.run(argv, cwd=ROOT, capture_output=True, timeout=timeout)
                    item.update(returncode=result.returncode, stdout=decoded(result.stdout),
                                stderr=decoded(result.stderr))
                except subprocess.TimeoutExpired as exc:
                    # Only these bounded transport/read operations are safe to
                    # poll again. Application/identity mutations are not retried.
                    item.update(timed_out=True, stdout=decoded(exc.stdout), stderr=decoded(exc.stderr),
                                error=f'{type(exc).__name__}: {exc}')
                except BaseException as exc:
                    item['error'] = f'{type(exc).__name__}: {exc}'
                    raise
                finally:
                    poll['operations'].append(item)
                return item
            while True:
                if deadline-time.monotonic() <= 0:
                    raise TimeoutError('Android boot budget exceeded')
                poll = {'at': time.time(), 'operations': []}
                boot = None
                try:
                    connected = boot_read('connect', adb+['connect', args.serial], 15, poll)
                    if not connected['timed_out']:
                        boot = boot_read('getprop', adb+['-s', args.serial, 'shell', 'getprop', 'sys.boot_completed'],
                                         20, poll)
                finally:
                    operations = {row['operation']: row for row in poll['operations']}
                    for operation, prefix in (('connect', 'connect'), ('getprop', 'boot')):
                        row = operations.get(operation, {})
                        poll.update({prefix+'_returncode': row.get('returncode'),
                                     prefix+'_stdout': row.get('stdout', ''),
                                     prefix+'_stderr': row.get('stderr', ''),
                                     prefix+'_timed_out': row.get('timed_out', False)})
                    poll['boot'] = poll['boot_stdout'].strip()
                    append(folder/'boot.jsonl', poll)
                if boot and not boot['timed_out'] and boot['returncode'] == 0 and boot['stdout'].strip() == '1':
                    break
                remaining = deadline-time.monotonic()
                if remaining <= 0: raise TimeoutError('Android boot budget exceeded')
                print(json.dumps({'stage': 'android-boot', 'status': 'waiting'}), flush=True)
                time.sleep(min(5, remaining))
            if getattr(args, 'display_compat', False):
                report['display_compat'] = dict(
                    report_path=(folder/'display-compat/report.json').relative_to(ROOT).as_posix(),
                    restore_script=(folder/'display-compat/restore-guest-settings.stdin.sh').relative_to(ROOT).as_posix(),
                    scope='Guest freezer/stayon settings only; original values and explicit restore command in the child report')
                save()
                call('display-compat', python+['tools/cloud/android_display.py', 'prepare',
                     '--adb', args.adb, '--adb-port', str(args.adb_port), '--serial', args.serial,
                     '--root-shell', args.root_shell, '--output', str(folder/'display-compat')], 360)
            if args.restart_app:
                # Explicitly ends the current app session. Never restart as a
                # transport-error recovery: an uncertain mutation stays uncertain.
                from tools.cloud.native_start import PACKAGE
                call('app-force-stop', adb+['-s', args.serial, 'shell',
                     *(['su', '0'] if args.root_shell == 'su0' else []),
                     'am', 'force-stop', '--user', '0', PACKAGE], 90)
                report['app_restart'] = True; save()
            call('game-start', python+['tools/cloud/native_start.py', 'start', *common,
                 '--confirm-immersive', '--forward', '--output', str(folder/'start')], 600)
            from royale_phase2.probe import ProbeClient
            deadline = time.monotonic()+args.content_timeout
            while True:
                client = ProbeClient(timeout=5)
                try:
                    status = client.status()
                    append(folder/'content-status.jsonl', {'at': time.time(), 'status': status})
                    if status.get('coldReady') and status.get('contentReady'): break
                except Exception as exc:
                    append(folder/'content-status.jsonl', {'at': time.time(), 'error': str(exc)})
                finally: client.close()
                if time.monotonic() >= deadline: raise TimeoutError('Native content readiness budget exceeded')
                print(json.dumps({'stage': 'native-content', 'status': 'waiting'}), flush=True)
                time.sleep(5)
            call('identity', python+['tools/cloud/native_identity.py', *common,
                 '--identity-output', str(folder/'identity.json'), '--output', str(folder/'identity-check')], 600)
            runtime_stability()
            call('connection', python+['tools/cloud/native_connection.py', 'init', '--manifest', args.manifest,
                 '--identity', str(folder/'identity.json'), '--timeout', '180',
                 '--output', str(folder/'connection.json')], 30)
            report.update(status='prepared', connection=(folder/'connection.json').relative_to(ROOT).as_posix(),
                          content_ready=True, identity_verified=True,
                          scope='Current process identity/content and finite availability checks; no battle/renderer acceptance or future uptime guarantee')
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                      error=f'{type(exc).__name__}: {exc}')
        if isinstance(exc, KeyboardInterrupt):
            raise  # Preserve cancellation after saving the interrupted report.
    finally:
        report['finished'] = datetime.now(timezone.utc).isoformat(); save()
    return report


def main():
    configure()
    def interrupted(signum, frame):
        raise KeyboardInterrupt(f'received signal {signum}')
    signal.signal(signal.SIGTERM, interrupted)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--adb', default='.cache/native-tools/bin/adb')
    parser.add_argument('--adb-port', type=int, default=5038)
    parser.add_argument('--serial', default='127.0.0.1:15555')
    parser.add_argument('--root-shell', choices=['adbd', 'su0'], default='su0')
    parser.add_argument('--restart-app', action='store_true',
                        help='显式结束当前游戏进程再启动；切换 rendered/headless 时使用，当前对局将结束')
    parser.add_argument('--display-compat', action='store_true',
                        help='启动完成后、游戏启动前设置可恢复的 Redroid guest freezer/stayon 兼容选项；保存旧值和恢复命令')
    parser.add_argument('--boot-timeout', type=float, default=600)
    parser.add_argument('--content-timeout', type=float, default=900)
    parser.add_argument('--stability-seconds', type=float, default=60,
                        help='身份校验后只读观察存活的秒数（0..180）；0仅即时最终检查，不验证持续稳定性')
    args = parser.parse_args()
    if not 1 <= args.boot_timeout <= 1800 or not 1 <= args.content_timeout <= 1800:
        parser.error('boot/content timeout must be 1..1800 seconds')
    if not 0 <= args.stability_seconds <= 180:
        parser.error('stability seconds must be 0..180')
    report = prepare(args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'prepared' else 2


if __name__ == '__main__': raise SystemExit(main())

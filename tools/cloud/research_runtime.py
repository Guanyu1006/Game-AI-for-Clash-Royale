"""研究任务使用的原生运行时；准备、复用和停止均保存回执。"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import re
import signal
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase3.common import configure, digest, local, native_lock, now, read, reference, write
from royale_phase3.workbench_config import NATIVE_CACHE, NATIVE_MANIFEST, NATIVE_LOG_ROOT

RUNTIME_POINTER = '.tmp/phase3/research-runtime.json'

DEFAULTS = {
    'cache': NATIVE_CACHE,
    'manifest': NATIVE_MANIFEST,
    'vm_output': NATIVE_LOG_ROOT,
    'adb': '.cache/native-tools/bin/adb', 'adb_port': 5038,
    'serial': '127.0.0.1:15555', 'root_shell': 'su0',
    'memory_mib': 3072, 'vcpus': 2, 'display_fps': 5,
    'boot_timeout': 600, 'content_timeout': 900, 'stability_seconds': 15,
}


def settings_for(settings=None):
    values = dict(DEFAULTS)
    if settings:
        unknown = set(settings) - set(DEFAULTS)
        if unknown:
            raise ValueError('Unknown runtime settings: ' + ', '.join(sorted(unknown)))
        values.update(settings)
    for key in ('cache', 'manifest', 'adb', 'vm_output'):
        values[key] = local(values[key]).relative_to(ROOT).as_posix()
    if values['memory_mib'] not in (1536, 2048, 3072, 4096) or values['vcpus'] not in (1, 2):
        raise ValueError('Unsupported VM memory/CPU configuration')
    if values['display_fps'] not in (1, 5, 10, 15, 30):
        raise ValueError('Unsupported Android display frame rate')
    if type(values['adb_port']) is not int or not 1024 <= values['adb_port'] <= 65535:
        raise ValueError('Invalid local ADB port')
    if not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', values['serial']):
        raise ValueError('Expected an explicit loopback Android serial')
    if not 1 <= int(values['serial'].split(':')[1]) <= 65535:
        raise ValueError('Invalid Android device port')
    if values['root_shell'] not in ('su0', 'adbd'):
        raise ValueError('Invalid guest root shell')
    for key, low, high in (('boot_timeout', 1, 1800), ('content_timeout', 1, 1800),
                           ('stability_seconds', 0, 180)):
        if type(values[key]) not in (float, int) or not low <= values[key] <= high:
            raise ValueError('Invalid ' + key)
    return values


@contextmanager
def manager_lock():
    """Serialize preparations without holding the child prepare's native lease."""
    path = local('.tmp/phase3/research-runtime.lock')
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as stream:
        stream.seek(0); stream.write(b'0'); stream.flush(); stream.seek(0)
        if os.name == 'nt':
            import msvcrt
            acquire = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
        try:
            acquire()
        except OSError:
            raise RuntimeError('已有运行时准备/停止任务，请等待其结束') from None
        try:
            yield
        finally:
            stream.seek(0); release()


def _cancelled(event):
    return event is not None and event.is_set()


def _stage(folder, report, label, argv, timeout, cancel_event=None):
    stage = {'name': label, 'command': argv, 'status': 'running', 'started': now(),
             'stdout': (folder / (label + '.stdout.log')).relative_to(ROOT).as_posix(),
             'stderr': (folder / (label + '.stderr.log')).relative_to(ROOT).as_posix()}
    report['stages'].append(stage)
    write(folder / 'report.json', report)
    print(json.dumps({'runtime_stage': label, 'status': 'running'}, ensure_ascii=False), flush=True)
    started = time.monotonic()
    process = None
    try:
        if _cancelled(cancel_event):
            raise InterruptedError('运行时准备已取消')
        with local(stage['stdout']).open('wb') as stdout, local(stage['stderr']).open('wb') as stderr:
            process = subprocess.Popen(argv, cwd=ROOT, stdout=stdout, stderr=stderr,
                                       start_new_session=True)
            stage['pid'] = process.pid
            heartbeat = started
            while process.poll() is None:
                if _cancelled(cancel_event):
                    raise InterruptedError('运行时准备已取消')
                if time.monotonic() - started > timeout:
                    raise TimeoutError(label + ' 超出准备预算')
                if time.monotonic() - heartbeat >= 5:
                    stage['elapsed_seconds'] = round(time.monotonic() - started, 2)
                    write(folder / 'report.json', report)
                    heartbeat = time.monotonic()
                time.sleep(.2)
            stage['returncode'] = process.returncode
            if process.returncode:
                raise RuntimeError(label + ' 失败；查看 ' + stage['stderr'])
        stage['status'] = 'completed'
    except BaseException as exc:
        stage.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                     error=f'{type(exc).__name__}: {exc}')
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
                process.wait(timeout=10)
            except ProcessLookupError:
                pass
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait(timeout=5)
        raise
    finally:
        stage.update(finished=now(), elapsed_seconds=round(time.monotonic() - started, 2))
        if process is not None:
            stage['returncode'] = process.poll()
        write(folder / 'report.json', report)


def inspect_runtime(settings=None):
    """Read-only availability; never contacts Android or starts a process."""
    values = settings_for(settings)
    missing = [key for key in ('manifest', 'adb') if not local(values[key]).is_file()]
    active = local(values['cache']) / 'active-vm.json'
    result = {'settings': values, 'missing': missing, 'vm': 'not_installed',
              'native_execution_verified': False, 'rpc_calls': 0}
    if active.is_file():
        try:
            from tools.cloud.software_android_vm import process_identity
            record = read(active)
            identity = process_identity(record['pid'])
            result['vm'] = ('running_record' if identity == record['process_starttime'] else
                            'stopped_record' if identity is None else 'pid_conflict')
            result['attempt'] = record['attempt']
        except (OSError, ValueError, KeyError, TypeError) as exc:
            result.update(vm='invalid_record', error=str(exc))
    else:
        missing.append('installed_vm')
    result['available'] = not missing and result['vm'] not in ('pid_conflict', 'invalid_record')
    return result


def _warm_reuse(folder, values, vm, cancel_event=None, required_mode=None):
    """Read-only same-process gate. The caller holds both runtime/native leases.

    Local version and saved complete identity evidence remain verified. This
    deliberately does not rehash every installed Android resource: it checks
    the same process and mapped paths freshly, around one content status call.
    """
    evidence = folder / 'reuse-check'; evidence.mkdir()
    check = {'schema': 'royale-research-runtime-reuse.v1', 'status': 'not_reused',
             'started': now(), 'rpc_calls': 0, 'commands': [],
             'fresh_process_identity_verified': False, 'remote_files_rehashed': False,
             'requested_mode': required_mode,
             'scope': 'Previously reviewed static resources plus fresh same-process/path and content readiness checks; '
                      'not a new full remote resource hash audit or a battle/renderer acceptance'}
    journal = None
    try:
        if _cancelled(cancel_event):
            raise InterruptedError('运行时准备已取消')
        if vm.get('status') != 'running':
            raise ValueError('VM is not currently verified running')
        previous = read(RUNTIME_POINTER)
        if previous.get('settings') != values:
            raise ValueError('Saved runtime settings differ')
        prior_report_path = local(previous['report_path'])
        prior = read(prior_report_path)
        baseline = read(prior_report_path.parent / 'connection-check.json')
        if (prior.get('status') != 'prepared' or prior.get('settings') != values or
                prior.get('connection') != previous.get('connection')):
            raise ValueError('Saved runtime report does not identify a prepared matching connection')
        if (previous.get('vm_record_fingerprint') and
                previous['vm_record_fingerprint'] != digest(vm['record'])):
            raise ValueError('VM process record changed after preparation')
        from royale_phase2.connection import check_connection, load_connection
        checked = check_connection(previous['connection'], connect=False)
        write(folder / 'connection-check.json', checked)
        if (checked.get('ready') is not True or baseline.get('ready') is not True or
                checked.get('connection') != baseline.get('connection') or
                checked.get('manifest_sha256') != baseline.get('manifest_sha256') or
                checked.get('version_fingerprint') != prior.get('version_fingerprint') or
                checked.get('version_fingerprint') != baseline.get('version_fingerprint')):
            raise ValueError('Saved connection/version gate no longer matches')
        current_identity = checked.get('checks', {}).get('identity', {})
        saved_identity = baseline.get('checks', {}).get('identity', {})
        if (current_identity.get('status') != 'pass' or saved_identity.get('status') != 'pass' or
                any(current_identity.get(key) != saved_identity.get(key) for key in ('path', 'sha256'))):
            raise ValueError('Saved Android identity receipt changed')
        connection = load_connection(previous['connection'])
        if connection is None or local(connection.version_manifest) != local(values['manifest']):
            raise ValueError('Connection manifest differs from the requested runtime')
        if connection.as_dict() != checked.get('connection'):
            raise ValueError('Connection changed between saved-evidence validation and loading')
        identity = read(current_identity['path']); manifest = read(connection.version_manifest)
        from tools.cloud.native_identity import Journal, PACKAGE, discovery_script, inventory, section
        package = manifest.get('client_package')
        if not isinstance(package, str) or not PACKAGE.fullmatch(package):
            raise ValueError('A strict pinned Android package is required')
        if (identity.get('device_serial') != values['serial'] or
                identity.get('version_fingerprint') != checked['version_fingerprint']):
            raise ValueError('Identity device/version differs from the requested runtime')
        expected = {'pid': identity['pid'], 'start_ticks': identity['process_start_ticks'],
                    **{key: identity['mapped_paths'][key] for key in ('apk', 'libg', 'probe', 'update_root')}}
        if any(type(expected[key]) is not int or expected[key] <= 0 for key in ('pid', 'start_ticks')):
            raise ValueError('Saved process identity must contain positive PID/start ticks')
        sources = {'connection': reference(previous['connection']), 'identity': reference(current_identity['path']),
                   'manifest': reference(connection.version_manifest), 'previous_report': reference(prior_report_path)}
        if sources['identity']['sha256'] != current_identity['sha256']:
            raise ValueError('Identity receipt changed while checking local evidence')
        check.update(sources=sources, expected_identity=expected,
                     vm_record_fingerprint=digest(vm['record']), status='checking')
        write(evidence / 'report.json', check)
        adb = str(local(values['adb']))
        journal = Journal(evidence, adb, values['serial'], values['adb_port'])
        client_version = journal.run('adb-version', [adb, 'version'], timeout=10)
        protocol = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', client_version)
        if not protocol:
            raise ValueError('Cannot verify the explicit ADB client version')
        journal.client_version = int(protocol[1])

        def discover(label):
            if _cancelled(cancel_event):
                raise InterruptedError('运行时准备已取消')
            raw = journal.device(label, ['shell', '-T', *(['su', '0'] if values['root_shell'] == 'su0' else []),
                                         'sh', '-s'], discovery_script(package), timeout=20)
            if len(raw.encode('utf-8')) > 2 * 1024**2:
                raise ValueError('Process discovery exceeds the 2 MiB evidence budget')
            found = inventory(raw, package)
            if (section(raw, 'STAT').split(maxsplit=1)[0] != str(found['pid']) or
                    any(found.get(key) != expected[key] for key in expected)):
                raise ValueError('Current Android PID/start ticks or mapped paths differ from the saved identity')
            value = {**found, 'maps': sorted(found['maps'])}
            write(evidence / (label + '.json'), value)
            return value

        check['before'] = discover('discover-runtime-before')
        if _cancelled(cancel_event):
            raise InterruptedError('运行时准备已取消')
        from royale_phase2.probe import ProbeClient
        client = ProbeClient(host=connection.host, port=connection.port,
                             timeout=min(float(connection.timeout), 5.), max_reply=256 * 1024)
        try:
            check['rpc_calls'] = 1
            status = client.status()
            check['native_status'] = status
            write(evidence / 'native-status.json', status)
            if (not isinstance(status, dict) or status.get('ok') is not True or
                    status.get('coldReady') is not True or status.get('contentReady') is not True):
                raise ValueError('Configured native endpoint is not cold/content ready')
        finally:
            client.close()
        check['after'] = discover('discover-runtime-after')
        for key, path in (('connection', previous['connection']), ('identity', current_identity['path']),
                          ('manifest', connection.version_manifest)):
            if reference(path) != sources[key]:
                raise ValueError('Local connection/identity/version changed during live reuse checks')
        check['fresh_process_identity_verified'] = True
        if required_mode is not None:
            actual_mode = status.get('mode')
            check['mode_check'] = {'requested_mode': required_mode, 'actual_mode': actual_mode,
                                   'passed': actual_mode == required_mode, 'read_only': True}
            if actual_mode != required_mode:
                # Only an identified, healthy native-render process permits the
                # explicit mode-change restart below. Unknown modes fail closed.
                check['restart_app_required'] = actual_mode == 'native-render' and required_mode == 'headless'
                raise ValueError(f'Requested {required_mode} runtime but current process mode is {actual_mode!r}')
        check['status'] = 'reused'
        return {'connection': previous['connection'], 'version_fingerprint': checked['version_fingerprint'],
                'prepared_report': prior.get('prepared_report'), 'reused': True,
                'fresh_identity_check': (evidence / 'report.json').relative_to(ROOT).as_posix(),
                'mode_check': check.get('mode_check'),
                'reuse_scope': check['scope']}
    except InterruptedError:
        check.update(status='interrupted', reason='Runtime reuse check cancelled')
        raise
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as exc:
        check.update(status='not_reused', reason=f'{type(exc).__name__}: {exc}')
        return None
    finally:
        check.update(finished=now(), commands=journal.records if journal else [])
        write(evidence / 'report.json', check)


def _remember_runtime(report, values, folder, vm):
    write(folder / 'report.json', report)
    write(RUNTIME_POINTER, {'connection': report['connection'], 'settings': values,
          'report_path': (folder / 'report.json').relative_to(ROOT).as_posix(), 'prepared': now(),
          'vm_record_fingerprint': digest(vm['record']) if vm.get('status') == 'running' else None})


def _check_prepared_mode(connection_path, required_mode):
    """Read-only status check; never configure, reset, restart or retry a mutation."""
    from royale_phase2.connection import load_connection
    from royale_phase2.probe import ProbeClient
    connection = load_connection(connection_path)
    if connection is None:
        raise ValueError('Prepared mode check requires an explicit connection')
    client = ProbeClient(host=connection.host, port=connection.port,
                         timeout=min(float(connection.timeout), 5.), max_reply=256 * 1024)
    check = {'requested_mode': required_mode, 'actual_mode': None, 'passed': False,
             'read_only': True, 'rpc_calls': 1, 'checked_at': now()}
    try:
        status = client.status()
        check['native_status'] = status
        if isinstance(status, dict):
            check['actual_mode'] = status.get('mode')
            check['passed'] = (status.get('ok') is True and status.get('coldReady') is True and
                               status.get('contentReady') is True and status.get('mode') == required_mode)
        return check
    finally:
        client.close()


def ensure_runtime(output, settings=None, cancel_event=None, required_mode=None):
    """Prepare the existing installation; do not create or replace Android assets."""
    if required_mode not in (None, 'headless'):
        raise ValueError('required_mode must be None or headless')
    values = settings_for(settings)
    folder = local(output); folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'royale-research-runtime.v1', 'status': 'preparing',
              'started': now(), 'settings': values, 'stages': [],
              'native_execution_verified': False, 'automatic_reset': False,
              'requested_mode': required_mode, 'app_restart_requested': False}
    write(folder / 'report.json', report)
    python = [sys.executable, '-B']
    try:
        with manager_lock():
            if _cancelled(cancel_event):
                raise InterruptedError('运行时准备已取消')
            for key in ('manifest', 'adb'):
                if not local(values[key]).is_file():
                    raise FileNotFoundError('首次环境资源未准备：' + values[key])
            with native_lock():
                from tools.cloud.native_runtime_state import inspect_vm
                vm = inspect_vm(values['cache'])
                report['vm'] = vm
                write(folder / 'report.json', report)
                reused = _warm_reuse(folder, values, vm, cancel_event, required_mode)
                report['reuse_check'] = (folder / 'reuse-check/report.json').relative_to(ROOT).as_posix()
                if reused:
                    report.update(status='prepared', **reused)
                    _remember_runtime(report, values, folder, vm)
                    return report
                reuse_check = read(report['reuse_check'])
                restart_app = reuse_check.get('restart_app_required') is True
                if restart_app:
                    report.update(app_restart_requested=True,
                                  restart_reason='Explicit headless evaluation requires restarting the freshly '
                                                 'identified native-render process; a new identity/connection is required',
                                  previous_mode_check=reuse_check['mode_check'])
                    write(folder / 'report.json', report)
            if vm['status'] == 'stopped':
                argv = python + ['tools/cloud/software_android_vm.py', 'start',
                    '--cache', values['cache'], '--output', values['vm_output'],
                    '--resume-attempt', vm['resume_attempt'], '--vcpus', str(values['vcpus']),
                    '--memory-mib', str(values['memory_mib']), '--display-fps', str(values['display_fps']),
                    '--timeout-multiplier', '20', '--tb-size-mib', '64']
                if os.name != 'nt' and shutil.which('nice'):
                    argv = ['nice', '-n', '10', *argv]
                _stage(folder, report, 'vm-start', argv, 240, cancel_event)
            argv = python + ['tools/cloud/native_prepare.py', '--manifest', values['manifest'],
                '--output', str((folder / 'prepare').relative_to(ROOT)),
                '--adb', values['adb'], '--adb-port', str(values['adb_port']),
                '--serial', values['serial'], '--root-shell', values['root_shell'],
                '--boot-timeout', str(values['boot_timeout']), '--content-timeout', str(values['content_timeout']),
                '--stability-seconds', str(values['stability_seconds']), '--display-compat']
            if restart_app:
                argv.append('--restart-app')
            _stage(folder, report, 'prepare', argv,
                   values['boot_timeout'] + values['content_timeout'] + 1800, cancel_event)
            prepared = read(folder / 'prepare/report.json')
            if prepared.get('status') != 'prepared' or not (folder / 'prepare/connection.json').is_file():
                raise RuntimeError('运行时未生成经过核验的连接；查看 prepare/report.json')
            from royale_phase2.connection import check_connection
            checked = check_connection(prepared['connection'], connect=False)
            write(folder / 'connection-check.json', checked)
            if not checked.get('ready'):
                raise RuntimeError('准备后的连接/版本核验失败；查看 connection-check.json')
            report.update(status='prepared', connection=prepared['connection'],
                          version_fingerprint=checked['version_fingerprint'],
                          reused=False,
                          prepared_report=(folder / 'prepare/report.json').relative_to(ROOT).as_posix())
            # The prepare child has released its own lease before this fresh VM
            # record is captured. Never hold our native lease across that child.
            with native_lock():
                final_vm = inspect_vm(values['cache'])
                if final_vm.get('status') != 'running':
                    raise RuntimeError('Prepared Android VM is no longer verified running')
                if required_mode is not None:
                    if _cancelled(cancel_event):
                        raise InterruptedError('运行时准备已取消')
                    mode_check = _check_prepared_mode(prepared['connection'], required_mode)
                    report['mode_check'] = mode_check
                    write(folder / 'mode-check.json', mode_check)
                    if not mode_check['passed']:
                        raise RuntimeError('Prepared Android mode does not match requested headless evaluation; '
                                           'no game was reset and no mode-change mutation will be retried')
                report['prepared_vm'] = final_vm
                _remember_runtime(report, values, folder, final_vm)
        return report
    except BaseException as exc:
        report.update(status='interrupted' if isinstance(exc, (KeyboardInterrupt, InterruptedError)) else 'failed',
                      error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        report.update(finished=now(), report_path=(folder / 'report.json').relative_to(ROOT).as_posix())
        write(folder / 'report.json', report)


def stop_runtime(output, settings=None):
    values = settings_for(settings)
    folder = local(output); folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'royale-research-runtime-stop.v1', 'status': 'running', 'started': now()}
    try:
        with manager_lock(), native_lock():
            from tools.cloud.native_runtime_state import inspect_vm
            from tools.cloud.software_android_vm import stop
            state = inspect_vm(values['cache'])
            if state['status'] == 'stopped':
                report.update(status='already_stopped', process_exited=True)
            else:
                stopped = stop(local(values['cache']))
                report.update(status='stopped', result=stopped,
                              process_exited=stopped.get('process_exited', False))
        return report
    except Exception as exc:
        report.update(status='failed', error=f'{type(exc).__name__}: {exc}')
        raise
    finally:
        report.update(finished=now(), report_path=(folder / 'report.json').relative_to(ROOT).as_posix())
        write(folder / 'report.json', report)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=('inspect', 'prepare', 'stop'))
    parser.add_argument('--output')
    args = parser.parse_args(argv)
    configure()
    try:
        if args.command == 'inspect':
            result = inspect_runtime()
        else:
            if not args.output:
                parser.error('--output is required for prepare/stop')
            result = ensure_runtime(args.output) if args.command == 'prepare' else stop_runtime(args.output)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as exc:
        print(json.dumps({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}'}, ensure_ascii=False), flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

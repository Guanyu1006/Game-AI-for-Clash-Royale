"""Preparation lifecycle contracts; all ADB, probe, child and signal I/O mocked."""
from contextlib import ExitStack, contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import signal
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.cloud import native_prepare as prepare


class FakeClock:
    def __init__(self):
        self.value = 0.0

    def monotonic(self):
        return self.value

    def sleep(self, seconds):
        self.value += seconds


class FakeStageProcess:
    def __init__(self, pid, code=0, *, hanging=False, escalate=False):
        self.pid = pid
        self.returncode = None if hanging else code
        self.escalate = escalate
        self.waits = []

    def poll(self):
        return self.returncode

    def wait(self, timeout):
        self.waits.append(timeout)
        if self.returncode is None:
            raise subprocess.TimeoutExpired('fixture owned stage', timeout)
        return self.returncode


class NativePrepareContracts(unittest.TestCase):
    @staticmethod
    def runtime_text(pid=1978, start=65976, uid=0, state='S'):
        stat = f'{pid} (fixture app) ' + ' '.join([state] + ['0']*18 + [str(start)])
        return f'UID={uid}\nPID={pid}\nSTAT_BEGIN\n{stat}\nSTAT_END\n'.encode()

    def setUp(self):
        root = prepare.local('.tmp/cloud-native-prepare-tests')
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.adb = self.folder / 'adb-fixture'
        self.adb.write_bytes(b'fixture; never executable')

    def args(self, name='evidence', **kwargs):
        values = dict(output=str(self.folder / name), adb=str(self.adb), adb_port=5038,
                      serial='fixture:15555', root_shell='su0', manifest='fixture-version.json',
                      boot_timeout=1, content_timeout=1, restart_app=False, stability_seconds=0)
        values.update(kwargs)
        return SimpleNamespace(**values)

    def run_fixture(self, args=None, *, statuses=None, failed_stage=None,
                    hanging_stage=None, escalate=False, read_results=None, expect_interrupt=False,
                    child_report=None, failed_code=7, identity_payload=None, stability_write_delay=0):
        args = args or self.args()
        clock = FakeClock()
        stages, devices, processes, signals, clients = [], [], [], [], []
        ready = {'ok': True, 'coldReady': True, 'contentReady': True}
        status_values = iter(statuses or [ready])
        read_values = {key: list(values) for key, values in (read_results or {}).items()}
        timeouts, stability_timeouts, scripts, probe_times = [], [], [], []
        lease = SimpleNamespace(active=False, entries=0, exits=0)
        @contextmanager
        def leased():
            self.assertFalse(lease.active)
            lease.active = True; lease.entries += 1
            try:
                yield
            finally:
                lease.active = False; lease.exits += 1
        def label(argv):
            if 'force-stop' in argv:
                return 'app-force-stop'
            if 'start-server' in argv:
                return 'adb-server'
            for filename, stage in (('native_start.py', 'game-start'), ('native_identity.py', 'identity'),
                                    ('native_connection.py', 'connection'), ('android_display.py', 'display-compat')):
                if any(str(value).endswith('/' + filename) for value in argv):
                    return stage
            raise AssertionError('Unexpected child command: ' + str(argv))
        def popen(argv, **kwargs):
            self.assertTrue(lease.active)
            stage = label(argv)
            self.assertIs(kwargs['start_new_session'], True)
            self.assertEqual(kwargs['cwd'], prepare.ROOT)
            stages.append((stage, argv))
            kwargs['stdout'].write(('fixture stage ' + stage).encode())
            kwargs['stderr'].write(b'fixture diagnostic')
            if stage == 'game-start' and child_report is not None:
                report_path = Path(args.output)/'start/report.json'
                report_path.parent.mkdir()
                report_path.write_text(child_report if isinstance(child_report, str) else json.dumps(child_report))
            if stage == 'identity' and failed_stage != stage:
                receipt = identity_payload
                if receipt is None:
                    receipt = {'pid': 1978, 'process_start_ticks': 65976}
                (Path(args.output)/'identity.json').write_bytes(receipt if isinstance(receipt, bytes)
                                                               else json.dumps(receipt).encode())
            process = FakeStageProcess(500001 + len(processes), failed_code if stage == failed_stage else 0,
                                       hanging=stage == hanging_stage, escalate=escalate)
            processes.append(process)
            return process
        def run(argv, **kwargs):
            self.assertTrue(lease.active)
            devices.append(argv)
            self.assertEqual(kwargs['cwd'], prepare.ROOT)
            if 'connect' in argv:
                operation = 'connect'
                output = b'connected to fixture:15555\n'
            elif argv[-2:] == ['getprop', 'sys.boot_completed']:
                operation = 'getprop'
                output = b'1\n'
            elif argv[-2:] == ['sh', '-s']:
                operation = 'stability'
                scripts.append(kwargs['input'])
                output = self.runtime_text()
            else:
                raise AssertionError('Unexpected ADB read command: ' + str(argv))
            if operation == 'stability':
                stability_timeouts.append(kwargs['timeout'])
            else:
                timeouts.append((operation, kwargs['timeout']))
            values = read_values.get(operation, [])
            if values:
                value = values.pop(0)
                if callable(value):
                    value = value(clock)
                if isinstance(value, subprocess.TimeoutExpired):
                    clock.value += kwargs['timeout']
                if isinstance(value, BaseException):
                    raise value
                if isinstance(value, subprocess.CompletedProcess):
                    return value
                if value is not None:
                    output = value
            return subprocess.CompletedProcess(argv, 0, output, b'')
        class Probe:
            def __init__(self, **kwargs):
                if not lease.active:
                    raise AssertionError('Probe constructed outside native lease')
                self.closed = False
                self.options = kwargs
                clients.append(self)
            def status(self):
                if not lease.active:
                    raise AssertionError('Probe read outside native lease')
                probe_times.append(clock.value)
                value = next(status_values, ready)
                if callable(value):
                    value = value(clock)
                if isinstance(value, BaseException):
                    raise value
                return value
            def close(self):
                self.closed = True
        def killpg(pid, sig):
            signals.append((pid, sig))
            process = next(p for p in processes if p.pid == pid)
            if sig == signal.SIGKILL:
                process.returncode = -9
            elif not process.escalate:
                process.returncode = -15
        real_append = prepare.append
        def append(path, value):
            real_append(path, value)
            if path.name == 'stability.jsonl':
                clock.sleep(stability_write_delay)
        with ExitStack() as stack:
            stack.enter_context(patch.object(prepare, 'native_lock', side_effect=leased))
            stack.enter_context(patch.object(prepare.subprocess, 'Popen', side_effect=popen))
            stack.enter_context(patch.object(prepare.subprocess, 'run', side_effect=run))
            stack.enter_context(patch('royale_phase2.probe.ProbeClient', Probe))
            stack.enter_context(patch.object(prepare.os, 'killpg', side_effect=killpg))
            stack.enter_context(patch.object(prepare, 'append', side_effect=append))
            stack.enter_context(patch.object(prepare.time, 'monotonic', side_effect=clock.monotonic))
            stack.enter_context(patch.object(prepare.time, 'sleep', side_effect=clock.sleep))
            stack.enter_context(redirect_stdout(io.StringIO()))
            if expect_interrupt:
                with self.assertRaises(KeyboardInterrupt):
                    prepare.prepare(args)
                report = json.loads((Path(args.output) / 'report.json').read_text())
            else:
                report = prepare.prepare(args)
        self.assertTrue(all(p.closed for p in clients))
        self.assertFalse(lease.active)
        self.assertEqual(lease.entries, 1)
        self.assertEqual(lease.exits, 1)
        self.assertEqual(json.loads((Path(args.output) / 'report.json').read_text()), report)
        return SimpleNamespace(report=report, stages=stages, devices=devices, processes=processes,
                               signals=signals, clients=clients, output=Path(args.output),
                               read_timeouts=timeouts, elapsed=clock.value,
                               stability_timeouts=stability_timeouts, scripts=scripts,
                               probe_times=probe_times, lease=lease)

    def test_display_compat_runs_after_boot_and_before_game_start_with_saved_output(self):
        result = self.run_fixture(self.args(display_compat=True))
        self.assertEqual(result.report['status'], 'prepared')
        self.assertTrue(result.report['display_compat_requested'])
        self.assertEqual([label for label, _ in result.stages],
                         ['adb-server', 'display-compat', 'game-start', 'identity', 'connection'])
        command = next(argv for label, argv in result.stages if label == 'display-compat')
        for flag, expected in (('--adb', str(self.adb)), ('--adb-port', '5038'),
                               ('--serial', 'fixture:15555'), ('--root-shell', 'su0'),
                               ('--output', str(result.output/'display-compat'))):
            self.assertEqual(command[command.index(flag)+1], expected)
        self.assertEqual(result.read_timeouts[:2], [('connect', 1), ('getprop', 1)])

    def test_display_compat_failure_does_not_launch_game_or_retry_mutation(self):
        result = self.run_fixture(self.args(display_compat=True), failed_stage='display-compat')
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual([label for label, _ in result.stages], ['adb-server', 'display-compat'])
        self.assertIn('display-compat exit 7', result.report['error'])

    def test_display_compat_does_not_run_before_android_boot_completes(self):
        timeout = subprocess.TimeoutExpired('fixture connect', 15)
        result = self.run_fixture(self.args(display_compat=True, boot_timeout=1),
                                  read_results={'connect': [timeout]})
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual([label for label, _ in result.stages], ['adb-server'])

    def test_connect_timeout_records_partial_streams_then_recovers_without_restarting(self):
        timeout = subprocess.TimeoutExpired('fixture connect', 15, output=b'partial connect', stderr=b'transient transport')
        result = self.run_fixture(self.args(boot_timeout=60), read_results={'connect': [timeout, None]})
        self.assertEqual(result.report['status'], 'prepared')
        rows = [json.loads(line) for line in (result.output / 'boot.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(rows[0]['connect_timed_out'])
        self.assertEqual(rows[0]['connect_stdout'], 'partial connect')
        self.assertEqual(rows[0]['connect_stderr'], 'transient transport')
        self.assertEqual([op['operation'] for op in rows[0]['operations']], ['connect'])
        self.assertEqual(rows[-1]['boot'], '1')
        self.assertEqual(result.read_timeouts, [('connect', 15), ('connect', 15), ('getprop', 20)])
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'game-start', 'identity', 'connection'])
        self.assertEqual(result.signals, [])

    def test_getprop_timeout_records_partial_streams_and_recovers_within_global_budget(self):
        timeout = subprocess.TimeoutExpired('fixture getprop', 20, output=b'boot partial', stderr=b'adb stalled')
        result = self.run_fixture(self.args(boot_timeout=60), read_results={'getprop': [timeout, b'1\n']})
        self.assertEqual(result.report['status'], 'prepared')
        rows = [json.loads(line) for line in (result.output / 'boot.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertFalse(rows[0]['connect_timed_out'])
        self.assertTrue(rows[0]['boot_timed_out'])
        self.assertEqual(rows[0]['boot_stdout'], 'boot partial')
        self.assertEqual(rows[0]['boot_stderr'], 'adb stalled')
        self.assertEqual(rows[0]['operations'][-1]['operation'], 'getprop')
        self.assertLessEqual(result.elapsed, 60)
        self.assertEqual(sum(name == 'game-start' for name, _ in result.stages), 1)
        self.assertFalse(result.report['app_restart'])

    def test_repeated_read_timeouts_exhaust_global_budget_and_never_start_game(self):
        timeout = subprocess.TimeoutExpired('fixture connect', 15, output='partial text', stderr='not bytes')
        result = self.run_fixture(self.args(boot_timeout=25), read_results={'connect': [timeout, timeout]})
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('Android boot budget exceeded', result.report['error'])
        self.assertEqual(result.read_timeouts, [('connect', 15), ('connect', 5)])
        self.assertEqual(result.elapsed, 25)
        self.assertEqual([name for name, _ in result.stages], ['adb-server'])
        self.assertFalse(result.clients)
        self.assertEqual(result.signals, [])
        rows = [json.loads(line) for line in (result.output / 'boot.jsonl').read_text().splitlines()]
        self.assertEqual(len(rows), 2)
        self.assertTrue(all(row['connect_timed_out'] for row in rows))
        self.assertEqual(rows[-1]['connect_stdout'], 'partial text')

    def test_read_sleep_is_capped_by_remaining_budget(self):
        result = self.run_fixture(self.args(boot_timeout=2), read_results={'getprop': [b'0\n']})
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual(result.elapsed, 2)
        self.assertEqual(result.read_timeouts, [('connect', 2), ('getprop', 2)])
        self.assertEqual([name for name, _ in result.stages], ['adb-server'])

    def test_keyboard_interrupt_is_saved_and_rethrown_without_read_or_mutation_retry(self):
        result = self.run_fixture(read_results={'connect': [KeyboardInterrupt('fixture cancelled')]}, expect_interrupt=True)
        self.assertEqual(result.report['status'], 'interrupted')
        self.assertIn('fixture cancelled', result.report['error'])
        self.assertEqual(len(result.devices), 1)
        self.assertEqual([name for name, _ in result.stages], ['adb-server'])
        self.assertEqual(result.signals, [])
        rows = [json.loads(line) for line in (result.output / 'boot.jsonl').read_text().splitlines()]
        self.assertIn('KeyboardInterrupt', rows[0]['operations'][0]['error'])

    def test_downstream_game_start_timeout_is_never_reissued_or_treated_as_boot_read(self):
        result = self.run_fixture(self.args(boot_timeout=60), hanging_stage='game-start')
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('game-start stage budget exceeded', result.report['error'])
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'game-start'])
        self.assertEqual(len(result.clients), 0)
        self.assertEqual(result.signals, [(result.processes[-1].pid, signal.SIGTERM)])

    def test_default_preparation_never_force_stops_and_refreshes_identity_before_connection(self):
        result = self.run_fixture()
        self.assertEqual(result.report['status'], 'prepared')
        self.assertFalse(result.report['app_restart'])
        self.assertFalse(result.report['restart_app_requested'])
        self.assertFalse(result.report['native_battle_execution_verified'])
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'game-start', 'identity', 'connection'])
        self.assertEqual(result.stages[0][1], [str(self.adb), '-P', '5038', 'start-server'])
        self.assertNotIn('-H', result.stages[0][1])
        self.assertNotIn('-L', result.stages[0][1])
        self.assertNotIn('-a', result.stages[0][1])
        self.assertFalse(any('force-stop' in argv for _, argv in result.stages))
        self.assertEqual(result.signals, [])
        for command in result.devices:
            self.assertIn('-P', command)
            self.assertIn('5038', command)
        game = result.stages[1][1]
        self.assertIn('--confirm-immersive', game)
        self.assertIn('--forward', game)
        self.assertIn('--identity', result.stages[-1][1])
        self.assertEqual(len(result.clients), 2)

    def test_custom_adb_port_bootstraps_locally_and_clients_keep_explicit_host_port(self):
        result = self.run_fixture(self.args(adb_port=5123))
        self.assertEqual(result.report['status'], 'prepared')
        self.assertEqual(result.stages[0][1], [str(self.adb), '-P', '5123', 'start-server'])
        self.assertNotIn('-H', result.stages[0][1])
        self.assertNotIn('-L', result.stages[0][1])
        self.assertNotIn('-a', result.stages[0][1])
        for command in result.devices:
            self.assertEqual(command[command.index('-H') + 1], '127.0.0.1')
            self.assertEqual(command[command.index('-P') + 1], '5123')
            self.assertNotIn('-L', command)
        for name, command in result.stages:
            if name in ('game-start', 'identity'):
                self.assertEqual(command[command.index('--adb-port') + 1], '5123')
        self.assertFalse(any('kill-server' in command or 'root' in command for _, command in result.stages))
        self.assertEqual(result.signals, [])

    def test_failed_adb_bootstrap_is_not_retried_and_never_starts_game(self):
        result = self.run_fixture(failed_stage='adb-server', failed_code=255)
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('adb-server exit 255', result.report['error'])
        self.assertEqual([name for name, _ in result.stages], ['adb-server'])
        self.assertEqual(result.report['stages'][0]['status'], 'failed')
        self.assertEqual(result.devices, [])
        self.assertEqual(result.clients, [])
        self.assertEqual(result.signals, [])
        self.assertFalse(result.report['app_restart'])

    def test_explicit_restart_force_stops_once_before_start_identity_and_connection(self):
        result = self.run_fixture(self.args(restart_app=True))
        self.assertEqual(result.report['status'], 'prepared')
        self.assertTrue(result.report['app_restart'])
        self.assertTrue(result.report['restart_app_requested'])
        self.assertEqual([name for name, _ in result.stages],
                         ['adb-server', 'app-force-stop', 'game-start', 'identity', 'connection'])
        argv = result.stages[1][1]
        self.assertEqual(argv[-8:], ['shell', 'su', '0', 'am', 'force-stop', '--user', '0',
                                     'nullsroyale.rel.free'])
        self.assertEqual(sum('force-stop' in command for _, command in result.stages), 1)
        self.assertEqual(sum(name == 'game-start' for name, _ in result.stages), 1)
        self.assertEqual(result.signals, [])

    def test_adbd_restart_uses_explicit_device_without_su_wrapper(self):
        result = self.run_fixture(self.args(restart_app=True, root_shell='adbd'))
        argv = next(argv for name, argv in result.stages if name == 'app-force-stop')
        self.assertNotIn('su', argv)
        self.assertIn('fixture:15555', argv)
        self.assertIn('force-stop', argv)

    def test_content_false_times_out_preserves_logs_and_does_not_repeat_start_or_restart(self):
        result = self.run_fixture(statuses=[{'coldReady': False, 'contentReady': False}] * 2)
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('content readiness budget', result.report['error'])
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'game-start'])
        self.assertFalse(result.report['app_restart'])
        self.assertEqual(len(result.clients), 2)
        self.assertEqual(result.signals, [])
        self.assertEqual(len((result.output / 'content-status.jsonl').read_text().splitlines()), 2)
        self.assertTrue((result.output / 'game-start.stdout.log').is_file())

    def test_content_read_errors_are_logged_without_retrying_game_mutation(self):
        result = self.run_fixture(self.args(restart_app=True),
                                  statuses=[RuntimeError('fixture unavailable')] * 2)
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'app-force-stop', 'game-start'])
        errors = [json.loads(line)['error'] for line in (result.output / 'content-status.jsonl').read_text().splitlines()]
        self.assertEqual(errors, ['fixture unavailable', 'fixture unavailable'])
        self.assertEqual(result.signals, [])

    def test_failed_start_is_not_retried_and_blocks_identity_connection(self):
        result = self.run_fixture(self.args(restart_app=True), failed_stage='game-start')
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('game-start exit 7', result.report['error'])
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'app-force-stop', 'game-start'])
        self.assertEqual(result.report['stages'][-1]['status'], 'failed')
        self.assertFalse(result.clients)
        self.assertEqual(result.signals, [])

    def test_child_start_error_and_accepted_mutation_are_preserved_in_parent_failure(self):
        child = {'error': 'RuntimeError: Android inspection ended with empty output after accepted launch',
                 'operation_status': 'failed', 'start_command_accepted': True}
        result = self.run_fixture(failed_stage='game-start', failed_code=2, child_report=child)
        self.assertEqual(result.report['status'], 'failed')
        self.assertIn('game-start exit 2', result.report['error'])
        self.assertIn(child['error'], result.report['error'])
        detail = result.report['stages'][-1]['detail']
        self.assertEqual(detail['diagnostic_status'], 'available')
        for key in ('error', 'operation_status', 'start_command_accepted'):
            self.assertEqual(detail[key], child[key])
        self.assertEqual(detail['report_path'], (result.output/'start/report.json').relative_to(prepare.ROOT).as_posix())
        self.assertEqual((result.output/'game-start.stdout.log').read_bytes(), b'fixture stage game-start')
        self.assertEqual((result.output/'game-start.stderr.log').read_bytes(), b'fixture diagnostic')
        self.assertEqual([name for name, _ in result.stages], ['adb-server', 'game-start'])
        self.assertFalse(result.clients)
        self.assertEqual(result.signals, [])

    def test_missing_child_start_report_keeps_original_nonzero_exit_and_does_not_retry(self):
        result = self.run_fixture(failed_stage='game-start', failed_code=2)
        self.assertIn('game-start exit 2', result.report['error'])
        self.assertEqual(result.report['stages'][-1]['detail']['diagnostic_status'], 'missing')
        self.assertEqual(sum(name == 'game-start' for name, _ in result.stages), 1)
        self.assertFalse(result.clients)

    def test_bad_child_json_is_diagnostic_only_and_never_masks_original_stage_exit(self):
        result = self.run_fixture(failed_stage='game-start', failed_code=2, child_report='{invalid json')
        self.assertIn('game-start exit 2', result.report['error'])
        detail = result.report['stages'][-1]['detail']
        self.assertEqual(detail['diagnostic_status'], 'unreadable')
        self.assertIn('JSONDecodeError', detail['diagnostic_error'])
        self.assertEqual(sum(name == 'game-start' for name, _ in result.stages), 1)
        self.assertFalse(result.clients)

    def test_nonobject_child_report_is_rejected_without_masking_original_exit(self):
        result = self.run_fixture(failed_stage='game-start', failed_code=2, child_report=['fixture not report object'])
        self.assertIn('game-start exit 2', result.report['error'])
        detail = result.report['stages'][-1]['detail']
        self.assertEqual(detail['diagnostic_status'], 'unreadable')
        self.assertIn('must be a JSON object', detail['diagnostic_error'])

    def test_stage_timeout_signals_only_its_owned_independent_process_group(self):
        result = self.run_fixture(hanging_stage='adb-server')
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual(result.signals, [(result.processes[0].pid, signal.SIGTERM)])
        self.assertEqual(result.processes[0].waits, [5])
        self.assertEqual(result.report['stages'][0]['status'], 'interrupted_or_timed_out')
        self.assertEqual([name for name, _ in result.stages], ['adb-server'])
        self.assertFalse(result.devices)

    def test_hanging_owned_stage_escalates_only_after_bounded_wait(self):
        result = self.run_fixture(hanging_stage='adb-server', escalate=True)
        pid = result.processes[0].pid
        self.assertEqual(result.signals, [(pid, signal.SIGTERM), (pid, signal.SIGKILL)])
        self.assertEqual(result.processes[0].waits, [5, 5])
        self.assertEqual(result.report['stages'][0]['returncode'], -9)
        self.assertEqual(result.report['status'], 'failed')

    def test_existing_evidence_directory_is_preserved_before_any_child_or_probe(self):
        args = self.args()
        folder = Path(args.output)
        folder.mkdir()
        old = folder / 'previous.json'
        old.write_bytes(b'preserved historical evidence')
        with patch.object(prepare.subprocess, 'Popen') as child, \
                patch.object(prepare.subprocess, 'run') as adb, patch('royale_phase2.probe.ProbeClient') as probe:
            with self.assertRaises(FileExistsError):
                prepare.prepare(args)
            child.assert_not_called()
            adb.assert_not_called()
            probe.assert_not_called()
        self.assertEqual(old.read_bytes(), b'preserved historical evidence')

    def stability_rows(self, result):
        return [json.loads(line) for line in (result.output/'stability.jsonl').read_text().splitlines()]

    def assert_stability_failure_stops_before_connection(self, result):
        self.assertEqual(result.report['status'], 'failed')
        self.assertEqual(result.report['runtime_stability']['status'], 'failed')
        self.assertFalse(result.report['runtime_stability']['stability_verified'])
        self.assertEqual(result.report['stages'][-1]['stage'], 'runtime-stability')
        self.assertEqual(result.report['stages'][-1]['status'], 'failed')
        self.assertNotIn('connection', [name for name, _ in result.stages])
        self.assertFalse((result.output/'connection.json').exists())
        self.assertFalse(result.report['app_restart'])
        self.assertFalse(any('force-stop' in argv for _, argv in result.stages))
        self.assertEqual(sum(name == 'game-start' for name, _ in result.stages), 1)
        self.assertEqual(result.signals, [])

    def test_sixty_second_stability_window_checks_original_identity_through_final_boundary(self):
        # Omitted direct-call option must preserve the production default too.
        args = self.args()
        del args.stability_seconds
        result = self.run_fixture(args)
        self.assertEqual(result.report['status'], 'prepared')
        summary = result.report['runtime_stability']
        self.assertEqual(summary['requested_seconds'], 60)
        self.assertEqual(summary['elapsed_seconds'], 60)
        self.assertEqual(summary['checks_attempted'], 13)
        self.assertEqual(summary['checks_passed'], 13)
        self.assertEqual(summary['status'], 'verified_window')
        self.assertTrue(summary['stability_verified'])
        rows = self.stability_rows(result)
        self.assertEqual([row['elapsed_seconds'] for row in rows], list(range(0, 61, 5)))
        self.assertTrue(all(row['status'] == 'passed' for row in rows))
        self.assertTrue(all(row['expected_identity']['pid'] == row['current_identity']['pid'] == 1978
                            and row['current_identity']['process_start_ticks'] == 65976 for row in rows))
        self.assertEqual(result.stability_timeouts, [30]*14)
        self.assertEqual(summary['final_current_identity'],
                         {'pid': 1978, 'process_start_ticks': 65976, 'process_state': 'S'})
        self.assertEqual(rows[-1]['final_identity_check']['current_identity'], summary['final_current_identity'])
        self.assertEqual([row['stage'] for row in result.report['stages']],
                         ['adb-server', 'game-start', 'identity', 'runtime-stability', 'connection'])
        self.assertEqual(len(result.clients), 14)
        self.assertTrue(all(client.options['timeout'] == 5 for client in result.clients))
        self.assertEqual(result.lease.entries, 1)
        self.assertEqual(result.signals, [])
        self.assertIn('no battle/renderer acceptance', summary['scope'])
        self.assertIn('future uptime guarantee', summary['scope'])

    def test_zero_seconds_is_only_an_immediate_final_check(self):
        result = self.run_fixture(self.args(stability_seconds=0))
        summary = result.report['runtime_stability']
        self.assertEqual(result.report['status'], 'prepared')
        self.assertEqual(summary['requested_seconds'], 0)
        self.assertEqual(summary['checks_attempted'], 1)
        self.assertEqual(summary['checks_passed'], 1)
        self.assertEqual(summary['status'], 'instantaneous_only')
        self.assertFalse(summary['stability_verified'])
        self.assertIn('stability window disabled', summary['scope'])
        self.assertEqual(len(self.stability_rows(result)), 1)
        self.assertEqual(result.elapsed, 0)
        shell = result.devices[-1]
        self.assertEqual(shell, [str(self.adb), '-H', '127.0.0.1', '-P', '5038', '-s',
                                 'fixture:15555', 'shell', '-T', 'su', '0', 'sh', '-s'])
        script = result.scripts[0].decode()
        self.assertIn('pidof nullsroyale.rel.free', script)
        self.assertIn('cat "/proc/$pid/stat"', script)
        for forbidden in ('configure', 'advance', 'force-stop', 'am start', 'kill ', 'setprop'):
            self.assertNotIn(forbidden, script)

    def test_application_exit_sixteen_seconds_after_identity_fails_next_check_without_retry(self):
        def liveness(clock):
            if clock.value >= 16:
                return subprocess.CompletedProcess([], 32, b'UID=0\nPID=\n', b'')
            return self.runtime_text()
        result = self.run_fixture(self.args(stability_seconds=60),
                                  read_results={'stability': [liveness]*13})
        self.assert_stability_failure_stops_before_connection(result)
        rows = self.stability_rows(result)
        self.assertEqual([row['elapsed_seconds'] for row in rows], [0, 5, 10, 15, 20])
        self.assertEqual([row['status'] for row in rows], ['passed']*4+['failed'])
        self.assertIsNone(rows[-1]['current_identity'])
        self.assertEqual(rows[-1]['adb_returncode'], 32)
        self.assertEqual(result.report['runtime_stability']['checks_passed'], 4)
        self.assertEqual(len(result.clients), 5)  # Content + four successful identity reads.
        self.assertEqual(result.elapsed, 20)

    def test_final_status_cannot_hide_process_replacement_or_exit_across_window_boundary(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True}
        for index, (seconds, replacement) in enumerate(((0, self.runtime_text(start=65977)),
                (0, subprocess.CompletedProcess([], 32, b'UID=0\nPID=\n', b'')),
                (60, self.runtime_text(pid=1979)))):
            with self.subTest(seconds=seconds, index=index):
                changed = {'value': False}
                def status_switch(clock):
                    changed['value'] = True
                    if seconds:
                        clock.sleep(seconds+1)  # A slow final status crosses the deadline.
                    return ready
                def current(clock):
                    return replacement if changed['value'] else self.runtime_text()
                result = self.run_fixture(self.args(name=f'final-status-{index}', stability_seconds=seconds),
                    statuses=[ready, status_switch], read_results={'stability': [current, current]})
                self.assert_stability_failure_stops_before_connection(result)
                rows = self.stability_rows(result)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['probe_status'], ready)
                self.assertEqual(rows[0]['current_identity']['pid'], 1978)
                self.assertIn('final_identity_check', rows[0])
                self.assertEqual(result.report['runtime_stability']['checks_passed'], 0)
                self.assertEqual(result.stability_timeouts, [30, 30])
                self.assertEqual(len(result.clients), 2)
                self.assertEqual(result.elapsed, seconds+1 if seconds else 0)

    def test_log_write_crossing_deadline_still_requires_a_final_bracketed_identity_check(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True}
        def nearly_done(clock):
            clock.sleep(59.999)
            return ready
        result = self.run_fixture(self.args(stability_seconds=60), statuses=[ready, nearly_done],
                                  stability_write_delay=.002)
        self.assertEqual(result.report['status'], 'prepared')
        rows = self.stability_rows(result)
        self.assertEqual(len(rows), 2)
        self.assertNotIn('final_identity_check', rows[0])
        self.assertIn('final_identity_check', rows[1])
        self.assertEqual(result.stability_timeouts, [30, 30, 30])
        self.assertEqual(result.report['runtime_stability']['checks_passed'], 2)
        self.assertEqual(result.report['runtime_stability']['final_current_identity']['pid'], 1978)

    def test_reused_pid_with_different_start_time_or_new_pid_is_rejected_before_probe(self):
        for index, (pid, start, state) in enumerate(((1978, 65977, 'S'), (1979, 65976, 'S'),
                                                    (1978, 65976, 'Z'))):
            with self.subTest(pid=pid, start=start, state=state):
                result = self.run_fixture(self.args(name=f'replaced-{index}', stability_seconds=60),
                    read_results={'stability': [self.runtime_text(pid, start, state=state)]})
                self.assert_stability_failure_stops_before_connection(result)
                row = self.stability_rows(result)[0]
                self.assertEqual(row['current_identity']['pid'], pid)
                self.assertEqual(row['current_identity']['process_start_ticks'], start)
                self.assertIsNone(row['probe_status'])
                self.assertEqual(len(result.clients), 1)
                self.assertEqual(result.elapsed, 0)

    def test_stability_timeout_or_empty_read_fails_immediately_without_read_retry(self):
        timeout = subprocess.TimeoutExpired('fixture stability read', 30, output=b'partial')
        for index, response in enumerate((timeout, b'')):
            with self.subTest(response=type(response).__name__):
                result = self.run_fixture(self.args(name=f'read-error-{index}', stability_seconds=60),
                                          read_results={'stability': [response, self.runtime_text()]})
                self.assert_stability_failure_stops_before_connection(result)
                rows = self.stability_rows(result)
                self.assertEqual(len(rows), 1)
                self.assertEqual(result.stability_timeouts, [30])
                self.assertEqual(len(result.clients), 1)
                self.assertEqual(rows[0]['status'], 'failed')
                if isinstance(response, subprocess.TimeoutExpired):
                    self.assertTrue(rows[0]['adb_timed_out'])
                    self.assertEqual(result.elapsed, 30)
                else:
                    self.assertIn('empty output', rows[0]['error'])
                    self.assertEqual(result.elapsed, 0)

    def test_stability_probe_failure_or_cold_readiness_regression_is_not_retried(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True}
        bad = [RuntimeError('fixture probe EOF'), {'ok': True, 'coldReady': False, 'contentReady': True},
               {'ok': True, 'coldReady': True, 'contentReady': False}, {'ok': False}, None]
        for index, value in enumerate(bad):
            with self.subTest(value=value):
                result = self.run_fixture(self.args(name=f'probe-error-{index}', stability_seconds=60),
                                          statuses=[ready, value, ready])
                self.assert_stability_failure_stops_before_connection(result)
                rows = self.stability_rows(result)
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]['status'], 'failed')
                self.assertEqual(len(result.clients), 2)
                self.assertEqual(result.stability_timeouts, [30])
                self.assertEqual(result.elapsed, 0)

    def test_bad_or_oversize_identity_receipt_blocks_stability_reads_and_connection(self):
        values = [b'not json', b'[]', b'{"pid":1978}',
                  {'pid': True, 'process_start_ticks': 65976},
                  {'pid': 1978, 'process_start_ticks': 0}, b' '*(2*1024*1024+1)]
        for index, payload in enumerate(values):
            with self.subTest(index=index):
                result = self.run_fixture(self.args(name=f'identity-invalid-{index}'), identity_payload=payload)
                self.assert_stability_failure_stops_before_connection(result)
                self.assertEqual(result.report['runtime_stability']['checks_attempted'], 0)
                self.assertEqual(result.stability_timeouts, [])
                self.assertEqual(len(result.clients), 1)

    def test_stability_read_cancellation_saves_evidence_without_connection_or_retry(self):
        result = self.run_fixture(self.args(stability_seconds=60),
            read_results={'stability': [KeyboardInterrupt('fixture stability cancelled')]}, expect_interrupt=True)
        self.assertEqual(result.report['status'], 'interrupted')
        self.assertEqual(result.report['runtime_stability']['status'], 'interrupted')
        self.assertNotIn('connection', [name for name, _ in result.stages])
        self.assertEqual(self.stability_rows(result)[0]['status'], 'interrupted')
        self.assertEqual(result.stability_timeouts, [30])
        self.assertEqual(result.signals, [])

    def test_cli_restart_is_opt_in_and_main_never_changes_real_signal_handlers(self):
        for option, expected in (([], False), (['--restart-app'], True)):
            with self.subTest(option=option), \
                    patch('sys.argv', ['native_prepare.py', '--manifest', 'fixture', '--output', 'fixture', *option]), \
                    patch.object(prepare, 'configure'), patch.object(prepare.signal, 'signal'), \
                    patch.object(prepare, 'prepare', return_value={'status': 'prepared'}) as operation, \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(prepare.main(), 0)
                self.assertEqual(operation.call_args.args[0].restart_app, expected)
                self.assertEqual(operation.call_args.args[0].stability_seconds, 60)

    def test_cli_stability_range_is_validated_before_prepare(self):
        for value in ('-1', '181', 'nan', 'inf'):
            with self.subTest(value=value), \
                    patch('sys.argv', ['native_prepare.py', '--manifest', 'fixture', '--output', 'fixture',
                                       '--stability-seconds', value]), \
                    patch.object(prepare, 'configure'), patch.object(prepare.signal, 'signal'), \
                    patch.object(prepare, 'prepare') as operation, \
                    patch('sys.stderr', new_callable=io.StringIO):
                with self.assertRaises(SystemExit) as caught:
                    prepare.main()
                self.assertEqual(caught.exception.code, 2)
                operation.assert_not_called()


if __name__ == '__main__':
    unittest.main()

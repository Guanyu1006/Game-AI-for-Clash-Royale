"""Launcher boundaries with every VM/ADB/process/server operation mocked."""
from contextlib import ExitStack, contextmanager, redirect_stderr, redirect_stdout
import base64
import hashlib
import io
import json
from pathlib import Path
import signal
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from tools.cloud import replay_app as app
from tools.cloud import software_android_vm as vm


class ReplayAppContracts(unittest.TestCase):
    def setUp(self):
        parent = app.local('.tmp/replay-app-tests'); parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)

    def args(self, **changes):
        values = dict(output=str(self.folder/'run'), port=8772, source=None, connection=None,
                      cache=str(self.folder/'cache'), manifest='fixture-manifest.json',
                      sources=str(self.folder/'sources'), display_compat=True, memory_mib=3072, display_fps=5,
                      capture_ticks=100, frame_wait=0.2)
        values.update(changes)
        return SimpleNamespace(**values)

    def run_launcher(self, args=None, *, states=None, port_error=False, locked=False,
                     failed_stage=None, interrupted_stage=None, imported=None, real_import=False):
        args = args or self.args()
        stages = []
        lease = SimpleNamespace(active=False)
        @contextmanager
        def lock():
            if locked: raise RuntimeError('Another phase3 controller owns native RPC')
            lease.active = True
            try: yield
            finally: lease.active = False
        def stage(folder, report, label, argv):
            self.assertFalse(lease.active, 'Launcher must release its check lease before child prepare/browser')
            stages.append((label, argv))
            if interrupted_stage == label: raise KeyboardInterrupt()
            if failed_stage == label: raise RuntimeError('fixture stage failed')
            if label == 'prepare':
                (folder/'prepare').mkdir()
                app.write(folder/'prepare/report.json', {'status': 'prepared'})
                app.write(folder/'prepare/connection.json', {'fixture': True})
        values = states or [{'status': 'running', 'action': 'reuse'}]
        from tools.cloud.native_browser import import_source
        with ExitStack() as stack:
            stack.enter_context(redirect_stdout(io.StringIO()))
            stack.enter_context(redirect_stderr(io.StringIO()))
            port = stack.enter_context(patch.object(app, 'port_available', side_effect=OSError() if port_error else None))
            stack.enter_context(patch.object(app, 'native_lock', lock))
            inspect = stack.enter_context(patch.object(app, 'inspect_vm', side_effect=values))
            stack.enter_context(patch.object(app, '_run_stage', side_effect=stage))
            connection = stack.enter_context(patch('royale_phase2.connection.load_connection', return_value=SimpleNamespace()))
            stack.enter_context(patch('royale_phase2.public_replay.PublicReplayPlan.load'))
            import_call = stack.enter_context(patch('tools.cloud.native_browser.import_source',
                side_effect=import_source if real_import else None,
                return_value=imported or {'source_id': 'fixture-source'}))
            result = app.launch(args)
        report = json.loads((Path(args.output)/'report.json').read_text())
        return result, stages, report, inspect, connection, import_call

    def test_occupied_port_or_controller_does_not_launch_any_child(self):
        for name, changes in (('port', {'port_error': True}), ('lock', {'locked': True})):
            with self.subTest(name=name):
                result, stages, report, inspect, _, _ = self.run_launcher(
                    self.args(output=str(self.folder/name)), **changes)
                self.assertEqual(result, 2); self.assertEqual(stages, [])
                self.assertEqual(report['status'], 'failed'); inspect.assert_not_called()

    def test_running_vm_is_reused_and_prepare_releases_lease(self):
        result, stages, report, inspect, _, _ = self.run_launcher()
        self.assertEqual(result, 0)
        self.assertEqual([x[0] for x in stages], ['prepare', 'browser'])
        prepare, browser = stages[0][1], stages[1][1]
        self.assertEqual(prepare[prepare.index('--stability-seconds')+1], '15')
        self.assertIn('--display-compat', prepare)
        self.assertIn('--connection', browser); self.assertIn('--sources', browser)
        self.assertEqual(browser[browser.index('--capture-ticks')+1], '100')
        self.assertEqual(browser[browser.index('--frame-wait')+1], '0.2')
        self.assertNotIn('--restart-app', prepare)
        self.assertFalse(report['automatic_playback']); self.assertFalse(report['automatic_reset'])
        inspect.assert_called_once()

    def test_guest_display_compat_can_be_explicitly_disabled(self):
        result, stages, _, _, _, _ = self.run_launcher(self.args(display_compat=False))
        self.assertEqual(result, 0)
        self.assertNotIn('--display-compat', stages[0][1])

    def test_capture_density_overrides_are_forwarded_and_invalid_cli_values_stop_before_launch(self):
        result, stages, _, _, _, _ = self.run_launcher(self.args(capture_ticks=20, frame_wait=1.1))
        self.assertEqual(result, 0)
        browser = stages[-1][1]
        self.assertEqual(browser[browser.index('--capture-ticks')+1], '20')
        self.assertEqual(browser[browser.index('--frame-wait')+1], '1.1')
        for flag, value in (('--capture-ticks', '0'), ('--capture-ticks', '201'),
                            ('--frame-wait', '-1'), ('--frame-wait', '11'),
                            ('--frame-wait', 'nan'), ('--frame-wait', 'inf')):
            with self.subTest(flag=flag, value=value), patch.object(app, 'launch') as launch, \
                    redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                app.main([flag, value])
            launch.assert_not_called()

    def test_only_verified_stopped_installation_is_resumed_once(self):
        states = [{'status': 'stopped', 'resume_attempt': 'outputs/fixture-latest'},
                  {'status': 'running', 'action': 'reuse'}]
        result, stages, _, inspect, _, _ = self.run_launcher(states=states)
        self.assertEqual(result, 0)
        self.assertEqual([s[0] for s in stages], ['vm-start', 'prepare', 'browser'])
        argv = stages[0][1]
        for flag, expected in (('--resume-attempt', 'outputs/fixture-latest'), ('--vcpus', '2'),
                               ('--timeout-multiplier', '20'), ('--tb-size-mib', '64'),
                               ('--memory-mib', '3072'), ('--display-fps', '5')):
            self.assertEqual(argv[argv.index(flag)+1], expected)
        self.assertEqual(inspect.call_count, 2)

    def test_unknown_vm_and_failed_prepare_never_start_browser(self):
        result, stages, report, _, _, _ = self.run_launcher(states=[ValueError('unknown VM identity')])
        self.assertEqual(result, 2); self.assertEqual(stages, [])
        self.assertIn('unknown VM identity', report['error'])
        result, stages, report, _, _, _ = self.run_launcher(
            self.args(output=str(self.folder/'failed-prepare')), failed_stage='prepare')
        self.assertEqual(result, 2); self.assertEqual([s[0] for s in stages], ['prepare'])

    def test_explicit_connection_skips_vm_prepare_and_imports_without_opening(self):
        source = self.folder/'input.json'; source.write_text('{"source":"fixture"}')
        result, stages, report, inspect, connection, imported = self.run_launcher(
            self.args(connection=str(self.folder/'explicit.json'), source=str(source)))
        self.assertEqual(result, 0); inspect.assert_not_called(); connection.assert_called_once()
        self.assertEqual([s[0] for s in stages], ['browser'])
        self.assertEqual(report['imported_source']['source_id'], 'fixture-source')
        self.assertEqual(base64.b64decode(imported.call_args.args[0]['raw_base64']), source.read_bytes())
        self.assertNotIn('source_open', str(stages)); self.assertNotIn('reset', str(stages))

    def test_source_import_preserves_original_utf8_bom_crlf_and_hash(self):
        from tests_cloud.test_public_replay import public_fixture
        source = self.folder/'source-with-bom-crlf.json'
        raw = b'\xef\xbb\xbf'+json.dumps(public_fixture(), ensure_ascii=False, indent=2).replace('\n', '\r\n').encode('utf-8')
        source.write_bytes(raw)
        result, stages, report, inspect, _, imported = self.run_launcher(
            self.args(connection=str(self.folder/'explicit.json'), source=str(source)), real_import=True)
        self.assertEqual(result, 0); inspect.assert_not_called()
        self.assertEqual([stage[0] for stage in stages], ['browser'])
        saved = report['imported_source']
        self.assertEqual(app.local(saved['raw_path']).read_bytes(), raw)
        self.assertEqual(saved['raw_sha256'], hashlib.sha256(raw).hexdigest())
        self.assertEqual(source.read_bytes(), raw)
        self.assertEqual(base64.b64decode(imported.call_args.args[0]['raw_base64']), raw)
        self.assertNotIn('text', imported.call_args.args[0])

    def test_ctrl_c_during_prepare_preserves_report_and_stops_progress(self):
        result, stages, report, _, _, _ = self.run_launcher(interrupted_stage='prepare')
        self.assertEqual(result, 130); self.assertEqual(report['status'], 'interrupted')
        self.assertEqual([s[0] for s in stages], ['prepare'])

    def test_child_interrupt_targets_only_the_owned_process_group(self):
        class Child:
            pid = 991234
            code = None
            def poll(self): return self.code
            def wait(self, timeout):
                if timeout == 1: raise KeyboardInterrupt()
                self.code = 0
                return 0
        folder = self.folder/'child'; folder.mkdir()
        report = {'stages': []}
        with redirect_stdout(io.StringIO()), patch.object(app.subprocess, 'Popen', return_value=Child()), \
                patch.object(app.os, 'killpg') as signal_group:
            with self.assertRaises(KeyboardInterrupt):
                app._run_stage(folder, report, 'prepare', ['fixture', 'never-executed'])
        signal_group.assert_called_once_with(991234, signal.SIGINT)
        self.assertEqual(report['stages'][0]['status'], 'interrupted')
        self.assertTrue((folder/'prepare.stdout.log').is_file())

    def test_vm_identity_guard_reuses_exact_record_and_refuses_reused_pid(self):
        cache = self.folder/'cache'; cache.mkdir()
        attempt = self.folder/'attempt'; attempt.mkdir()
        disk = attempt/'android.ext4'; disk.write_bytes(b'fixture disk')
        record = {'attempt': str(attempt), 'pid': 999999, 'process_starttime': '12345'}
        (cache/'active-vm.json').write_text(json.dumps(record))
        with patch.object(vm, 'checked_record', return_value=(record, disk)), \
                patch.object(vm, 'process_identity', return_value='12345'), \
                patch.object(vm, 'require_record', return_value=record), \
                patch.object(vm, 'qmp', return_value={'result': {'return': {'status': 'running'}}}), \
                patch.object(vm, 'record_stopped') as stopped:
            result = app.inspect_vm(cache)
            self.assertEqual(result['action'], 'reuse'); stopped.assert_not_called()
        with patch.object(vm, 'checked_record', return_value=(record, disk)), \
                patch.object(vm, 'process_identity', return_value='different-start-time'), \
                patch.object(vm, 'record_stopped') as stopped:
            with self.assertRaisesRegex(ValueError, '其他进程'):
                app.inspect_vm(cache)
            stopped.assert_not_called()


if __name__ == '__main__':
    unittest.main()

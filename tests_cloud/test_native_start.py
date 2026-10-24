"""Only fake ADB process fixtures: no device, daemon or probe is contacted."""
from pathlib import Path
from contextlib import nullcontext
import json
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.cloud import native_start as start


class FakeADB:
    def __init__(self, pids, *, launch_stdout=None, launch_stderr='', forwards='',
                 android_api=31, immersive_value='null', immersive_write_persists=True,
                 check_outputs=None):
        self.pids = iter(pids)
        self.calls = []
        self.launch_stdout = launch_stdout
        self.launch_stderr = launch_stderr
        self.forwards = forwards
        self.android_api = android_api
        self.immersive_value = immersive_value
        self.immersive_write_persists = immersive_write_persists
        self.check_outputs = check_outputs or {}

    def __call__(self, label, argv, script, timeout):
        self.calls.append((label, argv, script))
        if label == 'adb-version':
            text = 'Android Debug Bridge version 1.0.41\nVersion interface-only\n'
        elif label.startswith('server-version-'):
            text = '0029'
        elif label == 'get-state':
            text = 'device\n'
        elif label == 'check-app' or label.startswith('poll-app-'):
            if label in self.check_outputs:
                text = self.check_outputs[label]
            else:
                pid = next(self.pids)
                text = ('UID=0\nBOOT=1\nAPK_BEGIN\n'
                        'package:/data/app/nullsroyale.rel.free-fixture/base.apk\nAPK_END\n'
                        f'PID={pid if pid is not None else ""}\n')
        elif label == 'start-app':
            text = (self.launch_stdout if self.launch_stdout is not None
                    else f'Starting: Intent {{ cmp={start.ACTIVITY} }}\n')
        elif label == 'list-forwards':
            text = self.forwards
        elif label == 'forward-probe':
            text = ''
        elif label in ('check-immersive', 'verify-immersive'):
            text = f'UID=0\nSDK={self.android_api}\nIMMERSIVE_VALUE={self.immersive_value}\n'
        elif label == 'confirm-immersive':
            if self.immersive_write_persists:
                self.immersive_value = 'confirmed'
            text = ''
        else:
            raise AssertionError('Unexpected ADB action: ' + label)
        stderr = self.launch_stderr if label == 'start-app' else ''
        return subprocess.CompletedProcess(argv, 0, text.encode(), stderr.encode())


class NativeStartTests(unittest.TestCase):
    def setUp(self):
        (start.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='native-start-', dir=start.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = self.folder / 'manifest.json'
        self.meta = {'client_package': start.PACKAGE, 'ticks_per_second': 20}
        self.manifest.write_text(json.dumps(self.meta))
        self.adb = self.folder / 'adb'
        self.adb.write_bytes(b'not executable; interface fixtures only')

    def run_fixture(self, pids, name='evidence', *, fake_options=None,
                    expected_status='interface_only', **kwargs):
        runner = FakeADB(pids, **(fake_options or {}))
        with patch.object(start, 'verify_manifest', return_value={'manifest': self.meta, 'fingerprint': 'fixture'}):
            report = start.check_or_start(self.manifest, self.folder / name, serial='fixture:5555',
                adb=self.adb, adb_port=5038, runner=runner, sleeper=lambda _: None, **kwargs)
        self.assertEqual(report['status'], expected_status, report)
        self.assertFalse(report['native_battle_execution_verified'])
        self.assertFalse(report['native_identity_verified'])
        return report, runner

    def test_asynchronous_pid_and_exhausted_polling_preserve_accepted_launch(self):
        report, runner = self.run_fixture([None, None, None, 2832], start=True, polls=3)
        self.assertEqual(report['operation_status'], 'running')
        self.assertEqual(report['process']['pid'], 2832)
        self.assertEqual(report['pid_poll_attempts'], 3)
        self.assertTrue(report['start_command_accepted'])
        self.assertEqual(sum(label == 'start-app' for label, _, _ in runner.calls), 1)
        pending, runner = self.run_fixture([None, None, None], name='pending', start=True, polls=2)
        self.assertEqual(pending['operation_status'], 'start_pending')
        self.assertTrue(pending['start_command_accepted'])
        self.assertNotIn('error', pending)
        self.assertEqual(pending['start_commands'], 1)
        for command in pending['commands']:
            for key in ('stdin', 'stdout', 'stderr'):
                if key in command:
                    self.assertEqual(start.reference(command[key]['path'])['sha256'], command[key]['sha256'])

    def test_existing_pid_never_launches_again(self):
        report, runner = self.run_fixture([2832], start=True)
        self.assertTrue(report['already_running'])
        self.assertEqual(report['start_commands'], 0)
        self.assertFalse(report['requested']['confirm_immersive'])
        self.assertNotIn('immersive_confirmation', report)
        self.assertFalse(any(label == 'start-app' or label.startswith('poll-app-') for label, _, _ in runner.calls))
        self.assertFalse(any(script and ('am start' in script or 'svc power' in script) for _, _, script in runner.calls))

    def test_empty_pid_poll_preserves_accepted_launch_and_reports_unknown_transport(self):
        report, runner = self.run_fixture([None], start=True, expected_status='unavailable',
            fake_options={'check_outputs': {'poll-app-01': ''}})
        self.assertIn('Empty Android shell evidence', report['error'])
        self.assertIn('unknown', report['error'])
        self.assertNotIn('already-rooted', report['error'])
        self.assertTrue(report['start_command_accepted'])
        self.assertEqual(report['start_commands'], 1)
        self.assertEqual(sum(label == 'start-app' for label, _, _ in runner.calls), 1)
        self.assertFalse(any(label == 'forward-probe' for label, _, _ in runner.calls))

    def test_missing_or_duplicate_uid_is_not_treated_as_confirmed_nonroot(self):
        for text in ('BOOT=1\nPID=123\n', 'UID=0\nUID=0\n'):
            with self.subTest(text=text):
                with self.assertRaisesRegex(ValueError, 'root status is unknown'):
                    start.parse_check(text)
        with self.assertRaisesRegex(ValueError, 'already-rooted'):
            start.parse_check('UID=2000\nBOOT=1\nPID=123\n')

    def test_default_check_never_forwards_probes_or_mutates(self):
        with patch.object(start, 'probe_status', side_effect=AssertionError('Probe not requested')):
            report, runner = self.run_fixture([None])
        self.assertEqual(report['operation_status'], 'not_running')
        self.assertEqual(report['native_probe_rpc_calls'], 0)
        self.assertEqual(report['start_commands'], 0)
        prohibited = {'forward', 'start-server', 'kill-server', 'connect', 'install', 'push', 'root'}
        for _, argv, script in runner.calls:
            self.assertFalse(set(argv) & prohibited)
            self.assertFalse(script and ('am start' in script or 'svc power' in script))
            self.assertFalse(script and 'settings ' in script)
        device_commands = [argv for label, argv, _ in runner.calls if label in ('get-state', 'check-app')]
        self.assertTrue(all('-H' in argv and '-s' in argv for argv in device_commands))

    def test_am_errors_with_zero_exit_are_rejected_before_pid_polling(self):
        for stream in ('stdout', 'stderr'):
            with self.subTest(stream=stream):
                report, runner = self.run_fixture([None], name='error-' + stream,
                    start=True, fake_options={'launch_' + stream: 'Error type 3\nError: Activity does not exist.\n'},
                    expected_status='unavailable')
                self.assertIn('am start reported an error', report['error'])
                self.assertFalse(report['start_command_accepted'])
                self.assertEqual(report['start_commands'], 1)
                self.assertFalse(any(label.startswith('poll-app-') for label, _, _ in runner.calls))

    def test_existing_forward_is_reused_without_mutation(self):
        report, runner = self.run_fixture([2832], forward=True,
            fake_options={'forwards': 'fixture:5555 tcp:26789 tcp:26789\n'})
        self.assertEqual(report['forward'], 'already_present')
        self.assertFalse(any(label == 'forward-probe' for label, _, _ in runner.calls))

    def test_conflicting_forward_is_preserved(self):
        report, runner = self.run_fixture([2832], forward=True, expected_status='unavailable',
            fake_options={'forwards': 'other-device tcp:26789 tcp:26789\n'})
        self.assertIn('no existing mapping was changed', report['error'])
        self.assertFalse(any(label == 'forward-probe' for label, _, _ in runner.calls))
        self.assertEqual(report['native_probe_rpc_calls'], 0)

    def test_explicit_new_forward_uses_no_rebind(self):
        report, runner = self.run_fixture([2832], forward=True)
        self.assertEqual(report['forward'], 'created')
        requests = [argv for label, argv, _ in runner.calls if label == 'forward-probe']
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0][-4:], ['forward', '--no-rebind', 'tcp:26789', 'tcp:26789'])
        self.assertEqual(report['native_probe_rpc_calls'], 0)

    def test_explicit_status_fixture_cannot_claim_native_execution(self):
        clients = []
        class FakeProbe:
            def __init__(self, **kwargs):
                self.options = kwargs
                self.closed = False
                clients.append(self)
            def status(self):
                return {'ok': True, 'contentReady': False}
            def close(self):
                self.closed = True
        with patch('royale_phase3.common.native_lock', return_value=nullcontext()):
            report, runner = self.run_fixture([2832], probe=True, probe_factory=FakeProbe)
        self.assertEqual(report['native_probe_rpc_calls'], 1)
        self.assertTrue(report['probe_reachable'])
        self.assertEqual(len(clients), 1)
        self.assertTrue(clients[0].closed)
        self.assertEqual(clients[0].options, {'host': '127.0.0.1', 'port': 26789, 'timeout': 3})
        self.assertFalse(any(label == 'forward-probe' for label, _, _ in runner.calls))

    def test_fixture_cannot_implicitly_contact_real_probe(self):
        with self.assertRaisesRegex(ValueError, 'cannot access the real probe'):
            start.check_or_start(self.manifest, self.folder / 'no-contact', serial='fixture:5555',
                adb=self.adb, runner=FakeADB([2832]), probe=True)
        self.assertFalse((self.folder / 'no-contact').exists())

    def test_explicit_immersive_confirmation_only_changes_system_tutorial(self):
        report, runner = self.run_fixture([2832], confirm_immersive=True)
        evidence = report['immersive_confirmation']
        self.assertEqual(evidence['before'], 'null')
        self.assertEqual(evidence['after'], 'confirmed')
        self.assertTrue(evidence['setting_changed'])
        self.assertFalse(evidence['prompt_dismissal_verified'])
        self.assertFalse(evidence['window_focus_verified'])
        self.assertEqual(report['native_probe_rpc_calls'], 0)
        self.assertEqual(report['start_commands'], 0)
        mutations = [script for label, _, script in runner.calls if label == 'confirm-immersive']
        self.assertEqual(len(mutations), 1)
        self.assertIn('settings --user 0 put secure immersive_mode_confirmations confirmed', mutations[0])
        self.assertFalse(any(label in ('start-app', 'forward-probe') for label, _, _ in runner.calls))

    def test_already_confirmed_immersive_setting_is_not_written_again(self):
        report, runner = self.run_fixture([2832], confirm_immersive=True,
            fake_options={'immersive_value': 'confirmed'})
        self.assertEqual(report['immersive_confirmation']['status'], 'already_confirmed')
        self.assertFalse(report['immersive_confirmation']['setting_changed'])
        self.assertFalse(any(label in ('confirm-immersive', 'verify-immersive') for label, _, _ in runner.calls))

    def test_requested_immersive_confirmation_precedes_single_new_launch(self):
        report, runner = self.run_fixture([None, 2832], start=True, confirm_immersive=True)
        labels = [label for label, _, _ in runner.calls]
        self.assertLess(labels.index('verify-immersive'), labels.index('start-app'))
        self.assertEqual(labels.count('start-app'), 1)
        self.assertEqual(report['operation_status'], 'running')

    def test_immersive_confirmation_rejects_other_android_versions_before_write(self):
        report, runner = self.run_fixture([None], start=True, confirm_immersive=True,
            fake_options={'android_api': 30}, expected_status='unavailable')
        self.assertIn('Android 12/API 31', report['error'])
        self.assertFalse(any(label in ('confirm-immersive', 'start-app') for label, _, _ in runner.calls))

    def test_immersive_confirmation_requires_successful_readback(self):
        report, runner = self.run_fixture([2832], confirm_immersive=True,
            fake_options={'immersive_write_persists': False}, expected_status='unavailable')
        self.assertEqual(report['immersive_confirmation']['status'], 'readback_failed')
        self.assertIn('did not persist', report['error'])
        self.assertFalse(report['immersive_confirmation']['prompt_dismissal_verified'])


if __name__ == '__main__':
    unittest.main()

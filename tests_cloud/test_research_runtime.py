"""Warm-runtime contracts with fake ADB/RPC; never contact Android or start a VM."""
from contextlib import contextmanager, ExitStack
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import Mock, patch

from royale_phase3.common import ROOT, digest, read, reference, write
from tools.cloud import research_runtime as runtime
from tools.cloud.native_identity import discovery_script


class ResearchRuntimeReuseTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='runtime-reuse-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.manifest = self.folder / 'manifest.json'
        self.adb = self.folder / 'adb'; self.adb.write_bytes(b'nonexecutable ADB fixture')
        self.package = 'nullsroyale.rel.free'
        write(self.manifest, {'client_package': self.package})
        self.values = runtime.settings_for({'cache': str(self.folder / 'cache'),
            'manifest': str(self.manifest), 'adb': str(self.adb)})
        self.paths = {'apk': '/data/app/sample/base.apk',
                      'libg': '/data/app/sample/lib/arm64/libg.so',
                      'probe': '/data/app/sample/lib/arm64/libcrprobe.so',
                      'update_root': '/data/data/nullsroyale.rel.free/update'}
        self.identity = self.folder / 'identity.json'
        write(self.identity, {'pid': 123, 'process_start_ticks': 456,
            'device_serial': self.values['serial'], 'version_fingerprint': 'fixture-version',
            'mapped_paths': self.paths})
        self.connection = self.folder / 'connection.json'
        write(self.connection, {'schema': 'royale-native-connection.v1', 'transport': 'tcp-loopback',
            'host': '127.0.0.1', 'port': 26789, 'timeout': 180.,
            'version_manifest': str(self.manifest.relative_to(ROOT)),
            'manifest_sha256': reference(self.manifest)['sha256'], 'identity_path': str(self.identity)})
        self.checked = {'ready': True, 'version_fingerprint': 'fixture-version',
                        'connection': read(self.connection), 'manifest_sha256': reference(self.manifest)['sha256'],
                        'checks': {'identity': {'status': 'pass', 'path': str(self.identity),
                                                 'sha256': reference(self.identity)['sha256']}}}
        self.prior = self.folder / 'prior/report.json'
        write(self.prior, {'status': 'prepared', 'settings': self.values,
            'connection': str(self.connection.relative_to(ROOT)), 'version_fingerprint': 'fixture-version',
            'prepared_report': str((self.folder / 'prior/prepare/report.json').relative_to(ROOT))})
        write(self.prior.parent / 'connection-check.json', self.checked)
        self.pointer = self.folder / 'pointer.json'
        self.vm = {'status': 'running', 'record': {'pid': 900, 'process_starttime': '42', 'attempt': 'fixture'}}
        write(self.pointer, {'connection': str(self.connection.relative_to(ROOT)), 'settings': self.values,
            'report_path': str(self.prior.relative_to(ROOT)), 'vm_record_fingerprint': digest(self.vm['record'])})
        self.commands = []; self.held = {'manager': False, 'native': False}
        self.discoveries = []

    def discovery(self, *, pid=123, start=456, paths=None):
        paths = paths or self.paths
        stat = str(pid) + ' (fixture game) ' + ' '.join(['S'] + ['0'] * 18 + [str(start)])
        maps = '\n'.join('1000-2000 r-xp 00000000 00:00 1 ' + path for path in
            [paths['apk'], paths['libg'], paths['probe'], paths['update_root'] + '/assets.scdb'])
        return (f'UID=0\nAPK_BEGIN\npackage:{paths["apk"]}\nAPK_END\nPID={pid}\n'
                f'STAT_BEGIN\n{stat}\nSTAT_END\nMAPS_BEGIN\n{maps}\nMAPS_END\n')

    @contextmanager
    def lease(self, name):
        self.assertFalse(self.held[name]); self.held[name] = True
        try:
            yield
        finally:
            self.held[name] = False

    def environment(self, *, checked=None, vm=None, status=None, cancellation=None):
        owner = self

        class Journal:
            def __init__(self, folder, adb, serial, port):
                self.records = []
                owner.assertTrue(owner.held['manager']); owner.assertTrue(owner.held['native'])
                owner.assertEqual((adb, serial, port), (str(owner.adb), owner.values['serial'], owner.values['adb_port']))

            def run(self, label, argv, timeout):
                owner.assertEqual(label, 'adb-version')
                self.records.append({'label': label, 'command': argv}); return 'Android Debug Bridge version 1.0.41\n'

            def device(self, label, args, script, timeout):
                owner.assertTrue(owner.held['native']); owner.assertTrue(owner.held['manager'])
                owner.assertEqual(args, ['shell', '-T', 'su', '0', 'sh', '-s'])
                owner.assertEqual(script, discovery_script(owner.package))
                owner.commands.append(label); self.records.append({'label': label})
                if cancellation:
                    cancellation.set()
                return owner.discoveries.pop(0) if owner.discoveries else owner.discovery()

        def prepare(folder, report, label, argv, timeout, cancel_event):
            self.assertFalse(self.held['native'], 'prepare child must be able to acquire its own native lease')
            self.assertTrue(self.held['manager'])
            self.assertEqual(label, 'prepare')
            prepared_connection = folder / 'prepare/connection.json'
            write(folder / 'prepare/report.json', {'status': 'prepared',
                  'connection': str(prepared_connection.relative_to(ROOT))})
            write(prepared_connection, read(self.connection))

        client = Mock()

        def native_status():
            self.assertTrue(self.held['native']); self.assertTrue(self.held['manager'])
            if isinstance(status, list):
                return status.pop(0)
            return status or {'ok': True, 'coldReady': True, 'contentReady': True, 'mode': 'headless'}

        client.status.side_effect = native_status
        stack = ExitStack()
        stack.enter_context(patch.object(runtime, 'RUNTIME_POINTER', str(self.pointer)))
        stack.enter_context(patch.object(runtime, 'manager_lock', side_effect=lambda: self.lease('manager')))
        stack.enter_context(patch.object(runtime, 'native_lock', side_effect=lambda: self.lease('native')))
        stack.enter_context(patch('tools.cloud.native_runtime_state.inspect_vm', return_value=vm or self.vm))
        stack.enter_context(patch('royale_phase2.connection.check_connection', return_value=checked or self.checked))
        stack.enter_context(patch('tools.cloud.native_identity.Journal', Journal))
        probe = stack.enter_context(patch('royale_phase2.probe.ProbeClient', return_value=client))
        stage = stack.enter_context(patch.object(runtime, '_stage', side_effect=prepare))
        return stack, stage, client, probe

    def test_same_process_uses_fresh_identity_and_status_without_prepare(self):
        stack, stage, client, probe = self.environment()
        with stack:
            report = runtime.ensure_runtime(self.folder / 'new', self.values)
        stage.assert_not_called(); client.status.assert_called_once(); client.close.assert_called_once()
        self.assertEqual(self.commands, ['discover-runtime-before', 'discover-runtime-after'])
        self.assertTrue(report['reused']); self.assertEqual(report['status'], 'prepared')
        evidence = read(report['fresh_identity_check'])
        self.assertTrue(evidence['fresh_process_identity_verified'])
        self.assertFalse(evidence['remote_files_rehashed']); self.assertEqual(evidence['rpc_calls'], 1)
        self.assertEqual(evidence['before']['pid'], 123); self.assertEqual(evidence['after']['start_ticks'], 456)
        self.assertEqual(evidence['sources']['identity'], reference(self.identity))
        self.assertEqual(read(self.pointer)['vm_record_fingerprint'], digest(self.vm['record']))
        self.assertEqual(probe.call_args.kwargs['timeout'], 5.)
        self.assertFalse(any(self.held.values()))

    def test_stopped_vm_uses_short_current_log_root_without_executing_launcher(self):
        stack, stage, client, probe = self.environment(vm={'status': 'stopped', 'resume_attempt': 'fixture'})
        stage.side_effect = RuntimeError('fixture blocks VM execution')
        with stack, self.assertRaisesRegex(RuntimeError, 'fixture blocks VM execution'):
            runtime.ensure_runtime(self.folder / 'cold', self.values)
        self.assertEqual(stage.call_args.args[2], 'vm-start')
        argv = stage.call_args.args[3]
        output = argv[argv.index('--output') + 1]
        self.assertEqual(output, 'research/vm')
        # software_android_vm uses a timestamped child and a filesystem QMP
        # socket. Per-experiment output paths exceed its 104-byte limit here.
        socket_path = ROOT / output / '20261107T073000.123456Z-vm/qmp.sock'
        self.assertLess(len(str(socket_path).encode()), 104)
        client.status.assert_not_called()
        probe.assert_not_called()

    def test_headless_process_is_reused_only_after_mode_check(self):
        stack, stage, client, _ = self.environment()
        with stack:
            report = runtime.ensure_runtime(self.folder / 'headless-reuse', self.values,
                                            required_mode='headless')
        stage.assert_not_called(); client.status.assert_called_once()
        self.assertTrue(report['reused'])
        self.assertEqual(report['requested_mode'], 'headless')
        self.assertEqual(report['mode_check']['actual_mode'], 'headless')
        self.assertTrue(report['mode_check']['passed'])
        self.assertFalse(report['app_restart_requested'])

    def test_native_renderer_requires_explicit_restart_and_new_mode_check(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True}
        statuses = [{**ready, 'mode': 'native-render'}, {**ready, 'mode': 'headless'}]
        stack, stage, client, _ = self.environment(status=statuses)
        with stack:
            report = runtime.ensure_runtime(self.folder / 'headless-switch', self.values,
                                            required_mode='headless')
        stage.assert_called_once()
        self.assertIn('--restart-app', stage.call_args.args[3])
        self.assertFalse(report['reused']); self.assertTrue(report['app_restart_requested'])
        self.assertIn('native-render', report['restart_reason'])
        self.assertNotEqual(report['connection'], str(self.connection.relative_to(ROOT)))
        self.assertEqual(report['previous_mode_check']['actual_mode'], 'native-render')
        self.assertTrue(report['mode_check']['passed'])
        self.assertEqual(client.status.call_count, 2)
        self.assertEqual(read(report['reuse_check'])['status'], 'not_reused')

    def test_headless_mode_mismatch_after_prepare_fails_without_second_restart(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True, 'mode': 'native-render'}
        stack, stage, client, _ = self.environment(status=ready)
        with stack, self.assertRaisesRegex(RuntimeError, 'mode does not match'):
            runtime.ensure_runtime(self.folder / 'headless-still-rendered', self.values,
                                   required_mode='headless')
        stage.assert_called_once(); self.assertEqual(client.status.call_count, 2)
        report = read(self.folder / 'headless-still-rendered/report.json')
        self.assertEqual(report['status'], 'failed')
        self.assertFalse(report['mode_check']['passed'])
        self.assertEqual(report['mode_check']['actual_mode'], 'native-render')
        self.assertEqual(read(self.pointer)['report_path'], str(self.prior.relative_to(ROOT)))

    def test_unrecognized_mode_does_not_authorize_restart(self):
        ready = {'ok': True, 'coldReady': True, 'contentReady': True, 'mode': 'unknown-fixture'}
        stack, stage, client, _ = self.environment(status=ready)
        with stack, self.assertRaisesRegex(RuntimeError, 'mode does not match'):
            runtime.ensure_runtime(self.folder / 'headless-unknown-mode', self.values,
                                   required_mode='headless')
        stage.assert_called_once()
        self.assertNotIn('--restart-app', stage.call_args.args[3])
        self.assertEqual(client.status.call_count, 2)

    def test_changed_pid_start_or_mapping_falls_back_outside_native_lease(self):
        for name, changed in (('pid', self.discovery(pid=124)), ('start', self.discovery(start=457)),
                ('path', self.discovery(paths={**self.paths, 'libg': '/data/app/sample/lib/arm/libg.so'}))):
            with self.subTest(changed=name):
                self.discoveries = [changed]
                stack, stage, client, _ = self.environment()
                with stack:
                    report = runtime.ensure_runtime(self.folder / name, self.values)
                stage.assert_called_once(); client.status.assert_not_called()
                self.assertFalse(report['reused'])
                self.assertEqual(read(report['reuse_check'])['status'], 'not_reused')
                # Restore the original baseline pointer between independent cases.
                write(self.pointer, {'connection': str(self.connection.relative_to(ROOT)), 'settings': self.values,
                    'report_path': str(self.prior.relative_to(ROOT)), 'vm_record_fingerprint': digest(self.vm['record'])})

    def test_restart_during_status_or_content_not_ready_requires_prepare(self):
        for name, discoveries, status in (
                ('during-status', [self.discovery(), self.discovery(pid=124)], None),
                ('content', [], {'ok': True, 'coldReady': True, 'contentReady': False})):
            with self.subTest(changed=name):
                self.discoveries = discoveries
                stack, stage, client, _ = self.environment(status=status)
                with stack:
                    report = runtime.ensure_runtime(self.folder / name, self.values)
                stage.assert_called_once(); client.close.assert_called_once(); self.assertFalse(report['reused'])
                write(self.pointer, {'connection': str(self.connection.relative_to(ROOT)), 'settings': self.values,
                    'report_path': str(self.prior.relative_to(ROOT)), 'vm_record_fingerprint': digest(self.vm['record'])})

    def test_changed_connection_identity_settings_or_vm_does_not_contact_guest(self):
        for name in ('connection', 'identity', 'settings', 'vm', 'version'):
            with self.subTest(changed=name):
                changed = deepcopy(self.checked); pointer = read(self.pointer)
                if name == 'connection': changed['connection']['port'] = 26790
                if name == 'identity': changed['checks']['identity']['sha256'] = 'changed'
                if name == 'version': changed['version_fingerprint'] = 'changed'
                if name == 'settings': pointer['settings']['display_fps'] = 10
                if name == 'vm': pointer['vm_record_fingerprint'] = 'changed'
                write(self.pointer, pointer)
                self.commands = []
                stack, stage, client, _ = self.environment(checked=changed)
                with stack:
                    report = runtime.ensure_runtime(self.folder / name, self.values)
                stage.assert_called_once(); client.status.assert_not_called(); self.assertEqual(self.commands, [])
                self.assertFalse(report['reused'])
                write(self.pointer, {'connection': str(self.connection.relative_to(ROOT)), 'settings': self.values,
                    'report_path': str(self.prior.relative_to(ROOT)), 'vm_record_fingerprint': digest(self.vm['record'])})

    def test_connection_change_after_validation_requires_prepare_before_guest_access(self):
        changed = read(self.connection); changed['port'] = 26791
        write(self.connection, changed)
        stack, stage, client, _ = self.environment()
        with stack:
            report = runtime.ensure_runtime(self.folder / 'connection-race', self.values)
        stage.assert_called_once(); client.status.assert_not_called(); self.assertEqual(self.commands, [])
        self.assertFalse(report['reused'])

    def test_legacy_pointer_is_freshly_checked_and_next_reuse_remains_valid(self):
        pointer = read(self.pointer); pointer.pop('vm_record_fingerprint')
        write(self.pointer, pointer)
        stack, stage, _, _ = self.environment()
        with stack:
            first = runtime.ensure_runtime(self.folder / 'first-reuse', self.values)
            second = runtime.ensure_runtime(self.folder / 'second-reuse', self.values)
        stage.assert_not_called(); self.assertTrue(first['reused']); self.assertTrue(second['reused'])
        self.assertEqual(len(self.commands), 4)
        self.assertEqual(second['prepared_report'], read(self.prior)['prepared_report'])
        self.assertEqual(read(self.pointer)['vm_record_fingerprint'], digest(self.vm['record']))

    def test_native_lease_conflict_is_not_bypassed_by_full_prepare(self):
        stack, stage, client, _ = self.environment()
        with stack, patch.object(runtime, 'native_lock', side_effect=RuntimeError('synthetic native lease conflict')):
            with self.assertRaisesRegex(RuntimeError, 'lease conflict'):
                runtime.ensure_runtime(self.folder / 'locked', self.values)
        stage.assert_not_called(); client.status.assert_not_called()
        self.assertEqual(read(self.folder / 'locked/report.json')['status'], 'failed')

    def test_cancelled_reuse_does_not_fall_back_or_query_rpc(self):
        event = threading.Event()
        stack, stage, client, _ = self.environment(cancellation=event)
        with stack:
            with self.assertRaises(InterruptedError):
                runtime.ensure_runtime(self.folder / 'cancelled', self.values, event)
        stage.assert_not_called(); client.status.assert_not_called()
        self.assertEqual(read(self.folder / 'cancelled/report.json')['status'], 'interrupted')


if __name__ == '__main__':
    unittest.main()

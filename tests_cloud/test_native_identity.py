"""Read-only ADB identity interface fixtures; no daemon, device, probe or native claim."""
from pathlib import Path
import json
import re
import subprocess
import shutil
import tempfile
import unittest
from unittest.mock import Mock, patch

from tools.cloud import native_identity as identity


class FakeADB:
    def __init__(self, test):
        self.test = test
        self.calls = []
        self.bad_hash = False
        self.missing_hash = False
        self.changed_pid = False
        self.ready = True
        self.uid = 0
        self.server_version = '0029'

    def inventory(self, after=False):
        case = self.test
        pid = 322 if after and self.changed_pid else 321
        # /proc field 3 starts at index zero; index nineteen is start-time.
        stat = f'{pid} (fixture app) ' + ' '.join(['S'] + ['0'] * 18 + ['555'])
        paths = [case.apk_remote, case.lib_remote, case.probe_remote, case.update_root + '/assets.scdb']
        maps = '\n'.join(f'{1000+i:x}-{1001+i:x} r--p 00000000 00:01 {100+i} {path}'
                         for i, path in enumerate(paths))
        return (f'UID={self.uid}\nAPK_BEGIN\npackage:{case.apk_remote}\nAPK_END\nPID={pid}\n'
                f'STAT_BEGIN\n{stat}\nSTAT_END\nMAPS_BEGIN\n{maps}\nMAPS_END\n')

    def __call__(self, label, argv, script, timeout):
        self.calls.append((label, argv, script))
        if label == 'adb-version':
            body = 'Android Debug Bridge version 1.0.41\nVersion synthetic-local-fixture\n'
        elif label.startswith('server-version-'):
            body = self.server_version
        elif label == 'get-state':
            if not self.ready:
                return subprocess.CompletedProcess(argv, 1, b'', b'explicit fixture device unavailable')
            body = 'device\n'
        elif label == 'discover-runtime':
            body = self.inventory()
        elif label == 'verify-installed-files':
            paths = self.test.remote
            selected = list(paths)
            if self.missing_hash:
                selected.pop()
            hashes = '\n'.join(f'{("0" * 64 if self.bad_hash and i == 0 else paths[p]["sha256"])}  {p}'
                               for i, p in enumerate(selected))
            sizes = '\n'.join(f'{paths[p]["bytes"]} {p}' for p in selected)
            body = f'HASHES_BEGIN\n{hashes}\nHASHES_END\nSIZES_BEGIN\n{sizes}\nSIZES_END\n' + self.inventory(after=True)
        else:
            raise AssertionError('Unexpected operation: ' + label)
        return subprocess.CompletedProcess(argv, 0, body.encode(), b'')


class NativeIdentityTests(unittest.TestCase):
    def setUp(self):
        (identity.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='native-identity-', dir=identity.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.package = 'fixture.native.app'
        self.apk_remote = '/data/app/fixture.native.app-abc/base.apk'
        self.lib_remote = '/data/app/fixture.native.app-abc/lib/arm64/libg.so'
        self.probe_remote = '/data/app/fixture.native.app-abc/lib/arm64/libcrprobe.so'
        self.update_root = '/data/user/0/fixture.native.app/update'
        self.assets = []
        def asset(name, role, data):
            path = self.folder / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
            row = {'path': path.relative_to(identity.ROOT).as_posix(), 'role': role,
                   'bytes': len(data), 'sha256': identity.digest(data)}
            self.assets.append(row)
            return row
        header = bytearray(20); header[:6] = b'\x7fELF\x02\x01'; header[18:20] = (183).to_bytes(2, 'little')
        library = asset('libg.so', 'native_library', header)
        probe = asset('probe.so', 'native_probe', b'interface-only probe')
        apk = asset('new-build.apk', 'offline_apk', b'distinct new APK fixture')
        self.remote = {self.apk_remote: apk, self.lib_remote: library, self.probe_remote: probe}
        # Metadata uses the real resource prefix while files stay in a project-contained fixture root.
        # Patch only local verification, not hash checks against fake ADB results.
        for index in range(651):
            name = 'assets.scdb' if index == 0 else f'csv_logic/local-{index:03d}.csv'
            data = f'local resource fixture {index}'.encode()
            self.remote[self.update_root + '/' + name] = {
                'path': identity.CONTENT_PREFIX + name, 'role': 'paired_content',
                'bytes': len(data), 'sha256': identity.digest(data)}
        fingerprint = b'{"version":"15.535.86","sha":"fixture-content"}'
        self.remote[self.update_root + '/fingerprint.json'] = {
            'path': identity.CONTENT_PREFIX + 'fingerprint.json', 'role': 'content_fingerprint',
            'bytes': len(fingerprint), 'sha256': identity.digest(fingerprint)}
        self.assets += list(self.remote.values())[3:]
        self.receipt = self.folder / 'android-identity.json'
        self.manifest = self.folder / 'version.json'
        self.meta = {'schema': 'royale-phase2-version.v1', 'runtime_kind': 'native_game',
            'ticks_per_second': 20, 'client_package': self.package, 'assets': self.assets,
            'content_version': '15.535.86', 'content_manifest_sha1': 'fixture-content',
            'runtime_identity_receipt': self.receipt.relative_to(identity.ROOT).as_posix()}
        self.manifest.write_text(json.dumps(self.meta))
        self.adb = self.folder / 'adb'; self.adb.write_text('local fixture executable placeholder')
        self.runner = FakeADB(self)

    def collect(self, name='evidence', **options):
        # Fake local asset gate avoids writing hundreds of arbitrary fixtures into the real resource tree.
        with patch.object(identity, 'verify_manifest', return_value={'manifest': self.meta, 'fingerprint': 'fixture-version'}):
            return identity.collect_identity(self.manifest, self.folder / name,
                serial='fixture-serial:5555', adb=self.adb, runner=self.runner, **options)

    def test_complete_fixture_checks_new_apk_and_652_resources_without_identity_claim(self):
        report = self.collect()
        self.assertEqual(report['status'], 'interface_only')
        self.assertTrue(report['checks_passed'])
        self.assertEqual(report['installed_update_files'], 652)
        self.assertFalse(report['identity_written'])
        self.assertFalse(report['native_execution_verified'])
        self.assertFalse(self.receipt.exists())
        self.assertEqual(report['native_probe_rpc_calls'], 0)
        verification = next(script for label, argv, script in self.runner.calls if label == 'verify-installed-files')
        self.assertEqual(verification.count('sha256sum '), 1)
        self.assertIn(self.apk_remote, verification)
        self.assertEqual(len(self.remote), 655)
        for label, argv, script in self.runner.calls:
            if label not in ('adb-version',) and not label.startswith('server-version-'):
                self.assertIn('-H', argv); self.assertIn('-s', argv)
                self.assertIn('fixture-serial:5555', argv)
            self.assertFalse(set(argv) & {'devices', 'root', 'start-server', 'kill-server', 'install', 'push', 'connect'})
        for record in report['commands']:
            for name in ('stdout', 'stderr'):
                evidence = record[name]
                self.assertEqual(identity.reference(evidence['path'])['sha256'], evidence['sha256'])

    def test_no_device_is_unavailable_and_does_not_attempt_a_shell(self):
        self.runner.ready = False
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(self.receipt.exists())
        self.assertFalse(any(label == 'discover-runtime' for label, _, _ in self.runner.calls))
        self.assertIn('unavailable', report['error'])

    def test_wrong_installed_new_apk_hash_cannot_write_receipt(self):
        self.runner.bad_hash = True
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('SHA256/size', report['error'])
        self.assertFalse(self.receipt.exists())

    def test_missing_update_file_hash_is_rejected(self):
        self.runner.missing_hash = True
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('every expected path', report['error'])
        self.assertFalse(self.receipt.exists())

    def test_changed_process_and_non_root_shell_are_rejected(self):
        self.runner.changed_pid = True
        report = self.collect()
        self.assertIn('process or loaded paths changed', report['error'])
        self.runner.changed_pid = False; self.runner.uid = 2000
        report = self.collect('nonroot')
        self.assertIn('already-rooted', report['error'])
        self.assertFalse(self.receipt.exists())

    def test_server_protocol_mismatch_prevents_device_command(self):
        self.runner.server_version = '0028'
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertIn('version mismatch', report['error'])
        self.assertFalse(any(label == 'get-state' for label, _, _ in self.runner.calls))

    def test_serial_shell_injection_and_existing_output_are_rejected(self):
        with self.assertRaises(ValueError):
            identity.collect_identity(self.manifest, self.folder / 'invalid', serial='x;id', adb=self.adb, runner=self.runner)
        self.assertFalse((self.folder / 'invalid').exists())
        self.collect()
        with self.assertRaises(FileExistsError):
            self.collect()

    def test_existing_identity_is_never_overwritten_or_reused(self):
        self.receipt.write_bytes(b'preserve history')
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(self.receipt.read_bytes(), b'preserve history')
        self.assertFalse(self.runner.calls)

    def test_package_shell_injection_is_rejected_before_adb(self):
        self.meta['client_package'] = 'fixture.app; id'
        report = self.collect()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(self.runner.calls)
        self.assertFalse(self.receipt.exists())

    @unittest.skipUnless(shutil.which('bash'), 'Optional local shell parser unavailable')
    def test_generated_read_only_scripts_parse_without_execution(self):
        scripts = [identity.discovery_script(self.package),
                   identity.verification_script(self.package, {'pid': 321}, list(self.remote))]
        for script in scripts:
            parsed = subprocess.run(['bash', '-n'], input=script.encode(), capture_output=True)
            self.assertEqual(parsed.returncode, 0, parsed.stderr.decode())

    def test_actual_server_version_reader_only_uses_host_version(self):
        connection = Mock()
        connection.recv.side_effect = [b'OKAY', b'0004', b'0029']
        connection.__enter__ = Mock(return_value=connection)
        connection.__exit__ = Mock(return_value=None)
        with patch.object(identity.socket, 'create_connection', return_value=connection):
            self.assertEqual(identity.readonly_server_version(5038), b'0029')
        connection.sendall.assert_called_once_with(b'000chost:version')


if __name__ == '__main__':
    unittest.main()

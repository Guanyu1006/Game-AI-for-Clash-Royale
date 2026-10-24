"""Native connection interface tests; loopback fixtures are not game physics."""
import hashlib
import json
import os
from pathlib import Path
import socketserver
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch

from royale_phase2.connection import (NativeConnection, check_connection, load_connection,
                                      verify_runtime_identity)
from royale_phase2.runtime import ROOT
from royale_phase2.versioning import VersionMismatch, canonical_hash


class NativeConnectionTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='native-connection-', dir=ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.addCleanup(patch.stopall)
        patch.dict(os.environ, {}, clear=False).start()
        os.environ.pop('ROYALE_NATIVE_CONNECTION', None)

    def write(self, name, data):
        path = self.folder / name
        path.write_text(json.dumps(data), encoding='utf-8')
        return str(path.relative_to(ROOT))

    def evidence(self, port=26789):
        # Deliberately tiny, synthetic identity fixture; never a native-runtime claim.
        library = self.folder / 'fixture.so'
        header = bytearray(20)
        header[:6] = b'\x7fELF\x02\x01'
        struct.pack_into('<H', header, 18, 183)
        library.write_bytes(header)
        probe = self.folder / 'fixture-probe.bin'; probe.write_bytes(b'interface fixture probe')
        fingerprint = self.folder / 'fixture-fingerprint.json'; fingerprint.write_bytes(b'{}')
        assets = [{'path': str(path.relative_to(ROOT)), 'role': role,
                   'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                  for path, role in ((library, 'native_library'), (probe, 'native_probe'),
                                     (fingerprint, 'content_fingerprint'))]
        receipt = {'schema': 'phase2-native-adb-identity.v1', 'actual_adb_verified': True,
            'loaded_libg_verified': True, 'content_verified': True,
            'libg_sha256': assets[0]['sha256'], 'probe_sha256': assets[1]['sha256'],
            'content_fingerprint_sha256': assets[2]['sha256'], 'content_manifest_sha1': 'fixture-content',
            'apk_sha256': 'fixture-apk', 'content_version': 'fixture-version', 'pid': 123}
        identity = self.write('identity.json', receipt)
        manifest = {'schema': 'royale-phase2-version.v1', 'runtime_kind': 'native_game',
            'ticks_per_second': 20, 'content_manifest_sha1': receipt['content_manifest_sha1'],
            'offline_apk_sha256': receipt['apk_sha256'], 'content_version': receipt['content_version'],
            'assets': assets, 'runtime_identity_receipt': identity}
        manifest_path = self.write('manifest.json', manifest)
        settings = NativeConnection(port=port, timeout=1, version_manifest=manifest_path,
            identity_path=identity, manifest_sha256=hashlib.sha256((ROOT / manifest_path).read_bytes()).hexdigest())
        config = self.write('connection.json', settings.as_dict())
        return config, manifest, identity

    def local_build_evidence(self):
        # Synthetic saved text only: this fixture never certifies an actual device.
        from tools.cloud.native_identity import (CONTENT_PREFIX, discovery_script,
                                                verification_script)
        _, manifest, receipt_path = self.evidence()
        receipt = json.loads((ROOT / receipt_path).read_text())
        manifest.update(profile='separate_local_build_runtime_identity', client_package='fixture.native.app')
        for row in manifest['assets']:
            row['bytes'] = (ROOT / row['path']).stat().st_size
        manifest['assets'][-1]['path'] = CONTENT_PREFIX + 'fingerprint.json'
        apk = self.folder / 'fixture.apk'; apk.write_bytes(b'new APK interface fixture')
        apk_row = {'path': str(apk.relative_to(ROOT)), 'role': 'offline_apk',
                   'bytes': apk.stat().st_size, 'sha256': hashlib.sha256(apk.read_bytes()).hexdigest()}
        manifest['assets'].insert(0, apk_row)
        manifest['offline_apk_sha256'] = receipt['apk_sha256'] = apk_row['sha256']
        for index in range(651):
            data = f'interface update {index}'.encode()
            name = 'assets.scdb' if index == 0 else f'csv_logic/fixture-{index}.csv'
            manifest['assets'].append({'path': CONTENT_PREFIX + name, 'role': 'paired_content',
                                      'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()})
        manifest['fingerprint'] = canonical_hash(manifest)
        self.write('manifest.json', manifest)
        found = {'pid': 321, 'start_ticks': 555, 'apk': '/data/app/fixture.native.app-abc/base.apk',
            'libg': '/data/app/fixture.native.app-abc/lib/arm64/libg.so',
            'probe': '/data/app/fixture.native.app-abc/lib/arm64/libcrprobe.so',
            'update_root': '/data/user/0/fixture.native.app/update'}
        remote = {found[key]: next(row for row in manifest['assets'] if row['role'] == role)
                  for key, role in (('apk', 'offline_apk'), ('libg', 'native_library'), ('probe', 'native_probe'))}
        update = [row for row in manifest['assets'] if row['role'] in ('paired_content', 'content_fingerprint')]
        remote.update({found['update_root'] + '/' + row['path'].removeprefix(CONTENT_PREFIX): row for row in update})
        stat = '321 (fixture app) ' + ' '.join(['S'] + ['0'] * 18 + ['555'])
        paths = [found['apk'], found['libg'], found['probe'], found['update_root'] + '/assets.scdb']
        maps = '\n'.join(f'{1000+i:x}-{1001+i:x} r--p 00000000 00:01 {100+i} {path}'
                         for i, path in enumerate(paths))
        inventory = (f'UID=0\nAPK_BEGIN\npackage:{found["apk"]}\nAPK_END\nPID=321\n'
                     f'STAT_BEGIN\n{stat}\nSTAT_END\nMAPS_BEGIN\n{maps}\nMAPS_END\n')
        hashes = '\n'.join(f'{row["sha256"]}  {path}' for path, row in remote.items())
        sizes = '\n'.join(f'{row["bytes"]} {path}' for path, row in remote.items())
        files = {'05-discover-runtime.stdin.sh': discovery_script(manifest['client_package']),
            '05-discover-runtime.stdout.log': inventory, '05-discover-runtime.stderr.log': '',
            '07-verify-installed-files.stdin.sh': verification_script(manifest['client_package'], found, list(remote)),
            '07-verify-installed-files.stdout.log': f'HASHES_BEGIN\n{hashes}\nHASHES_END\nSIZES_BEGIN\n{sizes}\nSIZES_END\n' + inventory,
            '07-verify-installed-files.stderr.log': ''}
        for name, body in files.items():
            (self.folder / name).write_text(body, encoding='utf-8')
        files['manifest.json'] = ''  # The actual manifest was written above.
        receipt.update(version_fingerprint=manifest['fingerprint'], pid=321, process_start_ticks=555,
            device_serial='fixture-serial:5555', installed_update_file_count=652,
            installed_update_bytes=sum(row['bytes'] for row in update),
            mapped_paths={key: found[key] for key in ('apk', 'libg', 'probe', 'update_root')},
            source_evidence=[{'path': str((self.folder / name).relative_to(ROOT)),
                'sha256': hashlib.sha256((self.folder / name).read_bytes()).hexdigest()} for name in files])
        self.write('identity.json', receipt)
        return manifest, receipt, receipt_path

    def rewrite_source(self, receipt, name, body):
        path = self.folder / name
        path.write_text(body, encoding='utf-8')
        row = next(row for row in receipt['source_evidence'] if row['path'] == str(path.relative_to(ROOT)))
        row['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write('identity.json', receipt)

    def test_load_has_no_network_and_preserves_default(self):
        with patch('socket.create_connection') as connection:
            self.assertIsNone(load_connection())
            config, _, _ = self.evidence()
            loaded = load_connection(config)
            self.assertEqual(loaded.host, '127.0.0.1')
            with patch.dict(os.environ, {'ROYALE_NATIVE_CONNECTION': config}):
                self.assertEqual(load_connection(), loaded)
            connection.assert_not_called()

    def test_rejects_public_hosts_bad_bounds_and_escaping_paths(self):
        for options in ({'host': '0.0.0.0'}, {'host': '192.0.2.5'}, {'host': 'remote.example'},
                        {'port': True}, {'port': 65536}, {'timeout': float('nan')},
                        {'timeout': True}, {'timeout': 601}, {'identity_path': '../outside.json'}):
            with self.subTest(options=options):
                with self.assertRaises(ValueError): NativeConnection(**options)
        self.assertEqual(NativeConnection(host='localhost').host, '127.0.0.1')

    def test_config_unknown_keys_and_manifest_change_fail(self):
        config, _, _ = self.evidence()
        settings = json.loads((ROOT / config).read_text())
        self.write('connection.json', {**settings, 'password': 'not-supported'})
        with self.assertRaises(ValueError): load_connection(config)
        self.write('connection.json', settings)
        (ROOT / settings['version_manifest']).write_text('{}')
        with self.assertRaises(VersionMismatch): load_connection(config)

    def test_evidence_ready_does_not_connect_or_claim_native_runtime(self):
        config, _, _ = self.evidence()
        with patch('socket.create_connection') as connection:
            report = check_connection(config)
        connection.assert_not_called()
        self.assertEqual(report['status'], 'ready_assets')
        self.assertEqual(report['rpc_calls'], 0)
        self.assertFalse(report['native_runtime_verified'])
        self.assertFalse(report['native_battle_execution_verified'])
        self.assertFalse(report['checks']['identity']['current_process_identity_verified'])
        self.assertEqual(len(report['version_fingerprint']), 64)

    def test_missing_asset_and_wrong_receipt_block_without_fallback(self):
        config, manifest, identity = self.evidence()
        (ROOT / manifest['assets'][0]['path']).unlink()
        report = check_connection(config)
        self.assertEqual(report['status'], 'unavailable')
        self.assertEqual(report['checks']['local_assets']['missing_count'], 1)
        self.assertEqual(report['rpc_calls'], 0)
        self.assertFalse(report['fallback_used'])
        self.write('identity.json', {'schema': 'phase2-native-adb-identity.v1'})
        with self.assertRaises(VersionMismatch): verify_runtime_identity(manifest, identity)

    def test_local_build_complete_recorded_capture_and_empty_stderr_pass_without_network(self):
        manifest, receipt, path = self.local_build_evidence()
        with patch('socket.create_connection') as connection, patch('subprocess.run') as subprocess_run:
            actual, digest = verify_runtime_identity(manifest, path)
        self.assertEqual(actual, receipt); self.assertEqual(len(digest), 64)
        connection.assert_not_called(); subprocess_run.assert_not_called()

    def test_local_build_requires_version_and_legal_process_identity(self):
        manifest, receipt, path = self.local_build_evidence()
        for field, values in {'version_fingerprint': [None, '0' * 64], 'pid': [None, True, 0, -1, '321'],
                              'process_start_ticks': [None, True, 0, -1, '555'],
                              'device_serial': [None, '', 'x;id', '../device', 'x' * 161]}.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    self.write('identity.json', {**receipt, field: value})
                    with self.assertRaises(VersionMismatch): verify_runtime_identity(manifest, path)

    def test_local_build_minimal_handwritten_receipt_cannot_replace_saved_capture(self):
        manifest, receipt, path = self.local_build_evidence()
        for evidence in (None, [], receipt['source_evidence'][:-1],
                         [row for row in receipt['source_evidence'] if 'verify-installed-files.stdout' not in row['path']]):
            with self.subTest(evidence=evidence):
                self.write('identity.json', {**receipt, 'source_evidence': evidence})
                with self.assertRaises(VersionMismatch): verify_runtime_identity(manifest, path)

    def test_local_build_rejects_changed_missing_duplicate_or_escaping_source(self):
        manifest, receipt, path = self.local_build_evidence()
        row = receipt['source_evidence'][0]
        for bad in ({**row, 'sha256': '0' * 64}, {**row, 'path': '../outside.log'},
                    {**row, 'path': str((self.folder / 'absent.log').relative_to(ROOT))}):
            self.write('identity.json', {**receipt, 'source_evidence': [bad, *receipt['source_evidence'][1:]]})
            with self.assertRaises(VersionMismatch): verify_runtime_identity(manifest, path)
        self.write('identity.json', {**receipt, 'source_evidence': receipt['source_evidence'] + [row]})
        with self.assertRaises(VersionMismatch): verify_runtime_identity(manifest, path)

    def test_local_build_rejects_changed_process_even_when_log_hash_is_updated(self):
        manifest, receipt, path = self.local_build_evidence()
        name = '07-verify-installed-files.stdout.log'
        body = (self.folder / name).read_text()
        for before, after in (('PID=321\n', 'PID=322\n'), (' 555\nSTAT_END', ' 556\nSTAT_END')):
            with self.subTest(changed=before):
                self.rewrite_source(receipt, name, body.replace(before, after))
                with self.assertRaisesRegex(VersionMismatch, 'Process identity'):
                    verify_runtime_identity(manifest, path)

    def test_local_build_rejects_incomplete_installed_hashes_and_rewritten_script(self):
        manifest, receipt, path = self.local_build_evidence()
        name = '07-verify-installed-files.stdout.log'
        body = (self.folder / name).read_text()
        self.rewrite_source(receipt, name, body.replace(body.splitlines()[1] + '\n', '', 1))
        with self.assertRaisesRegex(VersionMismatch, 'every expected path'):
            verify_runtime_identity(manifest, path)
        first_hash = body.splitlines()[1].split()[0]
        self.rewrite_source(receipt, name, body.replace(first_hash, '0' * 64, 1))
        with self.assertRaisesRegex(VersionMismatch, 'hashes or byte sizes'):
            verify_runtime_identity(manifest, path)
        self.rewrite_source(receipt, name, body)
        self.rewrite_source(receipt, '07-verify-installed-files.stdin.sh', 'printf verified\n')
        with self.assertRaisesRegex(VersionMismatch, 'Verification script'):
            verify_runtime_identity(manifest, path)

    def test_backend_honors_config_without_opening_transport(self):
        from royale_phase2.backend import NativeBattleBackend
        config, _, _ = self.evidence(port=28765)
        with patch('socket.create_connection') as connection, \
                patch.dict(os.environ, {'ROYALE_NATIVE_CONNECTION': config}):
            backend = NativeBattleBackend()
            self.assertEqual(backend.client.address, ('127.0.0.1', 28765))
            self.assertEqual(backend.timeout, 1)
            backend.close()
        connection.assert_not_called()

    def test_explicit_status_uses_only_handshake_and_status(self):
        commands = []
        class InterfaceFixture(socketserver.StreamRequestHandler):
            def handle(self):
                commands.append(self.rfile.readline().decode().strip())
                self.wfile.write(b'{"ok":true}\n')
                commands.append(self.rfile.readline().decode().strip())
                self.wfile.write(b'{"ok":true,"contentReady":true,"mode":"headless"}\n')
        with socketserver.TCPServer(('127.0.0.1', 0), InterfaceFixture) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                config, _, _ = self.evidence(port=server.server_address[1])
                report = check_connection(config, connect=True)
            finally:
                server.shutdown(); thread.join(timeout=2)
        self.assertEqual(commands, ['session-v1', 'status'])
        self.assertEqual(report['status'], 'ready_assets')
        self.assertTrue(report['reachable']); self.assertEqual(report['rpc_calls'], 1)
        self.assertFalse(report['native_battle_execution_verified'])

    def test_connection_failure_is_unavailable_with_no_retry(self):
        config, _, _ = self.evidence()
        with patch('socket.create_connection', side_effect=ConnectionRefusedError('interface fixture')) as connection:
            report = check_connection(config, connect=True)
        connection.assert_called_once()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(report['reachable']); self.assertFalse(report['fallback_used'])
        self.assertIn('ConnectionRefusedError', report['checks']['transport']['error'])


if __name__ == '__main__':
    unittest.main()

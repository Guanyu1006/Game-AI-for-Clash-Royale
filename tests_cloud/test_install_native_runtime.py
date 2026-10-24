"""安装计划、严格资源门禁和假 ADB；不连接设备或启动游戏。"""
import hashlib
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from tools.cloud import install_native_runtime as installer


class InstallNativeRuntimeTests(unittest.TestCase):
    def setUp(self):
        (installer.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=installer.ROOT / '.tmp', prefix='native-install-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.adb = self.folder / 'adb'; self.adb.write_text('non-executable ADB fixture')
        self.source = self.folder / 'original.apk'; self.source.write_bytes(b'original fixture')
        self.apk = self.folder / 'offline.apk'
        self.lib, self.probe = b'fixture native libg', b'fixture probe'
        with zipfile.ZipFile(self.apk, 'w') as archive:
            archive.writestr('lib/arm64-v8a/libg.so', self.lib)
            archive.writestr('lib/arm64-v8a/libcrprobe.so', self.probe)
        self.build_report = self.folder / 'build.json'
        self.build = {'schema': 'cloud-native-apk-build.v1', 'success': True,
            'input_apk': installer.file_record(self.source), 'output_apk': installer.file_record(self.apk),
            'checks': dict.fromkeys(installer.CHECKS, True)}
        self.build_report.write_text(json.dumps(self.build))
        self.resources = self.folder / 'resources'; self.resources.mkdir()
        self.rows = []
        for index in range(651):
            name = 'assets.scdb' if index == 0 else f'csv_logic/file-{index:03d}.csv'
            path = self.resources / name; path.parent.mkdir(exist_ok=True)
            path.write_bytes(f'fixed fixture {index}'.encode())
            record = installer.file_record(path)
            self.rows.append({'file': name, 'bytes': record['bytes'], 'sha256': record['sha256']})
        self.fp = json.dumps({'version': '15.535.86', 'sha': installer.CONTENT_SHA1}).encode()
        (self.resources / 'fingerprint.json').write_bytes(self.fp)
        self.rows.append({'file': 'fingerprint.json', 'bytes': len(self.fp), 'sha256': self.sha(self.fp)})
        plan = self.folder / 'source-plan.json'; plan.write_text('{}')
        upstream = self.folder / 'upstream.json'; upstream.write_text('{"source_files":{}}')
        self.payload_receipt = self.folder / 'update.tar.json'
        self.payload = self.folder / 'update.tar'
        self.receipt = {'schema': 'phase2-native-update-package.v1', 'runtime_executed': False,
            'content_version': '15.535.86', 'content_manifest_sha1': installer.CONTENT_SHA1,
            'plan': installer.file_record(plan)['path'], 'plan_sha256': installer.file_record(plan)['sha256'],
            'upstream_source_manifest': installer.file_record(upstream)['path'],
            'upstream_source_manifest_sha256': installer.file_record(upstream)['sha256'],
            'resource_folder': self.resources.relative_to(installer.ROOT).as_posix(),
            'files': self.rows, 'file_count': 652, 'bytes': sum(r['bytes'] for r in self.rows)}
        self.make_payload()
        self.patches = [patch.object(installer.build, 'check_input_components', return_value=[{}] * 164),
                        patch.object(installer.build, 'LIBG_SHA256', self.sha(self.lib)),
                        patch.object(installer.build, 'PROBE_SHA256', self.sha(self.probe)),
                        patch.object(installer, 'FINGERPRINT_SHA256', self.sha(self.fp))]
        for fixture_patch in self.patches:
            fixture_patch.start(); self.addCleanup(fixture_patch.stop)
        self.calls = []

    @staticmethod
    def sha(data):
        return hashlib.sha256(data).hexdigest()

    def make_payload(self, extra=None):
        embedded = {k: v for k, v in self.receipt.items() if k not in ('archive', 'archive_hashes')}
        with tarfile.open(self.payload, 'w') as archive:
            for row in self.rows:
                archive.add(self.resources / row['file'], 'update/' + row['file'], recursive=False)
            sums = ''.join(f"{r['sha256']}  {r['file']}\n" for r in self.rows).encode()
            for name, body in [('SHA256SUMS', sums), ('manifest.json', json.dumps(embedded).encode())]:
                info = tarfile.TarInfo(name); info.size = len(body)
                archive.addfile(info, io.BytesIO(body))
            if extra:
                archive.addfile(extra)
        self.receipt.update(archive=self.payload.relative_to(installer.ROOT).as_posix(),
                            archive_hashes=installer.file_record(self.payload))
        self.payload_receipt.write_text(json.dumps(self.receipt))

    def fake_adb(self, label, argv, script, timeout):
        self.calls.append((label, argv, script))
        if label == 'adb-version':
            body = 'Android Debug Bridge version 1.0.41\n'
        elif label.startswith('server-version-'):
            body = '0029'
        elif label == 'get-state':
            body = 'device\n'
        elif label == 'preflight':
            body = 'PREFLIGHT_COMPLETE\n'
        elif label == 'install-runtime':
            body = 'INSTALL_COMPLETE\n'
        elif label == 'stage-guest-artifacts':
            body = 'GUEST_COPY_COMPLETE\n'
        elif label.startswith('push-'):
            body = 'fixture pushed\n'
        else:
            raise AssertionError(label)
        return subprocess.CompletedProcess(argv, 0, body.encode(), b'')

    def run_install(self, name='evidence', **options):
        return installer.install_runtime(self.folder / name, serial='127.0.0.1:15555', adb=self.adb,
            adb_port=5038, apk=self.apk, build_report=self.build_report, payload=self.payload,
            payload_receipt=self.payload_receipt, runner=self.fake_adb, **options)

    def test_default_generates_valid_shell_and_plan_with_zero_adb(self):
        report = self.run_install()
        self.assertEqual(report['status'], 'planned')
        self.assertFalse(report['installed_verified'])
        self.assertEqual(self.calls, [])
        script = (self.folder / 'evidence/install.sh').read_text()
        self.assertIn('pm install --user 0 --abi arm64-v8a -r', script)
        self.assertIn("stat -c '%u:%g'", script)
        self.assertIn('restorecon -RF', script)
        self.assertIn('update.backup-', script)
        self.assertNotIn('am start', script)
        self.assertNotIn('iptables', script)
        self.assertEqual(subprocess.run(['sh', '-n'], input=script.encode(), capture_output=True).returncode, 0)
        plan = json.loads((self.folder / 'evidence/plan.json').read_text())
        self.assertEqual(plan['update_files'], 652)
        self.assertEqual(plan['source_component_count'], 164)
        self.assertEqual(plan['operations'][0]['argv'][-4:], ['shell', '-T', 'sh', '-s'])

    def test_fake_execution_uses_su0_stdin_and_cannot_claim_real_installation(self):
        report = self.run_install(execute=True, root_shell='su0')
        self.assertEqual(report['status'], 'interface_only')
        self.assertFalse(report['installed_verified'])
        self.assertFalse(report['native_execution_verified'])
        for label, argv, script in self.calls:
            if label in ('preflight', 'install-runtime'):
                self.assertEqual(argv[-6:], ['shell', '-T', 'su', '0', 'sh', '-s'])
                self.assertIn('id -u', script)
            self.assertNotIn('start-server', argv)
            self.assertNotIn('connect', argv)
        self.assertEqual(report['activity_starts'], 0)

    def test_guest_copy_plan_and_execution_replace_push_without_default_adb(self):
        report = self.run_install(guest_source_dir='/data/local/tmp/royale-staging')
        self.assertEqual(report['status'], 'planned')
        self.assertEqual(self.calls, [])
        plan = json.loads((self.folder / 'evidence/plan.json').read_text())
        self.assertEqual([op['label'] for op in plan['operations']],
                         ['preflight', 'stage-guest-artifacts', 'install-runtime'])
        script = (self.folder / 'evidence/stage-guest.sh').read_text()
        self.assertIn('[ ! -L "$source/$name" ]', script)
        self.assertEqual(subprocess.run(['sh', '-n'], input=script.encode(), capture_output=True).returncode, 0)
        result = self.run_install('guest-execute', execute=True, guest_source_dir='/data/local/tmp/royale-staging')
        self.assertEqual(result['status'], 'interface_only')
        self.assertFalse(any(label.startswith('push-') for label, _, _ in self.calls))
        self.assertTrue(any(label == 'stage-guest-artifacts' for label, _, _ in self.calls))

    def test_unsafe_guest_source_is_rejected_before_evidence_or_adb(self):
        for path in ('/data/local/tmp/stage;id', '/data/local/tmp/../private', '/sdcard/stage'):
            with self.assertRaises(ValueError):
                self.run_install(guest_source_dir=path)
        self.assertEqual(self.calls, [])

    def test_failed_build_receipt_stops_before_device(self):
        self.build['success'] = False; self.build_report.write_text(json.dumps(self.build))
        report = self.run_install(execute=True)
        self.assertIn('successful verified', report['error'])
        self.assertEqual(self.calls, [])

    def test_changed_apk_and_source_are_rejected(self):
        self.apk.write_bytes(b'changed')
        self.assertIn('output APK', self.run_install()['error'])
        self.build['output_apk'] = installer.file_record(self.apk)
        self.build_report.write_text(json.dumps(self.build))
        self.source.write_bytes(b'changed source')
        self.assertIn('source APK', self.run_install('source-changed')['error'])

    def test_changed_payload_or_source_resource_is_rejected(self):
        with self.payload.open('ab') as stream:
            stream.write(b'changed')
        self.assertIn('update archive', self.run_install()['error'])
        self.make_payload()
        (self.resources / self.rows[0]['file']).write_bytes(b'changed source')
        self.assertIn('SHA256/size mismatch', self.run_install('changed-resource')['error'])

    def test_extra_or_link_tar_entry_is_rejected(self):
        link = tarfile.TarInfo('update/evil'); link.type = tarfile.SYMTYPE; link.linkname = '/data'
        self.make_payload(link)
        self.assertIn('exact regular', self.run_install()['error'])

    def test_failed_device_preflight_prevents_push(self):
        original = self.fake_adb
        def failure(label, argv, script, timeout):
            if label == 'preflight':
                self.calls.append((label, argv, script))
                return subprocess.CompletedProcess(argv, 31, b'', b'Already-rooted Android shell required')
            return original(label, argv, script, timeout)
        self.fake_adb = failure
        report = self.run_install(execute=True)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('Already-rooted', report['error'])
        self.assertFalse(any(label.startswith('push-') for label, _, _ in self.calls))
        self.assertFalse(report['installed_verified'])

    def test_wrong_server_version_cannot_restart_daemon_or_touch_device(self):
        original = self.fake_adb
        def wrong(label, argv, script, timeout):
            if label.startswith('server-version-'):
                return subprocess.CompletedProcess(argv, 0, b'0028', b'')
            return original(label, argv, script, timeout)
        self.fake_adb = wrong
        report = self.run_install(execute=True)
        self.assertIn('version mismatch', report['error'])
        self.assertFalse(any(label == 'get-state' for label, _, _ in self.calls))

    def test_existing_output_and_unsafe_serial_are_rejected(self):
        self.run_install()
        with self.assertRaises(FileExistsError):
            self.run_install()
        with self.assertRaises(ValueError):
            installer.install_runtime(self.folder / 'invalid', serial='bad; id', adb=self.adb, adb_port=5038,
                apk=self.apk, build_report=self.build_report, payload=self.payload, payload_receipt=self.payload_receipt)
        self.assertFalse((self.folder / 'invalid').exists())


if __name__ == '__main__':
    unittest.main()

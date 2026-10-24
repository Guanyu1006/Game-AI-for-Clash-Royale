"""固定资源清单门禁和既有打包器调用；不访问网络、ADB 或原生进程。"""
from dataclasses import replace
import gzip
import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from tools.cloud import native_payload as payload


class NativePayloadTests(unittest.TestCase):
    def setUp(self):
        (payload.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=payload.ROOT / '.tmp', prefix='native-payload-')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        def local(value):
            path = (self.root / value).resolve(); path.relative_to(self.root)
            return path
        for name, value in [('ROOT', self.root), ('local', local)]:
            obj = patch.object(payload, name, value); obj.start(); self.addCleanup(obj.stop)
        self.assets, self.fp_files = [], []
        def asset(name, role, data):
            path = local(name); path.parent.mkdir(parents=True, exist_ok=True); path.write_bytes(data)
            self.assets.append({**payload.record(name), 'role': role})
        for index in range(651):
            name = 'assets.scdb' if index == 0 else f'csv_logic/fixture-{index:03d}.csv'
            data = f'fixed content {index}'.encode()
            self.fp_files.append({'file': name, 'sha': hashlib.sha1(data).hexdigest()})
            asset(payload.PREFIX + name, 'paired_content', data)
        fp_data = json.dumps({'files': self.fp_files, 'version': payload.FINGERPRINT.version,
                              'sha': payload.FINGERPRINT.content}).encode()
        fp_lock = replace(payload.FINGERPRINT, derived_sha256=hashlib.sha256(fp_data).hexdigest(),
                          derived_bytes=len(fp_data))
        obj = patch.object(payload, 'FINGERPRINT', fp_lock); obj.start(); self.addCleanup(obj.stop)
        asset(payload.PREFIX + 'fingerprint.json', 'content_fingerprint', fp_data)
        catalog = {'version': 'competitive-static-data.v1', 'files': {}, 'source_files': {}}
        for name in sorted(payload.CATALOG_NAMES - {'manifest.json'}):
            data = ('fixed catalog ' + name).encode()
            key = name.removesuffix('.gz').removesuffix('.json')
            catalog['files'][key] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            asset(payload.CATALOG + name, 'source_extracted_catalog', gzip.compress(data) if name.endswith('.gz') else data)
        catalog_data = json.dumps(catalog).encode()
        obj = patch.object(payload, 'CATALOG_SHA256', hashlib.sha256(catalog_data).hexdigest())
        obj.start(); self.addCleanup(obj.stop)
        asset(payload.CATALOG + 'manifest.json', 'source_extracted_catalog', catalog_data)
        # A missing large APK proves the preparation path never reads unrelated runtime assets.
        self.assets.append({'path': 'absent-large.apk', 'role': 'offline_apk', 'bytes': 99999999, 'sha256': '0' * 64})
        self.manifest = self.root / 'runtime.json'
        self.locked = {'schema': 'royale-phase2-version.v1', 'runtime_kind': 'native_game',
            'ticks_per_second': 20, 'content_version': payload.FINGERPRINT.version,
            'content_manifest_sha1': payload.FINGERPRINT.content,
            'source_commits': {'firstlight3/FirstLight_CR': payload.SOURCE_COMMIT}, 'assets': self.assets}
        self.save_manifest()

    def save_manifest(self):
        self.locked['fingerprint'] = payload.canonical_hash({k: v for k, v in self.locked.items() if k != 'fingerprint'})
        self.manifest.write_text(json.dumps(self.locked))

    def test_complete_plan_reuses_existing_packer_without_reading_apk(self):
        output = self.root / 'packaged'
        def existing_packer(command, **options):
            self.assertEqual(command[0], str(self.root / '.venv-cloud/bin/python'))
            self.assertEqual(command[1], str(self.root / 'tools/phase2_native_pack_update.py'))
            self.assertEqual(options['cwd'], self.root)
            self.assertEqual(command[command.index('--resources') + 1], payload.PREFIX.rstrip('/'))
            plan = json.loads((output / 'plan.json').read_text())
            self.assertEqual(len(plan['files']), 651)
            self.assertEqual(plan['files'][0]['file'], 'assets.scdb')
            self.assertEqual(plan['source'], payload.record(self.manifest))
            archive = output / 'runtime-update.tar'; archive.write_bytes(b'interface fixture package')
            receipt = {'schema': 'phase2-native-update-package.v1', 'file_count': 652,
                'plan_sha256': payload.record(output / 'plan.json')['sha256'],
                'upstream_source_manifest_sha256': payload.CATALOG_SHA256,
                'archive_hashes': payload.record(archive)}
            (output / 'runtime-update.tar.json').write_text(json.dumps(receipt))
            return subprocess.CompletedProcess(command, 0, b'fixture packer completed', b'')
        with patch.object(payload.subprocess, 'run', side_effect=existing_packer) as runner:
            report = payload.create_payload(self.manifest, output)
        self.assertEqual(report['status'], 'packaged')
        self.assertEqual(runner.call_count, 1)
        self.assertEqual(report['verified_resource_files'], 652)
        self.assertFalse(report['native_execution_verified'])
        self.assertEqual(report['network_calls'], 0)
        with self.assertRaises(FileExistsError):
            payload.create_payload(self.manifest, output)

    def test_modified_resource_and_manifest_fail_before_pack_and_preserve_report(self):
        path = self.root / self.assets[0]['path']; path.write_bytes(b'modified')
        with patch.object(payload.subprocess, 'run') as runner:
            report = payload.create_payload(self.manifest, self.root / 'modified')
        runner.assert_not_called()
        self.assertEqual(report['status'], 'failed')
        self.assertIn('SHA256/size changed', report['error'])
        self.assertTrue((self.root / 'modified/report.json').is_file())
        self.locked['content_version'] = 'changed without fingerprint'
        self.manifest.write_text(json.dumps(self.locked))
        with self.assertRaises(ValueError):
            payload.prepare_plan(self.manifest)

    def test_incomplete_content_and_rehashed_catalog_change_are_rejected(self):
        missing = self.locked['assets'].pop(0); self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'Exactly 651'):
            payload.prepare_plan(self.manifest)
        self.locked['assets'].insert(0, missing)
        catalog_row = next(row for row in self.assets if row['path'].endswith('/card_support.json'))
        path = self.root / catalog_row['path']; path.write_bytes(b'rehashed but not fixed catalog')
        catalog_row.update(payload.record(path)); self.save_manifest()
        with self.assertRaisesRegex(ValueError, 'fixed unpacked'):
            payload.prepare_plan(self.manifest)


if __name__ == '__main__':
    unittest.main()

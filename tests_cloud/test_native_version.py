"""New-version asset identity fixtures; no real game, signing or Android RPC."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import zipfile

from royale_phase2.versioning import canonical_hash
from tools.cloud.native_version import CHECKS, ROOT, create_runtime_version, reference


class NativeVersion(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp/cloud').mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp/cloud')
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        body=bytearray(64);body[:4]=b'\x7fELF';body[4]=2;body[5]=1;body[18:20]=(183).to_bytes(2,'little')
        self.library=self.root/'libg.so';self.library.write_bytes(body)
        self.probe=self.root/'probe.so';self.probe.write_bytes(b'synthetic protocol fixture')
        self.input=self.root/'candidate.apk.partial';self.output=self.root/'local-build.apk'
        for path in (self.input,self.output):
            with zipfile.ZipFile(path,'w') as archive:
                archive.write(self.library,'lib/arm64-v8a/libg.so')
                if path==self.output:archive.write(self.probe,'lib/arm64-v8a/libcrprobe.so')
        assets=[{**reference(path),'role':role} for path,role in (
            (self.library,'native_library'),(self.probe,'native_probe'),
            (self.input,'original_apk'),(self.output,'offline_apk'))]
        assets.append({'path':(self.root/'missing-dataset-info.json').relative_to(ROOT).as_posix(),
            'sha256':'0'*64,'bytes':1,'role':'dataset_lock'})
        self.source=self.root/'baseline.json'
        source={'schema':'royale-phase2-version.v1','runtime_kind':'native_game',
            'ticks_per_second':20,'assets':assets,'original_apk_upstream_sha256':'0'*64}
        source['fingerprint']=canonical_hash(source)
        self.source.write_text(json.dumps(source),encoding='utf-8')
        self.source_bytes=self.source.read_bytes()
        self.receipt=self.root/'build.json'
        self.save_receipt()

    def save_receipt(self,**kwargs):
        receipt={'schema':'cloud-native-apk-build.v1','success':True,'input_apk':reference(self.input),
            'output_apk':reference(self.output),'checks':{name:True for name in CHECKS}}
        receipt.update(kwargs)
        self.receipt.write_text(json.dumps(receipt),encoding='utf-8')

    def create(self,target=None):
        return create_runtime_version(self.receipt,target or self.root/'new-version.json',source_manifest=self.source)

    def test_separate_runtime_lock_preserves_baseline_and_requires_android_identity(self):
        report=self.create()
        self.assertTrue(report['asset_identity_verified'])
        self.assertFalse(report['native_execution_verified'])
        self.assertFalse(report['historical_checkpoints_compatible'])
        self.assertEqual(self.source.read_bytes(),self.source_bytes)
        manifest=json.loads((ROOT/report['manifest']['path']).read_text())
        self.assertEqual(len(manifest['assets']),4)
        self.assertEqual(len(manifest['source_dataset_locks_excluded_from_runtime_profile']),1)
        self.assertFalse((ROOT/report['android_identity_required']).exists())
        with self.assertRaises(FileExistsError):self.create()
        with self.assertRaises(FileExistsError):self.create(self.source)

    def test_failed_apk_check_cannot_create_a_version(self):
        checks={name:True for name in CHECKS};checks['patch']=False
        self.save_receipt(checks=checks)
        with self.assertRaisesRegex(ValueError,'Every APK static'):
            self.create()
        self.assertFalse((self.root/'new-version.json').exists())

    def test_unsuccessful_build_receipt_cannot_be_promoted(self):
        self.save_receipt(success=False)
        with self.assertRaisesRegex(ValueError,'verified local APK build receipt'):
            self.create()

    def test_changed_build_artifact_cannot_create_a_version(self):
        with self.output.open('ab') as stream:stream.write(b'changed')
        with self.assertRaisesRegex(ValueError,'Build artifact changed'):
            self.create()

    def test_receipt_cannot_authorize_a_different_native_engine(self):
        with zipfile.ZipFile(self.output,'w') as archive:
            archive.writestr('lib/arm64-v8a/libg.so',b'unsupported engine')
            archive.write(self.probe,'lib/arm64-v8a/libcrprobe.so')
        self.save_receipt()
        with self.assertRaisesRegex(ValueError,'libg differs'):
            self.create()


if __name__=='__main__':unittest.main()

"""Safe artifact relocation with real dataset/checkpoint loaders, no native RPC."""
from copy import deepcopy
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from royale_phase3.common import ROOT, digest
from tools.cloud import reorganize_research as migration


@unittest.skipUnless(importlib.util.find_spec('torch'), 'Optional CPU Torch not installed')
class ResearchRelocationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import torch
        torch.set_num_threads(2)

    def setUp(self):
        from royale_phase3.learning import SmallPolicy, architecture, save_checkpoint
        import torch
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='research-relocation-')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.source = self.folder / 'source'; self.source.mkdir()
        self.target = self.folder / 'current'
        self.asset = self.source / 'native.bin'; self.asset.write_bytes(b'native fixture, not executable')
        manifest = {'schema': 'royale-phase2-version.v1', 'runtime_kind': 'native_game',
                    'assets': [{'path': self.rel(self.asset), 'role': 'native_library',
                                'sha256': migration.sha_bytes(self.asset.read_bytes())}]}
        manifest['fingerprint'] = digest(manifest)
        self.version = manifest['fingerprint']
        self.manifest = self.source / 'version.json'; self.save(self.manifest, manifest)
        dims = {'hud': 1, 'observation': 1, 'history': 2, 'belief': 1, 'feedback': 1}
        rows = [{'episode_id': split, 'native_owner': 0, 'split': split, 'tick': 90,
                 **{name: [0.] * size for name, size in dims.items()},
                 'mask': [True, False, False, False, False], 'action_index': 0,
                 'loss_mask': True, 'previous_action': 0, 'dt_ticks': 10,
                 'position_index': 0, 'xy': [0., 0.],
                 'targets': {'world': [0.] * 9, 'world_mask': [False] * 9}}
                for split in ('train', 'validation')]
        self.rows = self.source / 'rows.jsonl'
        self.rows.write_text(''.join(json.dumps(row) + '\n' for row in rows))
        meta = {'schema': 'phase3-learning-dataset.v1', 'control_ticks': 10,
                'rows': {'path': self.rel(self.rows), 'sha256': migration.sha_bytes(self.rows.read_bytes())},
                'feature_dims': dims, 'version_fingerprint_sha256': self.version,
                'observation_profile': 'fair', 'source': {'path': 'historical-source', 'sha256': 'provenance'}}
        self.metadata = self.source / 'metadata.json'; self.save(self.metadata, meta)
        model = SmallPolicy(architecture(meta, 'C', hidden=4))
        model_folder = self.source / 'model'; model_folder.mkdir()
        config = {'dataset': {'path': self.rel(self.metadata), 'sha256': migration.sha_bytes(self.metadata.read_bytes())},
                  'rows': meta['rows'], 'version_fingerprint_sha256': self.version,
                  'observation_profile': 'fair', 'control_ticks': 10, 'architecture': model.config}
        self.checkpoint = save_checkpoint(model_folder, model, torch.optim.Adam(model.parameters()),
                                         config, 1, [], 1., deepcopy(model.state_dict()))
        self.video = self.source / 'native.webm'
        self.video.write_bytes(b'video-copy fixture; no actual playable media claim')
        self.recording = self.source / 'recording.json'
        self.save(self.recording, {'source': 'actual_android_frameoutput', 'status': 'partial',
            'recording_complete': False, 'natural_terminal': False, 'path': self.rel(self.video),
            'sha256': migration.sha_bytes(self.video.read_bytes())})
        self.sources = {
            'schema': 'royale-research-migration-sources.v1',
            'datasets': [{'id': 'native', 'source': self.rel(self.metadata),
                          'target_metadata': self.rel(self.target / 'data/metadata.json'),
                          'target_rows': self.rel(self.target / 'data/rows.jsonl')}],
            'checkpoint': {'source': self.rel(self.checkpoint), 'dataset_id': 'native',
                           'target_metadata': self.rel(self.target / 'model/reference.json'),
                           'target_weights': self.rel(self.target / 'model/reference.npz')},
            'native_manifest': {'source': self.rel(self.manifest), 'target': self.rel(self.target / 'runtime/version.json')},
            'media': {'source_receipt': self.rel(self.recording), 'optional': True,
                      'target_video': self.rel(self.target / 'media/native-initialization.webm'),
                      'target_receipt': self.rel(self.target / 'media/native-initialization.json')}}
        self.sources_path = self.folder / 'sources.json'; self.save(self.sources_path, self.sources)
        self.originals = {self.rel(path): path.read_bytes() for path in self.source.rglob('*') if path.is_file()}

    def rel(self, path):
        return Path(path).relative_to(ROOT).as_posix()

    def save(self, path, data):
        Path(path).write_bytes(migration.encoded(data))

    def plan(self):
        return migration.plan_migration(self.rel(self.sources_path))

    def assert_originals_preserved(self):
        for path, payload in self.originals.items():
            self.assertEqual((ROOT / path).read_bytes(), payload, path)

    def test_plan_validates_real_loaders_without_creating_target_directory(self):
        receipt, _, _ = self.plan()
        self.assertEqual(receipt['status'], 'planned')
        self.assertFalse(self.target.exists())
        self.assertTrue(receipt['source_validation'][-1]['strict_tensor_state_loaded'])
        self.assertEqual(receipt['native_rpc_calls'], 0)
        self.assert_originals_preserved()

    def test_apply_rebases_live_references_and_preserves_model_and_native_identity(self):
        receipt = migration.apply_migration(*self.plan())
        self.assertEqual(receipt['status'], 'completed')
        source_meta = json.loads(self.metadata.read_text())
        target_meta = json.loads((self.target / 'data/metadata.json').read_text())
        source_meta['rows']['path'] = self.rel(self.target / 'data/rows.jsonl')
        self.assertEqual(target_meta, source_meta)
        self.assertEqual((self.target / 'data/rows.jsonl').read_bytes(), self.rows.read_bytes())
        checkpoint = json.loads((self.target / 'model/reference.json').read_text())
        original_checkpoint = json.loads(self.checkpoint.read_text())
        self.assertEqual((self.target / 'model/reference.npz').read_bytes(),
                         (ROOT / original_checkpoint['weights']['path']).read_bytes())
        self.assertEqual(checkpoint['config']['dataset']['path'], self.rel(self.target / 'data/metadata.json'))
        self.assertEqual(checkpoint['config']['rows'], target_meta['rows'])
        self.assertFalse(checkpoint['artifact_relocation']['training_performed'])
        self.assertEqual((self.target / 'runtime/version.json').read_bytes(), self.manifest.read_bytes())
        self.assertEqual(checkpoint['config']['version_fingerprint_sha256'], self.version)
        self.assertEqual(receipt['external_pinned_assets']['count'], 1)
        self.assertFalse(receipt['external_pinned_assets']['copied'])
        media = json.loads((self.target / 'media/native-initialization.json').read_text())
        self.assertEqual(media['status'], 'partial'); self.assertEqual(media['coverage'], 'initialization_only')
        self.assertFalse(media['complete_game']); self.assertFalse(media['recording_complete'])
        self.assert_originals_preserved()

    def test_repeated_apply_reuses_identical_targets_without_rewriting(self):
        migration.apply_migration(*self.plan())
        before = {path: path.stat().st_mtime_ns for path in self.target.rglob('*') if path.is_file()}
        receipt = migration.apply_migration(*self.plan())
        self.assertTrue(all(item['status'] == 'reused' for item in receipt['files']))
        self.assertEqual(before, {path: path.stat().st_mtime_ns for path in before})

    def test_one_conflict_blocks_every_target_and_preserves_existing_bytes(self):
        self.target.mkdir(); (self.target / 'data').mkdir()
        conflicting = self.target / 'data/rows.jsonl'; conflicting.write_bytes(b'user existing work')
        receipt, operations, sources = self.plan()
        self.assertEqual(receipt['status'], 'blocked')
        with self.assertRaises(FileExistsError):
            migration.apply_migration(receipt, operations, sources)
        self.assertEqual(conflicting.read_bytes(), b'user existing work')
        self.assertFalse((self.target / 'model').exists())
        self.assertFalse((self.target / 'data/metadata.json').exists())
        self.assert_originals_preserved()

    def test_corrupt_weight_or_source_digest_is_rejected_before_any_copy(self):
        self.sources['checkpoint']['source_sha256'] = '0' * 64
        self.save(self.sources_path, self.sources)
        with self.assertRaisesRegex(ValueError, 'SHA256'):
            self.plan()
        self.sources['checkpoint'].pop('source_sha256'); self.save(self.sources_path, self.sources)
        weights = ROOT / json.loads(self.checkpoint.read_text())['weights']['path']; weights.write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError, 'weights changed'):
            self.plan()
        self.assertFalse(self.target.exists())

    def test_symlink_sources_target_parents_and_nonlocal_paths_are_rejected(self):
        for value in ('../outside', '/tmp/outside', 'C:/outside', r'research\outside', 'a/../b', 'a//b'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                migration.safe_path(value)
        link = self.folder / 'linked'; link.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            migration.payload_for(self.rel(link / 'metadata.json'))
        self.target.symlink_to(self.source, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, '符号链接'):
            self.plan()
        self.assert_originals_preserved()

    def test_optional_missing_media_does_not_invent_video(self):
        self.sources['media']['source_receipt'] = self.rel(self.source / 'not-recorded.json')
        self.save(self.sources_path, self.sources)
        receipt = migration.apply_migration(*self.plan())
        self.assertEqual(receipt['media']['status'], 'unavailable')
        self.assertFalse((self.target / 'media').exists())

    def test_cli_default_is_plan_and_writes_only_explicit_new_receipt(self):
        receipt_path = self.folder / 'plan.json'
        with patch('sys.stdout', new_callable=io.StringIO):
            code = migration.main(['--sources', self.rel(self.sources_path), '--receipt', self.rel(receipt_path)])
        self.assertEqual(code, 0); self.assertTrue(receipt_path.is_file()); self.assertFalse(self.target.exists())
        self.assertEqual(json.loads(receipt_path.read_text())['mode'], 'plan')


if __name__ == '__main__':
    unittest.main()

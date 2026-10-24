"""Portable contracts, real saved model decoding and migration boundaries."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tools.cloud import workspace as w
from tools.cloud.import_repository import safe

class Portable(unittest.TestCase):
    def test_reviewed_asset_bytes(self):self.assertEqual(w.verify_assets()['status'],'pass')
    def test_model_matches_saved_decoding(self):
        result=w.infer();self.assertEqual(len(result['results']),3)
        self.assertTrue(all(r['matches_saved_decoding'] for r in result['results']))
        self.assertFalse(result['fresh_legality_revalidated']);self.assertEqual(result['native_rpc_calls'],0)
    def test_asset_destinations_stay_inside_workspace(self):
        for row in w.manifest()['files']:
            w.local(row['source'])
            if row.get('destination'):w.local(row['destination'])
    def test_import_rejects_git_and_parent_paths(self):
        for name in ('.git/config','../escape','/absolute','C:/escape','a\\b','x/../../escape'):
            with self.assertRaises(ValueError):safe(w.ROOT,name)
    def test_training_inputs_are_non_pickle_arrays(self):
        import numpy as np
        with np.load(w.local(w.manifest()['numeric']['arrays']),allow_pickle=False) as arrays:
            for key in arrays.files:self.assertFalse(arrays[key].dtype.hasobject)
            self.assertEqual(len(arrays['train_target']),13431)
            self.assertEqual(len(arrays['validation_target']),3348)
    def test_native_read_only_probe_does_not_claim_success(self):
        from tools.cloud.native_capabilities import inspect
        result=inspect();self.assertFalse(result['native_runtime_verified']);self.assertEqual(result['rpc_calls'],0)
    def test_cross_platform_lock_excludes_second_owner(self):
        from royale_phase3.common import native_lock
        root=w.local('.tmp');root.mkdir(exist_ok=True)
        # Exercise the real OS lock on an isolated file, so this offline test
        # can run while a browser legitimately owns the actual native session.
        with tempfile.TemporaryDirectory(prefix='portable-lock-',dir=root) as temporary:
            with patch('royale_phase3.common.local',return_value=Path(temporary)/'native.lock'):
                with native_lock():
                    with self.assertRaises(RuntimeError):
                        with native_lock():pass
                with native_lock():pass

if __name__=='__main__':unittest.main()

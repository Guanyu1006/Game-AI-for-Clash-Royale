"""Cloud checkpoint persistence, inference compatibility and tamper detection."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import numpy as np

from tools.cloud import workspace as w
from tools.cloud import train_cache


class TrainingPipeline(unittest.TestCase):
    def test_numeric_roundtrip_validation_and_tamper_detection(self):
        base=w.local('.tmp/cloud');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            folder=Path(directory);original=w.manifest();m={**original,'numeric':dict(original['numeric'])}
            with np.load(w.local(m['numeric']['arrays']),allow_pickle=False) as archive:
                arrays={}
                for split in ('train','validation'):
                    target=archive[split+'_target'];plays=np.flatnonzero(target)
                    chosen=np.unique(np.concatenate([np.arange(8),plays[:4]]))
                    for key in ('x','target','xy','masks'):arrays[split+'_'+key]=archive[split+'_'+key][chosen]
            cache=folder/'arrays.npz';np.savez_compressed(cache,**arrays);m['numeric']['arrays']=str(cache.relative_to(w.ROOT))
            with patch.object(train_cache,'manifest',return_value=m):
                report=train_cache.train(1,str((folder/'run').relative_to(w.ROOT)))
            self.assertTrue(report['checkpoint_roundtrip_exact']);self.assertFalse(report['test_evaluated'])
            saved=report['checkpoint']['path']
            result=train_cache.evaluate(saved,'validation',folder/'validation.json')
            self.assertEqual(result['metrics']['prediction_sha256'],report['validation_decoded']['prediction_sha256'])
            self.assertFalse(result['fresh_legality_revalidated'])
            parameters,meta=train_cache.load_checkpoint(saved)
            self.assertEqual(parameters['w1'].shape[0],meta['architecture']['input_width'])
            weights=w.local(meta['weights']['path']);weights.write_bytes(weights.read_bytes()+b'tampered')
            with self.assertRaisesRegex(ValueError,'hash'):train_cache.load_checkpoint(saved)


if __name__=='__main__':unittest.main()

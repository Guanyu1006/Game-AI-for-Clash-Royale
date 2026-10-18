"""Evidence-boundary unit tests. Synthetic headers are not native-game probes."""
import json
from pathlib import Path
import tempfile
import unittest
from royale_phase2.runtime import local
from royale_phase2.quality import EpisodeQuality,compare_outcome,compare_sequences
from royale_phase2.versioning import VersionMismatch,binary_identity,canonical_hash,file_hash,verify_manifest

class EvidenceTests(unittest.TestCase):
    def test_missing_outcome_is_unknown_not_zero(self):
        result=compare_outcome({}, {'winner':0,'crowns':[0,0]})
        self.assertEqual(result['status'],'unknown');self.assertEqual(result['compared_fields'],0)
    def test_outcome_matches_do_not_claim_trajectory(self):
        result=compare_outcome({'winner':1,'crowns':[0,1]}, {'winner':1,'crowns':[0,1]})
        self.assertEqual(result['status'],'pass');self.assertEqual(result['fields']['tower_hp']['status'],'unknown')
        self.assertNotIn('trajectory_validated',result)
    def test_partial_outcome_failure_is_not_hidden(self):
        result=compare_outcome({'winner':1,'crowns':[0,1]}, {'winner':1,'crowns':[0,3]})
        self.assertEqual(result['status'],'fail')
    def test_tolerance_is_explicit_and_finite(self):
        result=compare_outcome({'end_tick':100},{'end_tick':101},{'end_tick':1})
        self.assertEqual(result['status'],'pass')
        for tolerance in (-1,float('nan'),True):
            with self.assertRaises(ValueError):compare_outcome({'end_tick':100},{'end_tick':101},{'end_tick':tolerance})
    def test_parse_success_is_not_execution(self):
        row=EpisodeQuality('sample',parse_valid='pass').to_dict()
        self.assertEqual(row['runtime_replayable'],'not_run')
        with self.assertRaises(ValueError):EpisodeQuality('sample',runtime_replayable='pass').to_dict()
    def test_trajectory_claim_needs_independent_source(self):
        with self.assertRaises(ValueError):EpisodeQuality('sample',trajectory_validated='pass').to_dict()
    def test_semantic_hp_difference_is_preserved(self):
        expected=[{'tick':0,'units':[{'hp':100}]},{'tick':1,'units':[{'hp':90}]}]
        actual=[{'tick':0,'units':[{'hp':100}]},{'tick':1,'units':[{'hp':89}]}]
        self.assertEqual(compare_sequences(expected,actual)['first_divergence_index'],1)
    def test_empty_repeat_does_not_pass(self):
        self.assertEqual(compare_sequences([],[])['status'],'unknown')
    def test_repeat_requires_same_length_and_queue(self):
        first=[{'tick':0,'pending':[{'card':1,'tick':2}]}]
        second=[{'tick':0,'pending':[]}]
        self.assertEqual(compare_sequences(first,second)['status'],'fail')
        self.assertEqual(compare_sequences(first,first+first)['reason'],'sequence_length')
    def test_nan_cannot_be_hashed_as_semantic_state(self):
        with self.assertRaises(ValueError):canonical_hash({'hp':float('nan')})
    def test_project_path_cannot_escape(self):
        with self.assertRaises(ValueError):local('../escaped-phase2')

class AssetTests(unittest.TestCase):
    def test_file_verification_and_binary_architecture(self):
        base=local('.tmp/phase2');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            folder=Path(directory).resolve();folder.relative_to(local('.'))
            lib=folder/'synthetic-header.so'
            header=bytearray(64);header[:6]=b'\x7fELF\x02\x01';header[18:20]=(183).to_bytes(2,'little')
            lib.write_bytes(header)
            self.assertEqual(binary_identity(lib)['architecture'],'aarch64')
            manifest={'schema':'royale-phase2-version.v1','runtime_kind':'native_game','ticks_per_second':20,
                      'assets':[{'path':str(lib),'role':'native_library','sha256':file_hash(lib)}]}
            path=folder/'version.json';path.write_text(json.dumps(manifest),encoding='utf-8')
            result=verify_manifest(path);self.assertIn('does not prove battle execution',result['claim'])
            lib.write_bytes(header+b'changed')
            with self.assertRaises(VersionMismatch):verify_manifest(path)
    def test_old_simulator_identity_rejected(self):
        base=local('.tmp/phase2');base.mkdir(parents=True,exist_ok=True)
        with tempfile.TemporaryDirectory(dir=base) as directory:
            folder=Path(directory).resolve();folder.relative_to(local('.'))
            path=folder/'simulator.json';path.write_text(json.dumps({'schema':'royale-phase2-version.v1','runtime_kind':'reconstructed_simulator'}),encoding='utf-8')
            with self.assertRaises(VersionMismatch):verify_manifest(path)

if __name__=='__main__':unittest.main()

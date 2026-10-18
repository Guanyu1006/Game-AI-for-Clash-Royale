"""External source contracts; synthetic tests are not native match evidence."""
from __future__ import annotations
import ast
import json
import lzma
from pathlib import Path
import pickle
import struct
import sys
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_phase3.sources import (cochon_record, interpret_object_npy_xz, parse_cochon_raw,
    tv_record, validate_record, wty_records)


class SourceContracts(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'.tmp/phase3/source_tests'; self.root.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=self.root)
        self.addCleanup(self.temp.cleanup)

    def write_pickle(self,payload):
        header=b"{'descr': '|O', 'fortran_order': False, 'shape': (), }\n"
        raw=b'\x93NUMPY\x01\x00'+struct.pack('<H',len(header))+header+payload
        path=Path(self.temp.name)/'test.npy.xz'; path.write_bytes(lzma.compress(raw)); return path

    def test_forbidden_global_cannot_execute(self):
        # GLOBAL is inspected, never resolved. The target file must never exist.
        target=Path(self.temp.name)/'must_not_exist'
        expression=("__import__('pathlib').Path("+repr(str(target))+").touch()").encode('utf8')
        payload=b'\x80\x03cbuiltins\neval\nX'+struct.pack('<I',len(expression))+expression+b'\x85R.'
        with self.assertRaisesRegex(ValueError,'forbidden GLOBAL'):
            interpret_object_npy_xz(self.write_pickle(payload))
        self.assertFalse(target.exists())

    def test_arbitrary_reduce_not_permitted(self):
        # Even a non-global callable-like string cannot become a reducer.
        with self.assertRaisesRegex(ValueError,'unsupported REDUCE'):
            interpret_object_npy_xz(self.write_pickle(b'\x80\x03X\x03\x00\x00\x00bad)R.'))

    def test_persistent_id_and_object_opcodes_forbidden(self):
        for payload in (b'\x80\x03Panything\n.',b'\x80\x03N\x81.'):
            with self.assertRaisesRegex(ValueError,'forbidden pickle opcode'):
                interpret_object_npy_xz(self.write_pickle(payload))

    def test_compression_bomb_bounded(self):
        path=Path(self.temp.name)/'bomb.npy.xz';path.write_bytes(lzma.compress(b'x'*2000))
        with self.assertRaisesRegex(ValueError,'budget'):
            interpret_object_npy_xz(path,max_unpacked=1000)

    def test_outside_project_forbidden(self):
        with self.assertRaises(ValueError): interpret_object_npy_xz('C:/Windows/win.ini')

    def test_cochon_html_and_unknown_schema(self):
        parsed=parse_cochon_raw({'schema_version':1,'payload':{'success':True,'html':
            '<img data-card="knight" data-t="20" data-s="blue"><div data-c="knight" data-t="20" data-x="1" data-y="2">'}})
        self.assertEqual(parsed['schema_kind'],'raw_html_v1');self.assertEqual(len(parsed['events']),1)
        self.assertEqual(len(parsed['markers']),1)
        with self.assertRaises(ValueError): parse_cochon_raw({'snapshot':[]})

    def test_cochon_never_generates_ghost_or_tower_hp(self):
        r=cochon_record({'battle_id':'a','sequence':0,'ticks':20,'side':'team','event_type':'card_play',
                          'card':'knight','x':9500,'y':11000},{'path':'fixture','sha256':'0'*64})
        self.assertEqual(validate_record(r),[]);self.assertEqual(r['time']['seconds'],1)
        self.assertEqual(r['coordinates']['canonical_x'],9.5)
        self.assertEqual(r['quality']['ghost_state'],'not_generated')
        self.assertEqual(r['observation'],{});self.assertIsNone(r['action']['consumed'])

    def test_unknown_cochon_position_mask(self):
        r=cochon_record({'battle_id':'x','event_type':'card_play','x':None,'y':100,'card':'x'}, {})
        self.assertFalse(r['coordinates']['valid']);self.assertIsNone(r['coordinates']['canonical_y'])
        r=cochon_record({'battle_id':'x','event_type':'card_play','x':'None','y':'None','card':'x'}, {})
        self.assertEqual(r['coordinates']['source_x'],'None');self.assertFalse(r['coordinates']['valid'])

    def test_tv_never_multiplies_frame_id_into_time(self):
        r=tv_record({'replay':'r','frame':100,'card':'knight','x':-1,'y':-1}, {},'processed_offset1')
        self.assertEqual(validate_record(r),[]);self.assertIsNone(r['time']['seconds'])
        self.assertIsNone(r['time']['dt_seconds']);self.assertFalse(r['coordinates']['present'])
        self.assertFalse(r['coordinates']['valid'])

    def test_tv_even_present_xy_not_silently_native(self):
        r=tv_record({'replay':'r','frame':10,'card':'knight','x':100,'y':200}, {},'processed_hand_elixir')
        self.assertTrue(r['coordinates']['present']);self.assertFalse(r['coordinates']['valid'])
        self.assertIsNone(r['coordinates']['canonical_x']);self.assertIsNone(r['action']['consumed'])

    def test_tv_derivatives_same_match_group(self):
        item={'replay':'same','frame':1,'card':'knight'}
        self.assertEqual(tv_record(item,{},'processed_offset1')['group_id'],
                         tv_record(item,{},'processed_hand_elixir')['group_id'])

    def test_wty_file_end_never_true_terminal(self):
        data={'state':[{'time':0,'unit_infos':[]},{'time':1,'unit_infos':[]}],
              'action':[{'card_id':1,'xy':None},{'card_id':0,'xy':None}],'reward':[1.,0.]}
        rs=list(wty_records(data,{},'fixture'))
        self.assertEqual(len(rs),2);self.assertTrue(rs[-1]['end']['file_boundary'])
        self.assertIsNone(rs[-1]['end']['terminated']);self.assertEqual(validate_record(rs[0]),[])
        self.assertIsNone(rs[0]['observation']['source_offset'])

    def test_real_two_local_wty_samples_safely_decode(self):
        paths=sorted((ROOT/'references/library/assets/wty-yy__Clash-Royale-Replay-Dataset').rglob('*.xz'))
        self.assertEqual(len(paths),2)
        for path in paths:
            data,audit=interpret_object_npy_xz(path)
            self.assertEqual(audit['globals_executed'],0);self.assertEqual(audit['reducer_functions_called'],0)
            rs=list(wty_records(data,{'path':str(path)},path.stem))
            self.assertGreater(len(rs),100);self.assertTrue(all(not validate_record(x) for x in rs))

    def test_implementation_has_no_unsafe_load_or_dynamic_import(self):
        tree=ast.parse((ROOT/'royale_phase3/sources.py').read_text(encoding='utf8'))
        banned={'eval','exec','getattr','__import__','load','loads','Unpickler','find_class'}
        calls=[]
        for node in ast.walk(tree):
            if isinstance(node,ast.Call):
                calls.append(node.func.id if isinstance(node.func,ast.Name) else node.func.attr if isinstance(node.func,ast.Attribute) else '')
        self.assertFalse(set(calls)&banned)

    def test_auxiliary_context_does_not_include_future_outcome(self):
        from tools.phase3_sources import next_card_examples
        ref={'path':'fixture','sha256':'0'*64}
        first=cochon_record({'battle_id':'a','sequence':0,'ticks':10,'side':'team',
            'event_type':'card_play','card':'knight'},ref)
        future=cochon_record({'battle_id':'a','sequence':1,'ticks':20,'side':'team',
            'event_type':'card_play','card':'archers'},ref)
        examples=next_card_examples([first,future])
        future['action']['card']='giant';future['end']['outcome']='future_secret'
        mutated=next_card_examples([first,future])
        self.assertEqual(examples[0]['previous_public_card'],mutated[0]['previous_public_card'])
        self.assertNotEqual(examples[0]['target_next_card'],mutated[0]['target_next_card'])
        self.assertNotIn('outcome',examples[0])

    def test_real_auxiliary_whole_match_split_has_no_alias_overlap(self):
        path=ROOT/'outputs/phase3/sources/aux_next_card_v1/frozen_config.json'
        if not path.exists():self.skipTest('optional real auxiliary experiment not run yet')
        config=json.loads(path.read_text(encoding='utf8'))
        self.assertFalse(set(config['train_groups'])&set(config['validation_groups']))
        self.assertFalse(set(config['train_content_groups'])&set(config['validation_content_groups']))
        self.assertEqual(config['actor_features'],['previous_public_card'])
        self.assertTrue(config['not_main_bc_or_native_dynamics'])


if __name__=='__main__': unittest.main()

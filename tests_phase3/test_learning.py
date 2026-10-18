"""Synthetic contract checks only; these do not satisfy native-data gates."""
from copy import deepcopy
import json
import shutil
import tempfile
import unittest
from pathlib import Path

import numpy as np
import torch

from royale_phase3.common import configure, reference, write
from royale_phase3.learning import (SmallPolicy, StatefulPolicy, actor_vector,
    architecture, decode, evaluate, load_dataset, read_checkpoint, sequence_windows,
    train, _initial_hidden, _tensor_batch, loss)


def sample(episode='e1', tick=10, owner=0, split='train'):
    return {'episode_id':episode,'native_owner':owner,'split':split,'tick':tick,'dt_ticks':10,
        'hud':[.1,.2], 'observation':[.1,.2,0.], 'history':[.1,.2,0.,tick/100],
        'belief':[.4,.8], 'feedback':[1.,0.,0.,0.], 'previous_action':0,
        'mask':[True,True,False,False,False], 'action_index':1 if tick%20 else 0,
        'hand_cards':[28000000,26000001,None,None],
        'position_index':200,'xy':[.3,.4], 'position_mask':bool(tick%20),'card_id':26000000,
        'loss_mask':True,'targets':{'world':[.1]*9,'world_mask':[True]*9,'opponent':1,'opponent_mask':True},
        'terminated':False,'truncated':False}


def metadata():
    return {'schema':'phase3-learning-dataset.v1','feature_dims':{'hud':2,'observation':3,'history':4,'belief':2,'feedback':4},
        'observation_profile':'fair','version_fingerprint_sha256':'a'*64,'control_ticks':10,
        'task_id':'synthetic-contract-only','split_groups':{}}


class LearningContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls): configure();torch.set_num_threads(2)

    def setUp(self):
        self.folder=Path(tempfile.mkdtemp(prefix='learning-contract-',dir='.tmp/phase3'))

    def tearDown(self): shutil.rmtree(self.folder)

    def dataset(self):
        data=[sample(e,t,split=s) for e,s in [('a','train'),('b','validation'),('c','test')] for t in range(10,70,10)]
        path=self.folder/'rows.jsonl';path.write_text(''.join(json.dumps(r)+'\n' for r in data),encoding='utf8')
        meta=metadata();meta['rows']=reference(path);target=self.folder/'metadata.json';write(target,meta)
        return target,data

    def test_target_private_fields_do_not_enter_actor(self):
        a=sample();b=deepcopy(a);b['targets']['world']=[999.]*9;b['episode_id']='secret';b['card_id']=999
        for variant in 'ABCDE': self.assertEqual(actor_vector(a,variant),actor_vector(b,variant))

    def test_history_changes_recurrent_input_but_not_single_frame(self):
        a=sample();b=deepcopy(a);b['history'][-1]=.99
        self.assertEqual(actor_vector(a,'B'),actor_vector(b,'B'));self.assertNotEqual(actor_vector(a,'C'),actor_vector(b,'C'))

    def test_windows_preserve_owner_episode_and_count_every_row_once(self):
        data=[sample('a',t,o) for o in [0,1] for t in range(10,80,10)]+[sample('b',10)]
        windows=sequence_windows(data,3,2)
        self.assertEqual(sum(len(w['rows']) for w in windows),len(data))
        for w in windows:
            self.assertTrue(all((r['episode_id'],r['native_owner'])==w['key'] for r in w['rows']+w['context']))
            self.assertEqual(sum(w['valid']),len(w['rows']));self.assertLessEqual(w['burnin_count'],2)

    def test_padding_and_unknown_labels_have_no_loss(self):
        model=SmallPolicy(architecture(metadata(),'E',8,True));row=sample();row['loss_mask']=False
        windows=sequence_windows([row],4,2);batch=_tensor_batch(windows,'E',4);out,_=model(batch['x'])
        total,_=loss(model,out,batch);self.assertEqual(float(total.detach()),0.)
        self.assertFalse(batch['world_valid'].any());self.assertFalse(batch['position_valid'].any())

    def test_burnin_hidden_is_detached(self):
        model=SmallPolicy(architecture(metadata(),'C',8));windows=sequence_windows([sample(tick=t) for t in range(10,60,10)],2,2)
        state=_initial_hidden(model,windows,'C');self.assertFalse(state.requires_grad);self.assertEqual(tuple(state.shape),(1,3,8))

    def test_mask_always_forces_wait(self):
        model=SmallPolicy(architecture(metadata(),'D',8));out,_=model(torch.zeros(1,1,model.config['input_width']))
        self.assertEqual(decode(out,[True,False,False,False,False],model.config,sample())['action_index'],0)

    def test_card_conditioned_grid_and_coordinate_order(self):
        config=architecture(metadata(),'D',8);out={'action':torch.tensor([0.,2.,0.,0.,0.]),'position':torch.zeros(4,576)}
        out['position'][0,575]=9;out['position'][1,0]=20
        result=decode(out,[True,True,True,False,False],config,sample())
        self.assertEqual(result['xy_native'],[17500,31500])
        out['action'][2]=4
        self.assertEqual(decode(out,[True,True,True,False,False],config,sample())['xy_native'],[500,500])

    def test_position_mask_uses_predicted_public_card_not_target(self):
        config=architecture(metadata(),'D',8);out={'action':torch.tensor([0.,0.,4.,0.,0.]),'position':torch.zeros(4,576)}
        out['position'][1,575]=9;out['position'][1,20]=8
        row=sample();row['card_id']=28000001
        self.assertEqual(decode(out,[True,True,True,False,False],config,row)['xy_native'],[2500,1500])
        row['native_owner']=1
        self.assertEqual(decode(out,[True,True,True,False,False],config,row)['xy_native'],[17500,31500])

    def test_2d_position_public_region_transform_both_sides(self):
        config=architecture(metadata(),'C',8);out={'action':torch.tensor([0.,0.,4.,0.,0.]),'position':torch.tensor([.5,1.])}
        row=sample();self.assertEqual(decode(out,row['mask'][:2]+[True,False,False],config,row)['xy_native'][1],15999)
        row['native_owner']=1
        self.assertEqual(decode(out,[True,True,True,False,False],config,row)['xy_native'][1],31999)

    def test_same_tick_is_idempotent_and_changed_input_rejected(self):
        policy=StatefulPolicy(SmallPolicy(architecture(metadata(),'C',8)));row=sample()
        a=policy.predict(row);h=policy.hidden.clone();self.assertEqual(a,policy.predict(row));self.assertTrue(torch.equal(h,policy.hidden))
        row['history'][0]=99
        with self.assertRaises(ValueError):policy.predict(row)

    def test_reset_owner_and_episode_are_isolated(self):
        model=SmallPolicy(architecture(metadata(),'C',8));policy=StatefulPolicy(model)
        policy.predict(sample(tick=10));policy.predict(sample(tick=20));fresh=StatefulPolicy(model)
        self.assertEqual(policy.predict(sample('other',10,1)),fresh.predict(sample('other',10,1)))
        self.assertTrue(torch.equal(policy.hidden,fresh.hidden))

    def test_eval_single_step_and_sequence_path_agree(self):
        model=SmallPolicy(architecture(metadata(),'C',8));report=evaluate(model,[sample('v',t,split='validation') for t in range(10,60,10)],'validation')
        self.assertTrue(report['online_offline_exact_match']);self.assertEqual(report['rows'],5)

    def test_loading_rejects_hash_changes_and_split_leakage(self):
        path,data=self.dataset();loaded,_=load_dataset(path);self.assertEqual(len(loaded),18)
        data[-1]['episode_id']='a'
        raw=self.folder/'rows.jsonl';raw.write_text(''.join(json.dumps(r)+'\n' for r in data),encoding='utf8')
        with self.assertRaisesRegex(ValueError,'SHA256'):load_dataset(path)
        m=metadata();m['rows']=reference(raw);write(path,m)
        with self.assertRaisesRegex(ValueError,'leakage'):load_dataset(path)

    def test_safe_roundtrip_resume_equals_uninterrupted_and_excludes_test(self):
        path,data=self.dataset();full=self.folder/'full';resumed=self.folder/'resumed'
        one=train(path,full,variant='D',epochs=2,hidden=8,batch_size=2,sequence_length=3,burnin=2)
        partial=train(path,resumed,variant='D',epochs=2,hidden=8,batch_size=2,sequence_length=3,burnin=2,stop_after_epochs=1)
        self.assertFalse(partial['completed'])
        two=train(path,resumed,variant='D',epochs=2,hidden=8,batch_size=2,sequence_length=3,burnin=2,resume=True)
        self.assertTrue(two['completed']);self.assertFalse(two['test_evaluated'])
        m1,s1=read_checkpoint(one['checkpoint']['path']);m2,s2=read_checkpoint(two['checkpoint']['path'])
        for key in s1['model']:self.assertTrue(torch.equal(s1['model'][key],s2['model'][key]),key)
        p=StatefulPolicy.load(two['checkpoint']['path'],version='a'*64,profile='fair')
        expected=evaluate(p.model,data,'validation')['prediction_sha256']
        self.assertEqual(expected,two['best_validation']['prediction_sha256'])
        with self.assertRaises(ValueError):StatefulPolicy.load(two['checkpoint']['path'],version='b'*64)
        with self.assertRaises(FileExistsError):train(path,resumed,variant='D',epochs=2,hidden=8)


if __name__=='__main__':unittest.main()

"""Human controls and reaction-order checks; no native transport or browser."""
from copy import deepcopy
import json
from pathlib import Path
import queue
import tempfile
import threading
import unittest
from unittest.mock import patch

from royale_phase2.human import HumanSession, placement, DEFAULT_HUMAN_POLICY
from royale_phase2.platform import plugin
from royale_phase2.runtime import ROOT
from royale_phase2.visibility import project_actor, TowerAttestation
from royale_phase2.battlefield import build_battlefield_observation


class HumanTests(unittest.TestCase):
    def setUp(self):
        self.root=ROOT/'outputs/platform_next/fixes/20261018_manual_play'
        self.root.mkdir(parents=True,exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(dir=self.root)
        self.folder=Path(self.tmp.name)
        self.raw=json.loads((ROOT/'outputs/phase2/fixtures/native-v1/hero_ability_readiness_and_activation/states.jsonl').read_text(encoding='utf8').splitlines()[0])
        self.actor=project_actor(self.raw,1,TowerAttestation.from_reset(self.raw))

    def tearDown(self): self.tmp.cleanup()

    def test_measured_grid_reaches_centers_without_axis_flip(self):
        for x,y in ((500,500),(3500,21500),(17500,31500)):
            u=(111+x/18000*856)/1080; v=(436+y/32000*1230)/1920
            self.assertEqual(placement({'u':u,'v':v}),(x,y))
        self.assertEqual(placement({'u':54/1080,'v':282/1920}),(500,500))
        self.assertEqual(placement({'x':3500,'y':21500}),(3500,21500))
        for data in ({'u':True,'v':.5},{'u':.5,'v':float('nan')},{'u':.5,'v':.99},
                     {'u':0,'v':.5},{'x':True,'y':20000},{'x':3500,'y':32000}):
            with self.assertRaises(ValueError): placement(data)

    def inert(self):
        session=object.__new__(HumanSession)
        session.folder=self.folder; session.lock=threading.Lock(); session.closed=threading.Event()
        session.status={'ready':True,'jobs':{}};session.queue=queue.Queue()
        session.human_actions=[];session.state=deepcopy(self.raw);session.max_tick=7200;session.playing=False
        actor=deepcopy(self.actor);actor['own_elixir_raw']=100000
        class Fake:
            def observe_visible(self,owner):
                if owner!=1: raise AssertionError('Human input must only use owner1')
                return deepcopy(actor)
            def schedule_card(self,owner,card,x,y,tick,request_id):
                return {'owner':owner,'card_id':card,'x':x,'y':y,'tick':tick,'request_id':request_id}
        session.engine=Fake()
        return session,actor

    def data(self,actor):
        troop=next(card for card in actor['hand'] if card['card']<28000000)
        return {'slot':troop['slot'],'card':troop['card'],'generation':self.raw['generation'],'x':3500,'y':21500}

    def test_human_request_uses_bottom_owner_and_next_tick_with_stale_guards(self):
        session,actor=self.inert(); data=self.data(actor)
        for changed in ({**data,'card':999},{**data,'generation':True},{**data,'generation':999},
                        {**data,'y':11000},{**data,'slot':True}):
            with self.assertRaises(ValueError): session._human_play(changed)
        self.assertEqual(session.human_actions,[])
        session._human_play(data)
        row=session.human_actions[0]
        self.assertEqual(row['registration']['owner'],1)
        self.assertEqual(row['registration']['tick'],self.raw['tick']+1)
        self.assertEqual(row['action']['card'],data['card'])
        with self.assertRaisesRegex(ValueError,'等待执行'): session._human_play(data)

    def test_insufficient_budget_is_rejected_before_any_registration(self):
        session,actor=self.inert();actor['own_elixir_raw']=0
        with self.assertRaisesRegex(ValueError,'elixir'): session._human_play(self.data(actor))
        self.assertEqual(session.human_actions,[])

    def test_turn_offers_consumed_human_event_before_ai_then_pauses(self):
        session,actor=self.inert(); data=self.data(actor); called=[]
        class Transition:
            def observe_visible(self,owner): return deepcopy(actor)
            def schedule_card(self,owner,card,x,y,tick,request_id):
                called.append(('register_human',owner,tick))
                return {'owner':owner,'card_id':card,'tick':tick,'request_id':request_id}
            def advance_exact_ticks(self,ticks):
                called.append(('advance_before_ai',ticks)); raw=deepcopy(session.state);raw['tick']+=ticks
                raw['plays']=[[1,raw['tick'],1,data['card'],3]]
                return raw
        session.engine=Transition()
        def accept(): called.append(('accepted_event',session.state['plays'][0][2]))
        def decide():
            self.assertEqual(session.state['plays'][0][2],1)
            self.assertEqual(session.state['tick'],self.raw['tick']+1)
            called.append(('ai_reads_consumed_human',True))
        session._accept=accept;session._decide=decide
        def advance(ticks): called.append(('advance_after_ai',ticks));session.state['tick']+=ticks
        session._advance=advance;session._feedback=lambda:None;session._snapshot=lambda:None;session._report=lambda:None
        session._job('turn','human_turn',data)
        self.assertEqual(called,[('register_human',1,self.raw['tick']+1),('advance_before_ai',1),
                                 ('accepted_event',1),('ai_reads_consumed_human',True),('advance_after_ai',19)])
        self.assertFalse(session.playing)
        self.assertEqual(session.status['jobs']['turn']['after_tick'],self.raw['tick']+20)

    def test_play_only_queues_no_silent_advance_and_closed_submit_rejects(self):
        session,actor=self.inert();ticket=session.submit('human_play',**self.data(actor))
        self.assertEqual(session.queue.get_nowait()[0],ticket)
        self.assertEqual(session.human_actions,[])
        session.closed.set()
        with self.assertRaises(ValueError):session.submit('human_wait')

    def test_human_default_has_only_battlefield_policy_and_no_live_thread(self):
        with patch('threading.Thread.start') as start:
            session=HumanSession('human',self.folder/'session')
            self.assertEqual(session.policy_file,DEFAULT_HUMAN_POLICY)
            self.assertEqual(session.information_mode,'battlefield')
            start.assert_called_once()
        with self.assertRaises(ValueError): HumanSession('human',self.folder/'bad',information_mode='oracle')

    def test_explained_rule_depends_on_units_budget_and_actual_history(self):
        decide,_=plugin(DEFAULT_HUMAN_POLICY)
        actor=project_actor(self.raw,0,TowerAttestation.from_reset(self.raw))
        bf=build_battlefield_observation(self.raw,actor,[])
        bf['hand']=[{'slot':0,'card':28000001,'cost':3}];bf['own_elixir_raw']=100000
        bf['entities']=[{'owner':1,'hp':100,'x_raw':3500,'y_raw':21500}]
        first=decide(bf);self.assertEqual((first['x'],first['y']),(3500,21500))
        bf['entities'][0]['x_raw']=14500;self.assertEqual(decide(bf)['x'],14500)
        bf['history']=[{'owner':0,'tick':bf['tick']-1}]
        self.assertEqual(decide(bf)['kind'],'wait')
        bf['history']=[];bf['own_elixir_raw']=0
        self.assertEqual(decide(bf)['kind'],'wait')
        self.assertIn('reason',first)


if __name__=='__main__': unittest.main()

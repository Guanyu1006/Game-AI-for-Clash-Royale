import copy
import json
import unittest
from royale_lab.backend import NativeBackend,card_catalog,named_decks
from royale_lab.contracts import Action,validate_observation
from royale_lab.memory import Belief,regeneration
from royale_lab.policies import make_policy,SearchPolicy
from royale_lab.runtime import local
from royale_lab.server import Service
from royale_lab.session import Session

class PlatformTests(unittest.TestCase):
    def setUp(self):self.backend=NativeBackend(seed=14,max_ticks=300)
    def test_real_card_executes_and_duplicate_does_not_spend(self):
        obs=self.backend.observe();a=next(Action(**x) for x in obs['legal_actions'] if x['kind']=='card')
        first=self.backend.apply(0,a,'test');second=self.backend.apply(0,a,'test')
        self.assertEqual(first['status'],'accepted');self.assertTrue(second['duplicate'])
        self.assertGreater(first['elixir_spent'],0);self.assertNotEqual(first['before_hand'],first['after_hand'])
        self.assertEqual(len(self.backend.journal),1)
    def test_illegal_does_not_mutate(self):
        before=self.backend.oracle();result=self.backend.apply(0,Action('card',0,-1,400))
        self.assertEqual(result['status'],'rejected');self.assertEqual(before,self.backend.oracle())
    def test_delay_at_exact_boundary_and_dedup(self):
        a=next(Action(**x) for x in self.backend.observe()['legal_actions'] if x['kind']=='card')
        delayed=Action(a.kind,a.slot,a.x,a.y,10)
        self.backend.apply(0,delayed,'scheduled');self.backend.apply(0,delayed,'scheduled')
        self.assertEqual(len(self.backend.pending),1);self.backend.advance(10)
        self.assertEqual(len(self.backend.events),1);self.assertEqual(self.backend.events[0]['tick'],10)
    def test_snapshots_and_json_replay_are_exact(self):
        policy=make_policy('random',self.backend.catalog,1)
        for _ in range(10):self.backend.apply(0,policy.decide(self.backend.observe()).action);self.backend.advance(10)
        snap=self.backend.snapshot();before=self.backend.oracle()
        self.backend.advance(20);self.backend.restore(snap);self.assertEqual(before,self.backend.oracle())
        clone=NativeBackend();clone.load_snapshot(json.loads(json.dumps(self.backend.export_snapshot())))
        self.assertEqual(before,clone.oracle())
    def test_seed_reproduces_hand_and_actions(self):
        other=NativeBackend(seed=14,max_ticks=300)
        self.assertEqual(self.backend.oracle(),other.oracle())
    def test_visible_observation_has_no_hidden_opponent_fields(self):
        obs=self.backend.observe()
        for key in ('players','enemy_hand','opponent_hand','enemy_elixir','rng_state'):self.assertNotIn(key,obs)
        obs['enemy_hand']=[0,1,2,3]
        with self.assertRaises(ValueError):validate_observation(obs)
    def test_visibility_does_not_change_with_enemy_hidden_elixir(self):
        obs=self.backend.observe();self.backend.env.set_elixir_for_team(1,9)
        self.assertEqual(obs,self.backend.observe())
    def test_can_play_does_not_mutate_snapshot(self):
        before=self.backend.oracle()
        for a in self.backend.observe()['legal_actions']:self.backend.legal(0,Action(**a))
        self.assertEqual(before,self.backend.oracle())
    def test_deck_duplicates_rejected(self):
        with self.assertRaises(ValueError):NativeBackend(decks=[[0]*8,[0]*8])
    def test_mirror_observation_geometry(self):
        obs=self.backend.observe(1)
        for tower in obs['towers']:
            if tower['team']==0:self.assertLess(tower['y'],17)
            else:self.assertGreater(tower['y'],17)
    def test_policy_search_receives_only_obs(self):
        p=SearchPolicy(self.backend.catalog,max_candidates=3,horizon_ticks=20)
        before=self.backend.oracle();d=p.decide(self.backend.observe())
        self.assertTrue(self.backend.legal(0,d.action));self.assertEqual(before,self.backend.oracle())
    def test_candidate_mask_actions_all_execute_in_snapshot(self):
        obs=self.backend.observe();snap=self.backend.snapshot()
        for raw in obs['legal_actions']:
            self.backend.restore(snap);result=self.backend.apply(0,Action(**raw))
            self.assertIn(result['status'],('wait','accepted'))
    def test_phase_belief_and_event_dedup(self):
        self.assertAlmostEqual(regeneration(120,240),120*2/2.8)
        obs=self.backend.observe(1);memory=Belief().update(obs,self.backend.catalog)
        a=next(Action(**x) for x in self.backend.observe()['legal_actions'] if x['kind']=='card')
        self.backend.apply(0,a);self.backend.advance(10);obs=self.backend.observe(1)
        memory.update(obs,self.backend.catalog);count=memory.count;memory.update(obs,self.backend.catalog)
        self.assertEqual(count,memory.count);self.assertAlmostEqual(memory.high,self.backend.env.get_elixir_for_team(0),places=4)
    def test_session_closed_loop_reaches_terminal(self):
        s=Session(seed=12,controllers=['random','greedy'],max_ticks=300);s.advance(300)
        self.assertTrue(s.backend.done);self.assertGreater(len(s.backend.journal),0);self.assertEqual(s.rejections,0)
    def test_service_snapshot_fork_load(self):
        api=Service();state=api.create({'controllers':['random','greedy'],'max_ticks':300});sid=state['session']
        api.request('/api/advance',{'session':sid,'ticks':100});saved=api.request('/api/snapshot',{'session':sid})
        branch=api.request('/api/fork',{'session':sid,'label':saved['label']})
        self.assertEqual(state['state']['tick'],0);self.assertEqual(branch['state']['tick'],100)
        api.request('/api/advance',{'session':sid,'ticks':100})
        restored=api.request('/api/load',{'session':sid,'path':saved['path']})
        self.assertEqual(restored['state']['tick'],100);api.request('/api/advance',{'session':sid,'ticks':10})
    def test_paths_cannot_escape_workspace(self):
        with self.assertRaises(ValueError):local('../escaped.txt')
    def test_machine_readable_coverage_present(self):
        self.assertEqual(len(card_catalog()),173)
        for path in ('data/platform/card_coverage.csv','data/platform/mechanic_coverage.csv','data/platform/interaction_tests.json'):
            self.assertGreater(local(path).stat().st_size,1000)

class TrainingTests(unittest.TestCase):
    def test_ppo_sampling_seed_and_actual_play(self):
        backend=NativeBackend(seed=7,max_ticks=600)
        a=make_policy('ppo_sample',backend.catalog,5);b=make_policy('ppo_sample',backend.catalog,5)
        obs=backend.observe()
        first=[a.decide(obs).action for _ in range(12)];second=[b.decide(obs).action for _ in range(12)]
        self.assertEqual(first,second);self.assertTrue(any(x.kind=='card' for x in first))
    def test_gym_seed_mask_step_contract(self):
        from royale_lab.learning import CandidateGym
        from gymnasium.utils.env_checker import check_env
        check_env(CandidateGym(max_ticks=100),skip_render_check=True)
    def test_trained_checkpoints_execute_complete_games(self):
        for name in ('learned','ppo'):
            with self.subTest(policy=name):
                self.assertTrue(local(f'models/platform/{name}.pt').exists())
                s=Session(seed=19,controllers=[name,'greedy'],max_ticks=600);s.advance(600)
                self.assertTrue(s.backend.done);self.assertEqual(s.rejections,0)
                self.assertGreater(len(s.log),0)

class NumericConfigurationTests(unittest.TestCase):
    def test_json_changes_actual_combat_and_isolates_checkpoint(self):
        import os
        import subprocess
        import sys
        from royale_lab.runtime import ROOT
        env=dict(os.environ,PYTHONUTF8='1')
        result=subprocess.run([sys.executable,str(ROOT/'tools/balance_integration.py')],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
        self.assertEqual(result.returncode,0,result.stderr+'\n'+result.stdout)
        evidence=json.loads(local('outputs/platform/numeric_integration.json').read_text(encoding='utf-8'))['result']
        self.assertEqual(evidence['actual_attack_damage'],303)
        self.assertEqual(evidence['fireball_tower_damage'],123)
        self.assertEqual(evidence['accepted_cost'],2)
        self.assertTrue(evidence['legacy_checkpoint_rejected'])

if __name__=='__main__':unittest.main()

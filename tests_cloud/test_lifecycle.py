"""Offline lifecycle regression checks. Mock transport never contacts native RPC."""
from contextlib import nullcontext
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest
from unittest.mock import Mock, patch

from royale_phase3.actions import DECK
from royale_phase3.common import ROOT, read, rows, write
from royale_phase3.data import transition_end_tick
from royale_phase3.native_env import NativePhase3Env


class MockNativeBackend:
    """Synthetic clock and receipts; this is not a battle physics simulator."""
    def __init__(self, **kwargs):
        self.trace_path=kwargs['trace_path'];self.generation=0;self.terminal_tick=None
        self.version={'fingerprint':'offline-mock'};self.closed=False

    def reset(self, initialization):
        self.generation+=1;self.actions=[];self.events=[];self.closed=False
        self.state={'schema':'cr-minimal.v4','ok':True,'generation':self.generation,
            'tick':90,'finalized':False,'result':None,'crowns':[0,0],'entities':[],
            'plays':[],'next':1,'players':[{'owner':owner,'elixir':100000,
                'hand':[[slot,card,3,0,0] for slot,card in enumerate(DECK[:4])],
                'cycle':[]} for owner in (0,1)]}
        return deepcopy(self.state)

    def observe_visible(self, owner):
        from royale_phase2.visibility import project_actor
        return project_actor(self.state,owner)

    def schedule_card(self, owner, card, x, y, tick, request_id):
        row={'request_id':request_id,'kind':'play_card','owner':owner,
            'card_id':card,'x':x,'y':y,'tick':tick}
        self.actions.append(row);return deepcopy(row)

    def advance_exact_ticks(self, ticks):
        before=self.state['tick'];after=before+ticks
        if self.terminal_tick is not None:after=min(after,self.terminal_tick)
        for action in self.actions:
            if before<action['tick']<=after:
                self.events.append([len(self.events)+1,action['tick'],action['owner'],action['card_id'],3])
        self.state.update(tick=after,plays=deepcopy(self.events),next=len(self.events)+1)
        if self.terminal_tick is not None and after>=self.terminal_tick:
            self.state.update(finalized=True,result=0)
        return deepcopy(self.state)

    def execution_feedback(self, request_ids):
        return [{**deepcopy(row),'card_consumed':True,'native_injected':True,
            'actual_execution_tick':row['tick']} for row in self.actions if row['request_id'] in request_ids]

    def export_journal(self):
        return {'actions':deepcopy(self.actions),'actual_card_events':deepcopy(self.events)}

    def result(self):return {key:deepcopy(self.state[key]) for key in ('finalized','result','tick','crowns','generation')}
    def close(self):self.closed=True


class Lifecycle(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.tmp=tempfile.TemporaryDirectory(prefix='lifecycle-',dir=ROOT/'.tmp')
        self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name)

    def environment(self, **kwargs):
        with patch('royale_phase2.backend.NativeBattleBackend',MockNativeBackend), \
                patch('royale_phase2.backend._read_locked_elixir_rates',return_value={}), \
                patch('royale_phase3.native_env.match',return_value={'battle':{}}):
            env=NativePhase3Env(self.root/'env',opponent='wait',**kwargs)
        self.addCleanup(env.close)
        return env

    def reset(self, env, seed=1):
        with patch('royale_phase3.native_env.match',return_value={'battle':{},'seed':seed}):
            return env.reset(seed)

    def test_reset_seals_files_and_clears_feedback_history_and_decisions(self):
        env=self.environment(max_tick=100);first=self.reset(env)
        env.step({'kind':'play_card','slot':0,'x':3500,'y':11000})
        self.assertEqual(len(env.decisions),1);self.assertEqual(len(env.feedback),1)
        self.assertEqual(len(env.public_observation['history']),1)
        initial_bytes=(env.folder/'initialization.json').read_bytes()
        states_bytes=(env.folder/'states.jsonl').read_bytes()
        decisions_bytes=(env.folder/'decisions.jsonl').read_bytes()
        second=self.reset(env,2)
        self.assertEqual(env.folder,env.root/'episode-0002')
        self.assertEqual(env.backend.trace_path,env.folder/'wire.jsonl')
        self.assertNotEqual(first['episode_id'],second['episode_id'])
        self.assertEqual(env.feedback,[]);self.assertEqual(env.decisions,[])
        self.assertEqual(env.public_observation['history'],[])
        self.assertIsNone(env.public_observation['dt_seconds'])
        self.assertEqual(second['previous_action'],0);self.assertEqual(second['feedback'],[0.,0.,0.,0.])
        self.assertEqual((env.root/'initialization.json').read_bytes(),initial_bytes)
        self.assertEqual((env.root/'states.jsonl').read_bytes(),states_bytes)
        self.assertEqual((env.root/'decisions.jsonl').read_bytes(),decisions_bytes)
        self.assertEqual(read(env.root/'execution_feedback.json')[0]['owner'],0)
        self.assertEqual(len(list(rows(env.folder/'states.jsonl'))),1)
        env.step({'kind':'wait'});env.save()
        self.assertEqual(read(env.folder/'execution_feedback.json'),[])

    def test_natural_terminal_and_watchdog_are_distinct_and_require_reset(self):
        env=self.environment(max_tick=97);self.reset(env)
        _,reward,terminated,truncated,step=env.step({'kind':'wait'})
        self.assertFalse(terminated);self.assertTrue(truncated);self.assertEqual(reward,0.)
        self.assertEqual(step['after_tick'],97);self.assertEqual(step['actual_dt_seconds'],.35)
        with self.assertRaises(RuntimeError):env.step({'kind':'wait'})
        self.reset(env);env.backend.terminal_tick=93
        _,reward,terminated,truncated,step=env.step({'kind':'wait'})
        self.assertTrue(terminated);self.assertFalse(truncated);self.assertEqual(reward,1.)
        self.assertEqual(step['actual_dt_seconds'],.15)
        env.close()
        with self.assertRaises(RuntimeError):env.step({'kind':'wait'})

    def test_invalid_index_and_coordinates_are_audited_without_scheduling(self):
        env=self.environment(max_tick=200);self.reset(env)
        for proposed in ({'action_index':True},{'action_index':5},{'action_index':1},
                {'action_index':1,'xy_native':[0]},None,
                {'kind':'activate_ability','slot':0,'x':3500,'y':11000}):
            _,_,_,_,step=env.step(proposed)
            self.assertEqual(step['action'],{'kind':'wait'})
            self.assertIsNotNone(step['boundary_rejection']);self.assertEqual(step['registrations'],[])
        self.assertEqual(env.backend.actions,[])

    def test_explicit_reset_after_invalid_mutation_preserves_failure(self):
        from royale_phase2.backend import NativeStateInvalid
        env=self.environment();self.reset(env)
        original_result=env.backend.result
        env.backend.result=Mock(side_effect=NativeStateInvalid('synthetic failed mutation'))
        self.reset(env,2)
        failure=read(env.root/'reset_recovery.json')
        self.assertEqual(failure['status'],'native_state_invalid')
        self.assertEqual(env.folder.name,'episode-0002');self.assertFalse(env.done)
        env.backend.result=original_result

    def test_watchdog_validation_occurs_before_backend_creation(self):
        for value in (True,90,0,1.5):
            with patch('royale_phase2.backend.NativeBattleBackend') as backend:
                with self.assertRaises(ValueError):NativePhase3Env(self.root/'invalid',max_tick=value)
                backend.assert_not_called()

    def test_new_match_uses_reviewed_static_schema_without_historical_outputs(self):
        from royale_phase3 import actions
        # Choose the portable template even on a full local checkout.
        missing=self.root/'missing-initialization.json'
        with patch.object(actions,'LEGACY_INITIALIZATION',str(missing)):
            first=actions.match(123)
            second=actions.match(124)
        self.assertEqual(first['rndSeed'],123);self.assertEqual(second['rndSeed'],124)
        self.assertEqual(first['cmd'],[]);self.assertEqual(first['evt'],[])
        self.assertEqual(first['endTick'],7200)
        for owner in (0,1):
            self.assertEqual([row['d'] for row in first['battle'][f'deck{owner}']['sp']],list(DECK))
            self.assertEqual(first['battle']['hbd'][owner]['kt'],16)
        for seed in (True,-1,2**32):
            with self.assertRaises(ValueError):actions.match(seed)

    def test_belief_uses_the_backends_selected_version_manifest(self):
        class ConfiguredBackend(MockNativeBackend):
            connection_configuration={'version_manifest':'data/phase2/custom-native-manifest.json'}
        with patch('royale_phase2.backend.NativeBattleBackend',ConfiguredBackend), \
                patch('royale_phase3.native_env.PublicBelief') as belief:
            env=NativePhase3Env(self.root/'configured-env',opponent='wait')
        self.addCleanup(env.close)
        belief.assert_called_once_with(version_manifest='data/phase2/custom-native-manifest.json')

    def test_backend_reset_clears_previous_advance_metadata_without_rpc(self):
        from royale_phase2.backend import NativeBattleBackend
        backend=NativeBattleBackend.__new__(NativeBattleBackend)
        backend._last_advance={'advanced_ticks':20};backend._initialization_tick=90
        backend._match={'previous':True};backend.client=Mock();backend.rendered=False
        backend.version={'fingerprint':'offline'};backend.identity_receipt_sha256='offline'
        backend._request=Mock(side_effect=[{'contentReady':True,'mode':'headless'},{},{}])
        state={'schema':'cr-minimal.v4','tick':0,'generation':2,'entities':[]}
        backend._capture=Mock(return_value=state)
        backend.reset({'battle':{}},warmup_ticks=0)
        self.assertIsNone(backend.export_journal()['last_advance'])
        self.assertEqual(backend.export_journal()['initialization_tick'],0)
        backend.client.request.assert_not_called()

    def test_export_transition_uses_recorded_short_interval_and_actual_limit(self):
        states={90:{'finalized':False},91:{'finalized':False},97:{'finalized':False},100:{'finalized':False}}
        self.assertEqual(transition_end_tick(states,{'tick':90,'dt_ticks':7},97),97)
        self.assertEqual(transition_end_tick(states,{'tick':90},97),97)
        states[93]={'finalized':True}
        self.assertEqual(transition_end_tick(states,{'tick':90,'dt_ticks':7},97),93)
        with self.assertRaises(ValueError):transition_end_tick(states,{'tick':90,'dt_ticks':10},97)
        with self.assertRaisesRegex(ValueError,'Missing transition end state'):
            transition_end_tick({90:{'finalized':False},91:{'finalized':False}}, {'tick':90,'dt_ticks':7},97)

    def test_native_evaluator_passes_aligned_mask_as_keyword(self):
        from tools import phase3_native_evaluate as module
        supplied_mask={'values':[True,False,False,False,False]}
        calls=[];root=self.root
        class KeywordOnlyPolicy:
            def predict(self, actor, *, legal_mask=None):
                calls.append((actor,legal_mask));return {'kind':'wait'}
        class EvaluationEnv:
            def __init__(self, output, **kwargs):
                self.folder=Path(output);self.folder.mkdir(parents=True)
                self.actor={'tick':90};self.backend=SimpleNamespace(version={'fingerprint':'offline'})
                self.feedback=[];self.done=False;self.state={'result':0,'crowns':[1,0],'tick':100}
                for name in ('journal.json','execution_feedback.json','decisions.jsonl','states.jsonl','native_result.json'):
                    write(self.folder/name,[])
            def reset(self, seed):return {}
            def step(self, proposed):
                self.done=True;return {},1.,True,False,{'action':proposed,'boundary_rejection':None}
            def close(self):pass
        args=SimpleNamespace(output=root/'evaluation',policy='aligned_bc',checkpoint=None,
            seed=1,games=1,max_tick=100)
        with patch('royale_phase3.native_env.NativePhase3Env',EvaluationEnv), \
                patch('royale_phase2.learning.NumpyBCPolicy.load',return_value=KeywordOnlyPolicy()), \
                patch('royale_phase2.trajectories.audit_subspace_proof',return_value={}), \
                patch('royale_phase2.trajectories.subspace_mask',return_value=supplied_mask), \
                patch.object(module,'native_lock',return_value=nullcontext()):
            report=module.evaluate(args)
        self.assertEqual(report['statuses'],{'completed':1})
        self.assertEqual(calls,[({'tick':90},supplied_mask)])


if __name__=='__main__':unittest.main()

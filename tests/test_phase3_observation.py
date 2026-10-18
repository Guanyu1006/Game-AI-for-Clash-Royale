import copy
import json
from pathlib import Path
import unittest

from royale_phase2.visibility import project_actor, TowerAttestation
from royale_phase2.battlefield import build_battlefield_observation
from royale_phase3.observation import (ObservationProjector, project_observation,
    encode_observation, feature_spec, affordability_mask, actor_hash)
from royale_phase3.belief import PublicBelief, encode_belief
from royale_phase3.common import rows, local

SESSION = 'outputs/platform_next/sessions/20261018T060715.139286Z-human'


def fixture():
    return next(rows(SESSION+'/battlefield_observations.jsonl'))


def event(tick=91, owner=1, card=26000000, cost=3, sequence=1):
    return {'source': 'native_card_consumption_event', 'sequence': sequence,
        'tick': tick, 'owner': owner, 'native_card_id': card, 'cost_elixir': cost,
        'requested_position': {'x':3500,'y':21000},
        'position_reason': 'unique_owner_tick_exact_native_card_id_request'}


def non_tower(entity_id=6000000, **overrides):
    return dict({'entity_id':entity_id,'owner':1,'card_id_raw':26000000,
        'x_raw':3500,'y_raw':20000,'hp':None,'max_hp':100,
        'native_kind':'troop','deployment_remaining_ms':100,'visibility':'unknown'},**overrides)


class ObservationContractTests(unittest.TestCase):
    def test_fair_hidden_objects_do_not_change_input_or_mask(self):
        a=fixture(); b=copy.deepcopy(a);b['entities']=[non_tower(),non_tower(7000000,visibility='visible')]
        pa,pb=ObservationProjector(),ObservationProjector()
        oa,ob=pa.project(a),pb.project(b)
        self.assertEqual(oa,ob); self.assertEqual(encode_observation(oa),encode_observation(ob))
        self.assertEqual(affordability_mask(oa),affordability_mask(ob))
        self.assertEqual(pb.diagnostics['excluded_objects'],2)
        self.assertNotIn('excluded_objects',ob)

    def test_raw_enemy_private_and_future_source_invariance(self):
        raw=next(rows(SESSION+'/observations.jsonl'))['raw']
        changed=copy.deepcopy(raw)
        changed['players'][1]['hand']=[[0,26000003,0,123,456]]
        changed['players'][1]['elixir']=1;changed['players'][1]['cycle']=[999]
        changed['players'][1]['abilities']=[['secret']]
        changed['future_source']=[{'tick':5000,'kind':'play_card','x':1}]
        changed['entities'][0][7]=9999;changed['entities'][0][8]=10000
        def project(state,journal):
            att=TowerAttestation.from_reset(state)
            actor=project_actor(state,0,att)
            bf=build_battlefield_observation(state,actor,[],journal)
            return project_observation(bf)
        oa=project(raw,[]);ob=project(changed,[{'tick':999,'owner':1,'kind':'play_card'}])
        self.assertEqual(oa,ob);self.assertEqual(actor_hash(oa),actor_hash(ob))

    def test_history_never_exposes_requested_controller_coordinates(self):
        a=fixture();a['tick']=100;a['history']=[event()]
        b=copy.deepcopy(a);b['history'][0]['requested_position']={'x':17500,'y':500}
        self.assertEqual(project_observation(a),project_observation(b))
        self.assertIsNone(project_observation(a)['history'][0]['x'])

    def test_opaque_card_has_no_identity_or_cost(self):
        a=fixture();a['tick']=100;a['history']=[event(card=0)]
        obs=project_observation(a)
        self.assertIsNone(obs['history'][0]['card']);self.assertIsNone(obs['history'][0]['cost'])

    def test_future_native_event_fails_closed(self):
        a=fixture();a['history']=[event(tick=91)]
        with self.assertRaises(ValueError):project_observation(a)

    def test_tied_event_order_uses_native_sequence(self):
        a=fixture();a['tick']=100;a['history']=[event(owner=1,sequence=2),event(owner=0,sequence=1)]
        self.assertEqual([r['side'] for r in project_observation(a)['history']],['own','opponent'])

    def test_unattested_towers_are_not_actor_fields(self):
        a=fixture();a['tower_identity_basis']='unknown_without_reset_attestation'
        self.assertEqual(project_observation(a)['towers'],[])

    def test_unknown_hp_differs_from_measured_zero(self):
        a=fixture();a['towers'][0]['hp']=None
        b=copy.deepcopy(a);b['towers'][0]['hp']=0
        oa,ob=project_observation(a),project_observation(b)
        self.assertIsNone(oa['towers'][0]['hp']);self.assertNotEqual(encode_observation(oa),encode_observation(ob))

    def test_research_tracks_anonymous_with_no_cooldown_or_deploy(self):
        a=fixture();a['entities']=[non_tower()]
        p=ObservationProjector('research_battlefield');o=p.project(a)
        r=o['entities'][0];self.assertEqual(r['track'],1)
        self.assertNotIn('entity_id',r);self.assertNotIn('deployment_remaining_ms',r)
        self.assertIsNone(r['hp']);self.assertFalse(p.diagnostics['non_tower_visibility_verified'])

    def test_raw_entity_id_renaming_does_not_change_vector(self):
        a=fixture();a['entities']=[non_tower()]
        b=copy.deepcopy(a);b['entities'][0]['entity_id']=999999
        oa=project_observation(a,'research_battlefield');ob=project_observation(b,'research_battlefield')
        self.assertEqual(oa,ob);self.assertEqual(encode_observation(oa),encode_observation(ob))

    def test_disappearance_is_only_presence_change_not_death(self):
        p=ObservationProjector('research_battlefield');a=fixture();a['entities']=[non_tower()]
        p.project(a);a['tick']+=10;a['entities']=[];o=p.project(a)
        self.assertEqual(o['disappeared_tracks'],[1]);self.assertNotIn('deaths',o)

    def test_generation_reset_clears_tracking_history_dt(self):
        p=ObservationProjector('research_battlefield');a=fixture();a['tick']=100;a['entities']=[non_tower()];a['history']=[event()]
        p.project(a);a['generation']+=1;a['tick']=90;a['entities']=[non_tower(999999)];a['history']=[]
        o=p.project(a);self.assertEqual(o['entities'][0]['track'],1)
        self.assertEqual(o['history'],[]);self.assertIsNone(o['dt_seconds']);self.assertNotIn('generation',o)

    def test_manual_reset_for_new_backend_same_generation(self):
        p=ObservationProjector();a=fixture();p.project(a);a['tick']+=10;p.project(a)
        p.reset();a['tick']=90;self.assertIsNone(p.project(a)['dt_seconds'])

    def test_project_idempotent_and_elapsed_real_time(self):
        p=ObservationProjector();a=fixture();o=p.project(a);self.assertEqual(o,p.project(a))
        a['tick']+=10;o=p.project(a);self.assertEqual(o['dt_seconds'],0.5);self.assertEqual(o,p.project(a))

    def test_time_or_perspective_regression_rejected(self):
        p=ObservationProjector();a=fixture();p.project(a);a['tick']-=1
        with self.assertRaises(ValueError):p.project(a)
        a=fixture();a['native_owner']=1
        with self.assertRaises(ValueError):p.project(a)

    def test_encoder_shape_ab_and_missing_hand(self):
        a=fixture();a['hand']=a['hand'][:3];a['hand_complete']=False;o=project_observation(a)
        for ent in (True,False):
            for hist in (True,False):
                self.assertEqual(len(encode_observation(o,include_entities=ent,include_history=hist)),
                    feature_spec(include_entities=ent,include_history=hist)['dimension'])
        self.assertEqual(feature_spec()['dimension'],688)

    def test_history_pair_same_current_state_exposes_different_past(self):
        a=fixture();a['tick']=100;b=copy.deepcopy(a);a['history']=[event(card=26000000)];b['history']=[event(card=26000001)]
        oa,ob=project_observation(a),project_observation(b)
        self.assertNotEqual(encode_observation(oa),encode_observation(ob))
        self.assertEqual(encode_observation(oa,include_history=False),encode_observation(ob,include_history=False))

    def test_visibility_assertion_not_evidence(self):
        with self.assertRaises(ValueError):ObservationProjector(visibility_evidence={'visible':True})


class BeliefContractTests(unittest.TestCase):
    def obs(self,tick=100,history=None):
        a=fixture();a['tick']=tick;a['history']=history or []
        return project_observation(a)

    def test_default_unknown_deck_and_resource_modifiers_stay_unknown(self):
        p=PublicBelief();o=p.update(self.obs(history=[event()]))
        self.assertEqual(o['opponent_elixir_interval'],[0.,10.])
        self.assertIsNone(o['opponent_hand_distribution']);self.assertIsNone(o['opponent_next_card_distribution'])
        self.assertIsNone(o['opponent_seen_cards'][0]['minimum_other_plays_until_return'])
        self.assertEqual(len(encode_belief(o)),15)

    def test_known_mode_normal_scope_interval_uses_locked_current_csv(self):
        p=PublicBelief(ordinary_resource_only=True);o=p.update(self.obs(tick=91,history=[event()]))
        self.assertTrue(p.metadata['rate_supported']);self.assertEqual(o['opponent_elixir_interval'],[0.,7.])
        later=p.update(self.obs(tick=101,history=[event()]))
        expected=min(100000,70000+p._regeneration(91,101)[1])/10000
        self.assertEqual(later['opponent_elixir_interval'][1],expected)

    def test_unknown_mode_does_not_reuse_other_mode_rates(self):
        p=PublicBelief(game_mode=999,ordinary_resource_only=True);o=p.update(self.obs(history=[event()]))
        self.assertFalse(p.metadata['rate_supported']);self.assertEqual(o['opponent_elixir_interval'],[0.,10.])

    def test_sparse_and_sequential_updates_identical(self):
        history=[event(),event(tick=121,card=26000001,cost=3,sequence=2)]
        a=PublicBelief(ordinary_resource_only=True);b=PublicBelief(ordinary_resource_only=True)
        a.update(self.obs(tick=91,history=history[:1]));a.update(self.obs(tick=110,history=history[:1]))
        self.assertEqual(a.update(self.obs(tick=130,history=history)),b.update(self.obs(tick=130,history=history)))

    def test_unknown_opaque_cost_widens(self):
        p=PublicBelief(ordinary_resource_only=True);o=p.update(self.obs(tick=100,history=[event(card=0)]))
        self.assertEqual(o['opponent_elixir_interval'],[0.,10.])

    def test_reset_history_isolation_and_idempotence(self):
        p=PublicBelief();o=self.obs(history=[event()]);a=p.update(o);self.assertEqual(a,p.update(o))
        with self.assertRaises(ValueError):p.update(self.obs())
        p.reset();b=p.update(self.obs());self.assertEqual(b['opponent_seen_cards'],[])

    def test_new_historical_events_change_explicit_belief(self):
        a=PublicBelief().update(self.obs(history=[event(card=26000000)]))
        b=PublicBelief().update(self.obs(history=[event(card=26000001)]))
        self.assertNotEqual(encode_belief(a),encode_belief(b))

    def test_unsupported_cycle_evidence_not_silently_enabled(self):
        with self.assertRaises(ValueError):PublicBelief(standard_cycle_evidence={'cycle':4})


if __name__=='__main__':unittest.main()

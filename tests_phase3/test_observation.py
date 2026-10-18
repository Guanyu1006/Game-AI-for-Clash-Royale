"""Offline contract checks; synthetic controls are not visibility certification."""
from copy import deepcopy
import math
import unittest

from royale_phase3.observation import (ObservationProjector, project_observation,
    affordability_mask, encode_observation, feature_spec, actor_hash)


def fixture(tick=100, generation=1, owner=0):
    return {'schema': 'royale-platform-battlefield-observation.v1',
        'generation': generation, 'tick': tick, 'native_owner': owner,
        'hand': [{'slot': i, 'card': c, 'cost': i+1}
                 for i,c in enumerate((26000000,26000001,28000000,28000001))],
        'hand_complete': True, 'own_elixir_raw': 30000,
        'crowns': {'own': 0, 'opponent': 0},
        'tower_identity_basis': 'reset_attested_canonical_six_towers',
        'towers': [{'side': s, 'role': r, 'present': True, 'hp': 2400,
                    'max_hp': 2400} for s in ('own','opponent') for r in ('left','right','king')],
        'entities': [entity()], 'history': []}


def entity(identity=7000001, **changes):
    return {'entity_id': identity, 'owner': 1, 'card_id_raw': 26000000,
        'native_kind': 'troop', 'x_raw': 3500, 'y_raw': 17000,
        'hp': 500, 'max_hp': 1000, 'visibility': 'unknown', **changes}


def event(sequence=1, tick=91, **changes):
    return {'source': 'native_card_consumption_event', 'sequence': sequence,
        'tick': tick, 'owner': 1, 'native_card_id': 28000000,
        'cost_elixir': 4, 'requested_position': {'x': 3500, 'y': 20000}, **changes}


class ObservationContracts(unittest.TestCase):
    def test_fair_excludes_even_asserted_visible_objects_and_counts(self):
        b = fixture(); b['entities'] += [entity(8000001, visibility='visible')]
        p = ObservationProjector(); a = p.project(b)
        self.assertEqual(a['entities'], []); self.assertEqual(a['disappeared_tracks'], [])
        self.assertEqual(p.diagnostics['excluded_objects'], 2)
        self.assertNotIn('excluded_objects', a); self.assertNotIn('diagnostics', a)

    def test_enemy_private_future_source_quality_paths_and_ids_are_invariant(self):
        b = fixture(); base = project_observation(b)
        for field, value in [('oracle', {'opponent_hand':[1,2], 'elixir':99999}),
            ('future_source_commands', [{'tick':1000,'card':123}]),
            ('quality', 'test-only'), ('file_path', 'winner-directory'),
            ('enemy_private_cycle', [1,2,3]), ('raw_capture', {'hidden_cooldown':1})]:
            changed = deepcopy(b); changed[field] = value
            changed['entities'] = [entity(9999999, hp=0), entity(123)]
            got = project_observation(changed)
            self.assertEqual(got, base)
            self.assertEqual(encode_observation(got), encode_observation(base))
            self.assertEqual(affordability_mask(got), affordability_mask(base))

    def test_past_public_change_positive_control(self):
        b = fixture(); plain = project_observation(b)
        b['history'] = [event()]; changed = project_observation(b)
        self.assertNotEqual(actor_hash(plain), actor_hash(changed))
        self.assertNotEqual(encode_observation(plain), encode_observation(changed))

    def test_requested_positions_are_intentions_and_never_actor_positions(self):
        b = fixture(); b['history'] = [event()]; base = project_observation(b)
        b['history'][0]['requested_position'] = {'x':17000,'y':1000}
        b['history'][0]['actual_position'] = {'x':17000,'y':1000}
        self.assertEqual(base, project_observation(b))
        self.assertIsNone(base['history'][0]['x']); self.assertIsNone(base['history'][0]['y'])

    def test_opaque_consumption_masks_card_and_cost(self):
        b = fixture(); b['history'] = [event(native_card_id=0, cost_elixir=7)]
        a = project_observation(b)
        self.assertIsNone(a['history'][0]['card']); self.assertIsNone(a['history'][0]['cost'])

    def test_future_consumption_is_rejected(self):
        b = fixture(); b['history'] = [event(tick=101)]
        with self.assertRaises(ValueError): project_observation(b)

    def test_unconfirmed_source_duplicate_sequence_order_rejected(self):
        for history in ([event(source='planned_source')], [event(),event()],
                        [event(tick=99),event(2,tick=98)]):
            b = fixture(); b['history'] = history
            with self.assertRaises(ValueError): project_observation(b)

    def test_generation_reset_clears_dt_tracks_and_history(self):
        p = ObservationProjector('research_battlefield'); b = fixture()
        b['history'] = [event()]; first = p.project(b)
        b = fixture(90,2); b['entities'] = [entity(999)]
        reset = p.project(b)
        self.assertIsNone(reset['dt_seconds']); self.assertEqual(reset['history'], [])
        self.assertEqual(reset['entities'][0]['track'],1)
        self.assertEqual(reset['disappeared_tracks'],[])
        self.assertTrue(p.diagnostics['generation_reset'])
        self.assertEqual(first['entities'][0]['track'],1)

    def test_explicit_reset_same_generation_clears_state(self):
        p = ObservationProjector(); b = fixture(); b['history'] = [event()]
        p.project(b); p.reset(); a = p.project(fixture(90))
        self.assertEqual(a['history'],[]); self.assertIsNone(a['dt_seconds'])

    def test_regressing_tick_changed_perspective_or_history_fail_closed(self):
        for changed in (fixture(99),fixture(101,owner=1),fixture(101)):
            p = ObservationProjector(); b = fixture(); b['history'] = [event()]
            p.project(b)
            with self.assertRaises(ValueError): p.project(changed)

    def test_idempotent_same_tick_and_return_value_is_detached(self):
        p = ObservationProjector('research_battlefield'); b = fixture()
        first = p.project(b); first['entities'][0]['hp'] = 0
        got = p.project(b); self.assertEqual(got['entities'][0]['hp'],500)
        self.assertIsNone(got['dt_seconds'])
        b['tick'] = 110; got = p.project(b)
        self.assertEqual(got['dt_seconds'],.5); self.assertEqual(p.project(b),got)

    def test_no_caller_attestation_can_enable_entities(self):
        with self.assertRaises(ValueError): ObservationProjector(visibility_evidence='trust-me')
        with self.assertRaises(ValueError): ObservationProjector('certified')
        with self.assertRaises(ValueError): ObservationProjector(ticks_per_second=10)

    def test_research_profile_tracks_disappearance_not_death(self):
        p = ObservationProjector('research_battlefield'); a = p.project(fixture())
        b = fixture(110); b['entities'] = []; gone = p.project(b)
        self.assertEqual(gone['disappeared_tracks'],[a['entities'][0]['track']])
        self.assertIn('does not certify death', p.diagnostics['missingness'])

    def test_research_measured_object_positions_can_exceed_action_rectangle(self):
        b = fixture(); b['entities'][0].update(x_raw=-1,y_raw=33000)
        a = project_observation(b,'research_battlefield')
        self.assertEqual((a['entities'][0]['x'],a['entities'][0]['y']),(-1,33000))
        v = encode_observation(a)
        self.assertEqual(v[107:111],[-1/18000,1.,33000/32000,1.])

    def test_research_native_id_renumbering_and_tied_row_order_do_not_change_vector(self):
        b = fixture(); b['entities'] += [entity(999, hp=300)]
        first = project_observation(b, 'research_battlefield')
        b['entities'] = [entity(42,hp=300),entity(41,hp=500)]
        second = project_observation(b, 'research_battlefield')
        self.assertEqual(encode_observation(first),encode_observation(second))

    def test_missing_entity_hp_and_measured_zero_have_different_masks(self):
        b = fixture(); b['entities'][0]['hp'] = None
        missing = encode_observation(project_observation(b,'research_battlefield'))
        b['entities'][0]['hp'] = 0
        zero = encode_observation(project_observation(b,'research_battlefield'))
        start = 8+4*13+6*6
        self.assertEqual(missing[start+15:start+17],[0.,0.])
        self.assertEqual(zero[start+15:start+17],[0.,1.])

    def test_missing_tower_hp_and_zero_not_conflated(self):
        b = fixture(); b['towers'][0]['hp'] = None
        missing = encode_observation(project_observation(b))
        b['towers'][0]['hp'] = 0; zero = encode_observation(project_observation(b))
        self.assertNotEqual(missing,zero)

    def test_unattested_towers_are_masked(self):
        b = fixture(); b['tower_identity_basis'] = 'unknown_without_reset_attestation'
        a = project_observation(b); self.assertEqual(a['towers'],[])
        self.assertEqual(encode_observation(a)[60:96],[0.]*36)

    def test_incomplete_hand_masks_absent_slots_not_known_zero_cost(self):
        b = fixture(); b['hand'] = b['hand'][:3]; b['hand_complete'] = False
        a = project_observation(b); self.assertEqual(affordability_mask(a),[True,True,True,True,False])
        self.assertEqual(encode_observation(a)[47:60],[0.]*13)
        b['hand'][0]['cost'] = 0; a = project_observation(b)
        self.assertEqual(encode_observation(a)[18:20],[0.,1.])

    def test_budget_only_mask_never_imports_hidden_readiness(self):
        b = fixture(); a = project_observation(b)
        self.assertEqual(affordability_mask(a),[True,True,True,True,False])
        b['hidden_cooldowns'] = [100]*4; b['placement_legal'] = [False]*4
        self.assertEqual(affordability_mask(project_observation(b)),affordability_mask(a))
        b['own_elixir_raw'] = 40000
        self.assertEqual(affordability_mask(project_observation(b)),[True]*5)

    def test_shape_matches_all_entity_history_toggles_and_empty_dimensions(self):
        b = fixture(); b['history'] = [event()]
        for profile in ('fair','research_battlefield'):
            a = project_observation(b,profile)
            for include_e in (True,False):
                for include_h in (True,False):
                    for size in (0,1,16):
                        args = dict(max_entities=size,history_steps=size,
                            include_entities=include_e,include_history=include_h)
                        vector = encode_observation(a,**args); spec = feature_spec(**args)
                        self.assertEqual(len(vector),spec['dimension'])
                        self.assertTrue(all(math.isfinite(v) for v in vector))
        self.assertEqual(feature_spec()['dimension'],688)

    def test_inverse_perspective_is_once_at_encoding(self):
        b = fixture(owner=1); b['entities'][0]['owner'] = 1
        b['entities'][0]['x_raw'] = 0; b['entities'][0]['y_raw'] = 31999
        a = project_observation(b,'research_battlefield'); start = 96
        self.assertEqual(a['entities'][0]['x'],0)
        vector = encode_observation(a)
        self.assertEqual(vector[start+11:start+15],[17999/18000,1.,0.,1.])

    def test_invalid_hand_numeric_and_dimensions_are_rejected(self):
        for update in ({'own_elixir_raw':None},{'own_elixir_raw':False},{'hand_complete':False}):
            b = fixture(); b.update(update)
            with self.assertRaises(ValueError): project_observation(b)
        b = fixture(); b['hand'].append(b['hand'][0])
        with self.assertRaises(ValueError): project_observation(b)
        with self.assertRaises(ValueError): feature_spec(cards=[1,1])
        with self.assertRaises(ValueError): feature_spec(history_steps=-1)


if __name__ == '__main__': unittest.main()

"""Current manifest-derived public belief controls; no live native RPC."""
from copy import deepcopy
import unittest
from unittest.mock import patch

from royale_phase3.belief import PublicBelief, encode_belief
from royale_phase3.observation import project_observation
from tests_phase3.test_observation import fixture, event


class BeliefContracts(unittest.TestCase):
    def test_default_unknown_mechanics_keeps_full_interval_and_unknown_hand(self):
        b = fixture(); b['history'] = [event()]
        p = PublicBelief(); got = p.update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[0.,10.])
        self.assertIsNone(got['opponent_hand_distribution'])
        self.assertIsNone(got['opponent_next_card_distribution'])
        self.assertTrue(got['opponent_unknown_deck'])
        self.assertIsNone(got['opponent_seen_cards'][0]['minimum_other_plays_until_return'])

    def test_current_csv_rates_and_boundary_interval(self):
        p = PublicBelief(ordinary_resource_only=True)
        self.assertEqual(p.schedule,((0,2400,178),(2400,4800,357),(4800,6000,537)))
        self.assertEqual(p._regeneration(2399,2400),(178,357))
        self.assertEqual(p._regeneration(4799,4800),(357,537))
        self.assertIsNone(p._regeneration(6000,6001))

    def test_supported_ordinary_scope_spend_and_capped_gain(self):
        b = fixture(); b['history'] = [event()]
        got = PublicBelief(ordinary_resource_only=True).update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[.1602,6.1602])
        b['tick'] = 2399
        self.assertEqual(PublicBelief(ordinary_resource_only=True).update(project_observation(b))['opponent_elixir_interval'],[10.,10.])

    def test_unknown_mode_and_outside_timeline_widen(self):
        b = fixture(); b['history'] = [event()]
        got = PublicBelief(game_mode=72000000,ordinary_resource_only=True).update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[0.,10.])
        b['tick'] = 6001
        got = PublicBelief(ordinary_resource_only=True).update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[0.,10.])

    def test_opaque_card_cost_unknown_resets_bound(self):
        b = fixture(); b['history'] = [event(tick=100,native_card_id=0)]
        got = PublicBelief(ordinary_resource_only=True).update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[0.,10.])
        self.assertEqual(got['opponent_seen_cards'],[])

    def test_impossible_public_spends_widen_and_flag_inconsistency(self):
        b = fixture(); b['history'] = [event(tick=100,cost_elixir=9),event(2,tick=100,cost_elixir=9)]
        p = PublicBelief(ordinary_resource_only=True); got = p.update(project_observation(b))
        self.assertEqual(got['opponent_elixir_interval'],[0.,10.])
        self.assertFalse(p.diagnostics['resource_model_consistent'])
        self.assertNotIn('resource_model_consistent',got)

    def test_sparse_and_dense_online_updates_equal_offline_at_same_tick(self):
        dense = PublicBelief(ordinary_resource_only=True)
        for tick in (90,100,110,150):
            b = fixture(tick); b['history'] = [event()] if tick>=91 else []
            observed = project_observation(b); online = dense.update(observed)
        sparse = PublicBelief(ordinary_resource_only=True).update(observed)
        self.assertEqual(online,sparse)

    def test_past_event_positive_control_and_future_rejected(self):
        b = fixture(); a = project_observation(b); changed = deepcopy(a)
        b['history'] = [event()]; changed = project_observation(b)
        self.assertNotEqual(encode_belief(PublicBelief().update(a)),encode_belief(PublicBelief().update(changed)))
        changed['history'][0]['tick'] = 101
        with self.assertRaises(ValueError): PublicBelief().update(changed)

    def test_history_mutation_tick_regression_and_perspective_need_reset(self):
        b = fixture(); b['history'] = [event()]; a = project_observation(b)
        for operation in ('erase','past','owner'):
            p = PublicBelief(); p.update(a); changed = deepcopy(a)
            if operation=='erase': changed['history'] = []
            elif operation=='past': changed['tick'] = 99
            else: changed['native_owner'] = 1
            with self.assertRaises(ValueError): p.update(changed)
            p.reset(); self.assertEqual(p.update(project_observation(fixture(90)))['opponent_observed_play_count'],0)

    def test_same_tick_is_idempotent_and_results_detached(self):
        p = PublicBelief(); a = project_observation(fixture())
        output = p.update(a); output['opponent_elixir_interval'][0] = 9
        self.assertEqual(p.update(a)['opponent_elixir_interval'],[0.,10.])

    def test_scope_mode_and_uncertified_cycle_parameters_rejected(self):
        with self.assertRaises(ValueError): PublicBelief(ordinary_resource_only='False')
        with self.assertRaises(ValueError): PublicBelief(game_mode=True)
        with self.assertRaises(ValueError): PublicBelief(standard_cycle_evidence='old-engine')

    def test_csv_pin_tampering_fails_before_tracking(self):
        from royale_phase3 import belief
        manifest = belief.json.loads(belief.local('data/phase2/version_manifest.json').read_text(encoding='utf-8-sig'))
        manifest = deepcopy(manifest)
        row = next(r for r in manifest['assets'] if r['path'].replace('\\','/').endswith('/game_modes.csv'))
        row['sha256'] = '0'*64
        with patch.object(belief.json,'loads',return_value=manifest):
            with self.assertRaisesRegex(ValueError,'differs from locked manifest'): PublicBelief()

    def test_encoding_dimension_unknown_age_mask_and_observed_frequency(self):
        p = PublicBelief(); a = p.update(project_observation(fixture()))
        self.assertEqual(len(encode_belief(a)),15); self.assertEqual(encode_belief(a)[-2:],[0.,0.])
        b = fixture(); b['history'] = [event(),event(2,tick=99,native_card_id=123)]
        a = PublicBelief().update(project_observation(b))
        self.assertEqual(a['opponent_seen_cards'][0]['observed_frequency'],.5)
        self.assertEqual(encode_belief(a)[-3],.5); self.assertEqual(encode_belief(a)[-1],1.)


if __name__ == '__main__': unittest.main()

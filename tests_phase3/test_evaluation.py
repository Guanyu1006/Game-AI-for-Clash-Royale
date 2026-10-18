"""Synthetic inference contract tests, not native rollout evidence."""
from copy import deepcopy
import unittest
import numpy as np
from royale_phase2.learning import (NumpyBCPolicy, actor_features, decode_policy_output,
    feature_configuration, inference_configuration, init_parameters)
from royale_phase2.trajectories import TrajectoryError, audit_subspace_proof, subspace_mask
from royale_phase2.visibility import project_actor
from royale_phase3.evaluation import event_metrics, random_actions


def fixture():
    raw = {'schema': 'cr-minimal.v4', 'ok': True, 'generation': 1, 'tick': 90, 'crowns': [0, 0],
        'players': [{'owner': owner, 'elixir': 50000,
            'hand': [[slot, 28000000+slot, slot+1, 999, 888] for slot in range(4)]} for owner in (0, 1)]}
    actor = project_actor(raw, 0)
    return actor


class EvaluationContracts(unittest.TestCase):
    def test_bias_changes_choice_but_never_allows_masked_card(self):
        p = np.asarray([[.8, .19, .01, 0, 0]], dtype=np.float32)
        xy = np.asarray([[.5, .5]], dtype=np.float32); mask = np.asarray([[True, True, False, False, False]])
        self.assertEqual(int(decode_policy_output(p, xy, mask)[0][0]), 0)
        self.assertEqual(int(decode_policy_output(p, xy, mask, play_bias=4)[0][0]), 1)
        only_wait = np.asarray([[True, False, False, False, False]])
        self.assertEqual(int(decode_policy_output(p, xy, only_wait, play_bias=1e6)[0][0]), 0)

    def test_coordinate_rounding_matches_historical_scalar_dtype(self):
        p = np.ones((30, 5), np.float32)/5; xy = np.random.default_rng(11).random((30, 2), dtype=np.float32)
        _, actual = decode_policy_output(p, xy, np.ones_like(p, dtype=bool))
        expected = [[int(np.rint(x*17999)), int(np.rint(y*31999))] for x, y in xy]
        np.testing.assert_array_equal(actual, expected)

    def test_decoder_rejects_nonfinite_bias_and_invalid_mask(self):
        p = np.ones((1, 5))/5; xy = np.ones((1, 2))/2
        with self.assertRaises(TrajectoryError): decode_policy_output(p, xy, np.ones((1, 5)), play_bias=0)
        with self.assertRaises(TrajectoryError): decode_policy_output(p, xy, np.ones((1, 5), bool), play_bias=float('nan'))
        with self.assertRaises(TrajectoryError): inference_configuration({'inference_play_bias': float('inf')})

    def test_diagnostic_single_and_batch_actions_identical(self):
        actor = fixture(); config = feature_configuration([{'actor': actor}]); width = len(actor_features(actor, config))
        model = NumpyBCPolicy(init_parameters(width, 6), {'profile': 'native_label_bc', 'feature_configuration': config, 'inference_play_bias': 4.})
        actors = [actor, deepcopy(actor)]; actors[1]['tick'] += 10
        self.assertEqual(model.predict_many(actors), [model.predict(a) for a in actors])

    def test_forged_hard_e_mask_rejected_before_prediction(self):
        actor = fixture(); config = feature_configuration([{'actor': actor}]); width = len(actor_features(actor, config))
        version = '9b65f8d83414adb5acd92d7ea41dac4df83948f1ae22aeb2e12a9ec3e7157cba'
        model = NumpyBCPolicy(init_parameters(width, 6), {'profile': 'hard_e', 'feature_configuration': config,
            'version_fingerprint_sha256': version})
        proof = audit_subspace_proof('data/phase2/action_subspace_proof.json', version)
        mask = subspace_mask(actor, True, proof); mask['values'][4] = True
        with self.assertRaises(TrajectoryError): model.predict(actor, legal_mask=mask)

    def test_event_matching_is_one_to_one_and_has_frozen_tolerance(self):
        row = {'source_episode_id': 'synthetic', 'native_owner': 0, 'engine_tick': 10,
            'actual_execution_tick': 11, 'action': {'kind': 'play_card', 'card': 1}}
        rows = [row, {**row, 'engine_tick': 12, 'action': {'kind': 'wait'}}]
        actions = [{'kind': 'play_card', 'card': 1}]*2
        result = event_metrics(rows, actions, 10)
        self.assertEqual(result['matched_events'], 1); self.assertEqual(result['precision'], .5)
        rows[1]['engine_tick'] = 30
        self.assertEqual(event_metrics(rows, actions, 0)['matched_events'], 1)

    def test_random_legal_reproduces_state_and_digest(self):
        actor = fixture(); rows = [{'actor': actor, 'legal_action_mask': {'values': [True, True, False, False, False]}}]*20
        a, state = random_actions(rows, 31); b, other = random_actions(rows, 31)
        self.assertEqual(a, b); self.assertEqual(state, other)
        self.assertTrue(all(x['kind'] == 'wait' or x['slot'] == 0 for x in a))


if __name__ == '__main__': unittest.main()

"""Synthetic contract/numerical tests. These never establish native evidence."""
import copy
import json
from pathlib import Path
import unittest
import uuid

from royale_phase2.trajectories import (ROOT, TrajectoryError, _check_execution, _root_consumption_confirmation, _wait_provenance,
                                       audit_subspace_proof, canonical_bytes, digest, file_sha,
                                       freeze_learning_split, load_learning_split, project_path,
                                       subspace_mask, validate_actor, verify_json_value, write_json)
from royale_phase2.learning import (actor_features, feature_configuration, init_parameters,
                                    loss_and_gradients, numpy, train_bc)
from royale_phase2.visibility import project_actor
from royale_phase2.dataset import group_episodes


def actor():
    raw = {'schema': 'cr-minimal.v4', 'ok': True, 'generation': 1, 'tick': 90, 'crowns': [0, 0],
           'players': [{'owner': owner, 'elixir': 50000,
                        'hand': [[slot, 28000000+slot, slot+1, 999, 888] for slot in range(4)]} for owner in (0, 1)]}
    return project_actor(raw, 0)


class NativeTrajectoryContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = project_path(ROOT/'tmp/phase2-learning'/('synthetic-contracts-'+uuid.uuid4().hex))
        cls.directory.mkdir(parents=True)

    def test_split_is_frozen_by_group_and_contains_every_source(self):
        result = freeze_learning_split('data/phase2/frozen_manifest.json')
        self.assertEqual(len(result['entries']), 120)
        self.assertFalse(result['uses_native_results'])
        self.assertEqual(result, freeze_learning_split('data/phase2/frozen_manifest.json'))
        self.assertEqual(sum(result['counts'].values()), 120)
        path = self.directory/'split.json'; write_json(path, result)
        loaded, entries = load_learning_split(path)
        self.assertEqual(loaded['split_sha256'], result['split_sha256'])
        self.assertEqual(len(entries), 120)
        changed = copy.deepcopy(result); changed['entries'][0]['learning_split'] = 'test'
        if changed == result: changed['entries'][0]['learning_split'] = 'train'
        changed['split_sha256'] = digest({key: value for key, value in changed.items() if key != 'split_sha256'})
        write_json(path, changed)
        with self.assertRaises(TrajectoryError): load_learning_split(path)

    def test_actor_forbids_extra_private_keys_and_parameter_fields(self):
        source = actor(); validate_actor(source)
        poisoned = copy.deepcopy(source); poisoned['opponent_hand'] = [123]
        with self.assertRaises(TrajectoryError): validate_actor(poisoned)
        poisoned = copy.deepcopy(source); poisoned['hand'][0]['packed_parameter'] = 123
        with self.assertRaises(TrajectoryError): validate_actor(poisoned)
        poisoned = copy.deepcopy(source); poisoned['hand_complete'] = False
        with self.assertRaises(TrajectoryError): validate_actor(poisoned)

    def test_source_only_extension_preserves_original_denominator_and_groups(self):
        original = json.loads((ROOT/'data/phase2/frozen_manifest.json').read_text(encoding='utf-8'))
        combined, combined_entries = load_learning_split('data/phase2/learning/extended_split_manifest.json')
        previous, previous_entries = load_learning_split('data/phase2/learning/split_manifest.json')
        addition = json.loads((ROOT/'data/phase2/learning/additional_source_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(original['counts'], {'development': 20, 'formal': 100})
        self.assertEqual(len(combined_entries), 185)
        self.assertEqual(len(addition['entries']), 65)
        self.assertFalse(set(previous_entries) & {entry['source_episode_id'] for entry in addition['entries']})
        for source_id in previous_entries:
            self.assertEqual(previous_entries[source_id]['learning_split'], combined_entries[source_id]['learning_split'])
        parsed = [json.loads((ROOT/entry['episode']).read_text(encoding='utf-8')) for entry in original['entries']+addition['entries']]
        groups, duplicates = group_episodes(parsed)
        self.assertEqual(len(set(groups.values())), 185)
        self.assertEqual(duplicates, [])
        preflight = json.loads((ROOT/'outputs/phase2/data/firstlight_learning_extension_preflight.json').read_text(encoding='utf-8'))
        self.assertEqual(preflight['denominator'], 65)
        self.assertEqual(preflight['conversion_counts'], {'pass': 65})
        self.assertEqual(preflight['native_replays_executed'], 0)
        self.assertEqual(preflight['training_decision_samples'], 0)
        self.assertEqual(preflight['mixed_source_level_hypotheses'], [])

    def test_native_learning_cohort_is_fixed_by_source_only_groups(self):
        cohort = json.loads((ROOT/'data/phase2/learning/native_cohort_manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(cohort['counts'], {'train': 10, 'validation': 3, 'test': 3})
        self.assertEqual(len({entry['source_episode_id'] for entry in cohort['entries']}), 16)
        self.assertTrue(all(entry['source_events'] >= 25 for entry in cohort['entries']))
        self.assertFalse(cohort['uses_source_final_outcome'])
        self.assertFalse(cohort['uses_native_results'])
        self.assertEqual(cohort['formal_denominator'], 100)
        self.assertEqual(cohort['native_runs_executed_by_this_tool'], 0)
        self.assertEqual(cohort['training_samples_created'], 0)
        self.assertEqual(cohort['cohort_sha256'], digest({key: value for key, value in cohort.items() if key != 'cohort_sha256'}))

    def test_features_use_training_vocabulary_only(self):
        before = actor(); config = feature_configuration([{'actor': before}])
        changed = copy.deepcopy(before); changed['hand'][0]['card'] = 99999999
        self.assertNotIn(99999999, config['card_vocabulary'])
        self.assertEqual(actor_features(before, config).shape, actor_features(changed, config).shape)
        with self.assertRaises(TrajectoryError): feature_configuration([])

    def test_unverified_subspace_never_becomes_full_legal_mask(self):
        proof = audit_subspace_proof(None, 'a'*64)
        value = subspace_mask(actor(), True, proof)
        self.assertFalse(value['full_legality_verified'])
        self.assertEqual(value['values'], [True, False, False, False, False])

    def test_reference_hash_and_selected_json_value_must_match(self):
        path = self.directory/'synthetic-capture.json'
        payload = {'synthetic_fixture_not_native_evidence': True, 'capture': actor()}; write_json(path, payload)
        reference = {'path': str(path), 'sha256': file_sha(path), 'json_pointer': '/capture'}
        verify_json_value(reference, payload['capture'])
        changed = copy.deepcopy(payload['capture']); changed['tick'] += 1
        with self.assertRaises(TrajectoryError): verify_json_value(reference, changed)
        reference['sha256'] = 'b'*64
        with self.assertRaises(TrajectoryError): verify_json_value(reference, payload['capture'])

    def test_wait_never_hides_an_ability_or_other_source_action(self):
        decision = {'label_provenance': {'kind': 'sampling_grid_absence', 'human_explicit_wait': False, 'interval_ticks': 5}}
        episode = {'source_journal_coverage': 'complete', 'source_actions': [{'native_owner': 0, 'target_tick': 93, 'kind': 'activate_ability'}]}
        with self.assertRaises(TrajectoryError): _wait_provenance(decision, episode, actor(), 95)
        episode['source_actions'][0]['native_owner'] = 1
        _wait_provenance(decision, episode, actor(), 95)
        decision['label_provenance']['human_explicit_wait'] = True
        with self.assertRaises(TrajectoryError): _wait_provenance(decision, episode, actor(), 95)

    def test_execution_cannot_precede_observation(self):
        decision = {'timing': {'observation_phase': 'pre_submit', 'observation_sequence': 4, 'submit_sequence': 3},
                    'execution': {'executed_tick': 91}}
        with self.assertRaises(TrajectoryError): _check_execution(decision, actor(), {'kind': 'wait'})
        decision['timing']['submit_sequence'] = 5; decision['execution']['executed_tick'] = 90
        with self.assertRaises(TrajectoryError): _check_execution(decision, actor(), {'kind': 'wait'})

    def test_opaque_native_event_proves_root_consumption_without_forging_form(self):
        pre = {'tick': 90, 'hand': [[0, 26000017, 5, 12345, 26000017]], 'cycle': [26000052], 'elixir': 80000}
        post = {'tick': 91, 'hand': [[0, 26000052, 4, 67890, 26000052]], 'cycle': [26000017], 'elixir': 30178}
        before = {'tick': 90, 'players': [{'owner': 1, **{k: v for k, v in pre.items() if k != 'tick'}}]}
        after = {'tick': 91, 'players': [{'owner': 1, **{k: v for k, v in post.items() if k != 'tick'}}]}
        feedback = {'card_event': [1, 91, 1, 0, 5], 'own_boundary_consumption_evidence': True,
                    'before_registration': pre, 'after_execution': post,
                    'scheduler_feedback': {'state': 'succeeded', 'resolvedCardParameter': 12345},
                    'effective_form_identity': 'unknown',
                    'matched_by': 'unique_native_event_and_own_boundary_hand_cost_parameter_cycle'}
        confirmation = _root_consumption_confirmation(feedback, before, after, 1, {'card_id': 26000017})
        self.assertEqual(confirmation['raw_native_event_card'], 0)
        self.assertEqual(confirmation['input_root_card'], 26000017)
        self.assertEqual(confirmation['effective_form_identity'], 'unknown')
        self.assertNotIn('card', confirmation)
        changed = copy.deepcopy(feedback); changed['before_registration']['elixir'] = 99999
        with self.assertRaises(TrajectoryError): _root_consumption_confirmation(changed, before, after, 1, {'card_id': 26000017})
        changed = copy.deepcopy(feedback); changed['effective_form_identity'] = {'kind': 'hero', 'card_id': 26000017}
        with self.assertRaises(TrajectoryError): _root_consumption_confirmation(changed, before, after, 1, {'card_id': 26000017})
    def test_numerical_bc_gradient_without_training_or_checkpoint(self):
        np = numpy(); rng = np.random.default_rng(8)
        x = rng.normal(size=(4, 3)).astype(np.float32)
        target = np.asarray([0, 1, 2, 1]); xy = rng.uniform(size=(4, 2)).astype(np.float32)
        masks = np.ones((4, 5), dtype=bool); masks[:, 4] = False
        parameters = init_parameters(3, 4, 18); weights = np.asarray([1, 2, 1, 1, 1], dtype=np.float32)
        _, gradients = loss_and_gradients(parameters, x, target, xy, masks, class_weights=weights)
        for key, index in [('w1', (0, 1)), ('ws', (1, 2)), ('wx', (2, 0))]:
            old = float(parameters[key][index]); epsilon = 0.002
            parameters[key][index] = old+epsilon
            plus, _ = loss_and_gradients(parameters, x, target, xy, masks, class_weights=weights)
            parameters[key][index] = old-epsilon
            minus, _ = loss_and_gradients(parameters, x, target, xy, masks, class_weights=weights)
            parameters[key][index] = old
            self.assertAlmostEqual((plus-minus)/(2*epsilon), float(gradients[key][index]), delta=0.001)


if __name__ == '__main__':
    unittest.main()

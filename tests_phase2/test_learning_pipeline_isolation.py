"""Artifact/guard tests only: no engine, native pass, training or checkpoint."""
import copy
import unittest
import uuid

from royale_phase2.trajectories import (ROOT, TrajectoryError, export_episodes,
                                       file_sha, project_path, read_json, write_json)
from tools.phase2_learning_pipeline import (check_training_gates, completed_round,
                                           normalize_repeatability)


class LearningPipelineIsolation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.directory = project_path(ROOT/'tmp/phase2-learning'/('isolation-contracts-'+uuid.uuid4().hex))
        cls.directory.mkdir(parents=True)
        cls.actual_episode = ROOT/'outputs/phase2/replays/development/ordinary-first/episode.json'
        cls.source_id = read_json(cls.actual_episode)['source_episode_id']
        cls.version = read_json(cls.actual_episode)['version_fingerprint']

    def test_one_missing_job_does_not_discard_recorded_native_artifact(self):
        directory = self.directory/'round'; directory.mkdir()
        missing_id = 'deliberately_missing_contract_source'
        entries = [{'source_episode_id': self.source_id}, {'source_episode_id': missing_id}]
        write_json(directory/'queue.json', {
            'synthetic_queue_fixture_not_native_validation': True,
            'identity': {'grid_ticks': 5},
            'jobs': [{'source_episode_id': self.source_id, 'state': 'recorded',
                      'result': {'path': self.actual_episode.relative_to(ROOT).as_posix(),
                                 'sha256': file_sha(self.actual_episode)}}]})
        available, failures = completed_round(directory, entries)
        self.assertEqual(set(available), {self.source_id})
        self.assertEqual([failure['source_episode_id'] for failure in failures], [missing_id])
        self.assertEqual(available[self.source_id]['sha256'], file_sha(self.actual_episode))

    def test_missing_repeats_and_invalid_pass_are_per_source_isolated(self):
        other_id = 'missing_repeat_contract_source'
        entries = [{'source_episode_id': self.source_id}, {'source_episode_id': other_id}]
        reference = {'path': self.actual_episode.relative_to(ROOT).as_posix(), 'sha256': file_sha(self.actual_episode)}
        path = self.directory/'untrusted-repeat-claim.json'
        write_json(path, {'schema': 'royale-phase2-repeatability.v1',
                          'synthetic_negative_fixture_not_native_validation': True,
                          'episodes': [{'source_episode_id': self.source_id, 'status': 'pass',
                                        'version_fingerprint': self.version, 'runs': [reference]*3}]})
        normalized = normalize_repeatability([path], entries, [{self.source_id: reference}]*3, self.version)
        self.assertEqual(normalized['episode_denominator'], 2)
        self.assertEqual(normalized['episodes_passed'], 0)
        statuses = {entry['source_episode_id']: entry for entry in normalized['episodes']}
        self.assertEqual(statuses[self.source_id]['status'], 'fail')
        self.assertEqual(statuses[other_id]['status'], 'not_run')
        self.assertEqual(statuses[self.source_id]['reported_entry']['status'], 'pass')

    def test_missing_second_round_states_cannot_inherit_a_reported_pass(self):
        references = []
        for index in range(3):
            copied = copy.deepcopy(read_json(self.actual_episode))
            if index == 1:
                copied['files']['states.jsonl'] = {
                    'path': (self.directory/'deliberately_missing_states.jsonl').relative_to(ROOT).as_posix(),
                    'sha256': 'a'*64}
            path = self.directory/f'negative-run-{index}.json'; write_json(path, copied)
            references.append({'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path),
                               'native_generation': index+1, 'state_points': 1, 'sequence_sha256': 'b'*64})
        report_path = self.directory/'negative-missing-states-repeat.json'
        write_json(report_path, {'schema': 'royale-phase2-repeatability.v1',
                                 'synthetic_negative_fixture_not_native_validation': True,
                                 'episodes': [{'source_episode_id': self.source_id, 'version_fingerprint': self.version,
                                               'status': 'pass', 'sequence_hashes': ['b'*64]*3,
                                               'independent_reset_generations': True,
                                               'identical_native_initialization': True, 'runs': references}]})
        normalized = normalize_repeatability([report_path], [{'source_episode_id': self.source_id}],
                                             [{self.source_id: reference} for reference in references], self.version)
        self.assertEqual(normalized['episodes_passed'], 0)
        self.assertEqual(normalized['episodes'][0]['status'], 'fail')
        self.assertTrue(any('deliberately_missing_states' in reason
                            for reason in normalized['episodes'][0]['pipeline_exclusion_reasons']))

    def test_statistical_gate_ignores_failed_candidates_but_requires_all_real_play_subsets(self):
        # These are synthetic *counts* for the guard, never exported as training rows.
        summary = {'eligible_hard_e': 10000, 'failed_inputs': [{'reason': 'missing states'}],
                   'by_split': {split: {'eligible_hard_e': count, 'eligible_hard_e_play': 1}
                                for split, count in [('train', 7000), ('validation', 1500), ('test', 1500)]}}
        check_training_gates(summary)
        for split in ('train', 'validation', 'test'):
            changed = copy.deepcopy(summary); changed['by_split'][split]['eligible_hard_e_play'] = 0
            with self.assertRaises(TrajectoryError): check_training_gates(changed)
        changed = copy.deepcopy(summary); changed['eligible_hard_e'] = 9999
        with self.assertRaises(TrajectoryError): check_training_gates(changed)

    def test_missing_result_is_retained_in_actual_empty_export_denominator(self):
        frozen = read_json(ROOT/'data/phase2/learning/extended_split_manifest.json')
        source_id = frozen['entries'][0]['source_episode_id']
        report = export_episodes([], 'data/phase2/learning/extended_split_manifest.json',
                                 self.directory/'missing-export',
                                 missing_inputs=[{'source_episode_id': source_id, 'path': None,
                                                  'reason': 'No recorded native result; contract-only missing artifact'}])
        self.assertEqual(report['frozen_episode_denominator'], 185)
        self.assertEqual(report['attempted_source_episodes'], 1)
        self.assertEqual(report['unavailable_episode_inputs'], 1)
        self.assertNotIn(source_id, report['unattempted_frozen_source_ids'])
        self.assertEqual(report['failed_inputs'][0]['source_episode_id'], source_id)
        self.assertFalse(report['failed_inputs'][0]['input_available'])
        self.assertEqual(report['rows'], 0)
        self.assertEqual(report['eligible_hard_e'], 0)


if __name__ == '__main__': unittest.main()

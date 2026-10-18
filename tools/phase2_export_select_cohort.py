"""Freeze the first source-only native learning cohort; no outcome files read."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import (canonical_bytes, digest, file_sha, load_learning_split,
                                       project_path, read_json, write_json)


def main():
    additional_path = project_path('data/phase2/learning/additional_source_manifest.json')
    additional = read_json(additional_path)
    learning, by_id = load_learning_split('data/phase2/learning/extended_split_manifest.json')
    quotas = {'train': 10, 'validation': 3, 'test': 3}
    all_candidates = []
    for source_entry in additional['entries']:
        entry = by_id[source_entry['source_episode_id']]
        key = f'phase2-native-learning-cohort-v1|{additional["dataset_revision"]}|{entry["leakage_group"]}'
        all_candidates.append({**source_entry, 'learning_split': entry['learning_split'],
                               'group_selection_sha256': hashlib.sha256(key.encode()).hexdigest(),
                               'preferred_source_actions_at_least_25': source_entry['source_events'] >= 25})
    selected = []; shortages = {}; fallback = []
    for split, quota in quotas.items():
        ordered = sorted([entry for entry in all_candidates if entry['learning_split'] == split],
                         key=lambda entry: (not entry['preferred_source_actions_at_least_25'], entry['group_selection_sha256']))
        chosen = ordered[:quota]; selected.extend(chosen)
        if len(chosen) < quota: shortages[split] = quota-len(chosen)
        fallback.extend(entry['source_episode_id'] for entry in chosen if not entry['preferred_source_actions_at_least_25'])
    result = {'schema': 'royale-phase2-native-learning-cohort.v1',
              'artifact_kind': 'source_only_predeclared_native_run_selection_not_trajectories',
              'dataset_revision': additional['dataset_revision'],
              'upstream_manifest': {'path': additional_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(additional_path)},
              'combined_manifest': {'path': 'data/phase2/learning/combined_source_manifest.json',
                                    'sha256': file_sha(ROOT/'data/phase2/learning/combined_source_manifest.json')},
              'learning_split_sha256': learning['split_sha256'],
              'formal_denominator': 100, 'formal_manifest_sha256_unchanged': file_sha(ROOT/'data/phase2/frozen_manifest.json'),
              'selection_rule': 'Only the65 frozen learning additions. For each unchanged learning split, prefer source_actions>=25, then order by SHA256(phase2-native-learning-cohort-v1|dataset_revision|leakage_group). If preferred group count is insufficient, fill from remaining same-split groups by the identical hash order. Quotas train10/validation3/test3. No final outcome, native replay result or duration read.',
              'source_only_candidate_bias': 'Uniform source levels, no source activate_ability events, >=1 ordinary Fireball/Arrows source input. Omits ability-bearing decks/mixed levels and biases spell/deck/mechanism distribution; not the full game or original formal100 population.',
              'uses_source_final_outcome': False, 'uses_native_results': False,
              'requested_counts': quotas, 'counts': dict(Counter(entry['learning_split'] for entry in selected)),
              'source_candidate_count': len(all_candidates), 'source_actions_threshold': 25,
              'fallback_below_action_threshold': fallback, 'shortages': shortages,
              'execution_plan': {'independent_reset_runs_per_episode': 3, 'decision_grid_ticks': 5,
                                 'trajectory_run_selection': 'Use one predeclared actual run only; never duplicate across repeat runs to increase decision counts.'},
              'native_runs_executed_by_this_tool': 0, 'training_samples_created': 0, 'entries': selected}
    result['cohort_sha256'] = digest(result)
    output = project_path('data/phase2/learning/native_cohort_manifest.json')
    if output.exists() and canonical_bytes(read_json(output)) != canonical_bytes(result):
        raise ValueError('Refusing to change a previously frozen native learning cohort')
    if not output.exists(): write_json(output, result)
    print(json.dumps({'path': output.relative_to(ROOT).as_posix(), 'counts': result['counts'],
                      'cohort_sha256': result['cohort_sha256'], 'shortages': shortages,
                      'fallback_below_action_threshold': fallback,
                      'episodes': [{'source_episode_id': entry['source_episode_id'], 'learning_split': entry['learning_split'],
                                    'source_actions': entry['source_events']} for entry in selected]}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

"""Freeze two formal learning trials from source fields, never native outcomes."""
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import canonical_bytes, digest, file_sha, load_learning_split, project_path, read_json, write_json


def main():
    frozen_path = project_path('data/phase2/frozen_manifest.json'); frozen = read_json(frozen_path)
    coverage = read_json('outputs/phase2/data/learning_source_coverage.json')
    split, split_entries = load_learning_split('data/phase2/learning/extended_split_manifest.json')
    source_entries = {entry['source_episode_id']: entry for entry in frozen['entries']}
    candidates = []
    for row in coverage['episodes']:
        if row['reconstruction_split'] != 'formal' or not row['source_only_simple_spell_candidate']:
            continue
        entry = source_entries[row['source_episode_id']]; learning_split = split_entries[row['source_episode_id']]['learning_split']
        key = f'phase2-native-learning-cohort-v1|{frozen["dataset_revision"]}|{entry["leakage_group"]}'
        candidates.append({**entry, 'learning_split': learning_split,
                           'group_selection_sha256': hashlib.sha256(key.encode()).hexdigest(),
                           'preferred_source_actions_at_least_25': entry['source_events'] >= 25})
    selected = []; shortages = {}; fallback = []
    for learning_split in ('train', 'validation'):
        ordered = sorted([entry for entry in candidates if entry['learning_split'] == learning_split],
                         key=lambda entry: (not entry['preferred_source_actions_at_least_25'], entry['group_selection_sha256']))
        if not ordered:
            shortages[learning_split] = 1; continue
        selected.append(ordered[0])
        if not ordered[0]['preferred_source_actions_at_least_25']: fallback.append(ordered[0]['source_episode_id'])
    result = {'schema': 'royale-phase2-formal-training-cohort.v1',
              'artifact_kind': 'source_only_formal_subset_native_trial_selection_not_trajectories',
              'dataset_revision': frozen['dataset_revision'], 'formal_denominator': 100,
              'upstream_manifest': {'path': frozen_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(frozen_path)},
              'combined_manifest': {'path': 'data/phase2/learning/combined_source_manifest.json',
                                    'sha256': file_sha(ROOT/'data/phase2/learning/combined_source_manifest.json')},
              'learning_split_sha256': split['split_sha256'],
              'selection_rule': 'Within original formal100 only, lossless source levels + no source abilities + >=1 Fireball/Arrows source label. For each unchanged train/validation split prefer source_actions>=25 then SHA256(phase2-native-learning-cohort-v1|dataset_revision|leakage_group), select first1. Fill below25 only if needed by same hash. No native or final source outcome read.',
              'source_only_candidate_bias': 'Limited spell-bearing, uniform-level, no-ability source subset; not formal100 success-rate estimation or full-game fidelity. Actual failed selected trials stay failed and excluded from strict training.',
              'uses_source_final_outcome': False, 'uses_native_results': False,
              'requested_counts': {'train': 1, 'validation': 1}, 'counts': dict(Counter(entry['learning_split'] for entry in selected)),
              'source_candidate_count': len(candidates), 'shortages': shortages, 'fallback_below_action_threshold': fallback,
              'execution_plan': {'independent_reset_runs_per_episode': 3, 'decision_grid_ticks': 5,
                                 'trajectory_round': 'r1_only_predeclared'},
              'native_runs_executed_by_this_tool': 0, 'training_samples_created': 0, 'entries': selected}
    result['cohort_sha256'] = digest(result)
    path = project_path('data/phase2/learning/formal_training_cohort_manifest.json')
    if path.exists() and canonical_bytes(read_json(path)) != canonical_bytes(result):
        raise ValueError('Refusing to change an already frozen formal training cohort')
    if not path.exists(): write_json(path, result)
    print(json.dumps({'path': path.relative_to(ROOT).as_posix(), 'counts': result['counts'],
                      'cohort_sha256': result['cohort_sha256'], 'shortages': shortages,
                      'fallback': fallback, 'entries': selected}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

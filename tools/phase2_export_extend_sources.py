"""Freeze source-only learning additions without changing the formal denominator."""
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import load_source_shard
from royale_phase2.replays import parse_payload
from royale_phase2.trajectories import (canonical_bytes, file_sha, freeze_learning_split,
                                       project_path, read_json, write_json)


def immutable_json(path, value):
    path = project_path(path)
    if path.exists() and canonical_bytes(read_json(path)) != canonical_bytes(value):
        raise ValueError('Refuse to overwrite different frozen data: '+str(path))
    if not path.exists(): write_json(path, value)


def main():
    original_path = project_path('data/phase2/frozen_manifest.json'); original_sha = file_sha(original_path)
    split_path = project_path('data/phase2/learning/split_manifest.json'); split_sha = file_sha(split_path)
    original = read_json(original_path); coverage_path = project_path('outputs/phase2/data/learning_source_coverage.json')
    coverage = read_json(coverage_path)
    if coverage['source_outcomes_used_for_selection'] is not False:
        raise ValueError('Source-only extension must not use final outcomes')
    raw_rows, provenance, _ = load_source_shard()
    original_ids = {entry['source_episode_id'] for entry in original['entries']}
    added = []
    for candidate in sorted(coverage['already_downloaded_shard_additional_candidates'], key=lambda item: item['source_episode_id']):
        index = candidate['raw_row_index']; raw = raw_rows[index]
        if raw['replay_tag'] != candidate['source_episode_id'] or raw['replay_tag'] in original_ids:
            raise ValueError('Source-only extension row identity conflict')
        payload = json.loads(raw['payload_json'])
        levels = {card['level'] for side in ('team', 'opponent') for player in payload['battle'][side]['players'] for card in player['deck']}
        if (len(levels) != 1 or not all(1 <= level <= 16 for level in levels)
                or any(event['kind'] == 'activate_ability' for event in payload['events'])
                or not any(event.get('card_key') in {'fireball', 'arrows'} for event in payload['events'])):
            raise ValueError('Source-only candidate does not satisfy the declared public criterion')
        episode = parse_payload(raw, {**provenance, 'raw_row_index': index})
        if episode['quality']['parse_valid'] != 'pass': raise ValueError('Selected source row has parse errors')
        episode.update(split='learning_extension', leakage_group=episode['episode_id'])
        path = project_path('data/phase2/converted/source-v1/episodes')/(episode['episode_id']+'.json')
        immutable_json(path, episode)
        added.append({'source_episode_id': episode['episode_id'], 'split': 'learning_extension',
                      'leakage_group': episode['leakage_group'], 'raw_row_index': index,
                      'payload_sha256': episode['provenance']['payload_sha256'],
                      'content_sha256': episode['fingerprints']['content_sha256'], 'episode': path.relative_to(ROOT).as_posix(),
                      'parse_valid': 'pass', 'source_events': len(episode['action_journal'])})
    addition_manifest = copy.deepcopy(original)
    addition_manifest.update(entries=added, counts={'learning_extension': len(added)},
        selection_rule=coverage['source_only_candidate_rule']+'; select all matches among original-unselected shard rows, sorted ID; no outcomes',
        selection_scope_limitation='Supplementary learning pool only; not the original formal reconstruction denominator.',
        parent_frozen_manifest={'path': original_path.relative_to(ROOT).as_posix(), 'sha256': original_sha},
        selection_evidence={'path': coverage_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(coverage_path)},
        training_decision_samples=0, native_reconstruction_status='not_run')
    addition_manifest['unselected_source_ids'] = sorted(set(original['unselected_source_ids'])-{entry['source_episode_id'] for entry in added})
    immutable_json('data/phase2/learning/additional_source_manifest.json', addition_manifest)
    combined = copy.deepcopy(addition_manifest)
    combined.update(entries=copy.deepcopy(original['entries'])+added,
                    counts={**original['counts'], 'learning_extension': len(added)},
                    selection_rule='Preserve all original120; append all65 source-only spell/no-ability/uniform-level candidates. Original formal100 denominator unchanged.')
    immutable_json('data/phase2/learning/combined_source_manifest.json', combined)
    learning = freeze_learning_split('data/phase2/learning/combined_source_manifest.json',
                                     'data/phase2/learning/extended_split_manifest.json')
    prior = read_json(split_path)
    previous = {entry['source_episode_id']: entry['learning_split'] for entry in prior['entries']}
    if any(entry['source_episode_id'] in previous and previous[entry['source_episode_id']] != entry['learning_split'] for entry in learning['entries']):
        raise ValueError('Learning extension changed an original group split')
    if file_sha(original_path) != original_sha or file_sha(split_path) != split_sha:
        raise ValueError('Original manifests changed during independent source extension')
    report = {'schema': 'royale-phase2-learning-source-extension.v1',
              'status': 'source_only_additions_frozen_not_native_trajectories',
              'new_source_episodes': len(added), 'combined_source_episodes': len(combined['entries']),
              'extended_learning_split': learning['counts'], 'extended_split_sha256': learning['split_sha256'],
              'original_formal_denominator': original['counts']['formal'],
              'original_frozen_sha256_unchanged': original_sha, 'original_learning_split_file_sha256_unchanged': split_sha,
              'source_outcomes_used_for_selection': False, 'native_runs': 0, 'training_decisions': 0,
              'manifest': 'data/phase2/learning/combined_source_manifest.json',
              'additional_only_manifest': 'data/phase2/learning/additional_source_manifest.json',
              'limitation': 'Purposeful public source card/mechanism selection changes the learning distribution; whole frozen formal evaluation denominator is preserved.'}
    write_json('outputs/phase2/data/learning_source_extension.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

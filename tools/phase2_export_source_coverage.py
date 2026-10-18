"""Source-only learning coverage, before outcome filtering; no train rows made."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import file_sha, load_learning_split, read_json, write_json
from royale_phase2.dataset import load_source_shard
import hashlib


def main():
    split, entries = load_learning_split('data/phase2/learning/split_manifest.json')
    preflight = read_json('outputs/phase2/data/firstlight_preflight.json')
    conversions = {entry['source_episode_id']: entry for entry in preflight['episodes']}
    rows = []
    for source_id, entry in entries.items():
        conversion = conversions[source_id]
        if conversion.get('conversion_status') != 'pass': continue
        if file_sha(ROOT/conversion['hypothesis']) != conversion['hypothesis_sha256']:
            raise ValueError('Pinned hypothesis changed')
        hypothesis = read_json(conversion['hypothesis'])
        actions = hypothesis['commands']
        cards = Counter(action.get('card_id') for action in actions if action['kind'] == 'play_card')
        ability_count = sum(action['kind'] == 'activate_ability' for action in actions)
        preserved = hypothesis['assumptions'].get('source_levels_modified') is False
        rows.append({'source_episode_id': source_id, 'learning_split': entry['learning_split'],
                     'reconstruction_split': entry['reconstruction_split'], 'source_level_preserved': preserved,
                     'source_actions': len(actions), 'source_ability_actions': ability_count,
                     'source_fireball_actions': cards[28000000], 'source_arrows_actions': cards[28000001],
                     'source_has_supported_spell_labels': cards[28000000]+cards[28000001] > 0,
                     'source_only_simple_spell_candidate': preserved and ability_count == 0 and cards[28000000]+cards[28000001] > 0})
    candidates = [row for row in rows if row['source_only_simple_spell_candidate']]
    raw_rows, provenance, _ = load_source_shard()
    audit = read_json('outputs/phase2/data/source_schema_audit.json')
    if audit['raw_rows'] != len(raw_rows) or audit['leakage_groups'] != len(raw_rows) or audit['duplicate_or_near_duplicate_groups']:
        raise ValueError('Source grouping changed: recompute full grouping before planning additions')
    additional = []
    for raw_index, raw in enumerate(raw_rows):
        source_id = raw['replay_tag']
        if source_id in entries: continue
        payload = json.loads(raw['payload_json'])
        levels = {card['level'] for side in ('team', 'opponent') for player in payload['battle'][side]['players'] for card in player['deck']}
        if len(levels) != 1 or not all(1 <= value <= 16 for value in levels): continue
        if any(event['kind'] == 'activate_ability' for event in payload['events']): continue
        spell_count = sum(event.get('card_key') in {'fireball', 'arrows'} for event in payload['events'])
        if not spell_count: continue
        key = f'bc-split-v1|{provenance["dataset_revision"]}|{source_id}'
        bucket = int(hashlib.sha256(key.encode()).hexdigest()[:16], 16) % 100
        learning_split = 'train' if bucket < 70 else 'validation' if bucket < 85 else 'test'
        additional.append({'source_episode_id': source_id, 'raw_row_index': raw_index, 'learning_split': learning_split,
                           'source_spell_actions': spell_count, 'source_uniform_level': next(iter(levels))})
    report = {'schema': 'royale-phase2-source-learning-coverage.v1',
              'scope': 'Public source-only planning statistics; no native eligibility or training sample claim. Source final outcomes were not read or used.',
              'split_sha256': split['split_sha256'], 'source_outcomes_used_for_selection': False,
              'total_frozen_episodes': len(entries), 'ordinary_spell_card_ids': [28000000, 28000001],
              'source_only_simple_spell_candidates': len(candidates),
              'candidate_split_counts': dict(Counter(row['learning_split'] for row in candidates)),
              'source_only_candidate_rule': 'strict source levels preserved AND no activate_ability source events AND >=1 ordinary Fireball/Arrows source input',
              'warning': 'Candidate rule changes deck/mechanism distribution; it cannot replace full frozen formal denominator or outcome-independent engine evaluation.',
              'already_downloaded_shard_additional_candidates': additional,
              'additional_candidate_split_counts': dict(Counter(row['learning_split'] for row in additional)),
              'episodes': rows}
    write_json('outputs/phase2/data/learning_source_coverage.json', report)
    print(json.dumps({key: report[key] for key in ('total_frozen_episodes', 'source_only_simple_spell_candidates', 'candidate_split_counts', 'additional_candidate_split_counts')}, ensure_ascii=False, indent=2))
    for split_name in ('train', 'validation', 'test'):
        print(json.dumps({'split': split_name, 'candidate_ids': [row['source_episode_id'] for row in candidates if row['learning_split'] == split_name]}, ensure_ascii=False))


if __name__ == '__main__': main()

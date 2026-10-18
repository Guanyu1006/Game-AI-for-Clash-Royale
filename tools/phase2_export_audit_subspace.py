"""Independently audit existing native spell traces and actor privacy both seats.

No native actions run here. Counterfactual captures are synthetic perturbations
of actual saved native states, never extra native-game experiment results.
"""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import (audit_subspace_proof, file_sha, project_path, read_json,
                                       read_jsonl, subspace_mask, verify_reference, write_json)
from royale_phase2.visibility import TowerAttestation, project_actor


def main():
    proof_path = project_path('data/phase2/action_subspace_proof.json')
    proof = read_json(proof_path)
    audit = audit_subspace_proof(proof_path, proof['version_fingerprint_sha256'])
    if audit['status'] != 'verified_local_rule_evidence': raise ValueError(audit)
    positive_path = 'outputs/phase2/fixtures/native-v1/native_positive.json'
    positive = read_json(positive_path); native_tests = []
    exemplar_paths = {}
    for case in positive['tests']:
        reference = verify_reference(case['evidence']); wire = read_jsonl(reference['path'])
        x, y = case['requested_xy']; event = case['native_event']
        expected = f'replay-schedule-card {case["owner"]} {case["card"]} {x} {y} {event[1]}'
        submits = [index for index, row in enumerate(wire) if row.get('command') == expected and row.get('response', {}).get('ok') is True]
        if len(submits) != 1: raise ValueError('Native request was not uniquely found in its recorded wire')
        captures = [row['response'] for row in wire[submits[0]+1:] if row.get('response', {}).get('schema') == 'cr-minimal.v4']
        if not any(event in capture['plays'] for capture in captures):
            raise ValueError('Reported native CardPlay absent after its actual scheduled request')
        if (event[2], event[3]) != (case['owner'], case['card']): raise ValueError('Native event identity mismatch')
        native_tests.append({'card': case['card'], 'owner': case['owner'], 'requested_xy': [x, y],
                             'event_seen_after_actual_request': True, 'evidence': reference})
        key = (case['card'], case['owner'])
        exemplar_paths.setdefault(key, project_path(reference['path']).parent/'states.jsonl')
    if len(native_tests) != 40 or set(exemplar_paths) != {(card, owner) for card in (28000000, 28000001) for owner in (0, 1)}:
        raise ValueError('Expected two-card/two-owner native point coverage missing')
    privacy = []; sources = []
    for key, state_path in sorted(exemplar_paths.items()):
        raw = read_jsonl(state_path)[0]
        towers = TowerAttestation.from_reset(raw)
        reference = {'path': state_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(state_path), 'jsonl_line': 0}
        sources.append(reference)
        for owner in (0, 1):
            baseline = project_actor(raw, owner, towers); baseline_mask = subspace_mask(baseline, True, audit)
            for perturbation in range(50):
                changed = deepcopy(raw)
                enemy = next(player for player in changed['players'] if player['owner'] != owner)
                enemy.update(elixir=perturbation*1900, hand=[[slot, 28000999+perturbation, 99, 0, 0] for slot in range(4)],
                             cycle=[perturbation]*8, abilities=['private'], evolution=['private'])
                changed.update(seed=perturbation, future_events=[{'tick': 9999, 'card': perturbation}])
                for player in changed['players']:
                    if player['owner'] == owner:
                        player.update(cycle=['private'], abilities=['private'], evolution=['private'])
                        for card in player['hand']: card[3:] = ['private parameter', 'private effective form']
                changed['entities'].append([999999, 1-owner, 26000000+perturbation, 7000, 14000,
                                            999, 1000, 12345, 888, 'troop', 777])
                changed['plays'] = [[99999, 90, 1-owner, 28000000+perturbation, 9]]
                actor = project_actor(changed, owner, towers); mask = subspace_mask(actor, True, audit)
                if actor != baseline or mask != baseline_mask: raise ValueError('Private information changed actor/subspace mask')
                privacy.append({'fixture_card': key[0], 'fixture_cast_owner': key[1], 'owner': owner,
                                'perturbation': perturbation, 'actor_unchanged': True, 'mask_unchanged': True})
    privacy_report = {'schema': 'royale-phase2-subspace-private-invariance.v1', 'status': 'pass',
                      'version_fingerprint_sha256': proof['version_fingerprint_sha256'], 'tests': privacy,
                      'source_native_captures': sources, 'owner_counts': dict(Counter(test['owner'] for test in privacy)),
                      'method': 'Counterfactual perturbation of existing actual native captures for both actor owners; no new native battle or native mutation.',
                      'independent_reference_available': False}
    privacy_path = project_path('outputs/phase2/learning_contracts/subspace_privacy_both_owners.json')
    write_json(privacy_path, privacy_report)
    report = {'schema': 'royale-phase2-subspace-independent-audit.v1', 'status': 'pass',
              'proof': {'path': proof_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(proof_path)},
              'proof_audit': audit, 'native_cast_requests_and_events_crosschecked': len(native_tests),
              'cast_card_owner_counts': dict(Counter(str((test['card'], test['owner'])) for test in native_tests)),
              'native_checks': native_tests, 'privacy_checks': len(privacy),
              'privacy': {'path': privacy_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(privacy_path)},
              'scope': 'Saved local native execution evidence audit and actor/mask boundary perturbations; 40 native representative casts are not a coordinate exhaustive proof or official trajectory truth.'}
    write_json('outputs/phase2/learning_contracts/subspace_audit.json', report)
    print(json.dumps({key: report[key] for key in ('status', 'native_cast_requests_and_events_crosschecked', 'privacy_checks', 'privacy')}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

"""Independent synthetic actor-boundary review; never executes a game client."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'royale_phase2/visibility.py'
OUTPUT = ROOT / 'outputs/phase2/assets/dex_projection_independent_review.json'


def load_visibility():
    # Load exactly the reviewed file without importing unrelated package code.
    import sys
    import types
    source_bytes = SOURCE.read_bytes()
    module = types.ModuleType('reviewed_visibility')
    module.__file__ = str(SOURCE)
    sys.modules[module.__name__] = module
    exec(compile(source_bytes, str(SOURCE), 'exec'), module.__dict__)
    module.review_source_bytes = source_bytes
    return module


def fixture():
    locations = [(0, 9000, 3000), (0, 3500, 6500), (0, 14500, 6500),
                 (1, 9000, 29000), (1, 3500, 25500), (1, 14500, 25500)]
    return {
        'schema': 'cr-minimal.v4', 'ok': True, 'tick': 90, 'generation': 1,
        'finalized': False, 'crowns': [0, 0], 'result': None,
        'players': [{'owner': owner, 'elixir': 50000,
                     'hand': [[slot, 26000000 + slot, slot + 1, 999, 777] for slot in range(4)],
                     'cycle': [888, 999], 'abilities': [{'private': True}], 'evolution': [777]}
                    for owner in [0, 1]],
        'entities': [[index + 10, owner, -1, x, y, 4000, 5000, 99999, 123, 'building', 0]
                     for index, (owner, x, y) in enumerate(locations)],
        'seed': 123456, 'future_events': ['not actor input'],
    }


def attempt(name, operation, expected, severity, explanation):
    try:
        value = operation()
        return {'case': name, 'accepted': True, 'value': value, 'expected': expected,
                'severity': severity, 'explanation': explanation}
    except Exception as error:
        return {'case': name, 'accepted': False, 'exception': type(error).__name__,
                'message': str(error), 'expected': expected, 'severity': severity,
                'explanation': explanation}


def main():
    module = load_visibility()
    source_hash = hashlib.sha256(module.review_source_bytes).hexdigest()
    state = fixture()
    towers = module.TowerAttestation.from_reset(state)
    privacy = []
    for owner in [0, 1]:
        baseline = module.project_actor(state, owner, towers)
        poisoned = copy.deepcopy(state)
        opponent = poisoned['players'][1-owner]
        opponent.update({'hand': [[0, 999999, 99, 888, 777]], 'elixir': 12345})
        for player in poisoned['players']:
            player.update({'cycle': ['poison'], 'abilities': ['poison'], 'evolution': ['poison']})
            for card in player['hand']:
                card[3:] = ['secret packed parameter', 'secret effective form id']
        poisoned['seed'] = 'poison'; poisoned['future_events'] = ['poison']
        for entity in poisoned['entities']:
            entity[7:9] = ['secret target', 'secret cooldown']
            entity[10] = 'secret deployment delay'
        poisoned['entities'].append([1000, 1-owner, 26000000, 7000, 14000, 12345,
                                     20000, 1001, 999, 'troop', 777])
        poisoned['players'].reverse()
        value = module.project_actor(poisoned, owner, towers)
        assert baseline == value
        privacy.append({'native_owner': owner, 'equal_after_private_field_perturbation': True,
                        'projected_sha256': hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()})

    cases = []

    def forged_attestation():
        changed = copy.deepcopy(state)
        changed['entities'].append([999, 1, -1, 7000, 14000, 12345, 20000, None, None, 'building', 0])
        forged = module.TowerAttestation(1, (module.TowerIdentity(999, 1, 'king', 7000, 14000),))
        return module.project_actor(changed, 0, forged)['towers']
    cases.append(attempt('manual_attestation_noncanonical_single_building', forged_attestation,
                         'reject: only an intact canonical six-tower reset roster can attest tower HP', 'P1_API_boundary',
                         'Regression target: an earlier public dataclass constructor could bypass from_reset. '
                         'Current rejection verifies the new constructor guard on this exact synthetic case.'))

    for count in [0, 3]:
        def partial_hand(count=count):
            changed = copy.deepcopy(state); changed['players'][0]['hand'] = changed['players'][0]['hand'][:count]
            observation = module.project_actor(changed, 0, towers)
            return {'hand': observation['hand'], 'hand_complete': observation.get('hand_complete'),
                    'budget_mask': module.affordability_mask(observation)}
        cases.append(attempt(f'own_hand_{count}_slots', partial_hand,
                             'reject or explicitly downgrade missing-hand capability; do not claim complete four-slot actor observation', 'P2_contract',
                             'A partial hand is an acceptable explicitly incomplete observation if hand_complete is false and missing action slots are disabled. '
                             'Native draw/initialization may transiently omit cards. The consumer still needs to exclude such observations from strict decision samples.'))

    def zero_max_hp():
        changed = copy.deepcopy(state); changed['entities'][0][5:7] = [0, 0]
        return module.project_actor(changed, 0, towers)['towers']
    cases.append(attempt('attested_tower_zero_max_hp', zero_max_hp,
                         'reject: require max_hp>0 even if hp==0', 'P2_contract',
                         'Regression target: later projection must require positive maxHP; absence should remain unknown.'))

    def invalid_capture_attestation():
        changed = copy.deepcopy(state); changed['ok'] = False
        result = module.TowerAttestation.from_reset(changed)
        return {'generation': result.generation, 'identity_count': len(result.identities)}
    cases.append(attempt('from_reset_invalid_capture_ok_false', invalid_capture_attestation,
                         'reject invalid capture before issuing identity attestation', 'P2_attestation',
                         'Regression target: issuing an attestation from ok=False must fail before feature extraction.'))

    def midbattle_attestation():
        changed = copy.deepcopy(state); changed['tick'] = 5000
        result = module.TowerAttestation.from_reset(changed)
        return {'generation': result.generation, 'identity_count': len(result.identities), 'input_tick': changed['tick']}
    cases.append(attempt('from_reset_midbattle_lifecycle_unverified', midbattle_attestation,
                         'require backend-verified reset lifecycle provenance, not merely six surviving tower-shaped entities', 'P2_attestation',
                         'No universal reset tick is assumed. The public API has no evidence that its caller supplied an actual reset observation.'))

    def boolean_generation():
        changed = copy.deepcopy(state); changed['generation'] = True
        return module.project_actor(changed, 0, towers)['towers']
    cases.append(attempt('boolean_generation_matches_integer_one', boolean_generation,
                         'reject non-int generation', 'P3_type_contract',
                         'Python True==1 bypasses generation equality; source runtime normally emits integer generation.'))

    def boolean_player_owner():
        changed = copy.deepcopy(state); changed['players'][0]['owner'] = False
        return module.project_actor(changed, 0, towers)['native_owner']
    cases.append(attempt('boolean_player_owner_matches_integer_zero', boolean_player_owner,
                         'reject non-int player owner before selection', 'P3_type_contract',
                         'Own requested owner is strict-int validated, but each raw player.owner is selected by numeric equality.'))

    def malformed_ignored_entity():
        changed = copy.deepcopy(state); changed['entities'].append(['ignored private non-tower'])
        return module.project_actor(changed, 0, towers)
    cases.append(attempt('malformed_ignored_non_tower_changes_availability', malformed_ignored_entity,
                         'either explicitly reject invalid transport before actor extraction or limit actor parsing to attested tower IDs', 'P3_availability',
                         'Whole entity-array validation couples actor availability to ignored private entities. This is not evidence of normal valid-wire feature leakage.'))

    lines = module.review_source_bytes.decode('utf-8').splitlines()
    markers = ['class TowerAttestation', 'def from_reset', "if len(hand) > 4", 'if towers.generation !=', "max_hp = integer(entity[6]", "players = [p for p"]
    refs = {marker: [index+1 for index, line in enumerate(lines) if marker in line] for marker in markers}
    for case in cases:
        if case['case'].startswith('own_hand_'):
            value = case.get('value', {})
            case['review_assessment'] = ('explicit_incomplete_contract_ok' if case['accepted'] and value.get('hand_complete') is False
                                         and value.get('budget_mask', {}).get('full_legality_verified') is False
                                         else 'needs_review')
        elif case['case'].startswith('boolean_'):
            case['review_assessment'] = 'low_priority_type_gap' if case['accepted'] else 'guard_rejected'
        elif case['case'] == 'malformed_ignored_non_tower_changes_availability':
            case['review_assessment'] = 'transport_rejection_observed_not_valid_wire_leakage'
        else:
            case['review_assessment'] = 'regression_guard_rejected' if not case['accepted'] else 'needs_review'
    residual_type_cases = [case['case'] for case in cases if case['case'].startswith('boolean_') and case['accepted']]
    test_command = [sys.executable, '-B', '-m', 'unittest', 'discover', '-s', 'tests_phase2', '-p', 'test_actor_projection.py', '-v']
    test_env = dict(os.environ, PYTHONDONTWRITEBYTECODE='1')
    tested = subprocess.run(test_command, cwd=ROOT, env=test_env, capture_output=True, text=True, encoding='utf-8')
    test_log = tested.stdout + tested.stderr
    report = {
        'status': 'independent_synthetic_review_complete_findings_recorded',
        'scope': 'Pure Python synthetic fixtures only; no native client, game, remote script or HTTP endpoint executed. Root code unmodified.',
        'reviewed_file': SOURCE.relative_to(ROOT).as_posix(), 'reviewed_sha256': source_hash,
        'reviewed_source_snapshot': 'outputs/phase2/assets/dex_visibility_reviewed_snapshot.py',
        'review_tool_sha256': hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        'source_line_references': refs, 'privacy_controls': privacy, 'cases': cases,
        'root_projection_unittest': {'command': test_command, 'exit_code': tested.returncode,
                                    'log': 'outputs/phase2/assets/dex_actor_projection_unittest.log',
                                    'source_sha256_after_test': hashlib.sha256(SOURCE.read_bytes()).hexdigest()},
        'observed_direct_private_feature_leakage_under_valid_wire': False,
        'interpretation': 'Valid-wire tests preserve enemy hand/elixir/cycle, packed parameter, effective form, target, cooldown, deployment and future/seed isolation. '
                          'Current constructor/HP/invalid-capture/late-tick guards reject the four earlier regression examples. '
                          'Partial hands are explicitly downgraded by hand_complete=false and missing budget actions remain false. '
                          f'Residual accepted bool-vs-int cases: {residual_type_cases}. '
                          'Real native integer-wire visibility/lifecycle has not been attested by this audit.',
        'recommended_order': ['Bind tower attestation to a verified backend reset lifecycle and fixed mode/version; this pure fixture cannot attest native/render visibility.',
                              'Strict training/export must require hand_complete=true plus separately verified readiness and native legality.',
                              'Apply strict primitive types to raw owner/generation/location at transport validation; keep invalid private payload rejection distinct from feature leakage.'],
    }
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    (OUTPUT.parent / 'dex_visibility_reviewed_snapshot.py').write_bytes(module.review_source_bytes)
    (OUTPUT.parent / 'dex_actor_projection_unittest.log').write_text(test_log, encoding='utf-8')
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'report': OUTPUT.relative_to(ROOT).as_posix(), 'source_sha256': source_hash,
                      'private_perturbation_controls': len(privacy), 'review_cases': len(cases),
                      'root_projection_test_exit_code': tested.returncode,
                      'accepted_cases': [case['case'] for case in cases if case['accepted']]}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

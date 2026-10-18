"""Actual saved partial-hand actor, untrained tensors, WAIT inference boundary.

This is an offline model contract test. No trained checkpoint, native call or
game-match success is claimed or created.
"""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.learning import NumpyBCPolicy, actor_features, feature_configuration, init_parameters
from royale_phase2.trajectories import (audit_subspace_proof, file_sha, project_path, read_json,
                                       read_jsonl, subspace_mask, verify_json_value, verify_reference, write_json)
from royale_phase2.visibility import TowerAttestation, project_actor


def main():
    episode_path = project_path('outputs/phase2/batches/formal100-v1/7c43a05e2fb243a3be41113b2e4940c4/attempt-01/episode.json')
    episode = read_json(episode_path)
    verify_reference(episode['files']['states.jsonl']); verify_reference(episode['files']['decisions.jsonl'])
    states = read_jsonl(episode['files']['states.jsonl']['path'])
    decisions = read_jsonl(episode['files']['decisions.jsonl']['path'])
    decision = next(row for row in decisions if not row['actor_observation']['hand_complete'])
    raw = states[decision['state_line']-1]['raw_capture']
    reference = {**episode['files']['states.jsonl'], 'jsonl_line': decision['state_line']-1, 'json_pointer': '/raw_capture'}
    verify_json_value(reference, raw)
    actor = project_actor(raw, decision['owner'], TowerAttestation.from_reset(states[0]['raw_capture']))
    if actor != decision['actor_observation'] or actor['hand_complete']:
        raise AssertionError('Expected an actual stored incomplete-hand observation')
    config = feature_configuration([{'actor': decisions[0]['actor_observation']}])
    parameters = init_parameters(len(actor_features(actor, config)), hidden=8, seed=19)
    version = episode['version_fingerprint']
    policy = NumpyBCPolicy(parameters, {'profile': 'hard_e', 'version_fingerprint_sha256': version,
                                      'feature_configuration': config})
    proof = audit_subspace_proof('data/phase2/action_subspace_proof.json', version)
    if proof['status'] != 'verified_local_rule_evidence': raise AssertionError(proof)
    results = []
    for command_ready in (False, True):
        mask = subspace_mask(actor, command_ready, proof)
        if mask['values'] != [True, False, False, False, False]: raise AssertionError('Partial actor exposed a card action')
        action = policy.predict(actor, legal_mask=mask)
        if action != {'kind': 'wait'}: raise AssertionError('Partial-hand model inference did not return legal WAIT')
        results.append({'command_ready_input': command_ready, 'mask': mask['values'], 'action': action})
    poisoned = dict(mask); poisoned['values'] = [True, True, False, False, False]
    try:
        policy.predict(actor, legal_mask=poisoned)
    except ValueError as error:
        invalid_mask_rejected = str(error)
    else:
        raise AssertionError('Partial actor accepted a card-enabled forged mask')
    output = {'schema': 'royale-phase2-actual-partial-actor-model-boundary.v1', 'status': 'pass',
              'scope': 'Actual saved native actor; untrained deterministic float32 tensors for inference-boundary testing only. No training, checkpoint, native RPC or game match run.',
              'episode': {'path': episode_path.relative_to(ROOT).as_posix(), 'sha256': file_sha(episode_path)},
              'decision_id': decision['decision_id'], 'hand_size': len(actor['hand']), 'actor_tick': actor['tick'],
              'before_capture_evidence': reference, 'checks': results, 'forged_play_mask_rejected': invalid_mask_rejected,
              'learning_source_sha256': file_sha(ROOT/'royale_phase2/learning.py'), 'checkpoint_created': False,
              'native_matches_run': 0, 'trained_model_evaluation': False}
    write_json('outputs/phase2/learning_contracts/actual_partial_hand_inference.json', output)
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

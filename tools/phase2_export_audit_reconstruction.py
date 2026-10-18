"""Read-only source/wire/pre-submit attribution audit of two completed formal runs."""
from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import load_source_shard
from royale_phase2.replays import parse_payload
from royale_phase2.trajectories import (adapt_native_reconstruction, canonical_bytes, convert_episode, file_sha,
                                       load_learning_split, project_path, read_json, read_jsonl, verify_reference, write_json)
from royale_phase2.versioning import verify_manifest


def find_capture(value):
    if isinstance(value, dict):
        if value.get('schema') == 'cr-minimal.v4': return value
        for child in value.values():
            found = find_capture(child)
            if found is not None: return found
    return None


def audit_run(path, version, source_rows, provenance, split_entries):
    report = read_json(path); checked = Counter(); findings = []
    for reference in report['files'].values(): verify_reference(reference)
    if report['version_fingerprint'] != version: raise ValueError('Actual version mismatch')
    frozen = read_json('data/phase2/frozen_manifest.json')
    entry = next(entry for entry in frozen['entries'] if entry['source_episode_id'] == report['source_episode_id'])
    source = read_json(report['source_episode']); hypothesis = read_json(report['hypothesis'])
    expected = parse_payload(source_rows[entry['raw_row_index']], {**provenance, 'raw_row_index': entry['raw_row_index']})
    expected.update(split=entry['split'], leakage_group=entry['leakage_group'])
    if canonical_bytes(expected) != canonical_bytes(source): raise ValueError('Source episode differs from actual pinned raw row')
    checked['source_payload_lineage'] += 1
    actions = read_json(report['files']['actions.json']['path'])['actions']
    original = source['action_journal']; commands = hypothesis['commands']
    if len(actions) != len(original) or len(actions) != len(commands): raise ValueError('Source action was dropped or added')
    if [action['source_event_index'] for action in actions] != list(range(len(actions))): raise ValueError('Source event order changed')
    for event, command, action in zip(original, commands, actions):
        if action['tick'] != event['source_tick_20hz']+1 or action['tick'] != command['target_engine_tick']:
            raise ValueError('Action moved outside the declared +1 boundary conversion')
        if (action['owner'], action['kind'], action['source_side']) != (command['owner'], command['kind'], event['source_side']):
            raise ValueError('Player/action semantics changed')
        checked['source_action_order_owner_boundary'] += 1
    states = read_jsonl(report['files']['states.jsonl']['path'])
    decisions = read_jsonl(report['files']['decisions.jsonl']['path'])
    wire = read_jsonl(report['files']['wire.jsonl']['path'])
    by_request = {action['request_id']: action for action in actions}
    if [row['request_index'] for row in wire] != list(range(1, len(wire)+1)):
        raise ValueError('Transport sequence is incomplete or ambiguous')
    for decision in decisions:
        line = decision['state_line']; before = states[line-1]['raw_capture']
        observed = decision['observation_sequence']; submitted = decision['submit_sequence']
        if not 1 <= observed < submitted <= len(wire): raise ValueError('Observation did not precede recorded submission')
        if find_capture(wire[observed-1].get('response')) != before:
            raise ValueError('Reported observation sequence does not refer to the actual before-capture response')
        checked['actual_wire_before_capture'] += 1
        if decision.get('command_ready') is True and not decision['actor_observation']['hand_complete']:
            findings.append({'kind': 'readiness_claim_with_incomplete_hand', 'decision_id': decision['decision_id'],
                             'scope': 'Export should quarantine incomplete actor; no observed private-feature leakage.'})
        for label in decision['labels']:
            action = by_request[label['request_id']]
            if label['kind'] != 'play_card': continue
            expected_request = f'replay-schedule-card {decision["owner"]} {label["card_id"]} {label["x"]} {label["y"]} {label["target_tick"]}'
            if wire[submitted-1]['command'] != expected_request:
                raise ValueError('Submission sequence points to a different native action')
            feedback = action['feedback']; event = feedback['card_event']
            if not feedback['card_consumed'] or event is None: raise ValueError('Successful run contains unconfirmed card label')
            after_captures = [find_capture(row.get('response')) for row in wire[submitted:]]
            if not any(capture and event in capture['plays'] for capture in after_captures):
                raise ValueError('CardPlay was not observed after its actual native submission')
            if before['tick'] >= event[1]: raise ValueError('Card was already consumed in the purported pre-action state')
            checked['actual_request_then_cardplay'] += 1
    normalized = adapt_native_reconstruction(path, expected_version=version)
    rows, exported = convert_episode(normalized, split_entries[report['source_episode_id']],
                                     proof_path='data/phase2/action_subspace_proof.json')
    if any(row['eligible_hard_e'] or row['eligible_native_label_bc'] for row in rows):
        raise ValueError('First-run nondeterministic evidence was silently promoted to training quality')
    checked['actor_and_boundary_recomputed_by_adapter'] = len(rows)
    return {'source_episode_id': report['source_episode_id'], 'input': {'path': path.relative_to(ROOT).as_posix(), 'sha256': file_sha(path)},
            'checks': dict(checked), 'findings': findings,
            'actual_action_inputs': len(actions), 'raw_decisions': len(decisions),
            'adapter_rejections': dict(Counter(row['reason'] for row in normalized['adapter_rejections'])),
            'conversion_rejections': dict(Counter(row['reason'] for row in exported['rejected_decisions'])),
            'diagnostic_rows': len(rows), 'hard_e_eligible': 0, 'native_label_bc_eligible': 0,
            'quality_original_preserved': report['quality'],
            'consumption_confirmation_kinds': dict(Counter(row['execution_feedback']['confirmation']['kind'] for row in rows)),
            'status': 'pass_boundary_and_lineage_audit_only'}


def main():
    folder = project_path('outputs/phase2/batches/formal100-v1')
    paths = []
    for path in sorted(folder.rglob('episode.json')):
        report = read_json(path)
        if report.get('completed') and report['quality']['runtime_replayable'] == 'pass' and report['quality']['outcome_consistent'] == 'pass':
            paths.append(path)
        if len(paths) == 2: break
    if len(paths) != 2: raise ValueError('Two actually completed runtime/outcome passing formal runs are not yet available')
    version = verify_manifest('data/phase2/version_manifest.json')['fingerprint']
    raw, provenance, _ = load_source_shard(); _, entries = load_learning_split('data/phase2/learning/extended_split_manifest.json')
    results = [audit_run(path, version, raw, provenance, entries) for path in paths]
    report = {'schema': 'royale-phase2-independent-source-wire-audit.v1',
              'status': 'completed_read_only_audit', 'native_calls_executed': 0, 'training_samples_qualified': 0,
              'selection': 'First two lexicographically ordered actually completed formal100-v1 reports with runtime/outcome pass, for boundary audit only; not engine tuning or reconstruction success estimation.',
              'runs': results, 'root_files_modified': False,
              'limits': ['Two passing traces cannot establish full formal100 quality.',
                         'No independent official trajectory reference or reset repeatability was added.',
                         'command_ready is a runner/control provenance field; this audit checks its actual wire boundary and incomplete-hand consistency, not an independent arbitrary-card legality oracle.']}
    write_json('outputs/phase2/learning_contracts/formal_source_wire_audit.json', report)
    print(json.dumps({'status': report['status'], 'runs': [{key: value for key, value in item.items() if key not in {'input', 'quality_original_preserved'}} for item in results]}, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

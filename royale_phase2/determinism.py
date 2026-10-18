"""Compare all exposed native state/event and scheduler semantics across reset.

Only generation/epoch identifiers and per-process counter origins are
normalized. HP, positions, targets, cooldowns, cycles, packed parameters,
event ordering and native scheduler outcomes remain part of the hash.
"""
from copy import deepcopy
import json
from .runtime import local
from .versioning import canonical_hash, file_hash


def canonical_run(episode_path):
    path = local(episode_path); episode = json.loads(path.read_text(encoding='utf-8'))
    for artifact in episode['files'].values():
        if file_hash(artifact['path']) != artifact['sha256']: raise ValueError('Run artifact changed')
    states_path = local(episode['files']['states.jsonl']['path'])
    states = [json.loads(line)['raw_capture'] for line in states_path.read_text(encoding='utf-8').splitlines() if line]
    if not states: raise ValueError('No actual native state sequence')
    origin = states[0]['next']-1

    def state_packet(raw):
        state = deepcopy(raw); state.pop('generation')
        state['next'] -= origin
        for play in state['plays']: play[0] -= origin
        return state

    semantic_states = [state_packet(state) for state in states]
    actions = json.loads(local(episode['files']['actions.json']['path']).read_text(encoding='utf-8'))['actions']
    sequences = [r['feedback']['registration_receipt']['sequence'] for r in actions if r.get('feedback')]
    queue_origin = min(sequences)-1 if sequences else 0
    feedbacks = []
    for action in actions:
        feedback = action.get('feedback')
        item = {k: action[k] for k in ('request_id', 'source_event_index', 'kind', 'owner', 'tick', 'execution_status')}
        for k in ('card_id', 'x', 'y', 'hints'):
            if k in action: item[k] = action[k]
        if feedback:
            scheduler = deepcopy(feedback['scheduler_feedback'])
            scheduler.pop('generation', None); scheduler.pop('stateEpoch', None)
            scheduler['sequence'] -= queue_origin
            item['scheduler'] = scheduler
            item['consumed'] = feedback['card_consumed']
            item['consumption_status'] = feedback['consumption_status']
            item['actual_execution_tick'] = feedback['actual_execution_tick']
            event = deepcopy(feedback.get('card_event'))
            if event: event[0] -= origin
            item['card_event'] = event
            # These are not required for visibility but are real own runtime
            # semantics used by form-consumption attribution, so retain them.
            for key in ('before_registration', 'after_execution', 'own_boundary_consumption_evidence', 'effective_form_identity'):
                if key in feedback: item[key] = deepcopy(feedback[key])
        feedbacks.append(item)
    initialization = json.loads(local(episode['files']['initialization.json']['path']).read_text(encoding='utf-8'))
    packet = {'initialization': initialization, 'states': semantic_states, 'action_scheduler_and_events': feedbacks}
    return episode, packet


def compare_runs(paths):
    runs = [canonical_run(path) for path in paths]
    first_episode, expected = runs[0]
    records = []; hashes = []
    for path, (episode, actual) in zip(paths, runs):
        if episode['source_episode_id'] != first_episode['source_episode_id'] or episode['version_fingerprint'] != first_episode['version_fingerprint']:
            raise ValueError('Different episode/version cannot be a reset repeatability trial')
        sequence_hash = canonical_hash(actual); hashes.append(sequence_hash)
        record = {'path': local(path).relative_to(local('.')).as_posix(), 'sha256': file_hash(path),
                  'state_points': len(actual['states']), 'sequence_sha256': sequence_hash,
                  'native_generation': json.loads(local(episode['files']['states.jsonl']['path']).read_text(encoding='utf-8').splitlines()[0])['raw_capture']['generation']}
        if actual != expected:
            for index, (a, b) in enumerate(zip(expected['states'], actual['states'])):
                if a != b:
                    record.update(first_divergence_capture=index+1, first_divergence_tick=a['tick'],
                                  expected_state_sha256=canonical_hash(a), actual_state_sha256=canonical_hash(b)); break
            else:
                record['first_divergence_component'] = 'state_count' if len(expected['states']) != len(actual['states']) else 'initialization_or_action_scheduler'
        records.append(record)
    actual_resets = len({r['native_generation'] for r in records}) == len(records)
    identical_inputs = len({canonical_hash(packet['initialization']) for _, packet in runs}) == 1
    same = len(set(hashes)) == 1
    return {'source_episode_id': first_episode['source_episode_id'], 'version_fingerprint': first_episode['version_fingerprint'],
            'status': 'pass' if len(runs) >= 3 and same and actual_resets and identical_inputs else 'fail',
            'sequence_hashes': hashes, 'runs': records, 'independent_reset_generations': actual_resets,
            'identical_native_initialization': identical_inputs,
            'scope': 'All exposed minimal native semantic states/events and final scheduler receipts; not full memory or independent official fidelity',
            'normalization': 'generation/stateEpoch omitted; event and schedule monotonic counter origins shifted while preserving values/order/gaps',
            'not_available': ['unexposed native internal object memory', 'independent official trajectory reference']}

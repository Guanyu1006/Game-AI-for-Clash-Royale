"""Audit sealed research-console files without RPC, ADB, VM or networking."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.observability import build_observation
from royale_phase2.battlefield import build_battlefield_observation
from royale_phase2.platform import research_action
from royale_phase2.runtime import local
from royale_phase2.viewer import digest, inspect_mp4, semantic_packet, write_json
from royale_phase2.visibility import TowerAttestation, project_actor

REFERENCE = 'outputs/phase2/viewer/acceptance-001/headless/anchors.json'


def require(condition, message):
    if not condition:
        raise ValueError(message)


def read_json(path):
    return json.loads(local(path).read_text(encoding='utf-8-sig'))


def read_lines(path):
    return [json.loads(line) for line in local(path).read_text(encoding='utf-8-sig').splitlines() if line.strip()]


def sha(path):
    return hashlib.sha256(local(path).read_bytes()).hexdigest()


def human_requested_position(source):
    """Independent inverse of the documented pinned, measured HUD profile."""
    require(isinstance(source, dict) and set(source) <= {'u', 'v', 'x', 'y'},
            'Human placement source must contain normalized pointer or native coordinates')
    if 'u' in source or 'v' in source:
        for key in ('u', 'v'):
            require(type(source.get(key)) in (int, float) and math.isfinite(source[key]) and 0 <= source[key] <= 1,
                    'Human normalized placement source is invalid')
        px = source['u']*1080; py = source['v']*1920
        require(54 <= px <= 1026 and 282 <= py <= 1666, 'Human pointer is outside the measured arena catchment')
        # Padding clamps to the nearest logical cell. Neither this mapping nor
        # the native consumption event proves the final native placement.
        return (min(17, max(0, math.floor((px-111)*18/856)))*1000+500,
                min(31, max(0, math.floor((py-436)*32/1230)))*1000+500)
    for key, maximum in (('x', 17999), ('y', 31999)):
        require(type(source.get(key)) is int and 0 <= source[key] <= maximum,
                'Human native placement source is invalid')
    return source['x'], source['y']


def native_captures(value):
    if isinstance(value, dict):
        if value.get('schema') == 'cr-minimal.v4':
            yield value
        else:
            for child in value.values():
                yield from native_captures(child)
    elif isinstance(value, list):
        for child in value:
            yield from native_captures(child)


def audit_media(folder):
    """Independently re-read media bytes and receipts; never contact Android."""
    folder = local(folder)
    sealed = read_json(folder/'session.json')
    require(sealed.get('error') is None, 'Media session sealed with worker error')
    recording = read_json(folder/'recording.json')
    report = read_json(folder/'report.json')
    frames = read_lines(folder/'frames/index.jsonl')
    states = read_lines(folder/'states.jsonl')
    require(frames and states, 'No saved PNG/state sequence')
    origin = states[0]['raw_capture']['next']-1
    by_tick = {}
    for row in states:
        packet_sha = digest(semantic_packet(row['raw_capture'], row['all_observed_card_events'], origin))
        require(row['semantic_sha256'] == packet_sha, 'Media raw state semantic SHA mismatch')
        by_tick[row['raw_capture']['tick']] = packet_sha
    start = states[0]['raw_capture']['tick']; end = states[-1]['raw_capture']['tick']
    previous_tick = start
    for frame in frames:
        path = local(frame['path'])
        try: path.relative_to(folder/'frames')
        except ValueError: raise ValueError('PNG escapes session media directory') from None
        data = path.read_bytes()
        require(data.startswith(b'\x89PNG\r\n\x1a\n') and hashlib.sha256(data).hexdigest() == frame['sha256'],
                'PNG header or SHA256 differs from frame receipt')
        require(frame['bytes'] == len(data), 'PNG bytes differ from frame receipt')
        require(type(frame['tick']) is int and previous_tick <= frame['tick'] <= end, 'PNG ticks regress or exceed final state')
        require(by_tick.get(frame['tick']) == frame['native_semantic_sha256'], 'PNG tick/raw semantic binding differs')
        previous_tick = frame['tick']
    require(frames[0]['tick'] == start and frames[-1]['tick'] == end, 'PNG interval does not cover saved native interval')
    require(report['frames'] == len(frames), 'Report PNG count differs from frame index')
    require(recording.get('error') is None, 'Recorder reports an error')
    parts = recording.get('parts', [])
    require(isinstance(parts, list) and parts, 'No completed MP4 parts')
    require(report.get('recording', {}).get('parts') == parts, 'Report MP4 part receipts differ from recording.json')
    require(report['recording'].get('error') is None, 'Report recording has an error')
    videos = []; next_start = start
    for index, part in enumerate(parts, 1):
        path = local(part['path'])
        require(path.parent == folder and path.suffix.lower() == '.mp4', 'MP4 path escapes sealed session root')
        data = path.read_bytes(); inspected = inspect_mp4(data)
        require(inspected['valid_mp4'] is True, 'MP4 does not have duration-bearing ftyp/moov/mdat container')
        for key in ('bytes', 'sha256', 'valid_mp4', 'boxes', 'duration_seconds', 'avc1_codec_marker'):
            require(part.get(key) == inspected[key], 'MP4 receipt differs from actual bytes: ' + key)
        require(part.get('index') == index, 'MP4 part indices are not contiguous')
        require(type(part.get('start_tick')) is int and type(part.get('end_tick')) is int and
                part['start_tick'] == next_start <= part['end_tick'] <= end,
                'MP4 part native tick coverage has a gap, overlap, or invalid bound')
        next_start = part['end_tick']
        videos.append({'path': str(path.relative_to(ROOT)), **inspected,
                       'start_tick': part['start_tick'], 'end_tick': part['end_tick']})
    require(parts[0]['start_tick'] == start and next_start == end, 'MP4 receipts do not cover the full saved native interval')
    return {'schema': 'royale-platform-media-artifact-audit.v1', 'status': 'pass',
            'session': str(folder.relative_to(ROOT)), 'rpc_calls': 0,
            'native_terminal': states[-1]['raw_capture']['finalized'],
            'png_count': len(frames), 'png_hashes_and_raw_bindings_verified': True,
            'start_tick': start, 'end_tick': end, 'mp4_part_count': len(videos), 'mp4_parts': videos,
            'recording_receipt_sha256': sha(folder/'recording.json'),
            'frames_index_sha256': sha(folder/'frames/index.jsonl'),
            'report_sha256': sha(folder/'report.json'),
            'native_interval_seconds': (end-start)/20,
            'mp4_total_seconds': sum(part['duration_seconds'] for part in videos),
            'limitations': ['Video container validity and receipt consistency are checked; this is not a full pixel/codec decode audit.',
                           'Video uses wall time and includes pauses/capture overhead. Native tick receipt coverage does not prove frame-by-frame video/native synchronization.',
                           'Saved receipts and matching bytes do not independently authenticate engine provenance or official-game equivalence.']}


def audit_session(folder, reference=REFERENCE, require_terminal=False):
    folder = local(folder)
    # Written by worker finally, after final feedback/report: existence is the
    # console's seal, not an engine authenticity certificate.
    sealed = read_json(folder/'session.json')
    require(sealed.get('error') is None, 'Sealed session reports a worker error')
    report = read_json(folder/'report.json')
    require(report.get('schema') == 'phase2-research-platform-run.v1', 'Unexpected console report schema')
    mode = report['mode']
    require(mode in ('replay', 'agent', 'human'), 'Only native replay/agent/human sessions have wire evidence')
    states = read_lines(folder/'states.jsonl')
    observations = read_lines(folder/'observations.jsonl')
    wire = read_lines(folder/'wire.jsonl')
    frames = read_lines(folder/'frames/index.jsonl')
    journal = read_json(folder/'journal.json')
    feedback = read_json(folder/'execution_feedback.json')
    require(states and observations and frames and wire, 'Empty state/observation/frame/wire evidence')
    require(isinstance(feedback, list), 'Feedback must be the console action array')
    responses = [row.get('response') for row in wire if 'response' in row]
    response_hashes = {digest(row) for row in responses}
    captures = [raw for response in responses for raw in native_captures(response)]
    positions = defaultdict(list)
    for index, raw in enumerate(captures):
        positions[digest(raw)].append(index)
    require(captures, 'Wire has no native v4 capture responses')
    origin = states[0]['raw_capture']['next']-1
    generation = states[0]['raw_capture']['generation']
    cursor = -1
    events = {}
    by_tick = {}
    raw_hashes = set()
    entity_rows = 0
    for row in states:
        raw = row['raw_capture']
        build_observation(raw)
        require(raw['generation'] == generation, 'Session changes native generation')
        require(not by_tick or raw['tick'] >= max(by_tick), 'Native state ticks regress')
        matching = [index for index in positions[digest(raw)] if index >= cursor]
        require(matching, 'State has no ordered exact raw wire response at tick ' + str(raw['tick']))
        match = matching[0]
        for capture in captures[cursor+1:match+1]:
            require(capture['generation'] == generation, 'Wire capture generation changes')
            for event in capture['plays']:
                require(event[0] not in events or events[event[0]] == event, 'Conflicting native event sequence')
                events[event[0]] = event
        cursor = match
        cumulative = [events[key] for key in sorted(events)]
        require(row['all_observed_card_events'] == cumulative, 'Cumulative events do not match wire plays at tick ' + str(raw['tick']))
        packet = semantic_packet(raw, cumulative, origin)
        require(digest(packet) == row['semantic_sha256'], 'Raw state semantic SHA256 mismatch')
        if raw['tick'] in by_tick:
            require(by_tick[raw['tick']]['raw_capture'] == raw and
                    by_tick[raw['tick']]['all_observed_card_events'] == cumulative,
                    'Conflicting snapshots at one tick')
        by_tick[raw['tick']] = row
        raw_hashes.add(digest(raw)); entity_rows += len(raw['entities'])
    previous = None
    observed_hashes = set()
    observation_by_raw = defaultdict(list)
    for observation in observations:
        raw = observation['raw']
        require(digest(raw) in raw_hashes, 'Observation raw is absent from state log')
        rebuilt = build_observation(raw, previous)
        require(observation == rebuilt, 'Observation is not the lossless reproducible oracle projection')
        previous = raw
        observed_hashes.add(digest(raw))
        observation_by_raw[digest(raw)].append(observation)
    require(raw_hashes == observed_hashes, 'Some saved native states have no lossless observation')
    # The optional research-battlefield log is reconstructed from the wire-
    # bound raw state and cumulative events, never from its own claimed history.
    # Screenshot-only captures can repeat a raw state at the same tick without
    # another _accept, so coverage is by exact raw state rather than row count.
    battlefield_path = folder/'battlefield_observations.jsonl'
    battlefields = read_lines(battlefield_path) if battlefield_path.exists() else []
    battlefield_by_raw = defaultdict(list)
    if battlefield_path.exists():
        require(battlefields, 'Battlefield log is empty')
        reset_towers = TowerAttestation.from_reset(states[0]['raw_capture'])
        battlefield_hashes = set()
        previous_battlefield = None
        for battlefield in battlefields:
            require(isinstance(battlefield, dict), 'Battlefield observation must be an object')
            tick = battlefield.get('tick')
            require(type(tick) is int and tick in by_tick, 'Battlefield observation tick has no saved native state')
            row = by_tick[tick]; raw = row['raw_capture']
            require(battlefield.get('generation') == raw['generation'], 'Battlefield/native generation differs')
            actor = project_actor(raw, 0, reset_towers)
            rebuilt = build_battlefield_observation(raw, actor, row['all_observed_card_events'],
                                                    journal['actions'], previous_battlefield)
            require(digest(battlefield) == digest(rebuilt),
                    'Battlefield observation differs from independent raw/event reconstruction')
            previous_battlefield = rebuilt
            raw_hash = digest(raw)
            battlefield_hashes.add(raw_hash)
            battlefield_by_raw[raw_hash].append(rebuilt)
        require(raw_hashes == battlefield_hashes, 'Some saved native states have no reconstructed battlefield observation')
        require(digest(sealed.get('battlefield')) == digest(previous_battlefield),
                'Sealed battlefield differs from final reconstructed observation')
        require(previous_battlefield['tick'] == states[-1]['raw_capture']['tick'],
                'Battlefield log does not reach the final native state')
    else:
        require(sealed.get('battlefield') is None, 'Sealed battlefield requires its saved observation log')
    if mode == 'human':
        require(battlefield_path.exists(), 'Human AI decisions require a reconstructible battlefield log')
    for frame in frames:
        image = local(frame['path'])
        try:
            image.relative_to(folder/'frames')
        except ValueError:
            raise ValueError('Frame image escapes session frames directory') from None
        data = image.read_bytes()
        require(data.startswith(b'\x89PNG\r\n\x1a\n'), 'Frame is not a PNG')
        require(hashlib.sha256(data).hexdigest() == frame['sha256'], 'PNG SHA256 mismatch')
        require(frame.get('bytes') == len(data), 'PNG byte count mismatch')
        require(type(frame['tick']) is int and frame['tick'] in by_tick, 'Frame native tick has no raw state')
        require(frame['native_semantic_sha256'] == by_tick[frame['tick']]['semantic_sha256'], 'PNG/raw semantic binding mismatch')
    last = states[-1]['raw_capture']
    require(frames[0]['tick'] == states[0]['raw_capture']['tick'] and frames[-1]['tick'] == last['tick'],
            'Frames do not cover the saved native state interval')
    require(sealed['observation']['raw'] == last and sealed['tick'] == last['tick'], 'Sealed snapshot does not match final raw state')
    result = {key: last[key] for key in ('finalized', 'result', 'crowns', 'tick', 'generation')}
    require(report['native_result'] == result, 'Report native result differs from final raw state')
    require(report['complete_native_terminal'] == last['finalized'], 'Terminal claim differs from native state')
    if require_terminal:
        require(last['finalized'] is True, 'Natural native terminal required')
    actual = [events[key] for key in sorted(events)]
    require(sorted(journal['actual_card_events']) == actual, 'Journal event list differs from native wire events')
    actions = journal['actions']
    action_by_id = {action['request_id']: action for action in actions}
    require(len(action_by_id) == len(actions), 'Duplicate journal request identity')
    require(len(feedback) == len(actions), 'Feedback does not cover all registered actions')
    require({item['request_id'] for item in feedback} == set(action_by_id), 'Feedback request identities do not cover the journal')
    confirmed = []
    unconfirmed = []
    for item in feedback:
        request_id = item['request_id']
        require(request_id in action_by_id, 'Feedback has no registered journal action')
        action = action_by_id[request_id]
        for key in ('kind', 'owner', 'tick', 'generation', 'registration_receipt'):
            require(item.get(key) == action.get(key), 'Feedback/journal action mismatch: ' + key)
        require(digest(item['scheduler_feedback']) in response_hashes, 'Scheduler feedback has no raw wire response')
        if mode == 'human':
            require(all(digest(item.get(key)) == digest(value) for key, value in action.items()),
                    'Human-session feedback differs from complete journal action')
            status_command = 'replay-schedule-status ' + str(action['registration_receipt']['sequence'])
            require(any(row.get('command') == status_command and digest(row.get('response')) == digest(item['scheduler_feedback']) for row in wire),
                    'Human-session scheduler feedback has no exact status wire pair')
        if item.get('card_consumed') is True:
            event = item.get('card_event')
            require(isinstance(event, list) and len(event) == 5 and events.get(event[0]) == event,
                    'Claimed consumption has no native wire event')
            require(event[1] == item['tick'] == item['actual_execution_tick'] and event[2] == item['owner'],
                    'Claimed consumption tick/owner mismatch')
            require(item['scheduler_feedback'].get('state') == 'succeeded', 'Claimed consumption injection failed')
            confirmed.append(event[0])
        else:
            candidates = [event for event in actual if event[1] == item['tick'] and event[2] == item['owner']]
            category = ('single_opaque_zero_event_id' if len(candidates) == 1 and candidates[0][3] == 0 else
                        'single_event_id_differs_from_requested_root' if len(candidates) == 1 and candidates[0][3] != item.get('card_id') else
                        'no_same_owner_tick_event' if not candidates else 'other_unconfirmed_identity_or_boundary')
            unconfirmed.append({'request_id': request_id, 'tick': item['tick'], 'owner': item['owner'],
                                'requested_card_id': item.get('card_id'), 'native_candidates': candidates,
                                'scheduler_state': item['scheduler_feedback'].get('state'),
                                'before_registration_tick': item.get('before_registration', {}).get('tick'),
                                'own_boundary_evidence': item.get('own_boundary_consumption_evidence'),
                                'consumption_status': item.get('consumption_status'),
                                'observed_condition': category})
    require(len(confirmed) == len(set(confirmed)), 'One native consumption event is attributed to multiple actions')
    decisions_path = folder/'decisions.jsonl'
    decisions = read_lines(decisions_path) if decisions_path.exists() else []
    registrations = 0
    human_path = folder/'human_actions.jsonl'
    human_actions = read_lines(human_path) if human_path.exists() else []
    human_registrations = 0
    request_controller = {}

    def bind_registration(action, tick, owner, chosen, controller, *, strict_wire=False):
        receipt = action['registration_receipt']
        require(action['owner'] == owner and action['kind'] == 'play_card',
                'Action registration controller/owner/kind differs')
        require(action['tick'] == tick+1 == receipt['executeTick'] and receipt['registeredAtTick'] == tick,
                'Chosen action registration is not the next native tick')
        require(action['generation'] == generation, 'Registered action generation differs from native state')
        require(action.get('card_id') == chosen['card'] and action.get('x') == chosen['x'] and action.get('y') == chosen['y'],
                'Registration differs from chosen card or coordinates')
        request_id = action['request_id']
        require(request_id in action_by_id, 'Action registration absent from journal')
        saved = action_by_id[request_id]
        for key in ('kind', 'owner', 'card_id', 'x', 'y', 'tick', 'generation', 'registration_receipt'):
            require(digest(saved.get(key)) == digest(action.get(key)), 'Action/journal registration differs: ' + key)
        if 'before_registration' in action:
            require(digest(saved.get('before_registration')) == digest(action['before_registration']),
                    'Action/journal before-registration capture differs')
        require(digest(receipt) in response_hashes, 'Registration receipt has no raw wire response')
        if strict_wire:
            command = f'replay-schedule-card {owner} {chosen["card"]} {chosen["x"]} {chosen["y"]} {tick+1}'
            require(any(row.get('command') == command and digest(row.get('response')) == digest(receipt) for row in wire),
                    'Action command and registration receipt have no exact raw wire pair')
            raw = by_tick[tick]['raw_capture']
            player = next(player for player in raw['players'] if player['owner'] == owner)
            before = {'tick': tick, 'hand': player['hand'], 'cycle': player['cycle'], 'elixir': player['elixir']}
            require(digest(action.get('before_registration')) == digest(before),
                    'Action before-registration capture differs from raw state')
        require(request_id not in request_controller, 'Action appears in multiple decision/human records')
        request_controller[request_id] = controller

    if mode in ('agent', 'human'):
        towers = TowerAttestation.from_reset(states[0]['raw_capture'])
        for index, decision in enumerate(decisions):
            tick = decision['tick']
            require(decision['decision_index'] == index and tick in by_tick, 'Decision index/tick lacks saved state')
            raw = by_tick[tick]['raw_capture']
            actor = project_actor(raw, 0, towers)
            offered = decision['policy_input']
            if mode == 'human':
                require(decision['policy_information_mode'] == 'battlefield', 'Human AI must use battlefield policy input')
                require(digest(decision['opponent_action']) == digest({'kind': 'wait'}),
                        'Human mode cannot include an automated opponent action')
            if decision['policy_information_mode'] == 'player':
                require(offered == actor, 'Player policy input differs from conservative actor')
            elif decision['policy_information_mode'] == 'battlefield':
                require(battlefield_path.exists(), 'Battlefield policy input requires its saved observation log')
                require(any(digest(offered) == digest(battlefield) for battlefield in battlefield_by_raw[digest(raw)]),
                        'Battlefield policy input differs from the independently reconstructed observation')
            else:
                require(decision['policy_information_mode'] == 'oracle' and
                        any(offered == observation for observation in observation_by_raw[digest(raw)]),
                        'Oracle policy input differs from the lossless reconstructed observation')
            expected = []
            for owner, controller, key in ((0, 'model', 'model_action'), (1, 'public_heuristic', 'opponent_action')):
                action = decision[key]
                normalized = research_action(action, project_actor(raw, owner, towers))
                if normalized['kind'] == 'play_card':
                    require(action.get('card') == normalized['card'], 'Chosen action card differs from current hand slot')
                    expected.append((owner, controller, normalized))
            require(len(expected) == len(decision['registrations']), 'Decision registration count differs from chosen plays')
            for (owner, controller, chosen), record in zip(expected, decision['registrations']):
                action = record['registration']
                require(record['controller'] == controller and action['owner'] == owner, 'Decision registration controller/owner differs')
                bind_registration(action, tick, owner, chosen, controller, strict_wire=mode == 'human')
                registrations += 1
        if mode == 'human':
            previous_human_tick = -1
            previous_execute_tick = -1
            for index, row in enumerate(human_actions):
                tick = row['tick']
                require(type(row['human_action_index']) is int and row['human_action_index'] == index and
                        type(tick) is int and tick in by_tick and tick >= previous_human_tick,
                        'Human action index/tick lacks an ordered saved state')
                require(tick >= previous_execute_tick, 'A human action is registered before the preceding request executes')
                raw = by_tick[tick]['raw_capture']
                actor = project_actor(raw, 1, towers)
                require(digest(row['actor_before']) == digest(actor), 'Human actor_before differs from conservative owner1 raw projection')
                chosen = research_action(row['action'], actor)
                require(chosen['kind'] == 'play_card' and digest(chosen) == digest(row['action']),
                        'Human action is not the normalized current-slot play')
                require(chosen['card'] >= 28000000 or chosen['y'] >= 16000,
                        'Human ordinary-card request violates demo lower-half placement restriction')
                require(human_requested_position(row['placement_source']) == (chosen['x'], chosen['y']),
                        'Human requested position differs from its saved placement source')
                action = row['registration']
                require(action['request_id'] == f'platform:human:{index}', 'Human registration request identity differs from its index')
                bind_registration(action, tick, 1, chosen, 'human', strict_wire=True)
                previous_human_tick = tick; previous_execute_tick = action['tick']
                human_registrations += 1
        require(registrations + human_registrations == len(actions), 'Some agent/human journal actions have no recorded decision or human request')
    anchors_result = None
    if mode == 'replay':
        stored = read_json(folder/'anchors.json'); baseline = read_json(reference)
        expected = baseline['expected_anchors']
        require(len(expected) == 5 and stored['expected_anchors'] == expected, 'Replay requires the same five reference anchors')
        for key in ('version_fingerprint', 'initialization_sha256', 'journal_sha256'):
            require(stored[key] == baseline[key], 'Replay reference input differs: ' + key)
        # A manually closed/budgeted nonterminal replay can be audited without
        # pretending it reached the reference's future anchors. Completed native
        # replays and --require-terminal retain the full five-point requirement.
        compared_ticks = expected if require_terminal or last['finalized'] else [tick for tick in expected if tick <= last['tick']]
        require(compared_ticks, 'Bounded replay has not reached a reference anchor')
        hashes = {}
        for tick in compared_ticks:
            require(tick in by_tick, 'Replay missing raw anchor ' + str(tick))
            require(str(tick) in stored['anchors'], 'Replay missing stored anchor ' + str(tick))
            row = by_tick[tick]; entry = stored['anchors'][str(tick)]; reference_entry = baseline['anchors'][str(tick)]
            packet = semantic_packet(row['raw_capture'], row['all_observed_card_events'], origin)
            require(entry['raw_capture'] == row['raw_capture'] and entry['all_observed_card_events'] == row['all_observed_card_events'],
                    'Stored replay anchor differs from raw log')
            require(entry['semantic_packet'] == packet and entry['semantic_sha256'] == digest(packet), 'Replay anchor packet/hash differs')
            require(reference_entry['semantic_packet'] == packet and reference_entry['semantic_sha256'] == digest(packet),
                    'Replay differs from independent saved headless anchor ' + str(tick))
            hashes[str(tick)] = digest(packet)
        anchors_result = {'reference': str(local(reference).relative_to(ROOT)), 'sha256': sha(reference),
                          'five_points_equal': len(hashes) == 5,
                          'compared_points_equal': True, 'compared_anchors': compared_ticks,
                          'not_yet_reached_anchors': [tick for tick in expected if tick > last['tick']],
                          'comparison_scope': 'full_five_anchor_comparison' if len(hashes) == 5 else 'bounded_partial_reference_comparison',
                          'semantic_sha256': hashes}
    for key, expected in (('frames', len(frames)), ('decisions', len(decisions)),
                          ('actual_card_events', len(actual)), ('submitted_actions', len(actions)),
                          ('consumed_actions', len(confirmed))):
        require(report[key] == expected, 'Report count differs from raw artifacts: ' + key)
    if mode == 'human':
        require(report.get('human_actions') == len(human_actions), 'Report human action count differs from saved requests')
        require(report.get('human_owner') == 1 and report.get('ai_owner') == 0, 'Report human/AI ownership differs')
    input_files = ['session.json', 'report.json', 'states.jsonl', 'wire.jsonl', 'observations.jsonl',
                   'frames/index.jsonl', 'journal.json', 'execution_feedback.json']
    if decisions_path.exists(): input_files.append('decisions.jsonl')
    if battlefield_path.exists(): input_files.append('battlefield_observations.jsonl')
    if human_path.exists(): input_files.append('human_actions.jsonl')
    return {'schema': 'royale-platform-artifact-audit.v1', 'status': 'pass',
            'session': str(folder.relative_to(ROOT)), 'mode': mode, 'rpc_calls': 0,
            'native_terminal': last['finalized'], 'truncated': report.get('truncated'),
            'start_tick': states[0]['raw_capture']['tick'], 'last_tick': last['tick'],
            'state_rows': len(states), 'raw_wire_captures': len(captures), 'wire_rows': len(wire),
            'entity_rows': entity_rows, 'lossless_observations': len(observations), 'pngs_verified': len(frames),
            'battlefield_observations': len(battlefields),
            'battlefield_observations_verified': battlefield_path.exists(),
            'battlefield_policy_decisions': sum(row.get('policy_information_mode') == 'battlefield' for row in decisions),
            'battlefield_final': ({'tick': previous_battlefield['tick'],
                'non_tower_entities': len(previous_battlefield['entities']),
                'history_events': len(previous_battlefield['history']),
                'requested_position_annotations': sum(row['requested_position'] is not None for row in previous_battlefield['history']),
                'actual_positions_observed': sum(row['actual_position'] is not None for row in previous_battlefield['history']),
                'visibility_fairness_verified': previous_battlefield['visibility_fairness_verified']}
                if battlefields else None),
            'native_events': len(actual), 'registered_actions': len(actions), 'decisions': len(decisions),
            'decision_registrations_verified': registrations, 'confirmed_consumptions_cross_checked': len(confirmed),
            'human_actions_verified': human_registrations,
            'registered_by_controller': dict(Counter(request_controller.values())),
            'model_decision_kinds': dict(Counter(row['model_action']['kind'] for row in decisions)),
            'confirmed_by_controller': dict(Counter(request_controller.get(item['request_id'], 'preregistered_replay')
                                                    for item in feedback if item.get('card_consumed') is True)),
            'feedback_statuses': dict(Counter(item.get('consumption_status', 'missing') for item in feedback)),
            'unconfirmed_consumptions': {'count': len(unconfirmed), 'observed_conditions': dict(Counter(row['observed_condition'] for row in unconfirmed)),
                                        'actions': unconfirmed,
                                        'interpretation': 'Observed identity/boundary conditions, not proved runtime failure causes. Same-tick native events or successful injection do not by themselves prove requested root-card consumption.'},
            'replay_reference': anchors_result, 'input_sha256': {name: sha(folder/name) for name in input_files},
            'limitations': ['File consistency audit, not independent engine authenticity or official-game equivalence.',
                           'Native consumption events have no attributed attack/damage causal history.',
                           'Battlefield entity visibility remains unverified; request-position annotations are not native final placement evidence.',
                           'Confirmed consumption summary cross-checks saved backend proof with wire event, owner and tick; it does not re-prove all placement or evolution rules.']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('session')
    parser.add_argument('--reference', default=REFERENCE)
    parser.add_argument('--require-terminal', action='store_true')
    parser.add_argument('--output')
    parser.add_argument('--media-output', help='Additionally validate PNG/MP4 bytes against the sealed recording receipts')
    args = parser.parse_args()
    output = args.output or 'outputs/platform_next/checks/' + local(args.session).name + '/artifact_audit.json'
    try:
        result = audit_session(args.session, args.reference, args.require_terminal)
    except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
        result = {'schema': 'royale-platform-artifact-audit.v1', 'status': 'fail',
                  'session': str(local(args.session).relative_to(ROOT)), 'rpc_calls': 0,
                  'error': type(exc).__name__ + ': ' + str(exc)}
    write_json(output, result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.media_output:
        try:
            media = audit_media(args.session)
        except (ValueError, KeyError, TypeError, OSError, IndexError) as exc:
            media = {'schema': 'royale-platform-media-artifact-audit.v1', 'status': 'fail',
                     'rpc_calls': 0, 'error': type(exc).__name__ + ': ' + str(exc)}
        write_json(args.media_output, media)
        print(json.dumps(media, ensure_ascii=False, indent=2))
        if media['status'] != 'pass': return 1
    return 0 if result['status'] == 'pass' else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Uninstalled adapter candidate: independently audit exact 4->3 pending draw.

Reads saved native artifacts only. Never imports a backend, opens RPC, rewrites
an episode, promotes quality, or supplies training rows.
"""
from __future__ import annotations
import argparse
from copy import deepcopy
import csv
import gzip
import io
import json
import lzma
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import (TrajectoryError, canonical_bytes, file_sha, project_path,
                                       read_json, read_jsonl, strict_int, verify_reference, write_json)
from royale_phase2.versioning import verify_manifest


def locked_sources(manifest):
    """Resolve name joins and Ladder integer regeneration from pinned bytes."""
    assets = {item['path'].replace('\\', '/'): item for item in manifest['assets']}
    evidence = []
    def data(path):
        reference = {'path': path, 'sha256': assets[path]['sha256']}
        verify_reference(reference); evidence.append(reference)
        return project_path(path).read_bytes()
    def rows(name):
        encoded = data('third_party/phase2/runtime-assets/full-update/csv_logic/'+name+'.csv')
        plain = encoded if encoded.startswith(b'"Name"') else lzma.decompress(
            encoded[:9]+b'\0'*4+encoded[9:], format=lzma.FORMAT_ALONE)
        return list(csv.DictReader(io.StringIO(plain.decode('utf-8-sig'))))[1:]
    evolved = [row for row in rows('spells_evolved') if row.get('Name')]
    if len({row['Name'] for row in evolved}) != len(evolved):
        raise TrajectoryError('Ambiguous evolved source names')
    names = {row['Name']: 13000000+index for index, row in enumerate(evolved)}
    specs = json.loads(gzip.decompress(data('third_party/phase2/firstlight/native_runner/data/competitive/card_specs.json.gz')))['specs']
    aliases = {}
    for spec in specs:
        name = (spec.get('evolution') or {}).get('evolution_form_id')
        if name in names:
            aliases[spec['card_id']] = {'kind': 'evolution', 'form_name': name, 'event_card_id': names[name]}
    modes = [row for row in rows('game_modes') if row.get('Name')]
    if modes[6]['Name'] != 'Ladder' or modes[6]['BattleTimeline'] != 'Default':
        raise TrajectoryError('Unverified mode-to-timeline mapping')
    timeline = []; current = None; tick = 0
    for row in rows('battle_timelines'):
        if row.get('Name'): current = row['Name']; tick = 0
        if current != 'Default': continue
        duration, full_bar = row.get('ElixirRateLength'), row.get('ElixirFullBarMS')
        if duration and full_bar:
            duration = int(duration)*20; full_bar = int(full_bar)
            if duration <= 0 or full_bar <= 0: raise TrajectoryError('Unsupported Default phase convention')
            timeline.append((tick, tick+duration, 100000*50//full_bar)); tick += duration
    return {'aliases': aliases, 'mode': 72000006, 'timeline': timeline, 'evidence': evidence}


def candidate_confirmation(action, before, after, events, source_actions, match, sources):
    """Re-derive consumption; ignores all card_consumed/kind/version booleans."""
    owner = strict_int(action['owner'], 'owner', 0, 1)
    tick = strict_int(action['tick'], 'execution tick', 1)
    root = strict_int(action['card_id'], 'root card', 1)
    if action['kind'] != 'play_card' or match.get('cmd'):
        raise TrajectoryError('Unsupported kind or embedded commands')
    competing = [row for row in source_actions if row['owner'] == owner and row['tick'] == tick]
    candidates = [event for event in events if event[1] == tick and event[2] == owner]
    if len(competing) != 1 or len(candidates) != 1:
        raise TrajectoryError('Not one source command and one actual owner/tick CardPlay')
    event = candidates[0]
    if len(event) != 5 or before['tick'] != tick-1 or after['tick'] != tick:
        raise TrajectoryError('Missing exact action boundary')
    snapshots = []
    for raw in (before, after):
        players = [row for row in raw['players'] if row['owner'] == owner]
        if len(players) != 1: raise TrajectoryError('Ambiguous raw owner')
        player = players[0]
        snapshots.append({'tick': raw['tick'], 'hand': player['hand'], 'cycle': player['cycle'], 'elixir': player['elixir']})
    pre, post = snapshots
    if pre != action['before_registration'] or post != action['after_execution']:
        raise TrajectoryError('Saved own snapshots differ from raw native captures')
    selected = [row for row in pre['hand'] if row[1] == root]
    if len(selected) != 1: raise TrajectoryError('Ambiguous root in actual pre-hand')
    card = selected[0]
    if (len(pre['hand']) != 4 or {row[0] for row in pre['hand']} != {0, 1, 2, 3}
            or len(post['hand']) != 3
            or sorted(post['hand']) != sorted(row for row in pre['hand'] if row[0] != card[0])
            or len(pre['cycle']) != 4 or post['cycle'] != pre['cycle']+[root]):
        raise TrajectoryError('Not exact selected-slot removal with unchanged other rows and appended root')
    receipt = action['scheduler_feedback']; registered = action['registration_receipt']
    if (receipt.get('ok') is not True or receipt.get('state') != 'succeeded'
            or receipt.get('owner') != owner or receipt.get('cardId') != root
            or receipt.get('executeTick') != tick or receipt.get('registeredAtTick') != tick-1
            or receipt.get('resolvedCommandCardId') != root or receipt.get('resolvedCardParameter') != card[3]
            or receipt.get('sequence') != registered.get('sequence') or registered.get('ok') is not True
            or registered.get('registeredAtTick') != tick-1 or registered.get('executeTick') != tick
            or registered.get('generation') != before.get('generation')
            or any(receipt.get('generation') != raw.get('generation') for raw in (before, after))
            or event[4] != card[2]):
        raise TrajectoryError('Exact native receipt/parameter/cost/generation corroboration failed')
    if match.get('battle', {}).get('gamemode') != sources['mode']:
        raise TrajectoryError('No measured regeneration rule for this mode')
    rates = [rate for start, end, rate in sources['timeline'] if start < tick < end and start <= tick-1 < end]
    if len(rates) != 1: raise TrajectoryError('Unknown regeneration or exact phase boundary')
    fee = strict_int(card[2], 'cost', 0, 100)*10000
    available = strict_int(pre['elixir'], 'before elixir', 0, 100000)
    if available < fee or post['elixir'] != min(100000, available-fee+rates[0]):
        raise TrajectoryError('Exact fee plus source-defined native integer regeneration failed')
    if event[3] == root:
        form = {'kind': 'native_event_id', 'card_id': root}
    elif event[3] == 0:
        form = 'unknown'
    else:
        form = sources['aliases'].get(root)
        if not form or form['event_card_id'] != event[3]:
            raise TrajectoryError('Non-root event lacks exact pinned source-name evolution join')
    return {'kind': 'candidate_pending_draw_root_consumption', 'input_root_card': root,
            'raw_native_event_card': event[3], 'effective_form_identity': form, 'owner': owner,
            'tick': tick, 'expected_regeneration_raw': rates[0], 'source_evidence': sources['evidence'],
            'integrated_into_exporter': False, 'native_fresh_run_validated': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', default='outputs/phase2/batches/formal100-v1/91e21faee1ae4abbb424e759585bf2f0/attempt-01/episode.json')
    parser.add_argument('--source-event-index', type=int, default=26)
    parser.add_argument('--version-manifest', default='data/phase2/version_manifest.json')
    parser.add_argument('--output', default='outputs/phase2/learning_contracts/pending_draw_adapter_candidate.json')
    args = parser.parse_args()
    path = project_path(args.episode); report = read_json(path); directory = path.parent
    names = ['episode.json', 'states.jsonl', 'actions.json', 'journal.json', 'wire.jsonl']
    references = [{'path': (directory/name).relative_to(ROOT).as_posix(), 'sha256': file_sha(directory/name)} for name in names]
    actions = read_json(directory/'actions.json')['actions']; journal = read_json(directory/'journal.json')
    states = read_jsonl(directory/'states.jsonl'); wire = read_jsonl(directory/'wire.jsonl')
    rows = [row for row in actions if row['source_event_index'] == args.source_event_index]
    if len(rows) != 1: raise TrajectoryError('Expected one requested source action')
    row = rows[0]; action = row['feedback']; tick = row['tick']
    captures = {item['raw_capture']['tick']: item['raw_capture'] for item in states}
    before, after = captures[tick-1], captures[tick]
    unique_events = {}
    for item in states:
        for event in item['raw_capture']['plays']:
            if event[0] in unique_events and unique_events[event[0]] != event: raise TrajectoryError('Conflicting raw event sequence')
            unique_events[event[0]] = event
    source = verify_manifest(args.version_manifest)
    if source['fingerprint'] != report['version_fingerprint']: raise TrajectoryError('Actual version mismatch')
    sources = locked_sources(source['manifest'])
    result = candidate_confirmation(action, before, after, list(unique_events.values()), actions, journal['match'], sources)
    schedule_command = f"replay-schedule-card {action['owner']} {action['card_id']} {action['x']} {action['y']} {tick}"
    status_command = 'replay-schedule-status '+str(action['registration_receipt']['sequence'])
    for command, response in [(schedule_command, action['registration_receipt']), (status_command, action['scheduler_feedback'])]:
        if not any(item['command'] == command and item.get('response') == response for item in wire):
            raise TrajectoryError('Receipt is not present in actual wire response')
    for capture in (before, after):
        if not any(item.get('response') == capture for item in wire): raise TrajectoryError('Raw capture not in actual wire')
    controls = []
    def negative(name, mutate=None, events=None, commands=None, state_before=None, state_after=None, rules=None):
        changed = deepcopy(action)
        if mutate: mutate(changed)
        try:
            candidate_confirmation(changed, before if state_before is None else state_before,
                                   after if state_after is None else state_after,
                                   list(unique_events.values()) if events is None else events,
                                   actions if commands is None else commands, journal['match'], sources if rules is None else rules)
        except (KeyError, ValueError, TypeError, OSError):
            controls.append({'name': name, 'rejected': True}); return
        raise TrajectoryError('Negative control accepted: '+name)
    negative('self_reported_bool_cannot_repair_bad_parameter', lambda item: (item.update(card_consumed=True), item['scheduler_feedback'].update(resolvedCardParameter=0)))
    negative('wrong_receipt_owner', lambda item: item['scheduler_feedback'].update(owner=1-action['owner']))
    negative('wrong_receipt_root', lambda item: item['scheduler_feedback'].update(resolvedCommandCardId=1))
    negative('wrong_receipt_sequence', lambda item: item['scheduler_feedback'].update(sequence=1))
    negative('wrong_registration_generation', lambda item: item['registration_receipt'].update(generation=-1))
    negative('failed_receipt', lambda item: item['scheduler_feedback'].update(state='failed'))
    negative('missing_event', events=[])
    events = list(unique_events.values()); target = next(event for event in events if event[1:3] == [tick, action['owner']])
    negative('duplicate_owner_tick_event', events=events+[target])
    negative('second_same_owner_tick_command', commands=actions+[row])
    changed = deepcopy(after); next(p for p in changed['players'] if p['owner'] == action['owner'])['elixir'] += 1
    negative('one_raw_elixir_unit_wrong', state_after=changed,
             mutate=lambda item: item['after_execution'].update(elixir=item['after_execution']['elixir']+1))
    changed = deepcopy(after); next(p for p in changed['players'] if p['owner'] == action['owner'])['hand'][0][3] += 1
    negative('unrelated_hand_row_mutation', state_after=changed,
             mutate=lambda item: item['after_execution']['hand'][0].__setitem__(3, item['after_execution']['hand'][0][3]+1))
    wrong = deepcopy(sources); wrong['aliases'][action['card_id']]['event_card_id'] += 1
    negative('wrong_source_name_alias', rules=wrong)
    assert all(file_sha(project_path(reference['path'])) == reference['sha256'] for reference in references)
    output = {'schema': 'royale-phase2-pending-draw-adapter-review.v1', 'native_rpc_calls': 0,
              'original_episode_modified': False, 'original_quality': report['quality'],
              'source_episode_id': report['source_episode_id'], 'source_event_index': args.source_event_index,
              'candidate': result, 'wire_binding_verified': True, 'negative_controls': controls,
              'evidence': references, 'tool_sha256': file_sha(Path(__file__)),
              'scope': 'Independent read-only candidate check on old raw evidence; not new native validation, quality repair, or training eligibility.'}
    write_json(args.output, output)
    print(json.dumps({'candidate_supported_by_saved_raw_evidence': True, 'negative_controls': len(controls),
                      'original_runtime': report['quality']['runtime_replayable'], 'rpc_calls': 0}, ensure_ascii=False))


if __name__ == '__main__': main()

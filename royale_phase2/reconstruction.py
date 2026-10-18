"""Real source-journal reconstruction through the pinned native backend.

Initialization hypotheses are bounded and selected without outcome feedback.
Actions are submitted just before their target boundary so training captures
precede both submission and consumption. No source action is removed or moved.
"""
from collections import Counter
from copy import deepcopy
from pathlib import Path
import json
import math
import os
import sys
import time
import traceback
import ctypes

from .runtime import ROOT, configure, local
from .versioning import canonical_hash, file_hash
from .dataset import require_lossless_source_levels, write_json
from .quality import compare_outcome
from .visibility import affordability_mask


def process_resources():
    """Read the actual Python worker, not Windows venv's launcher process."""
    if sys.platform != 'win32': return {'available': False}
    class Memory(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [(n, ctypes.c_size_t) for n in
                    ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
                     'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    class FileTime(ctypes.Structure):
        _fields_ = [('low', ctypes.c_ulong), ('high', ctypes.c_ulong)]
    class IO(ctypes.Structure):
        _fields_ = [(n, ctypes.c_ulonglong) for n in ('ReadOperationCount', 'WriteOperationCount', 'OtherOperationCount',
                                                     'ReadTransferCount', 'WriteTransferCount', 'OtherTransferCount')]
    handle = ctypes.c_void_p(-1); memory = Memory(); memory.cb = ctypes.sizeof(memory)
    times = [FileTime() for _ in range(4)]; io = IO()
    ok = ctypes.windll.psapi.GetProcessMemoryInfo(handle, ctypes.byref(memory), memory.cb)
    ok &= ctypes.windll.kernel32.GetProcessTimes(handle, *[ctypes.byref(t) for t in times])
    ok &= ctypes.windll.kernel32.GetProcessIoCounters(handle, ctypes.byref(io))
    return {'available': bool(ok), 'pid': os.getpid(), 'peak_working_set_bytes': memory.PeakWorkingSetSize,
            'cpu_kernel_seconds': ((times[2].high << 32) + times[2].low)/1e7,
            'cpu_user_seconds': ((times[3].high << 32) + times[3].low)/1e7,
            'io_read_bytes': io.ReadTransferCount, 'io_write_bytes': io.WriteTransferCount,
            'scope': 'actual Python worker process lifetime; guest native CPU/memory measured separately by batch queue'}


def load_inputs(source_id):
    pools = [
        ('data/phase2/frozen_manifest.json', 'outputs/phase2/data/firstlight_preflight.json'),
        ('data/phase2/learning/additional_source_manifest.json', 'outputs/phase2/data/firstlight_learning_extension_preflight.json'),
    ]
    entry = None
    for manifest_path, preflight_path in pools:
        if not local(manifest_path).is_file(): continue
        frozen = json.loads(local(manifest_path).read_text(encoding='utf-8'))
        entry = next((e for e in frozen['entries'] if e['source_episode_id'] == source_id), None)
        if entry is not None:
            entry = {**entry, 'frozen_manifest': manifest_path, 'frozen_manifest_sha256': file_hash(manifest_path)}
            break
    if entry is None: raise ValueError('Source ID is not in any predeclared frozen source pool')
    preflight = json.loads(local(preflight_path).read_text(encoding='utf-8'))
    conversion = next(e for e in preflight['episodes'] if e['source_episode_id'] == source_id)
    if conversion.get('conversion_status') != 'pass': raise ValueError('Source conversion did not pass')
    if file_hash(conversion['hypothesis']) != conversion['hypothesis_sha256']: raise ValueError('Reconstruction hypothesis changed')
    source = json.loads(local(entry['episode']).read_text(encoding='utf-8'))
    hypothesis = json.loads(local(conversion['hypothesis']).read_text(encoding='utf-8'))
    return entry, source, hypothesis, conversion


def native_match(hypothesis):
    """Use the pinned upstream serializer with its declared source mapping."""
    firstlight = local('third_party/phase2/firstlight')
    os.environ['CR_WORKSPACE_ROOT'] = str(firstlight)
    os.environ['CR_COMPETITIVE_DATA_ROOT'] = str(firstlight / 'native_runner/data/competitive')
    sys.path.insert(0, str(firstlight))
    from native_runner.match_factory import MatchConfig
    config = hypothesis['upstream_replay']['episode_config']; tags = config['tags']
    kwargs = {k: config[k] for k in ('deck0', 'deck1', 'seed', 'game_mode', 'arena')}
    for name, default in [('location', 15000199), ('level_cap', 0), ('minimum_card_level', 0),
                          ('king_tower_level', None), ('tower_troop0_id', 159000000), ('tower_troop1_id', 159000000),
                          ('deck0_form_availability', [0]*8), ('deck1_form_availability', [0]*8),
                          ('owner0_name', 'Local-0'), ('owner1_name', 'Local-1')]:
        kwargs[name] = tags.get(name, default)
    # This is a scene watchdog, never the source result/duration injected as
    # the terminal condition. Native game-mode rules produce the actual end.
    kwargs['end_tick'] = 7200
    return MatchConfig(**kwargs).to_replay_dict()


def source_commands(source_id, hypothesis):
    result = []
    for command in hypothesis['commands']:
        index = command['source_event_index']
        row = {'request_id': f'{source_id}:{index}', 'source_event_index': index,
               'kind': command['kind'], 'owner': command['owner'], 'tick': command['target_engine_tick'],
               'source_side': command['source_side'], 'source_command': command}
        if row['kind'] == 'play_card':
            grid = command['target_grid']; offset = command.get('subcell_offset') or [0, 0]
            xy = [int(grid[i])*1000+500+int(round(offset[i]*1000)) for i in (0, 1)]
            if any(not math.isfinite(value) for value in xy): raise ValueError('Nonfinite converted coordinate')
            # Exact upstream BattleEnv._world_target rule, retained explicitly.
            row.update(card_id=command['card_id'], x=min(max(xy[0], 0), 17999), y=min(max(xy[1], 0), 31999),
                       converted_unclipped_xy=xy, coordinate_rule='pinned FirstLight cell center+subcell, native bounds clamp')
        elif row['kind'] == 'activate_ability':
            row['hints'] = command['metadata'].get('ability_runtime_hints', [])
        else: raise ValueError('Unsupported source action; do not silently skip')
        result.append(row)
    if [r['source_event_index'] for r in result] != list(range(len(result))):
        raise ValueError('Converted source action order changed')
    if any(b['tick'] < a['tick'] for a, b in zip(result, result[1:])):
        raise ValueError('Source action time is nonmonotonic')
    return result


def align_initial_deal(backend, match, hypothesis, folder, max_attempts=8):
    """Preserve one preselected whole-sequence-compatible deal, not search wins."""
    attempts = []; seen = set(); configured = deepcopy(match)
    wanted = [(list(hypothesis['opening_cards_hypothesis'][owner]), list(hypothesis['cycle_hypothesis'][owner])) for owner in (0, 1)]
    for attempt in range(1, max_attempts+1):
        state = backend.reset(configured)
        observed = {}
        for player in state['players']:
            observed[player['owner']] = ([card[1] for card in sorted(player['hand'], key=lambda c: c[0])], list(player['cycle']))
        row = {'attempt': attempt, 'match': deepcopy(configured), 'observed': observed, 'target': wanted,
               'reset_tick': state['tick'], 'generation': state['generation'],
               'selection_basis': 'fixed selected public-action-compatible deal only; no result/crowns/HP used'}
        attempts.append(row); write_json(folder / 'initialization_attempts.json', attempts)
        if all(observed.get(owner) == wanted[owner] for owner in (0, 1)):
            write_json(folder / 'initialization.json', configured)
            return state, configured, attempts
        signature = canonical_hash(configured)
        if signature in seen: raise ValueError('Initial-deal calibration cycled; no source action changed')
        seen.add(signature)
        updated = deepcopy(configured)
        for owner in (0, 1):
            opening, cycle = observed[owner]
            deck = configured['battle'][f'deck{owner}']['sp']
            by_id = {card['d']: card for card in deck}
            deck_ids = [card['d'] for card in deck]
            if len(opening) != 4 or len(cycle) != 4 or set(opening+cycle) != set(deck_ids):
                raise ValueError('Native initial hand/cycle does not expose a unique 4+4 deal')
            result = [None]*8
            for actual, desired in zip(opening+cycle, wanted[owner][0]+wanted[owner][1]):
                result[deck_ids.index(actual)] = deepcopy(by_id[desired])
            if any(card is None for card in result): raise ValueError('Incomplete bounded deal permutation')
            updated['battle'][f'deck{owner}']['sp'] = result
        configured = updated
    raise ValueError(f'Initial-deal calibration failed within declared {max_attempts} attempts')


def terminal_comparison(source, hypothesis, initial, final):
    reference = source['reference_terminal']; team = hypothesis['upstream_replay']['episode_config']['tags']['source_team_owner']
    winner = team if reference['result_from_team_view'] == 'victory' else 1-team if reference['result_from_team_view'] == 'defeat' else None
    expected_crowns = [None, None]
    for side, owner in [('team', team), ('opponent', 1-team)]: expected_crowns[owner] = reference['crowns'].get(side)
    raw = final.get('result'); observed_winner = raw if final.get('finalized') and type(raw) is int and raw in (0, 1) else None
    seconds = reference.get('source_duration', {}).get('timeline_seconds')
    # The source parser explicitly distinguishes timeline duration from an
    # authoritative engine terminal tick. Do not invent the latter from the
    # former (the timeline may include end-scene time).
    expected_tick = reference.get('terminal_engine_tick')
    expected = {'winner': winner, 'crowns': expected_crowns, 'end_tick': expected_tick, 'tower_hp': None}
    observed = {'winner': observed_winner, 'crowns': final.get('crowns'), 'end_tick': final.get('tick'), 'tower_hp': None}
    base = compare_outcome(expected, observed)
    initial_towers = {(e[1], e[3], e[4]): e[0] for e in initial.get('entities', []) if e[2] == -1 and e[9] == 'building'}
    final_by_id = {e[0]: e for e in final.get('entities', [])}
    hp_rows = []
    for side, owner in [('team', team), ('opponent', 1-team)]:
        supplied = reference.get('final_tower_hitpoints', {}).get(side) or []
        if len(supplied) != 1: continue
        y_king, y_princess = (3000, 6500) if owner == 0 else (29000, 25500)
        hp = []
        for x, y in [(9000, y_king), (3500, y_princess), (14500, y_princess)]:
            entity = final_by_id.get(initial_towers.get((owner, x, y)))
            hp.append(entity[5] if entity is not None else None)
        exp = supplied[0]
        actual = {'king': hp[0], 'princess_multiset': sorted(hp[1:]) if all(v is not None for v in hp[1:]) else None}
        desired = {'king': exp.get('king'), 'princess_multiset': sorted([exp['princess_left'], exp['princess_right']]) if all(exp.get(k) is not None for k in ('princess_left', 'princess_right')) else None}
        for component in ('king', 'princess_multiset'):
            a, b = actual[component], desired[component]
            hp_rows.append({'side': side, 'native_owner': owner, 'component': component,
                            'expected': b, 'actual': a, 'status': 'unknown' if a is None or b is None else 'pass' if a == b else 'fail',
                            'tolerance_hp': 0})
    base['tower_fields'] = hp_rows
    base['lane_alignment'] = 'not independently established; princess HP compared as an owner-local multiset, never optimized per outcome'
    if any(r['status'] == 'fail' for r in hp_rows): base['status'] = 'fail'
    base['duration_semantics'] = 'Source timeline duration retained as a separate diagnostic; authoritative source terminal_engine_tick is missing and remains unknown'
    if isinstance(seconds, (int, float)):
        base['duration_diagnostic'] = {'source_timeline_seconds': seconds, 'native_terminal_seconds': final.get('tick', 0)/20,
                                      'difference_seconds': final.get('tick', 0)/20-seconds,
                                      'validation_status': 'unknown', 'reason': 'Clock endpoint semantics not independently established; no fitted offset or outcome-driven time shift'}
    return base


def reconstruct(source_id, output, port=26789, grid_ticks=10, max_tick=7200, backend=None):
    configure(); folder = local(output); folder.mkdir(parents=True, exist_ok=True)
    if (folder / 'episode.json').exists(): raise ValueError('Use a new attempt directory; completed evidence is immutable')
    if type(grid_ticks) is not int or grid_ticks < 1: raise ValueError('Positive decision grid required')
    started = time.monotonic(); resource_before = process_resources(); engine = backend; decisions = []; captures = []
    report = {'schema_version': 'royale-phase2-native-reconstruction.v1', 'source_episode_id': source_id,
              'quality': {'parse_valid': 'not_run', 'runtime_replayable': 'not_run', 'deterministic': 'not_run',
                          'outcome_consistent': 'not_run', 'trajectory_validated': 'not_available'},
              'strict_source_levels': 'unknown', 'native_execution': False, 'completed': False,
              'training_decision_samples': 0, 'failures': [], 'files': {}, 'counts': {}}
    try:
        entry, source, hypothesis, conversion = load_inputs(source_id)
        report.update(leakage_group=entry['leakage_group'], source_split=entry['split'],
                      frozen_source_manifest=entry['frozen_manifest'], frozen_source_manifest_sha256=entry['frozen_manifest_sha256'],
                      converter_sha256=hypothesis['provenance']['converter_sha256'],
                      source_episode=entry['episode'], source_episode_sha256=file_hash(entry['episode']),
                      hypothesis=conversion['hypothesis'], hypothesis_sha256=conversion['hypothesis_sha256'],
                      source_payload_sha256=entry['payload_sha256'], dataset_revision=hypothesis['provenance']['dataset_revision'],
                      assumptions=hypothesis['assumptions'])
        report['quality']['parse_valid'] = source['quality']['parse_valid']
        commands = source_commands(source_id, hypothesis)
        report['counts']['source_actions'] = len(commands)
        report['source_actions'] = [{k: v for k, v in c.items() if k != 'source_command'} for c in commands]
        require_lossless_source_levels(source, hypothesis); report['strict_source_levels'] = 'pass'
        if engine is None:
            from .backend import NativeBattleBackend
            engine = NativeBattleBackend(port=port, trace_path=folder / 'wire.jsonl')
        report['version_fingerprint'] = engine.version['fingerprint']
        match = native_match(hypothesis)
        initial, match, attempts = align_initial_deal(engine, match, hypothesis, folder)
        report['initialization_attempts'] = len(attempts); report['native_execution'] = True
        if commands and commands[0]['tick'] <= initial['tick']:
            raise ValueError('Source action precedes playable capture; no time shifting allowed')
        by_target = {}
        for command in commands: by_target.setdefault(command['tick'], []).append(command)
        boundaries = {t-1 for t in by_target} | {t for t in by_target}
        state = initial; initial_tick = state['tick']; pending_rows = {}
        with (folder / 'states.jsonl').open('w', encoding='utf-8') as states, (folder / 'decisions.jsonl').open('w', encoding='utf-8') as decisions_file:
            while True:
                tick = state['tick']; capture_index = len(captures)+1
                raw_row = {'capture_index': capture_index, 'raw_capture': deepcopy(state)}
                captures.append(raw_row); states.write(json.dumps(raw_row, separators=(',', ':'), allow_nan=False)+'\n'); states.flush()
                if state['finalized'] or tick >= max_tick: break
                regular = (tick-initial_tick) % grid_ticks == 0
                due = by_target.get(tick+1, [])
                point = regular or bool(due)
                current_decisions = []
                if point:
                    for owner in (0, 1):
                        visible = engine.observe_visible(owner)
                        labels = []
                        own_hand = next(p['hand'] for p in state['players'] if p['owner'] == owner)
                        for command in due:
                            if command['owner'] != owner: continue
                            label = {k: v for k, v in command.items() if k != 'source_command'}
                            slot = [h[0] for h in own_hand if h[1] == command.get('card_id')]
                            label.update(slot=slot[0] if len(slot) == 1 else None, target_tick=command['tick'])
                            labels.append(label)
                        row = {'decision_id': f'{source_id}:{owner}:{tick}', 'source_episode_id': source_id,
                               'owner': owner, 'tick': tick, 'capture_index': capture_index, 'state_line': capture_index,
                               'actor_observation': visible, 'hand_budget_mask': affordability_mask(visible),
                               'labels': labels, 'sampling_origin': 'regular_grid' if regular else 'pre_source_action_boundary',
                               'sampling_interval_ticks': grid_ticks, 'absence_window_ticks': 1,
                               'observation_before_label': True, 'command_ready': True,
                               'observation_sequence': len(engine._wire), 'submit_sequence': None}
                        current_decisions.append(row)
                for command in due:
                    schedule = {k: v for k, v in command.items() if k not in ('source_command', 'source_event_index', 'source_side', 'converted_unclipped_xy', 'coordinate_rule')}
                    scheduled = engine.schedule_journal([schedule])[0]
                    pending_rows[command['request_id']] = {**command, 'registration': scheduled}
                    for row in current_decisions:
                        if row['owner'] == command['owner'] and row['submit_sequence'] is None: row['submit_sequence'] = len(engine._wire)
                future_grid = initial_tick + ((tick-initial_tick)//grid_ticks+1)*grid_ticks
                next_boundary = min([max_tick, future_grid] + [b for b in boundaries if b > tick])
                for row in current_decisions:
                    if row['submit_sequence'] is None: row['submit_sequence'] = len(engine._wire)+1  # actual next advance command
                    row['transition_end_tick'] = next_boundary
                    decisions.append(row); decisions_file.write(json.dumps(row, separators=(',', ':'), allow_nan=False)+'\n')
                decisions_file.flush(); state = engine.advance_exact_ticks(next_boundary-tick)
        feedback = engine.execution_feedback()
        outcomes = {r['request_id']: r for r in feedback}
        action_rows = []
        for command in commands:
            row = {k: v for k, v in command.items() if k != 'source_command'}
            row['feedback'] = outcomes.get(command['request_id'])
            row['execution_status'] = 'executed' if row['feedback'] and row['feedback']['card_consumed'] else 'unknown' if row['feedback'] else 'not_submitted_native_ended_early'
            action_rows.append(row)
        write_json(folder / 'actions.json', {'schema': 'royale-phase2-native-action-results.v1', 'actions': action_rows})
        write_json(folder / 'journal.json', engine.export_journal())
        report['terminal'] = engine.result(); report['outcome_compare'] = terminal_comparison(source, hypothesis, initial, state)
        counts = Counter(r['execution_status'] for r in action_rows)
        report['counts'].update(dict(counts)); report['counts'].update(decision_points=len(decisions), captures=len(captures),
            native_card_events=len(engine.export_journal()['actual_card_events']))
        complete = state['finalized'] and len(action_rows) == len(commands) and all(r['execution_status'] == 'executed' for r in action_rows)
        report['quality']['runtime_replayable'] = 'pass' if complete else 'fail'
        report['quality']['outcome_consistent'] = report['outcome_compare']['status']
        if not complete: report['failures'].append('Not all source commands have uniquely confirmed native consumption and a natural terminal; see action results (abilities unconfirmed in minimal)')
        report['completed'] = True
    except Exception as exc:
        if report['strict_source_levels'] == 'unknown': report['strict_source_levels'] = 'fail'
        report['quality']['runtime_replayable'] = 'fail'
        report['failures'].append(f'{type(exc).__name__}: {exc}')
        (folder / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        if engine is not None: engine.close()
        report['wall_seconds'] = time.monotonic()-started
        report['resources'] = {'before': resource_before, 'after': process_resources()}
        for path in sorted(folder.iterdir()):
            # Only files this function owns can be sealed here. The parent
            # process owns worker.log, which receives stdout after our return.
            if path.is_file() and path.name not in ('episode.json', 'worker.log'):
                report['files'][path.name] = {'path': path.relative_to(ROOT).as_posix(), 'sha256': file_hash(path)}
        write_json(folder / 'episode.json', report)
    return report

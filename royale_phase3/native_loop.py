"""Bounded native collection -> sequence BC -> fresh-process native rollout.

Battle physics remain exclusively in NativePhase3Env/NativeBattleBackend. Missing
Android assets or transport are reported as unavailable, never replaced with a
Python arena. Offline fixture tests exercise the recording interface only.
"""
from collections import Counter
from copy import deepcopy
import math
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

from .actions import DECK, STEP_TICKS, PublicTeacher, native_to_grid
from .common import (ROOT, append, digest, local, native_lock, now, read,
                     reference, rows, sha, write)
from .data import split_for, world_target

TRAJECTORY_SCHEMA = 'phase3-native-loop-transition.v1'


def runtime_preflight(connect=False):
    """Verify the actual backend prerequisites, optionally its read-only status.

    Instantiating the backend checks all pinned asset hashes and the actual ADB
    component receipt. It does not configure/reset a game. A successful status
    is only readiness evidence; natural termination is proved by run_batch().
    Caller owns native_lock whenever connect=True.
    """
    from royale_phase2.connection import check_connection
    result = check_connection(connect=connect)
    reasons = [item['error'] for item in result.get('checks', {}).values()
               if item.get('status') == 'blocked' and item.get('error')]
    return {**result, 'schema': 'phase3-native-loop-preflight.v1',
            'created_utc': now(), 'ready': result.get('status') in ('ready_assets', 'ready'),
            'native_execution_verified': False, 'fallback_used': False,
            'connection_requested': connect, 'evidence_scope': result.get('evidence_scope', 'unknown'),
            'reason': result.get('error') or '; '.join(reasons) or None}


def _budget(games, seed, max_tick, wall_seconds):
    if type(games) is not int or not 1 <= games <= 1000:
        raise ValueError('games must be an integer between 1 and 1000')
    if type(seed) is not int or not 0 <= seed < 2**31 - games:
        raise ValueError('Invalid seed budget')
    if type(max_tick) is not int or not 91 <= max_tick <= 1_000_000:
        raise ValueError('Invalid max_tick budget')
    if type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds) or not 1 <= wall_seconds <= 3600:
        raise ValueError('Per-game wall_seconds must be between 1 and 3600')


def make_transition(before, next_row, before_public, next_public, proposed, step,
                    *, policy, version, game_index, sequence_index):
    """Record pre-action input and observed outcome without relabeling requests.

    A rejected play is not an explicit WAIT label. Native injection alone is
    not consumption. Actual execution point remains unknown.
    """
    action = step['action']
    own_feedback = [f for f in step['feedback'] if f['owner'] == before['native_owner']]
    confirmed = action['kind'] == 'play_card' and len(own_feedback) == 1 and own_feedback[0]['card_consumed'] is True
    row = {'schema': TRAJECTORY_SCHEMA, 'episode_id': before['episode_id'],
           'native_owner': before['native_owner'], 'game_index': game_index,
           'sequence_index': sequence_index, 'version_fingerprint': version,
           'policy': deepcopy(policy), 'actor_input': deepcopy(before),
           'next_actor_input': deepcopy(next_row),
           'public_observation': deepcopy(before_public),
           'next_public_observation': deepcopy(next_public),
           'proposed_action': deepcopy(proposed),
           'execution': {name: deepcopy(step[name]) for name in
                         ('action', 'opponent', 'registrations', 'feedback', 'boundary_rejection')},
           'planned_tick': before['tick'] + 1 if action['kind'] == 'play_card' else None,
           'actual_execution_tick': own_feedback[0].get('actual_execution_tick') if confirmed else None,
           'card_consumed': bool(confirmed),
           'native_injected': bool(own_feedback and own_feedback[0].get('native_injected')),
           'actual_deployment_point': None,
           'reward': step['reward'], 'terminated': bool(step['terminated']),
           'truncated': bool(step['truncated']),
           'transition_dt_ticks': step['after_tick'] - step['before_tick'],
           'actual_dt_seconds': step['actual_dt_seconds'], 'discount': step['discount'],
           'loss_mask': step['boundary_rejection'] is None and (action['kind'] == 'wait' or confirmed)}
    validate_transition(row)
    return row


def validate_transition(row):
    if row.get('schema') != TRAJECTORY_SCHEMA:
        raise ValueError('Unsupported native loop trajectory')
    before, after = row['actor_input'], row['next_actor_input']
    if (before['episode_id'] != after['episode_id'] or
            before['episode_id'] != row['episode_id'] or
            before['native_owner'] != after['native_owner'] or
            before['native_owner'] != row['native_owner']):
        raise ValueError('Transition changed episode or controlled owner')
    dt = after['tick'] - before['tick']
    if type(dt) is not int or not 1 <= dt <= STEP_TICKS or row['transition_dt_ticks'] != dt:
        raise ValueError('Invalid native transition interval')
    if row['actual_dt_seconds'] != dt / 20:
        raise ValueError('Native dt does not match the 20 Hz clock')
    if type(row['reward']) not in (int, float) or not math.isfinite(row['reward']):
        raise ValueError('Non-finite native reward')
    if type(row['terminated']) is not bool or type(row['truncated']) is not bool or (row['terminated'] and row['truncated']):
        raise ValueError('Invalid native end flags')
    if row['card_consumed']:
        action = row['execution']['action']
        if action['kind'] != 'play_card' or row['actual_execution_tick'] is None:
            raise ValueError('Consumption requires an actual native play receipt')
        if not before['tick'] < row['actual_execution_tick'] <= after['tick']:
            raise ValueError('Consumed action outside its recorded transition')
    if row['execution']['boundary_rejection'] is not None and row['loss_mask']:
        raise ValueError('Rejected action cannot supervise an explicit WAIT')
    return row


def _manual_actions(path):
    selected = {}
    if path is not None:
        for item in rows(path):
            key = (item['game_index'], item['tick'])
            if key in selected:
                raise ValueError('Duplicate manual action for one game/tick')
            selected[key] = item['action']
    return selected


def _run_batch(output, *, games=4, seed=510000, max_tick=7200, policy='random',
              checkpoint=None, opponent='alternate', wall_seconds=300,
              rendered=False, actions=None, paired=False):
    """One serial native writer; partial/failed games remain in the denominator."""
    _budget(games, seed, max_tick, wall_seconds)
    if policy not in ('cycle', 'random', 'wait', 'manual', 'checkpoint'):
        raise ValueError('Unknown native controller')
    if opponent not in ('cycle', 'random', 'wait', 'alternate'):
        raise ValueError('Unknown native opponent')
    if (policy == 'checkpoint') != (checkpoint is not None):
        raise ValueError('A checkpoint is required only for checkpoint control')
    if policy == 'manual' and actions is None:
        raise ValueError('Manual control requires an action JSONL; omitted ticks explicitly WAIT')
    manual = _manual_actions(actions)
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    policy_ref = {'kind': policy, 'checkpoint': reference(checkpoint) if checkpoint else None,
                  'manual_actions': reference(actions) if actions else None}
    plans = [{'index': i, 'seed': seed + (i // 2 if paired else i),
              'split_group_index': i // 2 if paired else i, 'owner': i % 2,
              'opponent': ('cycle' if (i // 2) % 2 == 0 else 'random') if opponent == 'alternate' else opponent}
             for i in range(games)]
    config = {'schema': 'phase3-native-loop-batch-config.v1', 'policy': policy_ref,
              'games': plans, 'max_tick': max_tick, 'control_ticks': STEP_TICKS,
              'wall_seconds_per_game': wall_seconds, 'observation_profile': 'fair',
              'rendered': rendered, 'paired_seeds_and_swapped_seats': paired,
              'manual_missing_ticks': 'explicit WAIT', 'source': reference(__file__)}
    write(folder / 'configuration.json', config)
    report = {'schema': 'phase3-native-loop-batch.v1', 'started': now(), 'pid': os.getpid(),
              'status': 'running',
              'configuration': reference(folder / 'configuration.json'), 'games': [],
              'requested_games': games, 'native_execution_verified': False,
              'fallback_used': False, 'strength_improvement_claim': False}
    write(folder / 'report.json', report)
    with native_lock():
        readiness = runtime_preflight(connect=True)
        write(folder / 'runtime_check.json', readiness)
        if not readiness['ready']:
            report.update(status='unavailable', reason=readiness.get('reason'), finished=now())
            write(folder / 'report.json', report)
            return report
        actual_native_origin = readiness.get('evidence_scope') == 'native_prerequisites'
        report['evidence_scope'] = 'native_execution' if actual_native_origin else 'interface_only'
        from .native_env import NativePhase3Env
        for plan in plans:
            base = folder / f'game-{plan["index"]:03d}'
            base.mkdir()
            result = {**plan, 'status': 'running', 'natural_terminal': False,
                      'truncated': False, 'failure': None, 'decisions': 0,
                      'consumed': 0, 'boundary_rejections': 0, 'last_tick': None,
                      'wall_seconds': 0., 'updated': now()}
            write(base / 'result.json', result)
            # This file changes while a game runs; a hash reference would become
            # stale. Completed games alone enter games[] and final aggregates.
            report['active_game_ref'] = (base / 'result.json').relative_to(ROOT).as_posix()
            write(folder / 'report.json', report)
            env = None
            started = time.monotonic()
            last_progress = started
            try:
                env = NativePhase3Env(base / 'native', owner=plan['owner'],
                                      opponent=plan['opponent'], max_tick=max_tick,
                                      profile='fair', rendered=rendered)
                current = env.reset(plan['seed'])
                version = env.backend.version['fingerprint']
                result.update(episode_id=current['episode_id'], last_tick=current['tick'],
                              version_fingerprint=version,
                              native_directory=env.folder.relative_to(ROOT).as_posix(),
                              wall_seconds=time.monotonic() - started, updated=now())
                write(base / 'result.json', result)
                last_progress = time.monotonic()
                if policy == 'checkpoint':
                    from .learning import StatefulPolicy
                    controller = StatefulPolicy.load(checkpoint, version, 'fair')
                else:
                    controller = PublicTeacher(policy if policy != 'manual' else 'wait',
                                               plan['seed'] * 3 + plan['owner'])
                transition_file = base / 'transitions.jsonl'
                while not env.done:
                    before_public = deepcopy(env.public_observation)
                    if policy == 'checkpoint':
                        proposed = controller.predict(current)
                    elif policy == 'manual':
                        proposed = deepcopy(manual.get((plan['index'], current['tick']), {'kind': 'wait'}))
                    else:
                        proposed = controller.decide(env.actor)
                    next_row, reward, terminated, truncated, step = env.step(proposed)
                    transition = make_transition(current, next_row, before_public,
                                                 env.public_observation, proposed, step,
                                                 policy=policy_ref, version=version,
                                                 game_index=plan['index'],
                                                 sequence_index=result['decisions'])
                    append(transition_file, transition)
                    result['decisions'] += 1
                    result['consumed'] += int(transition['card_consumed'])
                    result['boundary_rejections'] += int(step['boundary_rejection'] is not None)
                    current = next_row
                    elapsed = time.monotonic()
                    result.update(last_tick=current['tick'], wall_seconds=elapsed - started)
                    if result['decisions'] % 25 == 0 or elapsed - last_progress >= 5:
                        result['updated'] = now()
                        write(base / 'result.json', result)
                        last_progress = elapsed
                    if result['decisions'] % 100 == 0:
                        print({'native_game': plan['index'], 'status': 'running',
                               'decisions': result['decisions'], 'last_tick': result['last_tick'],
                               'consumed': result['consumed'],
                               'seconds': round(result['wall_seconds'], 2)}, flush=True)
                    if elapsed - started > wall_seconds and not env.done:
                        raise TimeoutError('Per-game native wall budget exceeded; no implicit reset/retry')
                env.save()
                # Seed and wire generation alone do not make two games independent.
                semantic = [{key: state.get(key) for key in
                             ('tick', 'finalized', 'result', 'crowns', 'players', 'entities', 'plays')}
                            for state in rows(env.folder / 'states.jsonl')]
                result.update(status='completed' if terminated else 'truncated',
                              natural_terminal=terminated, truncated=truncated,
                              episode_id=current['episode_id'], last_tick=current['tick'],
                              winner_owner=env.state['result'], crowns=env.state['crowns'],
                              version_fingerprint=version,
                              semantic_trajectory_hash=digest(semantic),
                              transitions=reference(transition_file),
                              native_directory=env.folder.relative_to(ROOT).as_posix(),
                              artifacts=[reference(env.folder / name) for name in
                                         ('states.jsonl', 'decisions.jsonl', 'journal.json',
                                          'execution_feedback.json', 'native_result.json',
                                          'configuration.json', 'initialization.json')])
                if (env.folder / 'wire.jsonl').exists():
                    result['artifacts'].append(reference(env.folder / 'wire.jsonl'))
            except Exception as exc:
                result.update(status='failed', failure=f'{type(exc).__name__}: {exc}',
                              traceback=traceback.format_exc())
            finally:
                if env is not None:
                    env.close()
            result.update(wall_seconds=time.monotonic() - started, finished=now(), updated=now())
            write(base / 'result.json', result)
            report['games'].append(result)
            report.pop('active_game_ref', None)
            write(folder / 'report.json', report)
            print({'native_game': plan['index'], 'policy': policy, 'status': result['status'],
                   'consumed': result['consumed'], 'seconds': round(result['wall_seconds'], 2)}, flush=True)
            if result['status'] == 'failed':
                report['stop_reason'] = 'native_failure_requires_explicit_recovery'
                break
    statuses = Counter(g['status'] for g in report['games'])
    natural = [g for g in report['games'] if g['natural_terminal']]
    all_completed = len(natural) == games
    report.update(status='completed' if all_completed else 'incomplete', finished=now(),
                  statuses=dict(statuses), attempted_games=len(report['games']),
                  natural_terminal_games=len(natural),
                  native_execution_verified=bool(natural) and actual_native_origin,
                  consumed=sum(g['consumed'] for g in report['games']),
                  wins=sum(g['winner_owner'] == g['owner'] for g in natural),
                  losses=sum(g['winner_owner'] in (0, 1) and g['winner_owner'] != g['owner'] for g in natural),
                  draws=sum(g['winner_owner'] not in (0, 1) for g in natural),
                  win_denominator=games)
    write(folder / 'report.json', report)
    return report


def run_batch(output, **kwargs):
    """Persist preflight/lease errors as failures while preserving earlier runs."""
    folder = local(output)
    existed = folder.exists()
    try:
        return _run_batch(output, **kwargs)
    except Exception as exc:
        report_path = folder / 'report.json'
        if existed or not report_path.exists():
            raise
        report = read(report_path)
        write(folder / 'failure.json', {'error': f'{type(exc).__name__}: {exc}',
                                      'traceback': traceback.format_exc(), 'finished': now()})
        report.update(status='failed', native_execution_verified=False, finished=now(),
                      failure=reference(folder / 'failure.json'))
        report.pop('active_game_ref', None)
        write(report_path, report)
        return report


def _learning_row(transition, following, split, source):
    validate_transition(transition)
    before = transition['actor_input']
    action = transition['execution']['action']
    index = action['slot'] + 1 if transition['card_consumed'] else 0
    world, world_mask = world_target(transition['next_public_observation'])
    opponent_index = 0
    opponent_valid = following is not None
    if following is not None:
        other = following['execution']['opponent']
        if other['kind'] == 'play_card':
            receipts = [r for r in following['execution']['feedback']
                        if r['owner'] != before['native_owner']]
            opponent_valid = len(receipts) == 1 and receipts[0]['card_consumed'] is True
            if opponent_valid:
                opponent_index = DECK.index(other['card']) + 1
    row = {**deepcopy(before), 'split': split,
           'source_time': None, 'engine_tick': before['tick'], 'observation_tick': before['tick'],
           'planned_tick': transition['planned_tick'],
           'actual_execution_tick': transition['actual_execution_tick'],
           'action_semantics': 'requested native coordinate; actual deployment point unknown',
           'elapsed_dt_known': transition['public_observation']['dt_seconds'] is not None,
           'transition_dt_ticks': transition['transition_dt_ticks'], 'planned_control_ticks': STEP_TICKS,
           'sampling_probability': 1., 'natural_weight': 1.,
           'wait_semantics': 'explicit controller WAIT; rejected/unconfirmed play is excluded by loss_mask',
           'action_index': index,
           'position_index': native_to_grid(action['x'], action['y']) if index else 0,
           'xy': [action['x'] / 17999, action['y'] / 31999] if index else [0., 0.],
           'position_mask': bool(index), 'card_id': action['card'] if index else None,
           'loss_mask': transition['loss_mask'], 'reward': transition['reward'],
           'terminated': transition['terminated'], 'truncated': transition['truncated'],
           'discount': transition['discount'],
           'targets': {'world': world, 'world_mask': world_mask,
                       'opponent': opponent_index, 'opponent_mask': opponent_valid},
           'evidence': {'game': before['episode_id'], 'source_kind': 'native_loop_recorded_transition',
                        'trajectory': source, 'sequence_index': transition['sequence_index']}}
    return row


def export_trajectories(source, output):
    """Export whole games and a physically separate train/validation-only view.

    Failed games are excluded. Watchdog-truncated games remain explicitly marked
    and can teach pre-terminal actions, but never count as natural game evidence.
    Duplicate semantic games are excluded even if their seed/UUID differs.
    """
    from .data_contract import load_dataset
    from .observation import feature_spec
    source_folder = local(source)
    batch = read(source_folder / 'report.json')
    if batch.get('schema') != 'phase3-native-loop-batch.v1':
        raise ValueError('Expected an immutable native loop batch')
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    training_folder = folder / 'train_validation'
    training_folder.mkdir()
    split_groups = {name: [] for name in ('train', 'validation', 'test')}
    counts = {name: Counter() for name in split_groups}
    excluded = []
    seen = set()
    seed_splits = {}
    dim = None
    version = None
    for game in batch['games']:
        reason = None
        if game['status'] not in ('completed', 'truncated'):
            reason = 'failed_or_unfinished'
        elif not game.get('semantic_trajectory_hash'):
            reason = 'missing_semantic_trajectory_hash'
        if reason:
            excluded.append({'index': game['index'], 'reason': reason})
            continue
        for artifact in [game['transitions'], *game.get('artifacts', [])]:
            if sha(artifact['path']) != artifact['sha256']:
                raise ValueError('Native trajectory artifact changed: ' + artifact['path'])
        state_files = [item for item in game.get('artifacts', [])
                       if Path(item['path']).name == 'states.jsonl']
        if len(state_files) != 1:
            raise ValueError('Expected exactly one native state trace per game')
        semantic = [{key: state.get(key) for key in
                     ('tick', 'finalized', 'result', 'crowns', 'players', 'entities', 'plays')}
                    for state in rows(state_files[0]['path'])]
        if digest(semantic) != game['semantic_trajectory_hash']:
            raise ValueError('Native semantic trajectory hash does not match saved states')
        if game['semantic_trajectory_hash'] in seen:
            excluded.append({'index': game['index'], 'reason': 'duplicate_semantic_game'})
            continue
        seen.add(game['semantic_trajectory_hash'])
        trajectory = list(rows(game['transitions']['path']))
        if not trajectory:
            raise ValueError('Completed native game has no transitions')
        if not (trajectory[-1]['terminated'] or trajectory[-1]['truncated']):
            raise ValueError('Native game missing its final transition')
        split = split_for(game['split_group_index'])
        if game['seed'] in seed_splits and seed_splits[game['seed']] != split:
            raise ValueError('Paired native seed leaks across splits')
        seed_splits[game['seed']] = split
        episode = game['episode_id']
        if any(episode in group for group in split_groups.values()):
            raise ValueError('Duplicate episode UUID in native batch')
        split_groups[split].append(episode)
        if version is not None and version != game['version_fingerprint']:
            raise ValueError('Mixed native version fingerprints')
        version = game['version_fingerprint']
        previous_next = None
        for index, transition in enumerate(trajectory):
            validate_transition(transition)
            if (transition['episode_id'] != episode or transition['native_owner'] != game['owner'] or
                    transition['sequence_index'] != index):
                raise ValueError('Inconsistent native game trajectory identity/order')
            if transition['version_fingerprint'] != game['version_fingerprint']:
                raise ValueError('Transition native version differs from its game')
            if previous_next is not None and transition['actor_input'] != previous_next:
                raise ValueError('Native trajectory has a discontinuity')
            if index < len(trajectory) - 1 and (transition['terminated'] or transition['truncated']):
                raise ValueError('Transition follows native end flag')
            previous_next = transition['next_actor_input']
            following = trajectory[index + 1] if index + 1 < len(trajectory) else None
            row = _learning_row(transition, following, split, game['transitions'])
            shape = {key: len(row[key]) for key in ('hud', 'observation', 'history', 'belief', 'feedback')}
            if dim is not None and dim != shape:
                raise ValueError('Native feature dimension changed')
            dim = shape
            append(folder / 'rows.jsonl', row)
            if split != 'test':
                append(training_folder / 'rows.jsonl', row)
            counts[split]['rows'] += 1
            counts[split]['play' if row['action_index'] else 'wait'] += 1
            counts[split]['eligible'] += int(row['loss_mask'])
            counts[split]['terminated'] += int(row['terminated'])
            counts[split]['truncated'] += int(row['truncated'])
    if dim is None:
        write(folder / 'export_failure.json', {'status': 'no_usable_native_games', 'excluded': excluded})
        raise ValueError('No completed or watchdog-truncated native games to export')
    metadata = {'schema': 'phase3-learning-dataset.v1', 'task_id': digest(reference(source_folder / 'report.json'))[:16],
                'rows': reference(folder / 'rows.jsonl'), 'feature_dims': dim,
                'observation_profile': 'fair', 'version_fingerprint_sha256': version,
                'control_ticks': STEP_TICKS, 'ticks_per_second': 20,
                'max_tick': read(source_folder / 'configuration.json')['max_tick'],
                'split_groups': split_groups, 'card_vocabulary': list(DECK),
                'source': reference(source_folder / 'report.json'),
                'counts': {key: dict(value) for key, value in counts.items()},
                'feature_spec': feature_spec(cards=DECK), 'world_target_dimension': 9,
                'position_convention': 'native world coordinates; y-major 18x32 grid; no implicit seat flip',
                'fair_entities': 'Excluded until independently attested; unknown units remain unknown',
                'evidence_scope': batch.get('evidence_scope', 'unknown'),
                'fidelity': ('Actual native backend transitions; no independent official trajectory equivalence claim'
                             if batch.get('evidence_scope') == 'native_execution' else
                             'Interface-only synthetic fixture; no native battle execution evidence'),
                'qualification': {'excluded_games': excluded, 'duplicate_semantic_games_removed': True,
                                  'watchdog_truncation_preserved': True},
                'split_rule': 'seed group index modulo 5: 3 validation, 4 test, otherwise train; paired seats stay together',
                'feature_sources': [reference(__file__), reference('royale_phase3/native_env.py')]}
    write(folder / 'metadata.json', metadata)
    # Training never opens the held-out test row file. It receives this view only.
    if not (training_folder / 'rows.jsonl').exists():
        (training_folder / 'rows.jsonl').touch()
    training_metadata = {**deepcopy(metadata), 'rows': reference(training_folder / 'rows.jsonl'),
                         'split_groups': {**split_groups, 'test': []},
                         'counts': {**metadata['counts'], 'test': {}},
                         'training_view': 'Physically contains only train/validation rows; test not loaded by train()'}
    write(training_folder / 'metadata.json', training_metadata)
    exported, _ = load_dataset(folder / 'metadata.json')
    training_data, _ = load_dataset(training_folder / 'metadata.json')
    if any(row['split'] == 'test' for row in training_data):
        raise AssertionError('Held-out data leaked into physical training view')
    report = {'schema': 'phase3-native-loop-export.v1', 'status': 'exported',
              'metadata': reference(folder / 'metadata.json'),
              'training_metadata': reference(training_folder / 'metadata.json'),
              'rows': len(exported), 'training_validation_rows': len(training_data),
              'counts': metadata['counts'], 'excluded_games': excluded, 'test_evaluated': False}
    write(folder / 'report.json', report)
    return report


def train_validation_view(metadata, output):
    """Copy train/validation inputs, keeping every original episode assignment.

    Conversion validates the source once. The learner subsequently opens only
    the separate train/validation file and cannot select models using test.
    """
    from .data_contract import load_dataset
    data, meta = load_dataset(metadata)
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    target = folder / 'rows.jsonl'
    target.touch()
    counts = {name: Counter() for name in ('train', 'validation', 'test')}
    groups = {name: set() for name in counts}
    for row in data:
        if row['split'] == 'test':
            continue
        append(target, row)
        counts[row['split']]['rows'] += 1
        counts[row['split']]['eligible'] += int(row['loss_mask'])
        counts[row['split']]['play' if row['action_index'] else 'wait'] += 1
        groups[row['split']].add(row['episode_id'])
    if not all(counts[name]['eligible'] for name in ('train', 'validation')):
        raise ValueError('Independent eligible train and validation games required')
    selected = {**deepcopy(meta), 'rows': reference(target),
                'counts': {name: dict(counts[name]) for name in counts},
                'split_groups': {name: sorted(groups[name]) for name in groups},
                'origin_metadata': reference(metadata),
                'training_view': 'Physically contains train/validation only; no test rows supplied to learner'}
    write(folder / 'metadata.json', selected)
    load_dataset(folder / 'metadata.json')
    return folder / 'metadata.json'


def train_offline(metadata, output, *, epochs=2, hidden=32, seed=51001, play_weight=8.):
    """Small real CPU optimization on saved native-origin rows; zero live steps."""
    folder = local(output)
    if folder.exists():
        raise FileExistsError('Model output already exists; use a new experiment directory')
    training_metadata = train_validation_view(metadata, folder.with_name(folder.name + '-input'))
    from .learning import train
    report = train(training_metadata, folder, variant='C', epochs=epochs, hidden=hidden,
                   seed=seed, sequence_length=32, burnin=8, batch_size=8,
                   play_weight=play_weight)
    report.update(origin_metadata=reference(metadata),
                  physically_separate_training_metadata=reference(training_metadata),
                  fresh_native_environment_steps=0, test_rows_supplied_to_learner=0,
                  algorithm='supervised sequence behavior cloning; not reinforcement learning',
                  native_online_training_verified=False,
                  data_origin=read(metadata).get('evidence_scope', 'saved phase3 dataset; see origin metadata fidelity'),
                  data_fidelity=read(metadata).get('fidelity', 'not independently established here'))
    write(folder / 'report.json', report)
    return report


def _child(command, log_base):
    """Use the caller's explicit interpreter; persist stdout/stderr and exit code."""
    log_base = local(log_base)
    started = time.monotonic()
    with log_base.with_suffix('.stdout.log').open('w', encoding='utf-8') as stdout, \
            log_base.with_suffix('.stderr.log').open('w', encoding='utf-8') as stderr:
        process = subprocess.run([sys.executable, '-B', str(ROOT / 'tools/native_loop.py'), *command],
                                 cwd=ROOT, stdout=stdout, stderr=stderr, check=False)
    return {'command': [sys.executable, '-B', 'tools/native_loop.py', *command],
            'returncode': process.returncode, 'wall_seconds': time.monotonic() - started,
            'stdout': reference(log_base.with_suffix('.stdout.log')),
            'stderr': reference(log_base.with_suffix('.stderr.log'))}


def _run_loop(output, *, games=4, evaluation_games=2, seed=510000, max_tick=7200,
             epochs=2, hidden=32, wall_seconds=300, play_weight=8.):
    """One bounded iteration; no implicit large training or test-based selection."""
    _budget(games, seed, max_tick, wall_seconds)
    _budget(evaluation_games, seed + 10000, max_tick, wall_seconds)
    if games < 4:
        raise ValueError('At least four collected games are required for independent validation')
    if type(epochs) is not int or not 1 <= epochs <= 100:
        raise ValueError('epochs must be between 1 and 100')
    if type(hidden) is not int or not 4 <= hidden <= 512:
        raise ValueError('Invalid GRU hidden width')
    if type(play_weight) not in (int, float) or not math.isfinite(play_weight) or play_weight <= 0:
        raise ValueError('Invalid declared play class weight')
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    configuration = {'schema': 'phase3-native-loop-config.v1', 'games': games,
                     'evaluation_games': evaluation_games, 'seed': seed,
                     'max_tick': max_tick, 'epochs': epochs, 'hidden': hidden,
                     'train_play_class_weight': play_weight,
                     'wall_seconds_per_game': wall_seconds, 'iterations': 1,
                     'training_algorithm': 'sequence behavior cloning, GRU variant C',
                     'parent_pid': os.getpid(), 'source': reference(__file__)}
    write(folder / 'configuration.json', configuration)
    report = {'schema': 'phase3-native-loop.v1', 'started': now(), 'status': 'running',
              'configuration': reference(folder / 'configuration.json'), 'steps': [],
              'native_closed_loop_verified': False, 'strength_improvement_claim': False,
              'test_evaluated': False, 'fallback_used': False}
    write(folder / 'report.json', report)
    preflight = runtime_preflight(connect=False)
    write(folder / 'runtime_check.json', preflight)
    if not preflight['ready']:
        report.update(status='unavailable', reason=preflight.get('reason'), finished=now())
        write(folder / 'report.json', report)
        return report
    budget = ['--max-tick', str(max_tick), '--wall-seconds', str(wall_seconds)]
    collection = folder / 'collection'
    execution = _child(['collect', '--output', str(collection), '--games', str(games),
                        '--seed', str(seed), '--policy', 'random', *budget], folder / '01-collect')
    report['steps'].append(execution)
    write(folder / 'report.json', report)
    if execution['returncode'] == 0:
        exported = export_trajectories(collection, folder / 'dataset')
        report['dataset'] = exported
        train_command = ['train', '--metadata', exported['training_metadata']['path'],
                         '--output', str(folder / 'model'), '--epochs', str(epochs),
                         '--hidden', str(hidden), '--play-weight', str(play_weight)]
        execution = _child(train_command, folder / '02-train')
        report['steps'].append(execution)
        write(folder / 'report.json', report)
        if execution['returncode'] == 0:
            training = read(folder / 'model/report.json')
            checkpoint = training['checkpoint']['path']
            report['checkpoint'] = training['checkpoint']
            execution = _child(['evaluate', '--output', str(folder / 'evaluation'),
                                '--games', str(evaluation_games), '--seed', str(seed + 10000),
                                '--checkpoint', checkpoint, *budget], folder / '03-evaluate')
            report['steps'].append(execution)
            write(folder / 'report.json', report)
            if execution['returncode'] == 0:
                evaluation = read(folder / 'evaluation/report.json')
                report['evaluation'] = reference(folder / 'evaluation/report.json')
                report['recollected_dataset'] = export_trajectories(folder / 'evaluation', folder / 'recollected_dataset')
                report['checkpoint_loaded_in_fresh_process'] = evaluation['pid'] != configuration['parent_pid']
                collection_report = read(collection / 'report.json')
                report['model_card_consumption_verified'] = evaluation['consumed'] > 0
                report['native_closed_loop_verified'] = bool(
                    collection_report['natural_terminal_games'] == games and
                    evaluation['natural_terminal_games'] == evaluation_games and
                    collection_report['native_execution_verified'] and
                    evaluation['native_execution_verified'] and
                    report['model_card_consumption_verified'] and
                    report['checkpoint_loaded_in_fresh_process'] and training['completed'])
    report.update(status='completed' if report['native_closed_loop_verified'] else 'incomplete', finished=now())
    write(folder / 'report.json', report)
    return report


def run_loop(output, **kwargs):
    """Keep a terminal report on stage/export/lease errors without overwriting history."""
    folder = local(output)
    existed = folder.exists()
    try:
        return _run_loop(output, **kwargs)
    except Exception as exc:
        report_path = folder / 'report.json'
        if existed or not report_path.exists():
            raise
        report = read(report_path)
        failure = {'error': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc(),
                   'finished': now()}
        write(folder / 'failure.json', failure)
        report.update(status='failed', native_closed_loop_verified=False,
                      failure=reference(folder / 'failure.json'), finished=now())
        write(report_path, report)
        return report

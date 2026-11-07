"""One serial, persistent research queue over the existing phase-three CLIs.

Importing this module neither imports Torch nor touches Android.  A run keeps
its request, resolved identities, child logs, partial results and cancellation
receipt; restarting the portal marks unfinished work interrupted, never retries
an unknown native world.  Battle physics and policy training remain elsewhere.
"""
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import sys
import threading
import time
import traceback
import uuid

from .actions import DECK
from .common import ROOT, append, digest, local, now, read, reference, rows, sha, write
from .data_contract import load_dataset
from .workbench_config import (DATASETS, DEFAULT_RUN_ROOT, EXPERIMENT_DEFAULTS,
                               REFERENCE_CHECKPOINTS, SYSTEM_PATH)

SCHEMA = 'phase3-research-run.v1'
DEFAULT_ROOT = DEFAULT_RUN_ROOT
DEFAULTS = deepcopy(EXPERIMENT_DEFAULTS)
TERMINAL = {'completed', 'failed', 'cancelled', 'interrupted'}
RUN_ID = re.compile(r'^run-[0-9]{8}t[0-9]{12}z-[a-f0-9]{8}$')


class RunCancelled(Exception):
    pass


def _relative(path):
    return local(path).relative_to(ROOT).as_posix()


def _read_if(path):
    try:
        return read(path) if local(path).is_file() else None
    except (OSError, ValueError, KeyError):
        return None  # An incomplete child file is not a successful result.


def _dataset(dataset_id):
    if dataset_id not in DATASETS:
        raise ValueError('Unknown dataset; choose a registered dataset: ' + ', '.join(DATASETS))
    item = {'id': dataset_id, **DATASETS[dataset_id]}
    data, meta = load_dataset(item['path'])
    if meta['observation_profile'] != 'fair' or meta.get('card_vocabulary') != list(DECK):
        raise ValueError('Workbench requires the fixed eight-card fair observation contract')
    groups = {name: sorted({r['episode_id'] for r in data if r['split'] == name})
              for name in ('train', 'validation', 'test')}
    if not all(any(r['split'] == split and r['loss_mask'] for r in data)
               for split in ('train', 'validation')):
        raise ValueError('Independent eligible train and validation games are required')
    item.update(available=True, metadata=reference(item['path']), rows=meta['rows'],
                counts={split: dict(Counter('play' if r['action_index'] else 'wait'
                                           for r in data if r['split'] == split))
                        for split in groups}, split_groups=groups,
                version_fingerprint_sha256=meta['version_fingerprint_sha256'],
                observation_profile=meta['observation_profile'],
                feature_dims=meta['feature_dims'], feature_spec_sha256=digest(meta.get('feature_spec')),
                fidelity=meta.get('fidelity'), evidence_scope=meta.get('evidence_scope'))
    return item


def _checkpoint(path, version=None):
    """Check pickle-free artifact identities without importing Torch in HTTP."""
    path = local(path)
    meta = read(path)
    if (meta.get('schema') != 'phase3-small-policy.v1' or
            meta.get('checkpoint_sha256') != digest({k: v for k, v in meta.items()
                                                   if k != 'checkpoint_sha256'})):
        raise ValueError('Checkpoint metadata integrity failed; choose an epoch-*.json checkpoint')
    config = meta['config']; architecture = config['architecture']
    if (architecture.get('variant') != 'C' or architecture.get('recurrent') is not True or
            any(architecture.get(k) for k in ('grid', 'world', 'opponent', 'hazard')) or
            config.get('observation_profile') != 'fair' or config.get('control_ticks') != 10):
        raise ValueError('This workbench accepts only the fixed fair GRU C policy')
    if version and config['version_fingerprint_sha256'] != version:
        raise ValueError('Checkpoint and selected dataset native versions differ')
    weights = local(meta['weights']['path'])
    if weights.stat().st_size > 128 * 1024**2 or sha(weights) != meta['weights']['sha256']:
        raise ValueError('Checkpoint weights changed or are too large')
    return {**reference(path), 'weights': reference(weights),
            'checkpoint_sha256': meta['checkpoint_sha256'],
            'version_fingerprint_sha256': config['version_fingerprint_sha256'],
            'architecture': architecture, 'epoch': meta['epoch']}


def _code_identity():
    def git(*args):
        try:
            return subprocess.run(['git', *args], cwd=ROOT, capture_output=True,
                                  text=True, timeout=5, check=True).stdout.strip()
        except (OSError, subprocess.SubprocessError):
            return None
    return {'git_head': git('rev-parse', 'HEAD'),
            'worktree_porcelain': git('status', '--short'),
            'files': [reference(path) for path in (
                SYSTEM_PATH, 'royale_phase3/workbench_config.py',
                'royale_phase3/research.py', 'tools/cloud/research_worker.py',
                'tools/native_loop.py', 'royale_phase3/native_loop.py',
                'royale_phase3/learning.py', 'royale_phase3/actions.py',
                'royale_phase3/observation.py', 'royale_phase3/data_contract.py',
                'tools/cloud/research_app.py', 'tools/cloud/research_runtime.py',
                'tools/cloud/native_media.py', 'tools/cloud/native_frame_media.py',
                'tools/cloud/native_runtime_state.py',
                'tools/cloud/research_workbench.html')]}


class ResearchService:
    def __init__(self, root=DEFAULT_ROOT, runtime_settings=None, *, lock_path=None):
        self.root = local(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.runtime_settings = deepcopy(runtime_settings or {})
        self._mutex = threading.RLock()
        self._queue = queue.Queue()
        self._closed = False
        self._active = None
        self._child = None
        self._cancel_events = {}
        self._action_cache = {}
        # A second portal must not recover records owned by a live executor.
        lock = local(lock_path or '.tmp/phase3/research-service.lock')
        lock.parent.mkdir(parents=True, exist_ok=True)
        self._lease = lock.open('a+b')
        self._lease.seek(0); self._lease.write(b'0'); self._lease.flush(); self._lease.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._lease.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self._lease.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self._lease.close()
            raise RuntimeError('Another research executor is running; reuse its portal') from None
        for path in self.root.glob('run-*/run.json'):
            record = _read_if(path)
            if record and record.get('status') not in TERMINAL:
                record.update(status='interrupted', stage='interrupted', finished_at=now(),
                              updated_at=now(), error='Portal stopped before completion; no automatic retry')
                write(path, record)
                append(path.parent / 'events.jsonl', {'at': now(), 'event': 'interrupted_on_restart'})
        self._worker = threading.Thread(target=self._work, name='royale-research-worker', daemon=True)
        self._worker.start()

    def catalog(self):
        datasets = []
        for key, info in DATASETS.items():
            try:
                datasets.append(_dataset(key))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                datasets.append({'id': key, **info, 'available': False,
                                 'error': f'{type(exc).__name__}: {exc}'})
        candidates = list(REFERENCE_CHECKPOINTS)
        candidates.extend(r['checkpoint']['path'] for r in self.list_runs() if r.get('checkpoint'))
        checkpoints = []
        checkpoint_errors = []
        for path in dict.fromkeys(candidates):
            try:
                checkpoints.append(_checkpoint(path))
            except (OSError, ValueError, KeyError, TypeError) as exc:
                checkpoint_errors.append({'path': path, 'available': False,
                                          'error': f'{type(exc).__name__}: {exc}'})
        return {'datasets': datasets, 'models': [{'id': 'gru-c', 'label': 'GRU C · 序列行为克隆'}],
                'checkpoints': checkpoints, 'checkpoint_errors': checkpoint_errors,
                'defaults': deepcopy(DEFAULTS),
                'runtime': {'automatic_for': ['evaluate', 'full'], 'offline_needs_android': False},
                'limits': {'paired_games': True, 'opponent': 'cycle',
                           'test_evaluated': False, 'strength_improvement_claim': False}}

    def _path(self, run_id):
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise ValueError('Invalid research run id')
        path = self.root / run_id
        path.resolve().relative_to(self.root)
        return path

    def list_runs(self):
        with self._mutex:
            records = [_read_if(path) for path in self.root.glob('run-*/run.json')]
            return sorted([r for r in records if r], key=lambda r: r['created_at'], reverse=True)

    def get_run(self, run_id):
        with self._mutex:
            folder = self._path(run_id)
            if not (folder / 'run.json').is_file():
                raise KeyError(run_id)
            record = read(folder / 'run.json')
            record['training'] = _read_if(folder / 'model/report.json')
            record['progress'] = _read_if(folder / 'model/progress.json')
            record['runtime'] = _read_if(folder / 'runtime/report.json')
            record['offline_validation'] = _read_if(folder / 'offline_validation.json')
            record['evaluation'] = _read_if(folder / 'evaluation/report.json')
            record['curves'] = []
            latest = _read_if(folder / 'model/latest.json')
            if latest:
                saved = _read_if(latest.get('path', ''))
                record['curves'] = saved.get('curve', []) if saved else []
            record['episodes'] = [value for path in sorted((folder / 'evaluation').glob('game-*/result.json'))
                                  if (value := _read_if(path))]
            record['media'] = self._media(folder, record['episodes'])
            record['events'] = list(rows(folder / 'events.jsonl'))[-200:] if (folder / 'events.jsonl').exists() else []
            record['logs'] = [reference(path) for path in sorted((folder / 'logs').glob('*.log'))]
            record['log_tails'] = []
            for path in sorted((folder / 'logs').glob('*.log'))[-6:]:
                with path.open('rb') as stream:
                    stream.seek(max(0, path.stat().st_size - 8192))
                    tail = stream.read(8192).decode('utf-8', errors='replace')
                record['log_tails'].append({'path': _relative(path), 'text': tail})
            return record

    def _media(self, folder, episodes):
        """Expose only actual registered media files inside this immutable run."""
        found = []
        for episode in episodes:
            value = episode.get('media')
            if not isinstance(value, dict):
                continue
            item = deepcopy(value)
            item.setdefault('id', f'video-{episode.get("index", 0):03d}')
            item.update(episode_index=episode.get('index'), native_owner=episode.get('owner'))
            path = item.get('path')
            if path:
                try:
                    file = local(path); file.relative_to(folder)
                    if not file.is_file() or Path(path).suffix.lower() not in ('.mp4', '.webm'):
                        raise ValueError('Registered video is missing')
                    # Hashes come from the recorder's final receipt. Do not read
                    # a large video again on each progress poll.
                    item['path'] = _relative(file)
                except (ValueError, OSError):
                    item.pop('path', None); item['status'] = 'unavailable'
            item['actions'] = self._actions(episode, item)
            found.append(item)
        return found

    def _actions(self, episode, media=None):
        ref = episode.get('transitions')
        if not isinstance(ref, dict) or not ref.get('path'):
            return []
        path = local(ref['path'])
        try:
            stamp = (path.stat().st_size, path.stat().st_mtime_ns)
            cached = self._action_cache.get(str(path))
            if cached and cached[0] == stamp:
                actions = deepcopy(cached[1])
            else:
                actions = []
                for transition in rows(path):
                    action = transition.get('execution', {}).get('action', {})
                    if action.get('kind') != 'play_card':
                        continue
                    actions.append({'tick': transition.get('actual_execution_tick'),
                                    'planned_tick': transition.get('planned_tick'),
                                    'card_id': action.get('card'), 'owner': transition.get('native_owner'),
                                    'x': action.get('x'), 'y': action.get('y'),
                                    'card_consumed': transition.get('card_consumed'),
                                    'video_seconds': None})
                self._action_cache[str(path)] = (stamp, deepcopy(actions))
            if media and media.get('path'):
                from tools.cloud.native_media import video_interval_for_tick
                for action in actions:
                    interval = video_interval_for_tick(media, action['tick'])
                    if interval:
                        action.update(interval)
            return actions
        except (OSError, ValueError, KeyError):
            return []

    def _resolve(self, payload):
        if not isinstance(payload, dict):
            raise ValueError('Research request must be a JSON object')
        allowed = set(DEFAULTS) | {'checkpoint'}
        if set(payload) - allowed:
            raise ValueError('Unsupported research fields: ' + ', '.join(sorted(set(payload) - allowed)))
        config = {**DEFAULTS, **payload}
        if config['mode'] not in ('offline', 'evaluate', 'full') or config['model'] != 'gru-c':
            raise ValueError('Choose mode offline/evaluate/full and model gru-c')
        for name, low, high in (('epochs', 1, 50), ('hidden', 4, 512), ('seed', 0, 2**31 - 10001),
                                ('games', 2, 20), ('max_tick', 91, 1_000_000)):
            if type(config[name]) is not int or not low <= config[name] <= high:
                raise ValueError(f'{name} must be an integer in {low}..{high}')
        if config['games'] % 2:
            raise ValueError('Native evaluation uses paired owners: games must be even')
        for name, low, high in (('play_weight', .001, 1000), ('wall_seconds', 1, 3600)):
            if type(config[name]) not in (int, float) or not math.isfinite(config[name]) or not low <= config[name] <= high:
                raise ValueError(f'{name} must be finite in {low}..{high}')
        if type(config['record_video']) is not bool:
            raise ValueError('record_video must be boolean')
        indices = config['record_game_indices']
        # An inherited recording selection is irrelevant when recording is
        # disabled (or this is offline-only). Explicit malformed selections
        # still fail, rather than silently accepting a misspelled request.
        if 'record_game_indices' not in payload and (not config['record_video'] or config['mode'] == 'offline'):
            indices = []
        if (not isinstance(indices, list) or any(type(x) is not int or not 0 <= x < config['games']
                                                for x in indices) or len(set(indices)) != len(indices)):
            raise ValueError('record_game_indices must be distinct game indices within the requested budget')
        if config['record_video'] and not indices and config['mode'] != 'offline':
            raise ValueError('Recording requires at least one preselected game')
        config['record_game_indices'] = sorted(indices) if config['record_video'] else []
        if config['mode'] == 'full' and not config['record_video']:
            raise ValueError('Full experiment includes native recording; record_video must be true')
        data = _dataset(config['dataset'])
        if data['offline_only'] and config['mode'] != 'offline':
            raise ValueError('portable-pilot is offline-only; its native version differs from current Android')
        config['dataset_identity'] = data
        config.update(variant='C', observation_profile='fair', control_ticks=10,
                      sequence_length=32, burnin=8, batch_size=8,
                      algorithm='supervised sequence behavior cloning; not reinforcement learning',
                      evaluation_seed=config['seed'] + 10000, opponent='cycle', paired_owners=True,
                      rendered=config['record_video'] and config['mode'] != 'offline',
                      native_media_backend='frameoutput',
                      test_evaluated=False, strength_improvement_claim=False)
        if config['mode'] == 'evaluate':
            if not config.get('checkpoint'):
                raise ValueError('evaluate requires a saved checkpoint path from the catalog')
            config['checkpoint'] = _checkpoint(config['checkpoint'], data['version_fingerprint_sha256'])
        elif config.get('checkpoint'):
            raise ValueError('checkpoint is accepted only in evaluate mode; training creates a new model')
        return config

    def submit(self, payload):
        config = self._resolve(payload)
        with self._mutex:
            if self._closed:
                raise RuntimeError('Research service is closed')
            if not self._worker.is_alive():
                raise RuntimeError('Research executor stopped; inspect service logs and restart to mark unfinished tasks interrupted')
            stamp = datetime.now(timezone.utc).strftime('%Y%m%dt%H%M%S%f')
            run_id = f'run-{stamp}z-{uuid.uuid4().hex[:8]}'
            folder = self._path(run_id); folder.mkdir(exist_ok=False)
            record = {'schema': SCHEMA, 'id': run_id, 'run_id': run_id,
                      'run_dir': _relative(folder), 'status': 'queued', 'stage': 'queued',
                      'created_at': now(), 'updated_at': now(), 'request': deepcopy(payload),
                      'config': config, 'stages': [], 'checkpoint': None, 'summary': {},
                      'code': _code_identity(), 'cancel_requested': False,
                      'native_execution_verified': False, 'native_media_recording_complete': False,
                      'browser_playback_verified': False, 'strength_improvement_claim': False}
            write(folder / 'request.json', payload)
            write(folder / 'configuration.json', config)
            write(folder / 'run.json', record)
            append(folder / 'events.jsonl', {'at': now(), 'event': 'submitted'})
            self._cancel_events[run_id] = threading.Event()
            self._queue.put(run_id)
            return deepcopy(record)

    def _update(self, run_id, **updates):
        with self._mutex:
            folder = self._path(run_id); record = read(folder / 'run.json')
            record.update(updates, updated_at=now()); write(folder / 'run.json', record)
            append(folder / 'events.jsonl', {'at': now(), 'event': 'state',
                                           'status': record['status'], 'stage': record['stage']})
            return record

    def cancel(self, run_id):
        with self._mutex:
            record = self.get_run(run_id)
            if record['status'] in TERMINAL:
                return record
            event = self._cancel_events.get(run_id)
            if event:
                event.set()
            status = 'cancelling' if self._active == run_id else 'cancelled'
            return self._update(run_id, status=status, cancel_requested=True,
                                **({'finished_at': now()} if status == 'cancelled' else {}))

    def _check_cancel(self, run_id):
        if self._cancel_events[run_id].is_set():
            raise RunCancelled('Cancelled by the user; no automatic reset or retry')

    def _run_child(self, run_id, stage, arguments, env=None, allowed_returncodes=(0,)):
        folder = self._path(run_id); logs = folder / 'logs'; logs.mkdir(exist_ok=True)
        stdout = logs / (stage + '.stdout.log'); stderr = logs / (stage + '.stderr.log')
        command = [str(ROOT / '.venv-cloud/bin/python'), '-B', *map(str, arguments)]
        child_env = {**os.environ, 'PYTHONUNBUFFERED': '1', 'PYTHONDONTWRITEBYTECODE': '1',
                     'PYTHONUTF8': '1', **(env or {})}
        receipt = {'stage': stage, 'command': command, 'started_at': now(),
                   'stdout': _relative(stdout), 'stderr': _relative(stderr)}
        cancel_grace = 180 if stage == 'evaluate' else 15
        receipt['cancel_cleanup_grace_seconds'] = cancel_grace
        self._update(run_id, stage=stage)
        with stdout.open('wb') as out, stderr.open('wb') as err:
            with self._mutex:
                self._check_cancel(run_id)
                process = subprocess.Popen(command, cwd=ROOT, env=child_env, stdout=out, stderr=err,
                                           start_new_session=os.name != 'nt')
                self._child = process
                receipt['pid'] = process.pid
                record = read(folder / 'run.json'); record['stages'].append(receipt)
                write(folder / 'run.json', record)
            interrupted = None
            while process.poll() is None:
                if self._cancel_events[run_id].wait(.1):
                    if interrupted is None:
                        interrupted = time.monotonic()
                        if os.name == 'nt':
                            process.terminate()
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGINT)
                            except ProcessLookupError:
                                pass
                    elif time.monotonic() - interrupted > cancel_grace:
                        # Only this service's newly-created child group is terminated.
                        if os.name == 'nt':
                            process.kill()
                        else:
                            try:
                                os.killpg(process.pid, signal.SIGKILL if time.monotonic() - interrupted > cancel_grace + 5 else signal.SIGTERM)
                            except ProcessLookupError:
                                pass
                    time.sleep(.05)
            receipt.update(returncode=process.returncode, finished_at=now(),
                           cancel_signal_sent=interrupted is not None)
            with self._mutex:
                self._child = None
                record = read(folder / 'run.json'); record['stages'][-1] = receipt
                write(folder / 'run.json', record)
        self._check_cancel(run_id)
        if process.returncode not in allowed_returncodes:
            raise RuntimeError(f'{stage} exited {process.returncode}; inspect {receipt["stderr"]}')
        return receipt

    def _execute(self, run_id):
        folder = self._path(run_id)
        record = read(folder / 'run.json'); config = record['config']; data = config['dataset_identity']
        # A queued request cannot silently consume modified source data.
        if reference(data['path']) != data['metadata'] or sha(data['rows']['path']) != data['rows']['sha256']:
            raise ValueError('Dataset changed after submission; submit a new run after reviewing the data')
        self._check_cancel(run_id)
        if config['mode'] in ('offline', 'full'):
            self._run_child(run_id, 'train', ['tools/native_loop.py', 'train', '--metadata', data['path'],
                '--output', _relative(folder / 'model'), '--epochs', config['epochs'],
                '--hidden', config['hidden'], '--seed', config['seed'], '--play-weight', config['play_weight']])
            report = read(folder / 'model/report.json')
            if not report.get('completed') or report.get('test_evaluated') is not False:
                raise ValueError('Training did not complete the declared train/validation-only budget')
            checkpoint = _checkpoint(report['checkpoint']['path'], data['version_fingerprint_sha256'])
        else:
            checkpoint = _checkpoint(config['checkpoint']['path'], data['version_fingerprint_sha256'])
            if checkpoint != config['checkpoint']:
                raise ValueError('Checkpoint changed after submission')
        self._update(run_id, checkpoint=checkpoint)
        self._run_child(run_id, 'offline_validation', ['tools/cloud/research_worker.py', 'validate',
            '--metadata', data['path'], '--checkpoint', checkpoint['path'],
            '--output', _relative(folder / 'offline_validation.json')])
        validation = read(folder / 'offline_validation.json')
        if validation.get('status') != 'completed' or validation.get('checkpoint') != checkpoint:
            raise ValueError('Independent-process checkpoint validation identity failed')
        summary = {'offline_validation': validation.get('metrics'), 'test_evaluated': False,
                   'native_evaluation': 'not_requested', 'strength_improvement_claim': False}
        self._update(run_id, summary=summary)
        if config['mode'] == 'offline':
            return
        self._check_cancel(run_id)
        self._update(run_id, stage='runtime')
        from tools.cloud.research_runtime import ensure_runtime
        runtime = ensure_runtime(_relative(folder / 'runtime'), self.runtime_settings,
                                 cancel_event=self._cancel_events[run_id],
                                 required_mode=None if config['rendered'] else 'headless')
        write(folder / 'runtime.json', runtime)
        self._check_cancel(run_id)
        connection = runtime['connection']
        version = runtime.get('version_fingerprint')
        if not version:
            from royale_phase2.connection import check_connection
            checks = check_connection(connection, connect=False)
            write(folder / 'runtime_version.json', checks)
            version = checks.get('version_fingerprint')
        if version != data['version_fingerprint_sha256']:
            raise ValueError('Prepared Android version differs from the dataset/checkpoint; no game was reset')
        arguments = ['tools/native_loop.py', 'evaluate', '--output', _relative(folder / 'evaluation'),
                     '--checkpoint', checkpoint['path'], '--games', config['games'],
                     '--seed', config['evaluation_seed'], '--opponent', 'cycle',
                     '--max-tick', config['max_tick'], '--wall-seconds', config['wall_seconds']]
        if config['rendered']:
            arguments.append('--rendered')
        if config['record_video']:
            arguments += ['--record-video', '--record-backend', config['native_media_backend'], '--record-game-indices',
                          ','.join(map(str, config['record_game_indices']))]
            # Recording observes the exact device prepared by the runtime.
            from tools.cloud.research_runtime import settings_for
            device = settings_for(self.runtime_settings)
            for key in ('adb', 'adb_port', 'serial', 'root_shell'):
                arguments += ['--record-' + key.replace('_', '-'), str(device[key])]
        try:
            self._run_child(run_id, 'evaluate', arguments,
                            env={'ROYALE_NATIVE_CONNECTION': str(local(connection))},
                            allowed_returncodes=(0, 2))
        finally:
            # Even a media/timeout failure retains numerical and partial video evidence.
            evaluation = _read_if(folder / 'evaluation/report.json')
            if evaluation:
                summary['native_evaluation'] = evaluation
                self._update(run_id, summary=summary,
                             native_execution_verified=evaluation.get('native_execution_verified', False))
        if (not evaluation or evaluation.get('status') not in ('completed', 'incomplete') or
                evaluation.get('natural_terminal_games') != config['games'] or
                evaluation.get('native_execution_verified') is not True):
            raise ValueError('Requested natural-terminal native games did not complete; partial results retained')
        if _checkpoint(checkpoint['path']) != checkpoint:
            raise ValueError('Checkpoint changed during native evaluation')
        if config['record_video']:
            episodes = [read(path) for path in sorted((folder / 'evaluation').glob('game-*/result.json'))]
            by_index = {item['index']: item for item in episodes}
            requested = [by_index.get(index, {}).get('media', {}) for index in config['record_game_indices']]
            if (evaluation.get('media_complete') is not True or not requested or
                    any(item.get('status') != 'complete' or item.get('recording_complete') is not True or
                        not item.get('path') or not local(item['path']).is_file() for item in requested)):
                raise ValueError('Requested original native video did not complete; numerical results and partial media retained')
            self._update(run_id, native_media_recording_complete=True,
                         browser_playback_verified=False)
        if evaluation.get('status') != 'completed':
            raise ValueError('Native batch did not complete its declared contract; inspect evaluation/report.json')

    def _work(self):
        try:
            while True:
                run_id = self._queue.get()
                if run_id is None:
                    return
                with self._mutex:
                    record = read(self._path(run_id) / 'run.json')
                    if record['status'] in TERMINAL:
                        self._cancel_events.pop(run_id, None)
                        continue
                    self._active = run_id
                    self._update(run_id, status='running', stage='starting', started_at=now())
                try:
                    self._execute(run_id)
                    self._check_cancel(run_id)
                    self._update(run_id, status='completed', stage='completed', finished_at=now())
                except RunCancelled as exc:
                    self._update(run_id, status='cancelled', stage='cancelled', error=str(exc), finished_at=now())
                except Exception as exc:
                    write(self._path(run_id) / 'failure.json', {
                        'error': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc(), 'at': now()})
                    self._update(run_id, status='cancelled' if self._cancel_events[run_id].is_set() else 'failed',
                                 stage='failed', error=f'{type(exc).__name__}: {exc}', finished_at=now())
                finally:
                    with self._mutex:
                        self._active = None
                        self._cancel_events.pop(run_id, None)
        finally:
            self._lease.close()

    def stop_runtime(self):
        with self._mutex:
            if self._closed:
                raise RuntimeError('Research service is closed')
            if self._active or any(r['status'] not in TERMINAL for r in self.list_runs()):
                raise RuntimeError('Cancel or finish all research tasks before stopping Android')
            from tools.cloud.research_runtime import stop_runtime
            output = self.root / ('stop-' + uuid.uuid4().hex[:12])
            return stop_runtime(_relative(output), self.runtime_settings)

    def close(self):
        with self._mutex:
            if self._closed:
                return
            self._closed = True
            for record in self.list_runs():
                if record['status'] not in TERMINAL:
                    self.cancel(record['run_id'])
            self._queue.put(None)
        self._worker.join(timeout=200)
        # If native preparation has not yet acknowledged cancellation, retain
        # the executor lease until its thread exits. Never stop a shared VM.


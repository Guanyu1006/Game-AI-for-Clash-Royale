"""One RPC owner for actual rendered Android battles and journal replay.

HTTP only reads cached state/frame bytes and submits jobs. Battle physics and
rendering remain in the pinned Android library; there is no arena fallback.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import queue
import re
import secrets
import shutil
import threading
import time

from royale_phase3.common import ROOT, append, digest, local, native_lock, read, reference, write
from royale_phase3.actions import PublicTeacher, match, validate
from .human import CARD_NAMES
from .viewer import semantic_packet


def now():
    return datetime.now(timezone.utc).isoformat()


def bounded_int(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{label} 必须为 {low}—{high} 的整数')
    return value


class CloudNativeSession:
    def __init__(self, output, *, archive_root='outputs/cloud/native-archives', connection=None,
                 adb='.cache/native-tools/bin/adb', adb_port=5038, serial='127.0.0.1:15555',
                 root_shell='su0', frame_wait=1.1, capture_ticks=20, max_tick=7200,
                 engine_factory=None, bridge=None, lease_factory=native_lock, offline=False):
        if not 0 <= frame_wait <= 10:
            raise ValueError('frame_wait must be 0..10 seconds')
        bounded_int(capture_ticks, 'capture_ticks', 1, 200)
        bounded_int(max_tick, 'max_tick', 100, 100000)
        self.folder = local(output); self.folder.mkdir(parents=True, exist_ok=False)
        self.archive_root = local(archive_root); self.archive_root.mkdir(parents=True, exist_ok=True)
        self.connection = connection; self.engine_factory = engine_factory
        self.offline = bool(offline)
        self.bridge = bridge
        self.bridge_args = dict(adb=adb, adb_port=adb_port, serial=serial, root_shell=root_shell)
        self.frame_wait = frame_wait; self.capture_ticks = capture_ticks; self.max_tick = max_tick
        self.lease_factory = lease_factory
        self.lock = threading.RLock(); self.queue = queue.Queue(maxsize=16)
        self.closed = threading.Event(); self.pause_requested = threading.Event()
        self.csrf_token = secrets.token_hex(24)
        self.engine = None; self.native_state = None; self.replayer = None; self.source_replayer = None
        self.owner = 1; self.seed = None; self.opponent = None; self.mode = 'live'
        self.running = False; self.quantum = 10; self.frame_bytes = None
        self.active_dir = None; self.archive_id = None; self.replay_source_id = None
        self.frame_index = 0; self.last_frame_tick = None; self.event_origin = None
        self.frame_rows = []
        self._frame_catalog_cache = {}
        self.feedback = []; self.last_action = None
        self.status = dict(ready=False, busy=True, running=False, terminal=False, mode='live',
                           tick=None, generation=None, error=None, jobs={}, frame={'available': False},
                           status_message='正在校验原生资源与连接；不会自动建局')
        write(self.folder/'configuration.json', dict(schema='cloud-native-session.v1',
              connection=str(connection) if connection else None, **self.bridge_args,
              frame_wait_seconds=frame_wait, capture_ticks=capture_ticks, max_tick=max_tick,
              native_rpc_owner='none' if self.offline else 'single_worker', battle_physics='Android libg',
              training_started=False, native_rendered=not self.offline, offline=self.offline))
        if self.offline:
            self.mode = 'archive'; self.thread = None
            self.status.update(mode='archive', ready=False, busy=False, offline=True,
                               status_message='只读保存画面模式；不会启动ADB、获取原生锁或连接引擎')
            return
        self.thread = threading.Thread(target=self._worker, name='cloud-native-rpc-owner', daemon=True)
        self.thread.start()

    def _archive(self, archive_id):
        if not isinstance(archive_id, str) or not re.fullmatch(r'[a-z0-9][a-z0-9._-]{0,95}', archive_id):
            raise ValueError('无效档案编号')
        folder = (self.archive_root/archive_id).resolve()
        folder.relative_to(self.archive_root)
        if not folder.is_dir():
            raise ValueError('没有此档案')
        return folder

    def archives(self):
        values = []
        for path in sorted(self.archive_root.glob('*/manifest.json'), reverse=True):
            try:
                folder = self._archive(path.parent.name)
                value = read(folder/'manifest.json')
                values.append({k: value.get(k) for k in ('archive_id', 'kind', 'created', 'initial_tick',
                              'end_tick', 'frame_count', 'natural_terminal', 'complete_frame_coverage',
                              'capture_ticks', 'native_directory', 'replay_source_id')})
            except (ValueError, OSError, KeyError):
                continue
        return values

    def manifest(self, archive_id):
        folder = self._archive(archive_id); value = read(folder/'manifest.json')
        frames = self._saved_frames(folder, manifest=value)
        return {**value, 'frames': [{**row, 'index': i} for i, row in enumerate(frames)],
                'animation_url': f'/api/native/archive/{archive_id}/animation.png'
                    if (folder/'animation.png').is_file() else None}

    def _saved_frames(self, folder, *, manifest=None):
        manifest = read(folder/'manifest.json') if manifest is None else manifest
        info = manifest.get('frame_index') or {}
        sealed = manifest.get('sealed') is True
        expected_sha = info.get('sha256') if isinstance(info, dict) else None
        if sealed and not expected_sha and manifest.get('frame_count', 0):
            raise ValueError('封存档案缺少画面索引哈希')
        if expected_sha is not None and (not isinstance(expected_sha, str)
                or not re.fullmatch(r'[a-f0-9]{64}', expected_sha)):
            raise ValueError('画面索引SHA256格式无效')
        path = local(folder/'frames/index.jsonl')
        path.relative_to(folder.resolve())
        if expected_sha is not None and local(info.get('path', '')) != path:
            raise ValueError('封存画面索引路径与哈希引用不一致')
        if not path.is_file():
            if expected_sha is not None:
                raise ValueError('封存画面索引文件丢失')
            return []
        if sealed and expected_sha is None:
            raise ValueError('封存档案缺少画面索引哈希')
        stat = path.stat(); key = (stat.st_mtime_ns, stat.st_size, expected_sha, sealed)
        with self.lock:
            saved = self._frame_catalog_cache.get(str(path))
            if saved and saved[0] == key:
                return saved[1]
        raw = path.read_bytes()
        if expected_sha is not None and hashlib.sha256(raw).hexdigest() != expected_sha:
            raise ValueError('封存画面索引哈希不匹配')
        # A live writer may be midway through one JSONL record. A sealed
        # index must instead parse all hash-checked bytes, including its tail.
        if not sealed and expected_sha is None and raw and not raw.endswith(b'\n'):
            raw = raw.rsplit(b'\n', 1)[0] if b'\n' in raw else b''
        frames = [json.loads(line) for line in raw.splitlines() if line.strip()]
        with self.lock:
            self._frame_catalog_cache[str(path)] = (key, frames)
        return frames

    def archive_frame(self, archive_id, index):
        folder = self._archive(archive_id); frames = self._saved_frames(folder)
        bounded_int(index, '帧序号', 0, max(0, len(frames)-1))
        if not frames:
            raise ValueError('档案还没有原生画面')
        row = frames[index]
        path = local(row['path']); path.relative_to(folder)
        data = path.read_bytes()
        if hashlib.sha256(data).hexdigest() != row['sha256']:
            raise ValueError('保存画面哈希不匹配')
        return data

    def public(self):
        with self.lock:
            replay = self.replayer.status() if self.replayer else None
            if replay:
                replay.update(archive_id=self.replay_source_id, max_tick=replay['end_tick'],
                              seek_mode='reset_and_replay',
                              verified=self.replayer.report().get('saved_state_trajectory_verified', False))
            return {**deepcopy(self.status), 'csrf_token': self.csrf_token,
                    'owner': self.owner, 'seed': self.seed, 'mode': self.mode,
                    'archive_id': self.archive_id, 'feedback': deepcopy(self.feedback[-16:]),
                    'last_action': deepcopy(self.last_action), 'capture_ticks': self.capture_ticks,
                    'clock_control': '单 worker 精确推进；实时表示持续推进，不保证现实时间 20FPS',
                    'replay': replay,
                    'source_replay': self.source_replayer.status() if self.source_replayer else None}

    def latest_frame(self):
        with self.lock:
            if self.frame_bytes is None:
                raise ValueError('尚未取得 Android 原生画面')
            return self.frame_bytes

    def submit(self, command, data=None):
        if self.offline:
            raise ValueError('当前为离线保存画面模式，不能控制原生引擎')
        if command not in ('reset', 'play', 'step', 'run', 'pause', 'save', 'replay_open', 'replay_seek',
                           'source_open', 'source_seek', 'shutdown'):
            raise ValueError('未知原生命令')
        data = {} if data is None else data
        if not isinstance(data, dict):
            raise ValueError('命令参数必须为对象')
        if command == 'pause':
            self.pause_requested.set()
        job = secrets.token_hex(8)
        with self.lock:
            if self.closed.is_set():
                raise ValueError('原生会话已关闭')
            if self.status['busy'] and command not in ('pause', 'shutdown'):
                raise ValueError('上一个原生操作仍在执行，请等待状态更新')
            self.status['jobs'][job] = {'command': command, 'status': 'queued'}
            if command != 'pause':
                self.status['busy'] = True
            self.status['error'] = None
        try:
            self.queue.put_nowait((job, command, deepcopy(data)))
        except queue.Full:
            with self.lock:
                self.status['busy'] = False
            raise ValueError('控制队列已满')
        return job

    def _new_archive(self, kind):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ').lower()
        self.archive_id = f'{kind}-{stamp}-{secrets.token_hex(3)}'
        self.active_dir = self.archive_root/self.archive_id
        (self.active_dir/'native').mkdir(parents=True)
        (self.active_dir/'frames').mkdir()
        self.frame_index = 0; self.last_frame_tick = None; self.event_origin = None
        self.frame_rows = []
        self.engine.trace_path = self.active_dir/'native/wire.jsonl'
        self.archive_created = now(); self.archive_kind = kind
        self.feedback = []; self.last_action = None
        self._initial_tick = None

    def _state_record(self, state):
        self.native_state = deepcopy(state)
        if self.event_origin is None:
            self.event_origin = state['next']-1
            self._initial_tick = state['tick']
        events = [deepcopy(self.engine._plays[key]) for key in sorted(self.engine._plays)]
        packet = semantic_packet(state, events, self.event_origin)
        self.semantic_sha = digest(packet)
        append(self.active_dir/'native/states.jsonl', state)
        append(self.active_dir/'native/states-bound.jsonl', dict(raw_capture=state,
               semantic_sha256=self.semantic_sha, all_observed_card_events=events, recorded_at=now()))
        self._publish()

    def _publish(self):
        if self.native_state is None:
            return
        actor = self.engine.observe_visible(self.owner)
        names = {**CARD_NAMES, 27000000: '加农炮'}
        hand = [dict(slot=c['slot'], card_id=c['card'], name=names.get(c['card'], str(c['card'])),
                     cost=c['cost'], affordable=c['cost']*10000 <= actor['own_elixir_raw']) for c in actor['hand']]
        with self.lock:
            self.status.update(ready=True, tick=self.native_state['tick'],
                generation=self.native_state['generation'], terminal=self.native_state['finalized'],
                result=self.native_state['result'], crowns=self.native_state['crowns'],
                truncated=not self.native_state['finalized'] and self.native_state['tick'] >= self.max_tick,
                running=self.running, hand=hand, own_elixir=actor['own_elixir_raw']/10000,
                towers=deepcopy(actor.get('towers', [])))

    def _capture(self, force=False):
        tick = self.native_state['tick']
        if not force and self.last_frame_tick is not None and tick-self.last_frame_tick < self.capture_ticks:
            return
        if self.frame_index >= 10000 or shutil.disk_usage(ROOT).free < 256*1024*1024:
            raise RuntimeError('本次画面预算耗尽，已停止推进并保留档案')
        if self.frame_wait:
            time.sleep(self.frame_wait)
        before = deepcopy(self.engine.debug_state())
        png = self.bridge.capture()
        after = self.engine._capture(before['generation'], before['next'])
        if (after['generation'], after['tick']) != (before['generation'], before['tick']):
            raise RuntimeError('截图期间原生时钟或generation改变，拒绝帧绑定')
        from .cloud_frames import frame_info
        capture = getattr(self.bridge, 'last_capture', None) or {}
        info = deepcopy(capture['info']) if 'info' in capture else frame_info(png)
        image = self.active_dir/'frames'/f'{self.frame_index:06d}.png'
        with image.open('xb') as stream:
            stream.write(png)
        frame = dict(index=self.frame_index, sequence=self.frame_index, tick=tick,
                     generation=before['generation'], source='android_framebuffer',
                     path=image.relative_to(ROOT).as_posix(), captured_at=now(), **info,
                     native_semantic_sha256=self.semantic_sha,
                     source_state_tick=before['tick'], state_clock_unchanged=True,
                     capture_wall_seconds=capture.get('elapsed_seconds'),
                     mapping=None, available=True,
                     display_latency_note='截图前后逻辑tick相同；未证明像素呈现零延迟')
        append(self.active_dir/'frames/index.jsonl', frame)
        self.frame_rows.append(frame)
        self.frame_index += 1; self.last_frame_tick = tick
        with self.lock:
            self.frame_bytes = png; self.status['frame'] = frame
        self._save(seal=bool(self.native_state['finalized']))

    def _save(self, seal=True):
        if self.active_dir is None or self.native_state is None:
            return None
        journal = self.engine.export_journal()
        write(self.active_dir/'native/initialization.json', journal['match'])
        write(self.active_dir/'native/journal.json', journal)
        write(self.active_dir/'native/native_result.json', self.engine.result())
        write(self.active_dir/'native/execution_feedback.json', self.feedback)
        source_files = []
        if self.source_replayer:
            write(self.active_dir/'source-plan.json', self.source_replayer.plan.to_dict())
            write(self.active_dir/'source-execution.json', self.source_replayer.report())
            source_files = [reference(self.active_dir/name) for name in
                            ('source-plan.json', 'source-execution.json')] if seal else []
        frames = self.frame_rows
        path = self.active_dir/'frames/index.jsonl'
        ticks = [r['tick'] for r in frames]
        natural = bool(self.native_state['finalized'])
        coverage = bool(natural and ticks and ticks[0] == self._initial_tick and
                        ticks[-1] == self.native_state['tick'] and
                        all(0 <= b-a <= self.capture_ticks for a,b in zip(ticks,ticks[1:])))
        report = dict(schema='cloud-native-archive.v1', archive_id=self.archive_id,
                      kind=self.archive_kind, created=self.archive_created, updated=now(),
                      native_directory=(self.active_dir/'native').relative_to(ROOT).as_posix(),
                      version_fingerprint=journal['version_fingerprint'], owner=self.owner, seed=self.seed,
                      initial_tick=self._initial_tick, end_tick=self.native_state['tick'],
                      natural_terminal=natural, frame_count=len(frames), capture_ticks=self.capture_ticks,
                      complete_frame_coverage=coverage, capture_fps_game_time=20/self.capture_ticks,
                      full_20hz_samples=coverage and self.capture_ticks == 1,
                      full_20hz_frames=False, fresh_frame_rate_verified=False,
                      frame_rate_limitation='逐tick采样不证明Android每秒呈现20张新鲜画面',
                      source='Actual rendered Android framebuffer; no generated game imagery',
                      native_result=self.engine.result(), replay_source_id=self.replay_source_id,
                      sealed=bool(seal),
                      journal=reference(self.active_dir/'native/journal.json'),
                      source_files=[reference(self.active_dir/'native'/name) for name in
                          ('journal.json', 'initialization.json', 'states.jsonl', 'states-bound.jsonl',
                           'native_result.json', 'execution_feedback.json')] + source_files if seal else [],
                      public_source_reconstruction=bool(self.source_replayer),
                      source_progress=self.source_replayer.status() if self.source_replayer else None,
                      frame_index=(reference(path) if seal else {'path': path.relative_to(ROOT).as_posix(),
                                    'mutable_recording': True}) if path.exists() else None,
                      frame_pixels_non_black=bool(frames and all(f.get('non_black') for f in frames)),
                      rendering_equivalence='not an independent official pixel fidelity test')
        write(self.active_dir/'manifest.json', report)
        if self.replayer:
            write(self.active_dir/'replay-verification.json', self.replayer.report())
        return report

    def _reset(self, data):
        self.running = False
        if self.native_state is not None:
            self._seal_previous()
        if self.replayer:
            self.replayer.close(); self.replayer = None
        if self.source_replayer:
            self.source_replayer.close(); self.source_replayer = None
        self.mode = 'live'; self.replay_source_id = None
        self.owner = bounded_int(data.get('owner', 1), 'owner', 0, 1)
        self.seed = bounded_int(data.get('seed', 610024), 'seed', 0, 2147483647)
        self.opponent = PublicTeacher(data.get('opponent', 'cycle'), self.seed*2+1-self.owner)
        self._new_archive('live')
        self.native_state = None; self.pause_requested.clear()
        state = self.engine.reset(match(self.seed))
        self._state_record(state); self._capture(force=True)

    def _play(self, data):
        if self.mode != 'live':
            raise ValueError('原生重演时不能注入新的人工动作，请新建对局')
        if self.native_state is None or self.native_state['finalized'] or self.native_state['tick'] >= self.max_tick:
            raise ValueError('请先创建活动对局')
        if data.get('generation') != self.native_state['generation']:
            raise ValueError('对局已重置，请重新选牌')
        tick = bounded_int(data.get('expected_tick'), 'expected_tick', 0, self.max_tick)
        age = self.native_state['tick']-tick
        if age < 0 or age > (40 if self.running else 0):
            raise ValueError('观察已过期，请刷新后重新选牌；也可先暂停')
        actor = self.engine.observe_visible(self.owner)
        slot = bounded_int(data.get('slot'), 'slot', 0, 3)
        card = next((c for c in actor['hand'] if c['slot'] == slot), None)
        if card is None or data.get('card_id') != card['card']:
            raise ValueError('手牌槽已变化，请重新选牌')
        action = validate(actor, dict(kind='play_card', slot=slot, x=data.get('x'), y=data.get('y')))
        receipt = self.engine.schedule_card(self.owner, action['card'], action['x'], action['y'],
                    self.native_state['tick']+1, request_id='browser:'+secrets.token_hex(12))
        self.last_action = dict(action=action, registration=receipt, proposed_at_tick=tick,
                                card_consumed=False, status='registered_not_yet_consumed')
        self._advance(1, capture=False)
        feedback = self.engine.execution_feedback([receipt['request_id']])
        self.feedback.extend(feedback)
        self.last_action.update(card_consumed=bool(feedback and feedback[0]['card_consumed']),
                                feedback=feedback, status='consumed' if feedback and feedback[0]['card_consumed'] else 'not_confirmed')
        self._capture(force=True); self._save()

    def _advance(self, ticks, *, capture=True):
        remaining = ticks
        while remaining > 0:
            if self.native_state is None:
                raise ValueError('请先创建或打开原生对局')
            before = self.native_state['tick']
            distance = self.capture_ticks
            if self.last_frame_tick is not None:
                distance = max(1, self.capture_ticks-(before-self.last_frame_tick))
            chunk = min(remaining, distance)
            self._advance_once(chunk)
            advanced = self.native_state['tick']-before
            if capture:
                self._capture(force=self.native_state['finalized'] or not self.running and remaining <= chunk)
            if not advanced or self.native_state['finalized']:
                break
            remaining -= advanced
            if self.pause_requested.is_set():
                self.running = False
                self._publish()
                break

    def _advance_once(self, ticks):
        if self.native_state is None:
            raise ValueError('请先创建或打开原生对局')
        if self.native_state['finalized'] or self.native_state['tick'] >= self.max_tick:
            self.running = False; self._publish(); return
        ticks = min(ticks, self.max_tick-self.native_state['tick'])
        if self.mode == 'replay':
            state = self.replayer.step(ticks)
            self.native_state = deepcopy(state)
            self.feedback = [row['replayed_feedback'] for row in self.replayer.report().get('run', {}).get('feedback', [])]
        elif self.mode == 'source':
            state = self.source_replayer.step(ticks)
            self.native_state = deepcopy(state)
            self.feedback = deepcopy(self.source_replayer.feedback)
        else:
            other = self.opponent.decide(self.engine.observe_visible(1-self.owner))
            receipt = None
            if other['kind'] == 'play_card':
                receipt = self.engine.schedule_card(1-self.owner, other['card'], other['x'], other['y'],
                            self.native_state['tick']+1, request_id='browser-opponent:'+secrets.token_hex(12))
            state = self.engine.advance_exact_ticks(1); self._state_record(state)
            if receipt:
                self.feedback.extend(self.engine.execution_feedback([receipt['request_id']]))
            if ticks > 1 and not state['finalized']:
                self._state_record(self.engine.advance_exact_ticks(ticks-1))
        if (self.native_state['finalized'] or self.native_state['tick'] >= self.max_tick
                or self.replayer and self.native_state['tick'] >= self.replayer.source.end_tick):
            self.running = False
        self._publish()

    def _seal_previous(self):
        try:
            return self._save()
        except Exception as exc:
            if self.active_dir:
                write(self.active_dir/'interruption.json', dict(error=f'{type(exc).__name__}: {exc}',
                       at=now(), last_cached_state=self.native_state,
                       next_action='Explicit reset requested; unknown mutations are never retried'))

    def _preserve_source_failure(self):
        # A failed RPC may invalidate backend.result/export. Preserve the
        # controller's cached evidence without making another native request
        # or overwriting files pinned by an earlier sealed manifest.
        if self.source_replayer and self.active_dir:
            write(self.active_dir/'source-failure.json', dict(at=now(),
                  execution=self.source_replayer.report(), retry=False))

    def _replay_open(self, data):
        from .cloud_replay import NativeReplaySource, CloudNativeReplay
        archive_id = data.get('archive_id'); folder = self._archive(archive_id)
        if archive_id == self.archive_id and self.native_state is not None:
            self.running = False; self._save()
        manifest = read(folder/'manifest.json')
        for saved in manifest.get('source_files', []) + ([manifest['journal']] if 'journal' in manifest else []):
            if reference(saved['path'])['sha256'] != saved['sha256']:
                raise ValueError('原生回放来源在登记后发生变化，拒绝重演')
        source = NativeReplaySource.load(manifest['native_directory'])
        pinned = {str(local(ref['path'])): ref['sha256'] for ref in manifest.get('source_files', [])}
        if 'journal' in manifest:
            pinned[str(local(manifest['journal']['path']))] = manifest['journal']['sha256']
        if any(pinned.get(str(local(ref['path']))) != ref['sha256'] for ref in source.files):
            raise ValueError('档案路径与锁定的原生来源文件不一致')
        if (manifest.get('version_fingerprint') != source.journal['version_fingerprint'] or
                manifest.get('initial_tick') != source.initialization_tick or manifest.get('end_tick') != source.end_tick):
            raise ValueError('档案版本或起止tick与来源不一致')
        if self.native_state is not None:
            self._seal_previous()
        if self.replayer:
            self.replayer.close()
        if self.source_replayer:
            self.source_replayer.close(); self.source_replayer = None
        self.running = False; self.mode = 'replay'; self.replay_source_id = archive_id
        self.owner = manifest.get('owner', 1); self.seed = manifest.get('seed')
        self._new_archive('replay')
        self.native_state = None
        self.replayer = CloudNativeReplay(self.engine, source, lease_external=True,
                                          progress_callback=self._state_record,
                                          stop_requested=self.pause_requested.is_set)
        self.replayer.__enter__()
        self.pause_requested.clear()
        state = self.replayer.start(); self.native_state = deepcopy(state)
        self._capture(force=True)

    def _replay_seek(self, data):
        if self.replayer is None:
            raise ValueError('请先打开原生回放档案')
        target = bounded_int(data.get('tick'), 'tick', self.replayer.source.initialization_tick,
                             min(self.max_tick, self.replayer.source.end_tick))
        self.running = False
        if target < self.native_state['tick']:
            self._save(); self._new_archive('replay')
        self.pause_requested.clear()
        with self.lock:
            self.status['status_message'] = f'原生引擎重演至 tick {target}；向后回看会从头重新计算'
        self.native_state = deepcopy(self.replayer.seek(target))
        self.feedback = [row['replayed_feedback'] for row in self.replayer.report().get('run', {}).get('feedback', [])]
        self._capture(force=True)

    def _source_open(self, data):
        from .public_replay import PublicReplayPlan
        from .cloud_source import CloudSourceReplay
        if 'plan_path' in data:
            plan = PublicReplayPlan.load(data['plan_path'])
        elif isinstance(data.get('plan'), dict):
            plan = PublicReplayPlan.from_dict(data['plan'])
        else:
            raise ValueError('请先导入公开事件 JSON 并选择来源')
        # Parse and validate the complete plan before discarding an active match.
        source = CloudSourceReplay(self.engine, plan, max_tick=self.max_tick,
            progress_callback=self._state_record, stop_requested=self.pause_requested.is_set,
            message_callback=self._source_message,
            lease_external=True)
        owner = bounded_int(data.get('owner', 1), 'owner', 0, 1)
        self.running = False
        if self.native_state is not None:
            self._seal_previous()
        if self.replayer:
            self.replayer.close(); self.replayer = None
        if self.source_replayer:
            self.source_replayer.close()
        self.source_replayer = source
        self.mode = 'source'; self.opponent = None
        self.replay_source_id = data.get('source_id')
        self.owner = owner; self.seed = plan.match.get('rndSeed')
        self._new_archive('source')
        self.native_state = None; self.pause_requested.clear()
        state = source.start(self.active_dir/'native')
        self.native_state = deepcopy(state)
        self._capture(force=True)

    def _source_message(self, message):
        with self.lock:
            self.status['status_message'] = message

    def _source_seek(self, data):
        if self.source_replayer is None:
            raise ValueError('请先打开已导入的公开对局')
        source = self.source_replayer
        if not source.valid or self.native_state is None:
            raise ValueError('来源原生对局已失效，请显式重新打开来源；不会自动重发失败动作')
        target = bounded_int(data.get('tick'), 'tick', source.initialization_tick, self.max_tick)
        self.running = False; self.pause_requested.clear()
        with self.lock:
            self.status['status_message'] = f'执行公开来源至 tick {target}；后退会重建初手并从头执行'
        if self.native_state is None or target < self.native_state['tick']:
            if self.native_state is not None:
                self._save()
            self._new_archive('source')
            self.native_state = None
            source.start(self.active_dir/'native')
            self._capture(force=True)
        if target > self.native_state['tick'] and not self.native_state['finalized']:
            self._advance(target-self.native_state['tick'])
        self.feedback = deepcopy(source.feedback)
        self._capture(force=True); self._save()

    def _worker(self):
        try:
            with self.lease_factory():
                from .cloud_frames import CloudAndroidBridge
                from .backend import NativeBattleBackend
                self.bridge = self.bridge or CloudAndroidBridge(**self.bridge_args)
                self.engine = (self.engine_factory or NativeBattleBackend)(rendered=True,
                              connection_config=self.connection, trace_path=self.folder/'startup-wire.jsonl')
                with self.lock:
                    self.status.update(ready=True, busy=False, status_message='连接已核验。请新建对局或打开原生回放。')
                while not self.closed.is_set():
                    try:
                        job, command, data = self.queue.get(timeout=.05 if self.running else .2)
                    except queue.Empty:
                        if self.running:
                            if self.pause_requested.is_set():
                                self.running = False; self.pause_requested.clear(); self._publish()
                            else:
                                try:
                                    self._advance(self.quantum)
                                except Exception as exc:
                                    self.running = False
                                    self._preserve_source_failure()
                                    error = f'{type(exc).__name__}: {exc}'
                                    append(self.folder/'failures.jsonl', dict(command='continuous_advance',
                                           at=now(), error=error, retry=False))
                                    with self.lock:
                                        self.status.update(error=error, running=False, busy=False,
                                            status_message='连续推进失败；保留现场，等待显式新建对局')
                        continue
                    with self.lock:
                        self.status['jobs'][job] = dict(command=command, status='running', started_at=now())
                        self.status['status_message'] = (
                            '正在创建原生场景并对齐初始手牌…' if command == 'source_open'
                            else f'正在执行原生操作：{command}')
                    try:
                        if command == 'reset': self._reset(data)
                        elif command == 'play': self._play(data)
                        elif command == 'step':
                            self.running = False
                            self._advance(bounded_int(data.get('ticks', 10), 'ticks', 1, 200))
                        elif command == 'run':
                            if self.native_state is None: raise ValueError('请先创建对局')
                            self.quantum = bounded_int(data.get('ticks_per_step', 10), 'ticks_per_step', 1, 100)
                            self.pause_requested.clear(); self.running = True
                        elif command == 'pause':
                            self.running = False; self.pause_requested.clear()
                            if self.native_state is not None: self._capture(force=True)
                        elif command == 'save': self._save()
                        elif command == 'replay_open': self._replay_open(data)
                        elif command == 'replay_seek': self._replay_seek(data)
                        elif command == 'source_open': self._source_open(data)
                        elif command == 'source_seek': self._source_seek(data)
                        elif command == 'shutdown': self.closed.set()
                        answer = dict(status='done', command=command, tick=self.native_state['tick'] if self.native_state else None)
                        with self.lock:
                            self.status.update(error=None, status_message='操作完成')
                    except Exception as exc:
                        self.running = False
                        self._preserve_source_failure()
                        answer = dict(status='failed', command=command, error=f'{type(exc).__name__}: {exc}')
                        with self.lock: self.status.update(error=answer['error'], status_message='操作失败，现场已保留；没有自动重试')
                        append(self.folder/'failures.jsonl', dict(job_id=job, at=now(), **answer))
                    finally:
                        try:
                            self._publish()
                        except Exception:
                            with self.lock:
                                self.status.update(ready=False, status_message='原生状态已失效；可显式新建对局，禁止重发旧动作')
                        with self.lock:
                            self.status['jobs'][job] = answer
                            if len(self.status['jobs']) > 50:
                                del self.status['jobs'][next(iter(self.status['jobs']))]
                            self.status.update(busy=False, running=self.running, mode=self.mode)
                        self.queue.task_done()
                if self.native_state is not None:
                    self._save()
        except Exception as exc:
            with self.lock:
                self.status.update(ready=False, busy=False, running=False, error=f'{type(exc).__name__}: {exc}',
                                   status_message='原生控制已停止，未切换到近似引擎')
            write(self.folder/'failure.json', dict(at=now(), **deepcopy(self.status)))
        finally:
            if self.replayer:
                self.replayer.close()
            if self.source_replayer:
                self.source_replayer.close()
            if self.engine:
                self.engine.close()
            self.closed.set()
            write(self.folder/'report.json', {**self.public(), 'at': now(), 'csrf_token': 'omitted',
                  'training_started': False, 'native_rpc_owner': 'single_worker', 'closed': True})

    def close(self, timeout=60):
        if self.offline:
            self.closed.set()
            write(self.folder/'report.json', {**self.public(), 'csrf_token': 'omitted',
                  'closed': True, 'offline': True, 'native_rpc_calls': 0})
            return
        if not self.closed.is_set():
            self.pause_requested.set()
            self.queue.put((secrets.token_hex(8), 'shutdown', {}), timeout=5)
        self.thread.join(timeout=timeout)
        if self.thread.is_alive():
            raise RuntimeError('原生 worker 仍在执行；不得另启控制者，等待当前 RPC 超时并退出')

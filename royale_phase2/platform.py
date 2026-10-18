"""Research console: raw native telemetry, rendered frames and replaceable policies.

Only the worker owns native RPC. HTTP reads cached observations. This is an
oracle/debugging tool, not a new training export or an official replay oracle.
"""
from copy import deepcopy
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import hashlib
import importlib.util
import json
import os
import queue
import secrets
import threading
import time
import traceback
from urllib.parse import parse_qs, urlsplit

from .runtime import ROOT, local
from .observability import build_observation
from .battlefield import build_battlefield_observation
from .visibility import TowerAttestation, project_actor
from .viewer import AndroidBridge, AnchorLog, NativeRecorder, DEFAULT_EPISODE, load_episode, prepare_engine, write_json, append_json, digest, semantic_packet

DEFAULT_ARCHIVE = 'outputs/phase2/viewer/acceptance-001/rendered'
DEFAULT_CHECKPOINT = 'outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json'


def fresh_android(bridge, folder):
    """Release Android render resources between runs, before any battle exists."""
    rows=[]
    def run(label, command, timeout=30, check=True):
        result=bridge.run(command,timeout=timeout,check=False)
        rows.append({'label':label,'command':command,'exit_code':result.returncode,
                     'stdout':result.stdout.decode(errors='replace'),'stderr':result.stderr.decode(errors='replace')})
        write_json(folder/'application_restart.json',{'scope':'Fresh project Android container for a new console run; prior match is discarded',
                   'reason':'Render scenes and screenrecord can exhaust Surface resources across consecutive matches', 'commands':rows})
        if check and result.returncode: raise RuntimeError('Android session startup failed at '+label+': '+rows[-1]['stderr'][:1000])
        return result
    run('restart_android','sudo docker restart -t 10 phase2-android',60)
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        result=run('wait_android_boot','sudo docker exec phase2-android getprop sys.boot_completed',15,False)
        if result.returncode==0 and result.stdout.strip()==b'1':break
        time.sleep(1)
    else: raise TimeoutError('Fresh Android container boot timeout')
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        run('adb_connect','adb connect 127.0.0.1:5555',15,False)
        result=run('adb_ready','adb -s 127.0.0.1:5555 shell getprop sys.boot_completed',15,False)
        if result.returncode==0 and result.stdout.strip()==b'1':break
        time.sleep(1)
    else: raise TimeoutError('Fresh Android ADB connection timeout')
    run('app_start','adb -s 127.0.0.1:5555 shell am start -n nullsroyale.rel.free/com.supercell.clashroyale.GameApp',45)
    run('probe_forward','adb -s 127.0.0.1:5555 forward tcp:26789 tcp:26789',15)


def lines(path):
    return [json.loads(row) for row in local(path).read_text(encoding='utf-8-sig').splitlines() if row.strip()]


def plugin(path):
    path = local(path)
    if path.suffix != '.py':
        raise ValueError('Policy must be a project-local Python file')
    spec = importlib.util.spec_from_file_location('project_research_policy', path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    if not callable(getattr(module, 'decide', None)):
        raise ValueError('Policy must export decide(observation)')
    return module.decide, {'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def research_action(action, actor):
    """Budget/current-slot check only. Placement legality remains native/unproved."""
    if not isinstance(action, dict): raise ValueError('Policy action must be an object')
    if action.get('kind') == 'wait': return {'kind': 'wait'}
    if action.get('kind') != 'play_card': raise ValueError('Use wait or play_card')
    slot = action.get('slot')
    if type(slot) is not int or not 0 <= slot <= 3: raise ValueError('slot must be 0..3')
    cards = [card for card in actor['hand'] if card['slot'] == slot]
    if len(cards) != 1: raise ValueError('Current hand slot missing')
    card = cards[0]
    if card['cost'] * 10000 > actor['own_elixir_raw']: raise ValueError('Insufficient current elixir')
    for key, limit in (('x', 17999), ('y', 31999)):
        if type(action.get(key)) is not int or not 0 <= action[key] <= limit:
            raise ValueError(key + ' is outside native coordinate bounds')
    return {'kind': 'play_card', 'slot': slot, 'card': card['card'], 'x': action['x'], 'y': action['y']}


def frame_bytes(session, sha=None):
    """Resolve a UI frame by content hash, never by a request-supplied path."""
    if sha is not None and (len(sha) != 64 or any(c not in '0123456789abcdef' for c in sha)):
        raise ValueError('Frame version must be a lowercase SHA256')
    if not hasattr(session, 'lock'): return None
    with session.lock:
        latest = deepcopy(session.status.get('last_frame'))
        if sha is None or latest and latest.get('sha256') == sha:
            data = session.latest_frame
            expected = latest.get('sha256') if latest else None
            row = None
        else:
            row = deepcopy(session.frames_by_sha.get(sha))
            data = None; expected = sha
    if row is not None:
        path = local(row['path'])
        try: path.relative_to(session.folder/'frames')
        except ValueError: raise ValueError('Frame path escapes session frames folder') from None
        data = path.read_bytes()
    if data is None: return None
    if not data.startswith(b'\x89PNG\r\n\x1a\n') or not expected or hashlib.sha256(data).hexdigest() != expected:
        raise ValueError('Frame bytes do not match the requested native image SHA256')
    return data


class ArchiveSession:
    """Frame/state joins are hash checked before displaying saved evidence."""
    def __init__(self, folder=DEFAULT_ARCHIVE):
        self.folder = local(folder)
        self.frames = lines(self.folder/'frames/index.jsonl')
        if not self.frames: raise ValueError('Archive has no native frames')
        self.frames_by_sha = {row['sha256']: row for row in self.frames}
        states = lines(self.folder/'states.jsonl')
        if not states: raise ValueError('Archive has no native states')
        self.event_origin = states[0]['raw_capture']['next'] - 1
        self.rows = {}
        for row in states:
            raw = row['raw_capture']; tick = raw['tick']
            if type(tick) is not int: raise ValueError('Invalid archive state tick')
            # Recompute from raw data: comparing two declared hash strings would
            # silently accept a changed state with unchanged receipts.
            actual = digest(semantic_packet(raw, row['all_observed_card_events'], self.event_origin))
            if actual != row['semantic_sha256']: raise ValueError('Archive raw state semantic SHA256 mismatch')
            if tick in self.rows and (self.rows[tick]['raw_capture'] != raw or
                                      self.rows[tick]['all_observed_card_events'] != row['all_observed_card_events']):
                raise ValueError('Conflicting archive states at one native tick')
            self.rows[tick] = row
        # Rebuild forward once; seeking backwards must not retain future history.
        journal = []
        for name in ('journal.json', 'preregistered_journal.json'):
            candidate = self.folder/name
            if candidate.exists():
                saved = json.loads(candidate.read_text(encoding='utf-8-sig'))
                journal = saved.get('actions', saved.get('canonical_journal', [])) if isinstance(saved, dict) else saved
                break
        self.battlefields = {}; self.archive_actors = {}; previous = None
        first = states[0]['raw_capture']
        try: towers = TowerAttestation.from_reset(first)
        except ValueError: towers = None
        for tick in sorted(self.rows):
            row = self.rows[tick]; raw = row['raw_capture']
            actor = project_actor(raw, 0, towers)
            previous = build_battlefield_observation(raw, actor, row['all_observed_card_events'], journal, previous)
            self.battlefields[tick] = previous; self.archive_actors[tick] = actor
        self.index = 0; self.lock = threading.Lock(); self.closed = threading.Event()
        self.status = {'mode': 'archive', 'ready': True, 'playing': False, 'error': None,
                       'scope': 'Saved native frames and matching captured states; no running simulation',
                       'folder': str(self.folder.relative_to(ROOT)), 'jobs': {}, 'archive_count': len(self.frames)}
        self._load(0)

    def _load(self, index):
        if type(index) is not int or not 0 <= index < len(self.frames): raise ValueError('Archive index out of range')
        frame = self.frames[index]; path = local(frame['path'])
        try: path.relative_to(self.folder/'frames')
        except ValueError: raise ValueError('Archive image escapes its frames folder') from None
        data = path.read_bytes()
        if not data.startswith(b'\x89PNG\r\n\x1a\n'): raise ValueError('Archive image is not a PNG')
        if hashlib.sha256(data).hexdigest() != frame['sha256']: raise ValueError('Archive image SHA256 mismatch')
        if frame.get('bytes', len(data)) != len(data): raise ValueError('Archive image byte count mismatch')
        if type(frame.get('tick')) is not int or frame['tick'] not in self.rows:
            raise ValueError('Archive frame has no matching native tick')
        row = self.rows[frame['tick']]
        if frame['native_semantic_sha256'] != row['semantic_sha256']: raise ValueError('Frame/state binding mismatch')
        observation = build_observation(row['raw_capture'], getattr(self, 'observation', None))
        self.index = index; self.latest_frame = data
        self.observation = observation
        self.battlefield = self.battlefields[frame['tick']]
        self.actor = self.archive_actors[frame['tick']]
        self.events = row.get('all_observed_card_events', [])
        self.status.update(tick=frame['tick'], terminal=row['raw_capture']['finalized'],
                           result=row['raw_capture']['result'], last_frame=frame, archive_index=index,
                           actual_native_events=len(self.events))

    def public(self):
        with self.lock:
            return {**deepcopy(self.status), 'observation': deepcopy(self.observation),
                    'actual_card_events': deepcopy(self.events), 'decisions': [], 'feedback': [],
                    'actor': deepcopy(self.actor), 'battlefield': deepcopy(self.battlefield),
                    'policy': {'kind': 'saved_evidence_only', 'information_mode': 'no_policy_execution'}}

    def submit(self, command, **data):
        with self.lock:
            if command == 'seek': self._load(data.get('index'))
            elif command == 'step': self._load(min(self.index+1, len(self.frames)-1))
            elif command == 'shutdown': self.closed.set()
            else: raise ValueError('Archive supports seek, step or shutdown')
        return 'archive-synchronous'


class LiveSession:
    def __init__(self, mode, output, *, port=26789, quantum=20, speed=1, seed=20002,
                 checkpoint=DEFAULT_CHECKPOINT, policy_file=None, information_mode='auto',
                 episode=DEFAULT_EPISODE, autoplay=False, max_tick=7200, record=False):
        if mode not in ('replay', 'agent', 'human'): raise ValueError('Unsupported live mode')
        if type(quantum) is not int or not 1 <= quantum <= 100: raise ValueError('quantum must be 1..100')
        if not 0 <= speed <= 10: raise ValueError('speed must be 0..10')
        if information_mode not in ('auto', 'player', 'battlefield', 'oracle'): raise ValueError('Unknown information mode')
        if information_mode == 'auto': information_mode = 'battlefield' if policy_file else 'player'
        if mode == 'agent' and not policy_file and information_mode != 'player':
            raise ValueError('Saved BC supports player only; expanded inputs require a Python policy')
        self.mode=mode; self.folder=local(output); self.folder.mkdir(parents=True, exist_ok=False)
        (self.folder/'frames').mkdir(); self.port=port; self.quantum=quantum; self.speed=speed
        self.seed=seed; self.checkpoint=checkpoint; self.policy_file=policy_file; self.information_mode=information_mode
        self.episode_path=episode; self.autoplay=autoplay; self.max_tick=max_tick
        self.record_requested=record; self.recorder=None
        self.lock=threading.Lock(); self.queue=queue.Queue(); self.ready=threading.Event(); self.closed=threading.Event()
        self.engine=None; self.state=None; self.observation=None; self.latest_frame=None
        self.battlefield=None
        self.playing=False; self.stop_requested=False; self.frame_index=0; self.decisions=[]; self.feedback=[]
        self.frames_by_sha={}
        self.actors={}; self.events=[]; self.next_decision=90; self.decision_index=0; self.policy_info={}
        self.status={'mode':mode, 'ready':False, 'playing':False, 'terminal':False, 'error':None,
                     'folder':str(self.folder.relative_to(ROOT)), 'jobs':{}, 'quantum_ticks':quantum,
                     'speed':speed, 'tick_ms':50, 'clock':'Native remains paused; one worker advances exact ticks',
                     'scope':'All emitted probe fields; attack/damage causality and official trajectory fidelity remain unverified'}
        self.thread=threading.Thread(target=self._worker, name='platform-native-rpc-owner', daemon=True); self.thread.start()

    def public(self):
        with self.lock:
            return {**deepcopy(self.status), 'observation':deepcopy(self.observation),
                    'actor':deepcopy(self.actors.get(0)), 'policy':deepcopy(self.policy_info),
                    'battlefield':deepcopy(self.battlefield),
                    'decisions':deepcopy(self.decisions[-20:]), 'feedback':deepcopy(self.feedback),
                    'actual_card_events':deepcopy(self.events)}

    def submit(self, command, **data):
        if command not in ('play','pause','step','advance','capture','feedback','report','shutdown'):
            raise ValueError('Unsupported live command')
        if command=='advance' and (type(data.get('ticks')) is not int or not 1<=data['ticks']<=10000):
            raise ValueError('advance ticks must be 1..10000')
        ticket=secrets.token_hex(6)
        with self.lock:
            if self.closed.is_set(): raise ValueError('Worker closed; restart the console')
            self.status['jobs'][ticket]={'command':command, 'status':'queued'}
        self.queue.put((ticket,command,data)); return ticket

    def _capture_frame(self, sha, publish=True):
        time.sleep(.06); png=self.bridge.capture(); self.frame_index+=1
        path=self.folder/'frames'/f'{self.frame_index:06d}-tick{self.state["tick"]:06d}.png'; path.write_bytes(png)
        row={'frame_index':self.frame_index, 'tick':self.state['tick'], 'path':str(path.relative_to(ROOT)),
             'sha256':hashlib.sha256(png).hexdigest(), 'native_semantic_sha256':sha,
             'wall_time_utc':datetime.now(timezone.utc).isoformat(), 'bytes':len(png)}
        append_json(self.folder/'frames/index.jsonl',row)
        if publish:
            with self.lock:
                self.latest_frame=png; self.status['last_frame']=row
                self.frames_by_sha[row['sha256']]=row
        return png, row

    def _accept(self):
        sha=self.anchors.accept(self.engine,self.state)
        observation=build_observation(self.state, self.observation['raw'] if self.observation else None)
        actors={owner:self.engine.observe_visible(owner) for owner in (0,1)}
        events=[deepcopy(self.engine._plays[k]) for k in sorted(self.engine._plays)]
        battlefield=build_battlefield_observation(self.state, actors[0], events,
                                                  self.engine.export_journal(), self.battlefield)
        append_json(self.folder/'observations.jsonl', observation)
        append_json(self.folder/'battlefield_observations.jsonl', battlefield)
        png, frame = self._capture_frame(sha, publish=False)
        with self.lock:
            # Publish a matching state/frame pair in one snapshot. HTTP never
            # observes a newly captured frame paired with the previous state.
            self.latest_frame=png; self.status['last_frame']=frame
            self.frames_by_sha[frame['sha256']]=frame
            self.observation=observation; self.actors=actors; self.events=events
            self.battlefield=battlefield
            self.status.update(ready=True, tick=self.state['tick'], terminal=self.state['finalized'],
                               result=self.state['result'], crowns=self.state['crowns'], playing=self.playing,
                               actual_native_events=len(events), frame_count=self.frame_index,
                               decision_count=len(self.decisions), truncated=bool(not self.state['finalized'] and self.state['tick']>=self.max_tick))

    def _decide(self):
        from .agent_env import public_heuristic, validate_policy_action
        from .trajectories import subspace_mask
        actor=self.engine.observe_visible(0); tick=actor['tick']
        mask=subspace_mask(actor, actor['hand_complete'] and tick>=90, self.proof)
        offered=deepcopy({'oracle':self.observation, 'battlefield':self.battlefield,
                          'player':actor}[self.information_mode])
        if self.custom: chosen=research_action(self.custom(offered), actor)
        else: chosen=validate_policy_action(actor, mask, self.model.predict(actor, legal_mask=mask))
        opponent=public_heuristic(self.engine.observe_visible(1), self.decision_index)
        row={'decision_index':self.decision_index,'tick':tick,'policy_information_mode':self.policy_info['information_mode'],
             'policy_input':offered, 'actor_mask':mask, 'model_action':chosen,'opponent_action':opponent,'registrations':[]}
        for owner, controller, action in ((0,'model',chosen),(1,'public_heuristic',opponent)):
            if action['kind']=='play_card':
                receipt=self.engine.schedule_card(owner,action['card'],action['x'],action['y'],tick+1,
                    request_id=f'platform:{controller}:{self.decision_index}')
                row['registrations'].append({'controller':controller,'registration':receipt})
        append_json(self.folder/'decisions.jsonl',row)
        with self.lock: self.decisions.append(row)
        self.decision_index+=1; self.next_decision=tick+20

    def _advance(self, amount):
        target=min(self.state['tick']+amount,self.max_tick)
        while self.state['tick']<target and not self.state['finalized']:
            if self.mode in ('agent','human') and self.state['tick']>=self.next_decision: self._decide()
            stop=target
            if self.mode in ('agent','human'): stop=min(stop,self.next_decision)
            else:
                future=[tick for tick in self.episode['anchors'] if tick>self.state['tick']]
                if future: stop=min(stop,min(future))
            started=time.monotonic(); before=self.state['tick']
            self.state=self.engine.advance_exact_ticks(stop-before)
            if self.mode=='human':
                append_json(self.folder/'timings.jsonl',{'kind':'native_advance','before_tick':before,
                            'after_tick':self.state['tick'],'wall_seconds':time.monotonic()-started})
            self._accept()
        if self.state['finalized'] or self.state['tick']>=self.max_tick:
            self.playing=False; self._stop_recording(); self._feedback(); self._report()
        elif self.recorder and self.recorder.part:
            try: self.recorder.rotate_if_needed(self.state['tick'])
            except Exception as exc: self.recorder.error=f'{type(exc).__name__}: {exc}'
        with self.lock: self.status['playing']=self.playing

    def _feedback(self):
        feedback=self.engine.execution_feedback()
        with self.lock: self.feedback=feedback
        write_json(self.folder/'execution_feedback.json',feedback)

    def _prepare_match(self, match):
        for owner in (0,1):
            deck=match['battle'][f'deck{owner}']['sp']
            match['battle'][f'deck{owner}']['sp']=[c for c in deck if c['d'] in (28000000,28000001)]+[c for c in deck if c['d'] not in (28000000,28000001)]
        return match

    def _report(self):
        write_json(self.folder/'journal.json',self.engine.export_journal())
        report={'schema':'phase2-research-platform-run.v1','mode':self.mode,'native_result':self.engine.result(),
                'policy':self.policy_info,'decisions':len(self.decisions),'actual_card_events':len(self.events),
                'consumed_actions':sum(row.get('card_consumed') is True for row in self.feedback),
                'submitted_actions':len(self.engine.export_journal()['actions']), 'frames':self.frame_index,
                'field_scope':'All cr-minimal.v4 fields, not all native internals or attributed combat events',
                'official_fidelity_verified':False, 'training_export':False,
                'complete_native_terminal':bool(self.state['finalized']),
                'truncated':bool(not self.state['finalized'] and self.state['tick']>=self.max_tick)}
        if self.recorder:
            report['recording']={'requested':True,'parts':deepcopy(self.recorder.parts),'error':self.recorder.error,
                                 'timing':'Actual Android screen recording; native ticks and video/wall times differ during pauses/capture overhead'}
        write_json(self.folder/'report.json',report); return report

    def _stop_recording(self):
        if self.recorder and self.recorder.part:
            try: self.recorder.stop(self.state['tick'])
            except Exception as exc: self.recorder.error=f'{type(exc).__name__}: {exc}'

    def _job(self,ticket,command,data):
        before=self.state['tick']
        if command=='play':
            if self.state['finalized'] or self.state['tick']>=self.max_tick: raise ValueError('Match already ended')
            self.playing=True
        elif command=='pause': self.playing=False
        elif command in ('step','advance'):
            self.playing=False; self._advance(1 if command=='step' else data['ticks'])
        elif command=='capture': self._capture_frame(self.anchors.accept(self.engine,self.state))
        elif command=='feedback': self._feedback()
        elif command=='report': self._feedback(); self._report()
        elif command=='shutdown': self.playing=False; self.stop_requested=True
        answer={'command':command,'status':'done','before_tick':before,'after_tick':self.state['tick']}
        append_json(self.folder/'controls.jsonl',{'ticket':ticket,**answer})
        with self.lock: self.status['jobs'][ticket]=answer; self.status['playing']=self.playing

    def _worker(self):
        owner_lock=None
        try:
            # OS lock is crash-released. The launcher never resets a match.
            import msvcrt
            lockpath=local('outputs/platform_next/native-owner.lock'); lockpath.parent.mkdir(parents=True,exist_ok=True)
            owner_lock=lockpath.open('a+b'); owner_lock.seek(0)
            if not owner_lock.read(1): owner_lock.write(b'0'); owner_lock.flush()
            owner_lock.seek(0); msvcrt.locking(owner_lock.fileno(),msvcrt.LK_NBLCK,1)
            self.bridge=AndroidBridge(); self.custom=None
            if self.mode=='human':
                self.bridge.ssh=[arg.replace('StrictHostKeyChecking=no','StrictHostKeyChecking=yes') for arg in self.bridge.ssh]
            # Start each new render run with clean Surface resources. Never
            # retry a partially executed battle invisibly.
            fresh_android(self.bridge,self.folder)
            from .probe import ProbeClient
            deadline=time.monotonic()+120; ready=False; last_error=None
            while time.monotonic()<deadline:
                probe=ProbeClient(port=self.port,timeout=5)
                try:
                    status=probe.status()
                    if status.get('contentReady'):
                        write_json(self.folder/'startup_native_status.json',status); ready=True; break
                except Exception as exc: last_error=str(exc)
                finally: probe.close()
                time.sleep(.5)
            if not ready: raise TimeoutError('Cold application content did not become ready: '+str(last_error))
            if self.mode=='replay':
                self.episode=load_episode(self.episode_path)
                self.engine,self.state=prepare_engine(self.episode,self.folder,True,self.port)
                self.policy_info={'kind':'preregistered_source_replay','information_mode':'researcher_oracle',
                                  'training_eligible':False,'source':self.episode['episode_path']}
            else:
                from .backend import NativeBattleBackend
                from .agent_env import NativeAgentEnv
                from .learning import NumpyBCPolicy
                from .trajectories import audit_subspace_proof
                self.engine=NativeBattleBackend(port=self.port,rendered=True,trace_path=self.folder/'wire.jsonl',timeout=120)
                self.proof=audit_subspace_proof('data/phase2/action_subspace_proof.json',self.engine.version['fingerprint'])
                if self.proof['status']!='verified_local_rule_evidence': raise ValueError('Action proof is not verified')
                match=self._prepare_match(NativeAgentEnv.default_match(self.seed))
                if self.policy_file:
                    self.custom,receipt=plugin(self.policy_file)
                    self.policy_info={'kind':'project_python_plugin','information_mode':self.information_mode,**receipt,
                                      'legality':'Current-slot/budget/bounds checked; general placement legality is not proven'}
                else:
                    self.model=NumpyBCPolicy.load(self.checkpoint,version_fingerprint=self.engine.version['fingerprint'])
                    self.policy_info={'kind':'saved_numpy_bc','information_mode':'player','checkpoint':self.checkpoint,
                                      'action_space':'WAIT or verified ordinary Fireball/Arrows',
                                      'inference_play_bias':self.model.metadata.get('inference_play_bias',0)}
                self.policy_info.update(seed=self.seed,owner=0,opponent='public_heuristic',spell_first_deck=True)
                self.state=self.engine.reset(match); write_json(self.folder/'initialization.json',match)
                self.episode={'anchors':[],'version_fingerprint':self.engine.version['fingerprint'],
                              'initialization_sha256':digest(match),'journal':[]}
            self.anchors=AnchorLog(self.folder,self.episode,'research-platform-rendered')
            self._accept(); self.ready.set(); self.playing=self.autoplay
            if self.record_requested:
                self.recorder=NativeRecorder(self.bridge,self.folder)
                try: self.recorder.start(self.state['tick'])
                except Exception as exc: self.recorder.error=f'{type(exc).__name__}: {exc}'
            while not self.stop_requested:
                started=time.monotonic()
                try:
                    ticket,command,data=self.queue.get(timeout=0 if self.playing else .2)
                    try: self._job(ticket,command,data)
                    except Exception as exc:
                        self.playing=False
                        with self.lock: self.status['jobs'][ticket]={'status':'error','error':str(exc)}; self.status['playing']=False
                        append_json(self.folder/'control_errors.jsonl',{'ticket':ticket,'error':str(exc),'traceback':traceback.format_exc()})
                except queue.Empty:
                    if self.playing:
                        before=self.state['tick']; self._advance(self.quantum)
                        delay=(self.state['tick']-before)/20/self.speed if self.speed else 0
                        # Queue timeout also makes pause responsive during pacing.
                        remaining=max(0,started+delay-time.monotonic())
                        if remaining:
                            try:
                                ticket,command,data=self.queue.get(timeout=remaining); self._job(ticket,command,data)
                            except queue.Empty: pass
        except Exception as exc:
            with self.lock: self.status.update(error=f'{type(exc).__name__}: {exc}',playing=False)
            (self.folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8'); self.ready.set()
        finally:
            if self.engine:
                self._stop_recording()
                try: self._feedback(); self._report()
                except Exception: (self.folder/'finalize_error.log').write_text(traceback.format_exc(),encoding='utf-8')
                self.engine.close()
            if owner_lock: owner_lock.close()
            self.closed.set()
            write_json(self.folder/'session.json',self.public())


def make_server(session, port=8770, page_path='royale_phase2/platform_ui.html'):
    token=secrets.token_urlsafe(24)
    page=local(page_path).read_text(encoding='utf-8').replace('__TOKEN__',json.dumps(token))
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self,status,data,kind='application/json; charset=utf-8'):
            body=json.dumps(data,ensure_ascii=False,allow_nan=False).encode() if kind.startswith('application/json') else data
            self.send_response(status); self.send_header('Content-Type',kind); self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store'); self.send_header('X-Content-Type-Options','nosniff')
            self.send_header('Content-Security-Policy',"default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; frame-ancestors 'none'")
            self.end_headers(); self.wfile.write(body)
        def trusted(self):
            return self.headers.get('Host') in (f'127.0.0.1:{self.server.server_port}',f'localhost:{self.server.server_port}')
        def do_GET(self):
            if not self.trusted(): return self.reply(403,{'error':'Loopback Host required'})
            session = self.server.session
            path=urlsplit(self.path).path
            if path=='/': return self.reply(200,page.encode(),'text/html; charset=utf-8')
            if path=='/api/state': return self.reply(200,session.public())
            if path=='/api/stream.mjpg':
                stream=getattr(session,'stream',None)
                if stream is None: return self.reply(503,{'error':'Native video stream is not ready'})
                self.send_response(200); self.send_header('Content-Type','multipart/x-mixed-replace; boundary=frame')
                self.send_header('Cache-Control','no-store'); self.end_headers()
                try:
                    for part in stream.iter_mjpeg(boundary='frame'):
                        self.wfile.write(part); self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError, OSError): pass
                return
            if path=='/api/frame':
                try:
                    versions = parse_qs(urlsplit(self.path).query).get('v', [])
                    if len(versions) > 1: raise ValueError('One frame version is required')
                    frame=frame_bytes(session,versions[0] if versions else None)
                except (ValueError,OSError) as exc:
                    return self.reply(409,{'error':str(exc)})
                return self.reply(200,frame,'image/png') if frame else self.reply(404,{'error':'No frame yet'})
            return self.reply(404,{'error':'Not found'})
        def do_POST(self):
            # Drain bounded request bodies before rejecting them. Closing an
            # unread POST on Windows can reset TCP before the 403 is received.
            try:
                length=int(self.headers.get('Content-Length','0'))
                self.connection.settimeout(5)
                body=self.rfile.read(length) if 0<length<=65536 else b''
                body_ok=0<length<=8192 and len(body)==length
            except (ValueError, OSError):
                body=b''; body_ok=False
            if not self.trusted() or self.headers.get('X-Platform-Token')!=token:
                return self.reply(403,{'error':'Local page token required'})
            if urlsplit(self.path).path!='/api/control': return self.reply(404,{'error':'Not found'})
            try:
                if not body_ok: raise ValueError('Body must be 1..8192 bytes')
                data=json.loads(body)
                if not isinstance(data,dict): raise ValueError('Control body must be a JSON object')
                command=data.pop('command')
                if not isinstance(command,str): raise ValueError('command must be a string')
                return self.reply(200,{'ticket':self.server.session.submit(command,**data)})
            except (ValueError,TypeError,KeyError,json.JSONDecodeError) as exc: return self.reply(400,{'error':str(exc)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler); server.daemon_threads=True
    server.session = session
    return server

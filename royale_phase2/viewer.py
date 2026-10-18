"""Loopback viewer for an actual paused native renderer; no battle model here.

The single worker owns RPC. HTTP serves cached conservative HUD and unmodified
Android frames. Source journals and oracle anchors are audit artifacts only.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import queue
import secrets
import shlex
import struct
import subprocess
import threading
import time
import traceback
from urllib.parse import parse_qs, urlsplit

from .runtime import ROOT, local

DEFAULT_EPISODE = 'outputs/phase2/replays/development/ordinary-feedback-fixed/episode.json'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def write_json(path, value):
    path = local(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')


def append_json(path, value):
    with local(path).open('a', encoding='utf-8') as stream:
        stream.write(json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)+'\n')


def load_episode(path):
    path = local(path); raw = path.read_bytes(); episode = json.loads(raw)
    init_info = episode['files']['initialization.json']; init_path = local(init_info['path'])
    if hashlib.sha256(init_path.read_bytes()).hexdigest() != init_info['sha256']:
        raise ValueError('Initialization artifact changed')
    actions = []
    for source in episode['source_actions']:
        keys = ('kind', 'owner', 'tick', 'request_id') + (('card_id', 'x', 'y') if source['kind'] == 'play_card' else ('hints',))
        row = {key: deepcopy(source[key]) for key in keys if key in source}
        if row['kind'] not in ('play_card', 'activate_ability') or row['tick'] <= 90 or row['owner'] not in (0, 1):
            raise ValueError('Unsupported source journal')
        actions.append(row)
    terminal = episode['terminal']['tick']
    if terminal <= 3000 or not episode.get('completed'):
        raise ValueError('Viewer acceptance needs a complete actual native source episode')
    return {'episode_path': str(path.relative_to(ROOT)), 'episode_sha256': hashlib.sha256(raw).hexdigest(),
            'source_episode_id': episode['source_episode_id'], 'initialization': json.loads(init_path.read_bytes()),
            'initialization_sha256': init_info['sha256'], 'source_actions': episode['source_actions'],
            'journal': actions, 'anchors': [90, 1000, 2000, 3000, terminal], 'terminal_tick': terminal,
            'version_fingerprint': episode['version_fingerprint']}


class AndroidBridge:
    """Use the existing project SSH key; all guest writes are in project qcow2."""
    def __init__(self):
        self.ssh = ['C:/Windows/System32/OpenSSH/ssh.exe', '-i', str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),
                    '-p', '22225', '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=10', '-o', 'StrictHostKeyChecking=no',
                    '-o', 'UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts', 'researcher@127.0.0.1']

    def run(self, command, timeout=45, check=True):
        result = subprocess.run(self.ssh+[command], cwd=ROOT, capture_output=True, timeout=timeout)
        if check and result.returncode:
            raise RuntimeError(f'SSH command exit{result.returncode}: {result.stderr.decode(errors="replace")[:2000]}')
        return result

    def android(self, args, timeout=45, check=True):
        return self.run('sudo docker exec phase2-android '+shlex.join(args), timeout, check)

    def capture(self):
        result = self.run('adb -s 127.0.0.1:5555 exec-out screencap -p')
        if not result.stdout.startswith(b'\x89PNG\r\n\x1a\n'):
            raise RuntimeError('Android framebuffer did not return a PNG')
        return result.stdout

    def restart_headless(self):
        # This closes the render process; it is not a state restore.
        result = self.run('adb -s 127.0.0.1:5555 shell am force-stop nullsroyale.rel.free && '
                          'adb -s 127.0.0.1:5555 shell am start -n nullsroyale.rel.free/com.supercell.clashroyale.GameApp && '
                          'adb -s 127.0.0.1:5555 forward tcp:26789 tcp:26789')
        return {'stdout': result.stdout.decode(errors='replace'), 'stderr': result.stderr.decode(errors='replace'),
                'scope': 'Cold application restart; previous rendered match is discarded'}


def doctor(output):
    """Read-only ADB/codec inspection, with no RPC or application lifecycle calls."""
    folder = local(output); folder.mkdir(parents=True, exist_ok=True); bridge = AndroidBridge()
    checks = []
    for label, args in [('screenrecord_help', ['screenrecord', '--help']), ('media_codecs', ['dumpsys', 'media.codec']),
                        ('android_properties', ['getprop', 'ro.build.fingerprint'])]:
        result = bridge.android(args, check=False)
        (folder/(label+'.stdout.log')).write_bytes(result.stdout)
        (folder/(label+'.stderr.log')).write_bytes(result.stderr)
        checks.append({'label': label, 'exit_code': result.returncode, 'stdout_bytes': len(result.stdout),
                       'stderr_bytes': len(result.stderr), 'command': args,
                       'diagnostic': result.stderr.decode(errors='replace')[:300]})
    png = bridge.capture(); (folder/'readonly_frame.png').write_bytes(png)
    report = {'schema': 'phase2-native-viewer-doctor.v1', 'rpc_used': False, 'app_restart': False,
              'screenrecord_started': False, 'checks': checks, 'actual_frame_sha256': hashlib.sha256(png).hexdigest(),
              'actual_frame_bytes': len(png), 'screenrecord_execution': 'not_run; codec start must be tested in exclusive window'}
    write_json(folder/'report.json', report); return report


def semantic_packet(raw, cumulative_events, event_origin):
    """Retain every minimal field, only normalize generation/counter origin."""
    packet = deepcopy(raw); packet.pop('generation')
    packet['next'] -= event_origin
    packet['plays'] = deepcopy(cumulative_events)
    for event in packet['plays']: event[0] -= event_origin
    return packet


def diff_values(expected, actual, path='$', result=None):
    result = [] if result is None else result
    if len(result) >= 30: return result
    if type(expected) is not type(actual): result.append({'path': path, 'expected': expected, 'actual': actual})
    elif isinstance(expected, dict):
        for key in sorted(set(expected)|set(actual)):
            if key not in expected or key not in actual: result.append({'path': path+'.'+key, 'expected_present': key in expected, 'actual_present': key in actual})
            else: diff_values(expected[key], actual[key], path+'.'+key, result)
    elif isinstance(expected, list):
        if len(expected) != len(actual): result.append({'path': path+'.length', 'expected': len(expected), 'actual': len(actual)})
        for index, (a, b) in enumerate(zip(expected, actual)): diff_values(a, b, f'{path}[{index}]', result)
    elif expected != actual: result.append({'path': path, 'expected': expected, 'actual': actual})
    return result


def compare_anchors(reference, rendered, output):
    reference = json.loads(local(reference).read_text()); rendered = json.loads(local(rendered).read_text())
    if (reference['initialization_sha256'] != rendered['initialization_sha256'] or reference['journal_sha256'] != rendered['journal_sha256']
            or reference['version_fingerprint'] != rendered['version_fingerprint'] or reference['expected_anchors'] != rendered['expected_anchors']):
        raise ValueError('Anchor inputs differ')
    rows = []
    for tick in reference['expected_anchors']:
        a = reference['anchors'].get(str(tick)); b = rendered['anchors'].get(str(tick))
        same = a is not None and b is not None and a['semantic_packet'] == b['semantic_packet']
        rows.append({'tick': tick, 'equal': same, 'reference_sha256': a.get('semantic_sha256') if a else None,
                     'rendered_sha256': b.get('semantic_sha256') if b else None,
                     'differences': diff_values(a['semantic_packet'], b['semantic_packet']) if a and b and not same else []})
    result = {'schema': 'phase2-native-render-headless-anchor-comparison.v1', 'status': 'pass' if len(rows)==5 and all(r['equal'] for r in rows) else 'fail',
              'anchors': rows, 'normalization': 'generation omitted; monotonic play sequence origin shifted; plays use accumulated complete observed events',
              'retained': 'All remaining cr-minimal.v4 fields, entity IDs/HP/position/targets/cooldowns, hand/cycle/packed forms, clock/result and ordered events',
              'initialization_sha256': reference['initialization_sha256'], 'journal_sha256': reference['journal_sha256']}
    write_json(output, result); return result


def validate_viewer_evidence(folder):
    """Rebuild viewer bindings from raw state/wire/media artifacts.

    Comparing two derived ``anchors.json`` files alone is insufficient: both
    could have been generated from the same fabricated input.  This validator
    re-reads the source receipt, anchor state logs, raw RPC wire, frame hashes
    and the exact video/encoding receipt, and raises on any missing binding.
    It performs no RPC or application lifecycle operation.
    """
    folder = local(folder)
    report = json.loads((folder/'report.json').read_text(encoding='utf-8-sig'))
    source_path = folder/'headless'
    rendered_path = folder/'rendered'
    source_hint = report.get('reference', {}).get('episode_path')
    if not source_hint and (rendered_path/'preregistered_journal.json').is_file():
        source_hint = json.loads((rendered_path/'preregistered_journal.json').read_text(encoding='utf-8-sig')).get('source')
    episode_path = local(source_hint or DEFAULT_EPISODE)
    episode = load_episode(episode_path)
    def sha(path): return hashlib.sha256(path.read_bytes()).hexdigest()
    def read_lines(path): return [json.loads(line) for line in path.read_text(encoding='utf-8-sig').splitlines() if line.strip()]
    init_ref = json.loads(episode_path.read_text(encoding='utf-8'))['files']['initialization.json']
    journal_sha = digest(episode['journal'])
    expected = [int(t) for t in episode['anchors']]
    anchor_records = {}
    for mode, root in (('headless', source_path), ('rendered', rendered_path)):
        anchor_file = root/'anchors.json'; states_file = root/'states.jsonl'; wire_file = root/'wire.jsonl'
        if not (anchor_file.is_file() and states_file.is_file() and wire_file.is_file()):
            raise ValueError(f'Missing raw {mode} viewer evidence')
        anchors = json.loads(anchor_file.read_text(encoding='utf-8-sig'))
        if (anchors.get('initialization_sha256') != init_ref['sha256']
                or anchors.get('journal_sha256') != journal_sha
                or anchors.get('version_fingerprint') != episode['version_fingerprint']
                or anchors.get('expected_anchors') != expected):
            raise ValueError(f'{mode} anchor receipt identity mismatch')
        state_rows = read_lines(states_file); wire_rows = read_lines(wire_file)
        if not state_rows: raise ValueError(f'{mode} state log is empty')
        origin = state_rows[0]['raw_capture']['next']-1
        by_tick = {row['raw_capture']['tick']: row for row in state_rows}
        rebuilt = {}
        for tick in expected:
            row = by_tick.get(tick); stored = anchors.get('anchors', {}).get(str(tick))
            if row is None or stored is None: raise ValueError(f'{mode} missing anchor tick {tick}')
            packet = semantic_packet(row['raw_capture'], row['all_observed_card_events'], origin)
            packet_sha = digest(packet)
            if row.get('semantic_sha256') != packet_sha or stored.get('semantic_sha256') != packet_sha:
                raise ValueError(f'{mode} derived semantic hash mismatch at tick {tick}')
            if stored.get('raw_capture') != row['raw_capture'] or stored.get('all_observed_card_events') != row['all_observed_card_events']:
                raise ValueError(f'{mode} anchor is not bound to its raw state row at tick {tick}')
            if not any(item.get('response') == row['raw_capture'] for item in wire_rows):
                raise ValueError(f'{mode} anchor tick {tick} has no raw wire response')
            rebuilt[str(tick)] = packet_sha
        schedule_count = sum(str(item.get('command','')).startswith(('replay-schedule-card ', 'replay-schedule-ability ')) for item in wire_rows)
        if schedule_count < len(episode['journal']):
            raise ValueError(f'{mode} wire has fewer registered source actions than the journal')
        anchor_records[mode] = {'anchors': rebuilt, 'state_rows': len(state_rows), 'wire_rows': len(wire_rows),
                                'schedule_command_records': schedule_count, 'origin': origin}
    if anchor_records['headless']['anchors'] != anchor_records['rendered']['anchors']:
        raise ValueError('Headless and rendered raw-bound anchor hashes differ')
    frames_file = rendered_path/'frames'/'index.jsonl'
    frame_rows = read_lines(frames_file)
    if len(frame_rows) < 2 or frame_rows[0].get('tick') != expected[0] or frame_rows[-1].get('tick') != expected[-1]:
        raise ValueError('Rendered frame sequence does not cover source start through native terminal')
    rendered_states = {row['raw_capture']['tick']: row for row in read_lines(rendered_path/'states.jsonl')}
    for row in frame_rows:
        image = local(row['path'])
        if not image.is_file() or not image.read_bytes().startswith(b'\x89PNG\r\n\x1a\n') or sha(image) != row.get('sha256'):
            raise ValueError('Rendered frame bytes/hash are not bound to the frame index')
        state_row = rendered_states.get(row.get('tick'))
        if state_row is None or row.get('native_semantic_sha256') != state_row.get('semantic_sha256'):
            raise ValueError('Rendered frame has no matching raw native semantic state')
    video_receipt = None; video_path = None
    recording = rendered_path/'recording.json'
    if recording.is_file():
        video_receipt = json.loads(recording.read_text(encoding='utf-8-sig'))
        parts = video_receipt.get('parts', [])
        if parts and all(p.get('valid_mp4') for p in parts):
            if parts[0].get('start_tick') != expected[0] or parts[-1].get('end_tick') != expected[-1]:
                raise ValueError('Native recording parts do not cover the full source interval')
            for part in parts:
                path = local(part['path'])
                if not path.is_file() or not inspect_mp4(path.read_bytes())['valid_mp4'] or sha(path) != part.get('sha256'):
                    raise ValueError('Native recording receipt does not bind a valid MP4')
            video_path = [p['path'] for p in parts]
    if video_path is None:
        fallback = rendered_path/'native-full-match.mp4'; encoding = rendered_path/'encoding.json'
        if not (fallback.is_file() and encoding.is_file() and inspect_mp4(fallback.read_bytes())['valid_mp4']):
            raise ValueError('No complete native recording or frame-bound fallback video')
        enc = json.loads(encoding.read_text(encoding='utf-8-sig'))
        if enc.get('output') != fallback.relative_to(ROOT).as_posix() or enc.get('real_native_frames') != len(frame_rows):
            raise ValueError('Fallback video receipt is not bound to the complete frame index')
        video_path = [fallback.relative_to(ROOT).as_posix()]
    return {'schema': 'phase2-native-viewer-evidence-validation.v1', 'status': 'pass',
            'source_episode': episode['episode_path'], 'source_episode_sha256': episode['episode_sha256'],
            'expected_anchors': expected, 'raw_anchor_bindings': anchor_records,
            'frame_count': len(frame_rows), 'frame_start_tick': frame_rows[0]['tick'],
            'frame_terminal_tick': frame_rows[-1]['tick'], 'video_paths': video_path,
            'video_receipt_sha256': sha(recording) if recording.is_file() else None,
            'claim': 'Raw native state/wire/frame/video binding verified; no official-art asset fidelity claim'}


class AnchorLog:
    def __init__(self, folder, episode, mode):
        self.folder = folder; self.expected = episode['anchors']; self.event_origin = None
        self.value = {'schema': 'phase2-native-viewer-anchors.v1', 'mode': mode,
                      'version_fingerprint': episode['version_fingerprint'],
                      'initialization_sha256': episode['initialization_sha256'], 'journal_sha256': digest(episode['journal']),
                      'expected_anchors': self.expected, 'anchors': {}}

    def accept(self, engine, state):
        if self.event_origin is None: self.event_origin = state['next']-1
        events = [deepcopy(engine._plays[key]) for key in sorted(engine._plays)]
        packet = semantic_packet(state, events, self.event_origin)
        if state['tick'] in self.expected:
            self.value['anchors'][str(state['tick'])] = {'raw_capture': deepcopy(state), 'all_observed_card_events': events,
                                                        'semantic_packet': packet, 'semantic_sha256': digest(packet)}
            write_json(self.folder/'anchors.json', self.value)
        append_json(self.folder/'states.jsonl', {'wall_time_utc': datetime.now(timezone.utc).isoformat(),
                    'raw_capture': state, 'all_observed_card_events': events, 'semantic_sha256': digest(packet)})
        return digest(packet)


def prepare_engine(episode, folder, rendered, port):
    from .backend import NativeBattleBackend
    engine = NativeBattleBackend(rendered=rendered, port=port, trace_path=folder/'wire.jsonl', timeout=120)
    if engine.version['fingerprint'] != episode['version_fingerprint']:
        engine.close(); raise ValueError('Source native run version differs from the current manifest')
    try:
        state = engine.reset(episode['initialization'])
        # Register the complete source order once. No training observations are sampled.
        receipts = engine.schedule_journal(episode['journal'])
    except Exception:
        engine.close(); raise
    write_json(folder/'preregistered_journal.json', {'source': episode['episode_path'], 'source_sha256': episode['episode_sha256'],
               'source_actions': episode['source_actions'], 'canonical_journal': episode['journal'], 'receipts': receipts,
               'training_samples': 0, 'purpose': 'Full-journal playback/renderer comparison, never a policy actor'})
    write_json(folder/'initialization.json', episode['initialization'])
    return engine, state


def reference_run(episode_path, output, port=26789):
    episode = load_episode(episode_path); folder = local(output); folder.mkdir(parents=True, exist_ok=False)
    engine = None; result = {'schema': 'phase2-native-viewer-reference.v1', 'completed': False}
    try:
        engine, state = prepare_engine(episode, folder, False, port); anchors = AnchorLog(folder, episode, 'headless')
        anchors.accept(engine, state)
        for tick in episode['anchors'][1:]:
            state = engine.advance_exact_ticks(tick-state['tick']); anchors.accept(engine, state)
        result.update(completed=state['finalized'], terminal=engine.result(),
                      actual_native_events=len(engine._plays), source_action_count=len(episode['journal']))
    except Exception as exc:
        result.update(error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        raise
    finally:
        if engine: engine.close()
        write_json(folder/'report.json', result)
    return result


def inspect_mp4(data):
    """Validate actual screenrecord container and duration without another codec."""
    result = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'valid_mp4': False}
    offset = 0; boxes = []; duration = None; moov = b''
    while offset+8 <= len(data):
        size, kind = struct.unpack_from('>I4s', data, offset); header = 8
        if size == 1:
            if offset+16 > len(data): break
            size = struct.unpack_from('>Q', data, offset+8)[0]; header = 16
        if size == 0: size = len(data)-offset
        if size < header or offset+size > len(data): break
        boxes.append(kind.decode(errors='replace'))
        if kind == b'moov': moov = data[offset+header:offset+size]
        offset += size
    pos = moov.find(b'mvhd')
    if pos >= 0 and len(moov)>pos+24:
        if moov[pos+4] == 0:
            scale, ticks = struct.unpack_from('>II', moov, pos+16)
        else:
            scale = struct.unpack_from('>I', moov, pos+24)[0]; ticks = struct.unpack_from('>Q', moov, pos+28)[0]
        if scale: duration = ticks/scale
    result.update(boxes=boxes, duration_seconds=duration, avc1_codec_marker=b'avc1' in moov,
                  valid_mp4='ftyp' in boxes and 'moov' in boxes and 'mdat' in boxes and bool(duration and duration>0))
    return result


class NativeRecorder:
    def __init__(self, bridge, folder):
        self.bridge = bridge; self.folder = folder; self.run_id = secrets.token_hex(6); self.part = None; self.parts=[]; self.error=None

    def start(self, tick):
        index = len(self.parts)+1; path=f'/data/local/tmp/phase2-view-{self.run_id}-{index:03d}.mp4'
        script = f'screenrecord --bit-rate 2000000 --size 720x1280 --time-limit 180 {path} >{path}.log 2>&1 < /dev/null & echo $!'
        result = self.bridge.android(['sh', '-c', script]); pid = int(result.stdout.strip())
        self.part={'index': index, 'device_path': path, 'pid': pid, 'started_monotonic': time.monotonic(),
                   'start_tick': tick, 'source': 'Actual Android screenrecord, not reconstructed video'}

    def stop(self, tick):
        if not self.part: return
        part=self.part; self.part=None
        self.bridge.android(['kill', '-2', str(part['pid'])], check=False)
        script=f'for i in $(seq 1 40); do kill -0 {part["pid"]} 2>/dev/null || break; sleep 0.1; done'
        self.bridge.android(['sh', '-c', script], timeout=10, check=False)
        data=self.bridge.android(['cat', part['device_path']], timeout=60).stdout
        if len(data)>80*1024*1024: raise ValueError('Recording exceeded the planned local segment budget')
        path=self.folder/f'part-{part["index"]:03d}.mp4'; path.write_bytes(data)
        info=inspect_mp4(data); part.update(info); part.pop('started_monotonic',None)
        part.update(path=str(path.relative_to(ROOT)),end_tick=tick)
        part['device_log']=self.bridge.android(['cat',part['device_path']+'.log'],check=False).stdout.decode(errors='replace')
        self.parts.append(part); write_json(self.folder/'recording.json',{'parts':self.parts,'error':self.error,'frame_fallback':'frames/index.jsonl'})
        if not info['valid_mp4']: raise RuntimeError('Android codec did not produce a valid duration-bearing MP4; native frame fallback retained')

    def rotate_if_needed(self,tick):
        if self.part and time.monotonic()-self.part['started_monotonic']>165:
            self.stop(tick);self.start(tick)


class ViewerSession:
    def __init__(self, episode_path, output, port=26789, quantum=10, speed=1.0, record=False):
        if type(quantum) is not int or not 1<=quantum<=100: raise ValueError('Quantum must be1..100 ticks')
        if not 0<=speed<=10: raise ValueError('Speed must be0(uncapped)..10')
        self.episode=load_episode(episode_path);self.folder=local(output);self.folder.mkdir(parents=True,exist_ok=False)
        (self.folder/'frames').mkdir();self.bridge=AndroidBridge();self.port=port;self.quantum=quantum;self.speed=speed
        self.record_requested=record;self.queue=queue.Queue();self.lock=threading.Lock();self.ready=threading.Event();self.closed=threading.Event()
        self.engine=None;self.state=None;self.playing=False;self.stop_requested=False;self.latest_frame=None;self.frame_index=0
        self.status={'ready':False,'tick':None,'playing':False,'terminal':False,'error':None,'jobs':{},'source_episode_id':self.episode['source_episode_id'],
                     'source_action_count':len(self.episode['journal']),'training_decision_samples':0,'clock_control':'Native remains paused; a single worker advances exact ticks',
                     'requested_speed':speed,'quantum_ticks':quantum,'hud_scope':'Conservative selected-owner projection; no enemy hand/cycle, targets or cooldowns',
                     'anchors':self.episode['anchors']}
        self.huds={};self.recorder=NativeRecorder(self.bridge,self.folder)
        self.thread=threading.Thread(target=self._worker,name='native-viewer-rpc-owner',daemon=True);self.thread.start()

    def submit(self,command,ticks=None):
        if command not in ('play','pause','step','advance','pause_check','capture','shutdown'):raise ValueError('Unknown viewer command')
        if command=='advance' and (type(ticks) is not int or not 1<=ticks<=10000):raise ValueError('Advance must be1..10000 ticks')
        ticket=secrets.token_hex(6)
        with self.lock:self.status['jobs'][ticket]={'command':command,'status':'queued','ticks':ticks}
        self.queue.put((ticket,command,ticks));return ticket

    def public(self,owner=0):
        if owner not in (0,1):raise ValueError('Owner must be0/1')
        with self.lock:return {**deepcopy(self.status),'hud':deepcopy(self.huds.get(owner)), 'selected_owner':owner}

    def _publish(self):
        with self.lock:
            self.status.update(ready=True,tick=self.state['tick'],playing=self.playing,terminal=self.state['finalized'],
                               result=self.state['result'],crowns=deepcopy(self.state['crowns']),native_generation=self.state['generation'],
                               actual_native_events=len(self.engine._plays),frame_count=self.frame_index)
            self.huds={owner:self.engine.observe_visible(owner) for owner in (0,1)}

    def _frame(self,semantic_sha):
        # Native simulation is paused on entry; allow its drawing thread a frame.
        time.sleep(0.06);png=self.bridge.capture();self.frame_index+=1
        path=self.folder/'frames'/f'{self.frame_index:06d}-tick{self.state["tick"]:06d}.png';path.write_bytes(png)
        row={'frame_index':self.frame_index,'tick':self.state['tick'],'wall_time_utc':datetime.now(timezone.utc).isoformat(),
             'path':str(path.relative_to(ROOT)),'sha256':hashlib.sha256(png).hexdigest(),'bytes':len(png),
             'native_semantic_sha256':semantic_sha,'clock':'Source native tick; wall/video time is separately recorded'}
        append_json(self.folder/'frames/index.jsonl',row)
        with self.lock:self.latest_frame=png;self.status['last_frame']=row

    def _accept(self):
        sha=self.anchors.accept(self.engine,self.state);self._frame(sha);self._publish()

    def _advance(self,ticks):
        if self.state['finalized']:return
        future=[t for t in self.episode['anchors'] if t>self.state['tick']]
        amount=min(ticks,min(future)-self.state['tick']) if future else ticks
        self.state=self.engine.advance_exact_ticks(amount);self._accept()
        if self.state['finalized']:
            self.playing=False
            if self.recorder.part:
                try:self.recorder.stop(self.state['tick'])
                except Exception as exc:self.recorder.error=f'{type(exc).__name__}: {exc}'
            write_json(self.folder/'terminal.json',self.engine.result());self._publish()

    def _job(self,ticket,command,ticks):
        before=self.state['tick'];started=time.monotonic();answer={}
        if command=='play':
            if self.state['finalized']:raise ValueError('Episode already terminal')
            self.playing=True
        elif command=='pause':self.playing=False
        elif command in ('step','advance'):
            self.playing=False;target=self.state['tick']+(1 if command=='step' else ticks)
            while self.state['tick']<target and not self.state['finalized']:self._advance(min(self.quantum,target-self.state['tick']))
        elif command=='capture':self._frame(self.anchors.accept(self.engine,self.state))
        elif command=='pause_check':
            self.playing=False;generation=self.state['generation'];origin=self.anchors.event_origin
            a=self.engine._capture(generation,self.state['next']);events=list(self.engine._plays.values())
            time.sleep(0.35);b=self.engine._capture(generation,a['next']);self.state=b
            answer={'paused_native_tick_before':a['tick'],'paused_native_tick_after':b['tick'],
                    'all_minimal_semantics_stable':semantic_packet(a,events,origin)==semantic_packet(b,events,origin)}
        elif command=='shutdown':self.playing=False;self.stop_requested=True
        self._publish();answer.update(command=command,status='done',before_tick=before,after_tick=self.state['tick'],elapsed_seconds=time.monotonic()-started)
        with self.lock:self.status['jobs'][ticket]=answer
        append_json(self.folder/'controls.jsonl',{'ticket':ticket,**answer});return answer

    def _worker(self):
        try:
            self.engine,self.state=prepare_engine(self.episode,self.folder,True,self.port)
            self.anchors=AnchorLog(self.folder,self.episode,'native-render');self._accept()
            if self.record_requested:
                try:self.recorder.start(self.state['tick'])
                except Exception as exc:self.recorder.error=f'{type(exc).__name__}: {exc}'
            self.ready.set();next_step=time.monotonic()
            while not self.stop_requested:
                wait=max(0,next_step-time.monotonic()) if self.playing else 0.5
                try:
                    ticket,command,ticks=self.queue.get(timeout=wait)
                    try:self._job(ticket,command,ticks)
                    except Exception as exc:
                        with self.lock:self.status['jobs'][ticket]={'status':'error','error':f'{type(exc).__name__}: {exc}'}
                        raise
                except queue.Empty:
                    if self.playing:
                        started=time.monotonic();before=self.state['tick'];self._advance(self.quantum)
                        if self.recorder.part:
                            try:self.recorder.rotate_if_needed(self.state['tick'])
                            except Exception as exc:self.recorder.error=f'{type(exc).__name__}: {exc}'
                        delay=(self.state['tick']-before)/20/self.speed if self.speed else 0
                        next_step=max(started+delay,time.monotonic())
        except Exception as exc:
            self.playing=False
            with self.lock:self.status.update(error=f'{type(exc).__name__}: {exc}',playing=False)
            (self.folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8');self.ready.set()
        finally:
            if self.recorder.part:
                try:self.recorder.stop(self.state['tick'] if self.state else None)
                except Exception as exc:self.recorder.error=f'{type(exc).__name__}: {exc}'
            write_json(self.folder/'recording.json',{'parts':self.recorder.parts,'error':self.recorder.error,
                       'fallback':'Unmodified native PNG sequence and complete frames/index.jsonl are always retained',
                       'complete_source_interval':bool(self.state and self.state['finalized']),
                       'source_ticks':[90,self.state['tick'] if self.state else None],
                       'requested_realtime_pacing':self.speed==1, 'wall_time_equals_source_time':False,
                       'timing_note':'Screenshot/codec overhead and interactive pauses affect measured wall/video time; source ticks are separately indexed'})
            if self.engine:self.engine.close()
            write_json(self.folder/'session.json',self.public());self.closed.set()


PAGE = r'''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>皇室战争 · 本地原生回放</title><style>
body{margin:0;background:#121a25;color:#e8eef6;font:16px system-ui,sans-serif}main{max-width:1250px;margin:auto;padding:24px;display:grid;grid-template-columns:minmax(260px,480px) 1fr;gap:28px}img{width:100%;max-height:90vh;object-fit:contain;background:#080b10;border-radius:12px}.panel{background:#1c2939;padding:22px;border-radius:12px}h1{font-size:25px;margin:0 0 10px}p{color:#aebdd0;line-height:1.6}button,select,input{font:inherit;padding:10px 12px;margin:5px 4px 5px 0;border:1px solid #53708d;border-radius:7px;background:#294158;color:white}button{cursor:pointer}button:disabled{opacity:.4;cursor:wait}button.primary{background:#2378ac}input{width:85px}table{width:100%;border-collapse:collapse;font-size:14px}td,th{padding:9px;text-align:left;border-bottom:1px solid #34465a}.tag{color:#80d5bd}#message{white-space:pre-wrap;overflow-wrap:anywhere;color:#f2cc83}code{font-size:12px;overflow-wrap:anywhere}@media(max-width:760px){main{grid-template-columns:1fr;padding:12px}}
</style><main><section><img id="frame" alt="正在读取实际 Android 原生画面"></section><section class="panel"><h1>本地原生回放</h1><div class="tag" id="clock">正在初始化…</div><p>原生时钟保持暂停，由本地工作线程按精确 tick 推进。画面来自 Android framebuffer；此页用于观看和调试，不生成训练样本。</p>
<div id="buttons"><button class="primary" data-command="play">开始 / 继续</button><button data-command="pause">暂停</button><button data-command="step">单步 +1 tick</button><button data-command="capture">保存截图</button><br><input id="ticks" type="number" min="1" max="10000" value="100"><button data-command="advance">定量推进</button><button data-command="pause_check">检查暂停</button></div>
<p>查看玩家 <select id="owner"><option value="0">native owner 0</option><option value="1">native owner 1</option></select> <span id="elixir"></span></p><h3>该玩家真实手牌</h3><table><thead><tr><th>槽位</th><th>卡牌 ID</th><th>费用</th></tr></thead><tbody id="hand"></tbody></table><h3>公开塔血量</h3><table><tbody id="towers"></tbody></table><p id="message"></p><p><code id="episode"></code></p><p>手牌与圣水来自所选玩家的保守可见字段。对手私有手牌、轮转、目标和冷却不在网页数据中。录像可能比源对局快或慢，tick 与墙钟索引分别保留。</p></section></main>
<script>
const token=__TOKEN__;let owner=0;let imageKey='';const $=id=>document.getElementById(id);
async function command(name){$('message').textContent='已提交 '+name;const body={command:name};if(name==='advance')body.ticks=Number($('ticks').value);const r=await fetch('/api/control',{method:'POST',headers:{'Content-Type':'application/json','X-Viewer-Token':token},body:JSON.stringify(body)});const a=await r.json();if(!r.ok)$('message').textContent=JSON.stringify(a);else $('message').textContent='操作排队：'+a.ticket;}
document.querySelectorAll('[data-command]').forEach(b=>b.onclick=()=>command(b.dataset.command));$('owner').onchange=e=>{owner=Number(e.target.value);poll()};
async function poll(){try{const s=await(await fetch('/api/state?owner='+owner)).json();$('clock').textContent='tick '+s.tick+' · '+(s.terminal?'自然终局':s.playing?'推进中':'暂停')+' · '+s.actual_native_events+' 次原生消费';$('episode').textContent=s.source_episode_id;document.querySelectorAll('button').forEach(b=>b.disabled=!s.ready||!!s.error||(s.terminal&&['play','step','advance'].includes(b.dataset.command)));if(s.error)$('message').textContent=s.error;const h=s.hud;if(h){$('elixir').textContent='圣水 '+(h.own_elixir_raw/10000).toFixed(2);$('hand').innerHTML=h.hand.map(c=>'<tr><td>'+c.slot+'</td><td>'+c.card+'</td><td>'+c.cost+'</td></tr>').join('');$('towers').innerHTML=h.towers.map(t=>'<tr><td>'+t.side+' '+t.role+'</td><td>'+(t.hp===null?'已消失 / 未确认':t.hp+'/'+t.max_hp)+'</td></tr>').join('');}if(s.last_frame&&imageKey!==s.last_frame.sha256){imageKey=s.last_frame.sha256;$('frame').src='/api/frame?v='+imageKey;}const jobs=Object.values(s.jobs||{});if(jobs.length&&!s.error){const j=jobs[jobs.length-1];if(j.status==='done')$('message').textContent=j.command+' 已完成：tick '+j.before_tick+' → '+j.after_tick+(j.all_minimal_semantics_stable===undefined?'':'；暂停语义稳定 '+j.all_minimal_semantics_stable);}}catch(e){$('message').textContent=String(e)}}setInterval(poll,500);poll();
</script></html>'''


def make_server(session, port=8766):
    token=secrets.token_hex(24)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*_):pass
        def allowed(self):return self.headers.get('Host','').split(':')[0] in ('127.0.0.1','localhost')
        def send(self,status,data,kind='application/json'):
            if isinstance(data,(dict,list)):data=json.dumps(data,ensure_ascii=False).encode()
            if isinstance(data,str):data=data.encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(data)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
        def do_GET(self):
            if not self.allowed():return self.send(403,{'error':'Loopback host required'})
            url=urlsplit(self.path)
            if url.path=='/':return self.send(200,PAGE.replace('__TOKEN__',json.dumps(token)),'text/html; charset=utf-8')
            if url.path=='/api/state':
                try:return self.send(200,session.public(int(parse_qs(url.query).get('owner',['0'])[0])))
                except ValueError as exc:return self.send(400,{'error':str(exc)})
            if url.path=='/api/frame':
                with session.lock:frame=session.latest_frame
                return self.send(200,frame,'image/png') if frame else self.send(503,{'error':'Native frame not ready'})
            return self.send(404,{'error':'Unknown local endpoint'})
        def do_POST(self):
            if not self.allowed() or self.headers.get('X-Viewer-Token')!=token:return self.send(403,{'error':'Local viewer token required'})
            if urlsplit(self.path).path!='/api/control':return self.send(404,{'error':'Unknown endpoint'})
            try:
                size=int(self.headers.get('Content-Length','0'))
                if not 1<=size<=4096:raise ValueError('Bounded JSON body required')
                body=json.loads(self.rfile.read(size));ticket=session.submit(body['command'],body.get('ticks'))
                return self.send(202,{'ticket':ticket,'status':'queued'})
            except (ValueError,KeyError,TypeError) as exc:return self.send(400,{'error':str(exc)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler);server.daemon_threads=True
    return server,token

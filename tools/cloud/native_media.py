"""Segmented Android screenrecord with conservative media/clock evidence.

The caller owns the native lease. No VM/app startup or native RPC is used.
Browser playback is a separate acceptance check. Tick/video links are intervals.
"""
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import threading
import time

from royale_phase3.common import ROOT, append, digest, local, reference, write
from tools.cloud.native_identity import Journal, SERIAL
from royale_phase2.platform_stream import _PROC_START


def find_media_tool(name, explicit=None):
    candidates = ([explicit] if explicit else [f'.cache/native-tools/bin/{name}',
                   f'.venv-cloud/bin/{name}', shutil.which(name)])
    for candidate in candidates:
        if candidate:
            path = Path(candidate)
            if not path.is_absolute(): path = local(path)
            if path.is_file(): return path.resolve()
    raise FileNotFoundError(f'缺少 {name}；请在项目 .cache/native-tools/bin 或 .venv-cloud/bin 准备工具，或显式指定路径')


def _number(value):
    result = float(value)
    if not math.isfinite(result): raise ValueError('Nonfinite media timestamp')
    return result


def inspect_probe(value):
    """Inspect real packet and decoded-frame PTS; this alone is not full decode."""
    streams = [s for s in value.get('streams', []) if s.get('codec_type') == 'video']
    packets, frames = value.get('packets', []), value.get('frames', [])
    if len(streams) != 1 or not packets or not frames:
        raise ValueError('ffprobe did not find one nonempty video stream and decoded frames')
    stream = streams[0]
    if not stream.get('codec_name') or any(type(stream.get(k)) is not int or stream[k] <= 0 for k in ('width','height')):
        raise ValueError('Invalid video dimensions/codec')
    pts = [_number(p['pts_time']) for p in packets]
    frame_pts = [_number(f.get('best_effort_timestamp_time', f.get('pts_time'))) for f in frames]
    if any(b <= a for a,b in zip(frame_pts, frame_pts[1:])):
        raise ValueError('Decoded video frame PTS does not increase')
    duration = _number(value.get('format', {}).get('duration', stream.get('duration',0)))
    if duration <= 0 or frame_pts[-1]-frame_pts[0] > duration+.1: raise ValueError('Invalid video duration')
    keyframes = [stamp for frame,stamp in zip(frames,frame_pts) if frame.get('key_frame') == 1]
    if not keyframes or keyframes[0] != frame_pts[0]: raise ValueError('Video does not begin with a keyframe')
    packet = min(packets, key=lambda p:_number(p['pts_time']))
    last_packet = max(packets, key=lambda p:_number(p['pts_time']))
    last_packet_duration = (_number(last_packet['duration_time'])
                            if 'duration_time' in last_packet else None)
    position,size = int(packet['pos']),int(packet['size'])
    if position < 0 or size <= 0: raise ValueError('Invalid first packet position/size')
    dts = [_number(p['dts_time']) for p in packets if 'dts_time' in p]
    if any(b < a for a,b in zip(dts,dts[1:])): raise ValueError('Video packet DTS regresses')
    # Packet PTS may regress with B frames; decoded display PTS may not.
    return dict(codec=stream['codec_name'],width=stream['width'],height=stream['height'],
        duration_seconds=duration,first_pts_seconds=min(pts),last_pts_seconds=max(pts),
        first_frame_pts_seconds=frame_pts[0],last_frame_pts_seconds=frame_pts[-1],
        last_packet_duration_seconds=last_packet_duration,
        frame_span_seconds=frame_pts[-1]-frame_pts[0],frame_count=len(frames),
        measured_encoded_fps=(len(frames)-1)/(frame_pts[-1]-frame_pts[0]) if len(frames)>1 else None,
        fresh_frame_rate_verified=False,packet_count=len(pts),
        packet_pts_monotonic=all(b >= a for a,b in zip(pts,pts[1:])),
        frame_pts_monotonic=True,keyframe_count=len(keyframes),keyframe_pts_seconds=keyframes,
        first_packet=dict(position=position,size=size),frame_pts_seconds=frame_pts,
        packet_pts_seconds=pts,container_verified=True)


def video_interval_for_tick(media_report, tick):
    """Nearest measured tick anchor, never tick/20 or interpolated wall time."""
    if type(tick) is not int: return None
    candidates = [a for a in media_report.get('mapping_anchors',[])
                  if type(a.get('tick')) is int and a.get('video_seconds_range')]
    if not candidates: return None
    anchor = min(candidates,key=lambda a:(abs(a['tick']-tick),a['tick']))
    low,high = anchor['video_seconds_range']
    return dict(video_seconds_range=[low,high],anchor_tick=anchor['tick'],
        tick_distance=abs(anchor['tick']-tick),uncertainty_seconds=high-low,
        timing_scope=media_report.get('timing_scope',
            'Nearest device-uptime/PTS interval; pixel presentation latency remains unknown'),
        exact_frame_tick_binding=False)


class NativeMediaRecorder:
    """Inert construction. start()->bool; stop()->idempotent media receipt.

    anchor() samples ADB uptime only. Caller still owns/pauses the native world.
    Always stop in finally. Failed start attempts to release only its own guest
    process; no global kill or native mutation retry is used.
    """
    def __init__(self,output,*,adb='.cache/native-tools/bin/adb',adb_port=5038,
                 serial='127.0.0.1:15555',root_shell='su0',ffmpeg=None,ffprobe=None,
                 segment_seconds=170,size='360x640',bitrate=1000000,context=None,
                 max_segment_gap_seconds=1.0,encoder_probe_seconds=10,runner=None,popen=None,clock=None):
        if not isinstance(serial,str) or not SERIAL.fullmatch(serial) or root_shell not in ('su0','adbd'):
            raise ValueError('Explicit safe ADB serial/root shell required')
        if type(adb_port) is not int or not 1 <= adb_port <= 65535: raise ValueError('Invalid ADB port')
        if type(segment_seconds) is not int or not 5 <= segment_seconds <= 180:
            raise ValueError('screenrecord segment seconds must be 5..180')
        if not isinstance(size,str) or not re.fullmatch(r'[1-9]\d{1,3}x[1-9]\d{1,3}',size):
            raise ValueError('Explicit video size required')
        width,height=map(int,size.split('x'))
        if width%2 or height%2 or width>1920 or height>1920: raise ValueError('Video size must be even and <=1920 per axis')
        if type(bitrate) is not int or not 100000 <= bitrate <= 10000000: raise ValueError('Invalid video bitrate')
        if type(max_segment_gap_seconds) not in (float,int) or not 0 <= max_segment_gap_seconds <= 5:
            raise ValueError('Explicit segment gap budget must be 0..5 seconds')
        if type(encoder_probe_seconds) is not int or encoder_probe_seconds not in (5,10):
            raise ValueError('Encoder probe must explicitly use 5 or 10 seconds')
        self.folder=local(output); self.adb=local(adb); self.port=adb_port; self.serial=serial
        self.root_shell=root_shell; self.ffmpeg_arg=ffmpeg; self.ffprobe_arg=ffprobe
        self.segment_seconds=segment_seconds; self.size=size; self.bitrate=bitrate
        self.encoder_probe_seconds=encoder_probe_seconds
        self.context=deepcopy(context or {}); self.runner=runner; self.popen=popen or subprocess.Popen
        self.clock=clock or time.monotonic; self.token=secrets.token_hex(12)
        self.remote='/data/local/tmp/royale-media-'+self.token
        self.process=None; self.thread=None; self.journal=None; self.log=None
        self.ready=threading.Event(); self.events=[]; self.anchors=[]; self._event_lock=threading.Lock()
        self.started=None; self.stop_started=None; self.finished=False; self._release_attempted=False
        self.report=dict(schema='royale-native-video.v2',status='not_started',requested=True,
            source='actual_android_screenrecord',source_kind='original_episode',
            evidence_scope='interface_only' if runner or popen else 'actual_android_recording',
            context=self.context,serial=serial,adb_port=adb_port,root_shell=root_shell,
            remote_directory=self.remote,segment_seconds=segment_seconds,requested_size=size,bitrate=bitrate,
            encoder_probe_seconds=encoder_probe_seconds,encoder_probe_automatic_retries=0,
            max_segment_gap_seconds=float(max_segment_gap_seconds),parts=[],anchors=0,mapping_anchors=[],
            recording_complete=False,native_rpc_calls=0,native_rendered_required=True,
            exact_frame_tick_binding=False,official_fidelity_verified=False,game_content_visually_verified=False,
            browser_seek_verified=False,decodable_verified=False,seek_verified=False,
            timing_scope='Device uptime/real PTS intervals; native presentation latency is not measured',
            remote_cleanup_verified=False,errors=[])

    def _save(self): write(self.folder/'recording.json',self.report)

    def _shell(self,label,script,timeout=30):
        return self.journal.device(label,['shell','-T',*(['su','0'] if self.root_shell=='su0' else []),'sh','-s'],script,timeout=timeout)

    def _error(self,exc):
        self.report['errors'].append(f'{type(exc).__name__}: {exc}')
        self.report.update(status='failed',recording_complete=False); self._save()

    def _encoder_probe(self):
        """One explicit short recording; retain guest diagnostics even on failure."""
        try:
            self._shell('encoder-probe',f"set -eu\nmkdir '{self.remote}'\n"
                f'/system/bin/screenrecord --verbose --size {self.size} --bit-rate {self.bitrate} '
                f'--time-limit {self.encoder_probe_seconds} '
                f"'{self.remote}/encoder-probe.mp4' >'{self.remote}/encoder-probe.log' 2>&1\n",timeout=90)
        finally:
            # Pull is a read, not a second encoder attempt. An absent diagnostic
            # must never hide the original launch/timeout exception.
            try:
                path=self.folder/'encoder-probe.guest.log'
                self.journal.device('pull-encoder-probe-log',
                    ['pull',self.remote+'/encoder-probe.log',str(path)],timeout=30)
                if path.stat().st_size>2*1024*1024: raise ValueError('Encoder diagnostic exceeds 2 MiB')
                self.report['encoder_probe_log']=reference(path)
            except Exception as exc:
                self.report['encoder_probe_log_error']=f'{type(exc).__name__}: {exc}'
            self._save()
        probe=self.folder/'encoder-probe.mp4'
        self.journal.device('pull-encoder-probe',['pull',self.remote+'/encoder-probe.mp4',str(probe)],timeout=90)
        return probe

    def _probe(self,path,label):
        base=[str(self.ffprobe),'-v','error','-select_streams','v:0']
        raw=self.journal.run(label,base+['-show_streams','-show_format','-show_packets','-show_entries',
            'stream=codec_name,codec_type,width,height,duration:format=duration,size:packet=pts_time,dts_time,duration_time,pos,size,flags','-of','json',str(path)],timeout=180)
        frames=self.journal.run(label+'-frames',base+['-show_frames','-show_entries',
            'frame=best_effort_timestamp_time,pts_time,key_frame','-of','json',str(path)],timeout=180)
        if len(raw.encode())+len(frames.encode())>16*1024*1024: raise ValueError('ffprobe exceeds the 16 MiB timing budget')
        value=json.loads(raw); value['frames']=json.loads(frames).get('frames',[])
        info=inspect_probe(value)
        timing={key:info.pop(key) for key in ('frame_pts_seconds','packet_pts_seconds')}
        index=self.folder/(label+'-pts.json'); write(index,timing); info['pts_index']=reference(index)
        points=timing['frame_pts_seconds']; first=points[0]
        info['seek_points_seconds']=[max(0,points[i]-first-.000001) for i in (0,len(points)//2,len(points)-1)]
        return info

    def _decode(self,path,label,info,seek=False):
        progress=self.journal.run(label+'-decode',[str(self.ffmpeg),'-nostdin','-hide_banner','-v','error',
            '-xerror','-err_detect','explode','-nostats','-progress','pipe:1','-i',str(path),
            '-map','0:v:0','-an','-fps_mode','passthrough','-f','framehash','-'],timeout=300)
        decoded=re.findall(r'^frame=(\d+)\s*$',progress,re.M)
        if not decoded or int(decoded[-1]) != info['frame_count']:
            raise ValueError('Full ffmpeg decode frame count differs from ffprobe')
        hashes=[line.rsplit(',',1)[-1].strip() for line in progress.splitlines()
                if re.match(r'^\s*\d+\s*,',line)]
        if len(hashes)!=info['frame_count']: raise ValueError('Full decoded pixel hash count differs from frame count')
        changes=sum(a!=b for a,b in zip(hashes,hashes[1:]))
        info['full_decode_verified']=True; info['decoded_frames']=int(decoded[-1])
        info.update(unique_decoded_pixel_hashes=len(set(hashes)),consecutive_pixel_changes=changes,
            pixel_change_fps=changes/info['frame_span_seconds'] if info['frame_span_seconds'] else None,
            pixel_change_scope='Decoded pixel changes per video second; HUD changes are not battle FPS or independent freshness attestation')
        if seek:
            receipts=[]
            for index,point in enumerate(info['seek_points_seconds']):
                raw=self.journal.run(label+f'-seek-{index}',[str(self.ffmpeg),'-nostdin','-hide_banner','-v','error',
                    '-xerror','-ss',f'{point:.6f}','-i',str(path),'-map','0:v:0','-frames:v','1',
                    '-an','-f','framehash','-'],timeout=90)
                hashes=[line for line in raw.splitlines() if re.match(r'^\s*\d+\s*,',line)]
                if len(hashes)!=1: raise ValueError('Seeking did not produce one decoded frame')
                receipts.append(dict(requested_seconds=point,decoded_frame_hash=hashes[0]))
            info['seek_checks']=receipts; info['seek_verified']=True
        return info

    def _start_script(self):
        return _PROC_START+f'''
set -u
[ "$(id -u)" = 0 ] || exit 31
uptime_now() {{ read up rest </proc/uptime; printf '%s' "$up"; }}
pid=0; started=0
stop_owned() {{
  : >'{self.remote}/stop'
  if [ "$pid" -gt 1 ] && [ "$(proc_start "$pid" || true)" = "$started" ]; then
    kill -2 "$pid" 2>/dev/null || true
  fi
}}
trap stop_owned HUP INT TERM EXIT
i=0
while [ ! -e '{self.remote}/stop' ]; do
  i=$((i+1)); name=$(printf 'part-%05d.mp4' "$i"); begin=$(uptime_now)
  /system/bin/screenrecord --size {self.size} --bit-rate {self.bitrate} --time-limit {self.segment_seconds} '{self.remote}/'"$name" >'{self.remote}/'"$name.log" 2>&1 < /dev/null &
  pid=$!; started=$(proc_start "$pid") || exit 76
  printf '%s %s %s %s\\n' "$pid" "$started" '{self.token}' "$name" >'{self.remote}/recorder.pid.tmp'
  mv '{self.remote}/recorder.pid.tmp' '{self.remote}/recorder.pid'
  # Close the rotation race: cleanup may have inspected the previous PID just
  # before this child was registered. It still observes this stop marker.
  if [ -e '{self.remote}/stop' ]; then kill -2 "$pid" 2>/dev/null || true; fi
  printf '{{"event":"segment_start","segment":%s,"pid":%s,"start_ticks":%s,"file":"%s","device_uptime":%s}}\\n' "$i" "$pid" "$started" "$name" "$begin"
  tries=0
  while [ "$tries" -lt 300 ] && [ ! -e '{self.remote}/stop' ]; do
    bytes=$(stat -c %s '{self.remote}/'"$name" 2>/dev/null || echo 0)
    if [ "$bytes" -ge 1024 ]; then
      cp '{self.remote}/'"$name" '{self.remote}/ready-'"$name" || exit 78
      bytes=$(stat -c %s '{self.remote}/ready-'"$name")
      printf '{{"event":"segment_ready","segment":%s,"file":"%s","bytes":%s,"device_uptime":%s}}\\n' "$i" "$name" "$bytes" "$(uptime_now)"; break
    fi
    [ "$(proc_start "$pid" || true)" = "$started" ] || break
    tries=$((tries+1)); sleep 0.1
  done
  wait "$pid"; result=$?
  printf '{{"event":"segment_end","segment":%s,"exit_code":%s,"file":"%s","device_uptime":%s}}\\n' "$i" "$result" "$name" "$(uptime_now)"
  [ -e '{self.remote}/stop' ] && break
  [ "$result" = 0 ] || exit "$result"
done
'''

    def _stop_script(self):
        expected=f'/system/bin/screenrecord --size {self.size} --bit-rate {self.bitrate} --time-limit {self.segment_seconds} '
        return _PROC_START+f'''
set -eu
[ "$(id -u)" = 0 ] || exit 31
mkdir -p '{self.remote}'; : >'{self.remote}/stop'
if [ ! -r '{self.remote}/recorder.pid' ]; then
  printf '{{"released":true,"signal_sent":false,"reason":"no_owned_recorder_record"}}\\n'; exit 0
fi
read pid started token name <'{self.remote}/recorder.pid'
case "$pid:$started" in *[!0-9:]*|:*|*:) exit 73;; esac
[ "$token" = '{self.token}' ] && [ "$pid" -gt 1 ] || exit 74
current=$(proc_start "$pid" || true)
if [ -z "$current" ] || [ "$current" != "$started" ]; then
  printf '{{"released":true,"signal_sent":false,"reason":"owned_process_already_exited"}}\\n'; exit 0
fi
cmd=$(tr '\\000' ' ' <"/proc/$pid/cmdline")
[ "$cmd" = '{expected}'"{self.remote}/$name " ] || exit 75
[ "$(proc_start "$pid")" = "$started" ] || exit 76
kill -2 "$pid"
i=0
while [ "$i" -lt 100 ]; do
  current=$(proc_start "$pid" || true)
  if [ -z "$current" ] || [ "$current" != "$started" ]; then
    printf '{{"released":true,"signal_sent":true,"ownership_verified":true}}\\n'; exit 0
  fi
  i=$((i+1)); sleep 0.1
done
printf '{{"released":false,"signal_sent":true,"ownership_verified":true}}\\n'; exit 77
'''

    def _read_events(self):
        try:
            while True:
                line=self.process.stdout.readline(4097)
                if not line: break
                if len(line)>4096 or not line.endswith(b'\n'): raise ValueError('Invalid bounded supervisor event')
                event=json.loads(line)
                if (event.get('event') not in ('segment_start','segment_ready','segment_end')
                    or type(event.get('segment')) is not int or not 1<=event['segment']<=100
                    or event.get('file') != f'part-{event["segment"]:05d}.mp4'
                    or _number(event.get('device_uptime'))<0): raise ValueError('Invalid supervisor event fields')
                event.update(received_monotonic=self.clock(),received_utc=datetime.now(timezone.utc).isoformat())
                with self._event_lock: self.events.append(event)
                append(self.folder/'segment-events.jsonl',event)
                if event['event']=='segment_ready' and event['segment']==1: self.ready.set()
        except Exception as exc:
            self.report['reader_error']=f'{type(exc).__name__}: {exc}'; self.ready.set()

    def _release(self):
        if not self.process or self._release_attempted: return
        self._release_attempted=True
        interrupted=None
        try:
            receipt=json.loads(self._shell('stop-owned-recorder',self._stop_script(),timeout=30))
            self.report['stop_receipt']=receipt
            if receipt.get('released') is not True: raise RuntimeError('Owned guest recorder release not verified')
            self.process.wait(timeout=35)
        except BaseException as exc:
            self.report['guest_release_unverified']=True
            self.report['errors'].append(f'Guest cleanup: {type(exc).__name__}: {exc}')
            if not isinstance(exc,Exception): interrupted=exc
        finally:
            if self.process.poll() is None:
                self.process.terminate()
                try: self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=5)
            if self.thread:
                self.thread.join(timeout=5)
                if self.thread.is_alive(): self.report['reader_release_unverified']=True
            if self.log: self.log.close()
        if interrupted: raise interrupted

    def start(self):
        self.folder.mkdir(parents=True,exist_ok=False)
        self.report['status']='preparing'; self._save()
        try:
            if not self.adb.is_file(): raise FileNotFoundError('Existing repository-local ADB is required')
            self.ffmpeg=find_media_tool('ffmpeg',self.ffmpeg_arg); self.ffprobe=find_media_tool('ffprobe',self.ffprobe_arg)
            self.journal=Journal(self.folder,str(self.adb),self.serial,self.port,self.runner)
            for name,executable in (('ffmpeg',self.ffmpeg),('ffprobe',self.ffprobe)):
                self.journal.run(name+'-version',[str(executable),'-version'])
            client=self.journal.run('adb-version',[str(self.adb),'version'])
            protocol=re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)',client)
            if not protocol: raise ValueError('Cannot verify ADB client version')
            self.journal.client_version=int(protocol[1])
            if self.journal.device('get-state',['get-state']).strip()!='device': raise RuntimeError('Selected Android is not ready')
            self._shell('capabilities','set -eu\n[ "$(id -u)" = 0 ] || exit 31\n'
                '[ "$(getprop sys.boot_completed)" = 1 ] || exit 32\n'
                'test -x /system/bin/screenrecord\n/system/bin/screenrecord --help\ndumpsys media.codec\n')
            probe=self._encoder_probe()
            self.report['encoder_probe']=self._decode(probe,'encoder-probe',self._probe(probe,'verify-encoder-probe'))
            self.report['tools']={k:str(v) for k,v in dict(adb=self.adb,ffmpeg=self.ffmpeg,ffprobe=self.ffprobe).items()}
            script=self._start_script(); (self.folder/'start.stdin.sh').write_text(script,encoding='utf-8')
            argv=[str(self.adb),'-H','127.0.0.1','-P',str(self.port),'-s',self.serial,'shell','-T',
                  *(['su','0'] if self.root_shell=='su0' else []),'sh','-s']
            self.report['supervisor_command']=argv; self.log=(self.folder/'supervisor.stderr.log').open('wb')
            self.started=self.clock(); self.report['started_monotonic']=self.started
            self.process=self.popen(argv,cwd=ROOT,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=self.log,start_new_session=True)
            self.thread=threading.Thread(target=self._read_events,name='native-video-events',daemon=True); self.thread.start()
            self.process.stdin.write(script.encode()); self.process.stdin.close()
            if not self.ready.wait(35) or self.report.get('reader_error') or self.process.poll() is not None:
                raise RuntimeError('Android encoder did not produce output while its supervisor remained active')
            snapshot=self.folder/'before-reset-part-00001.mp4'
            self.journal.device('pull-readiness-snapshot',['pull',self.remote+'/ready-part-00001.mp4',str(snapshot)],timeout=60)
            if snapshot.stat().st_size<1024 or snapshot.stat().st_size>16*1024*1024:
                raise ValueError('Encoder readiness snapshot outside size budget')
            self.report.update(status='recording',readiness_snapshot=reference(snapshot),
                started_before_reset=False,readiness_scope='Output bytes before reset; first packet payload is verified after finalization')
            self.anchor('before_reset'); self._save(); return True
        except BaseException as exc:
            self._error(exc); self._release()
            if self.journal: self.report['commands']=self.journal.records
            self._save()
            if not isinstance(exc,Exception): raise
            return False

    def anchor(self,phase,*,state=None,sequence=None,transition=None):
        if self.started is None or self.finished: return
        stamp=self.clock()
        row=dict(schema='royale-native-video-tick-anchor.v2',phase=str(phase),host_monotonic=stamp,
            host_utc=datetime.now(timezone.utc).isoformat(),recording_elapsed_seconds=stamp-self.started,
            tick=state.get('tick') if state else None,generation=state.get('generation') if state else None,
            state_sha256=digest(state) if state else None,sequence_index=sequence,exact_frame_tick_binding=False)
        try:
            before=self.clock()
            uptime=_number(self._shell('anchor-clock-'+str(len(self.anchors)),
                "set -eu\nread up rest </proc/uptime\nprintf '%s\\n' \"$up\"\n",timeout=20).strip())
            row.update(device_uptime=uptime,device_clock_resolution_seconds=.01,
                clock_sample_host_interval=[before,self.clock()])
        except Exception as exc:
            row['clock_sample_error']=f'{type(exc).__name__}: {exc}'
        if transition:
            row.update(episode_id=transition.get('episode_id'),planned_tick=transition.get('planned_tick'),
                actual_execution_tick=transition.get('actual_execution_tick'),card_consumed=transition.get('card_consumed'),
                request_ids=[r['registration']['request_id'] for r in transition.get('execution',{}).get('registrations',[])])
        self.anchors.append(row); append(self.folder/'tick-anchors.jsonl',row); self.report['anchors']=len(self.anchors)

    def _timeline(self,starts,events):
        """Bound frame uptime; report concat gaps rather than filling/hiding them."""
        timeline=[]; offset=0.
        for part,start in zip(self.report['parts'],starts):
            ready=next((e for e in events if e['segment']==start['segment'] and e['event']=='segment_ready'),None)
            end=next((e for e in events if e['segment']==start['segment'] and e['event']=='segment_end'),None)
            if not ready or not end: raise ValueError('Missing segment readiness/end timing')
            if not part.get('first_packet_in_readiness_snapshot'):
                raise ValueError('Segment output readiness does not attest a complete first video packet')
            low=_number(start['device_uptime'])-.01
            high=min(_number(ready['device_uptime'])+.01,
                     _number(end['device_uptime'])+.01-part['frame_span_seconds'])
            if high<low: raise ValueError('Packet duration incompatible with device process lifetime')
            timeline.append(dict(segment=start['segment'],video_start_seconds=offset,
                video_end_seconds=offset+part['duration_seconds'],first_frame_device_uptime_range=[low,high],
                last_frame_device_uptime_range=[low+part['frame_span_seconds'],high+part['frame_span_seconds']],
                encoder_start_uncertainty_seconds=high-low))
            offset+=part['duration_seconds']
        gaps=[]
        for previous,current in zip(timeline,timeline[1:]):
            a,b=previous['last_frame_device_uptime_range'],current['first_frame_device_uptime_range']
            gaps.append(dict(after_segment=previous['segment'],before_segment=current['segment'],
                missing_time_seconds_range=[max(0,b[0]-a[1]),max(0,b[1]-a[0])],
                scope='Last/first sample timestamp gap, including normal frame cadence'))
        mappings=[]
        for anchor in self.anchors:
            if type(anchor.get('tick')) is not int or 'device_uptime' not in anchor: continue
            stamp=anchor['device_uptime']; candidates=[]
            for item in timeline:
                first,last=item['first_frame_device_uptime_range'],item['last_frame_device_uptime_range']
                if first[0]-.01 <= stamp <= last[1]+.01:
                    low=max(item['video_start_seconds'],item['video_start_seconds']+stamp-.01-first[1])
                    high=min(item['video_end_seconds'],item['video_start_seconds']+stamp+.01-first[0])
                    if high>=low: candidates.append((low,high))
            if candidates:
                mappings.append(dict(tick=anchor['tick'],generation=anchor.get('generation'),phase=anchor['phase'],
                    video_seconds_range=[min(x[0] for x in candidates),max(x[1] for x in candidates)],
                    scope='Device uptime to PTS; first-frame/presentation uncertainty remains explicit'))
        self.report.update(segment_timeline=timeline,segment_gaps=gaps,mapping_anchors=mappings,
            segment_switch_observed=len(timeline)>1,
            segment_gaps_within_budget=all(g['missing_time_seconds_range'][1]<=self.report['max_segment_gap_seconds'] for g in gaps),
            concatenation='Original packets copied; measured gaps are not filled or counted as video')

    def _prefix_verified(self):
        snapshot=self.report.get('readiness_snapshot'); parts=self.report['parts']
        if not snapshot or not parts: return False
        return self._snapshot_contains_packet(local(snapshot['path']), local(parts[0]['path']), parts[0])

    @staticmethod
    def _snapshot_contains_packet(before, actual, info):
        position,size=info['first_packet']['position'],info['first_packet']['size']
        if before.stat().st_size<position+size: return False
        with before.open('rb') as old,actual.open('rb') as new:
            old.seek(position); new.seek(position)
            return old.read(size)==new.read(size)

    def stop(self,*,natural_terminal=False,native_result=None,context=None):
        if self.finished: return deepcopy(self.report)
        self.stop_started=self.clock()
        self.report.update(natural_terminal=bool(natural_terminal),native_result=deepcopy(native_result),
            context={**self.context,**deepcopy(context or {})})
        exited_before_stop=bool(self.process and self.process.poll() is not None)
        interrupted=None
        try:
            if not self.process: raise RuntimeError('Video recording did not start')
            if natural_terminal and isinstance(native_result,dict) and native_result.get('finalized'):
                self.anchor('natural_terminal',state=native_result)
                # Leave the terminal state displayed for a measured recording
                # tail. Full PTS bounds below still decide coverage, not sleep.
                self._shell('terminal-recording-tail', 'sleep 1\n', timeout=30)
            self._release()
            if self.report.get('guest_release_unverified') or self.report.get('reader_error') or self.report.get('reader_release_unverified'):
                raise RuntimeError('Recorder release/event collection did not complete cleanly')
            with self._event_lock: events=deepcopy(self.events)
            starts=[e for e in events if e['event']=='segment_start']
            ends=[e for e in events if e['event']=='segment_end']
            if not starts or len(starts)>100: raise ValueError('Missing or over-budget video segments')
            if [e['segment'] for e in starts] != list(range(1,len(starts)+1)): raise ValueError('Missing or duplicate segment')
            expected=[(index,event) for index in range(1,len(starts)+1)
                      for event in ('segment_start','segment_ready','segment_end')]
            if [(e['segment'],e['event']) for e in events]!=expected:
                raise ValueError('Segment start/readiness/end receipts are missing, duplicated or out of order')
            if any(b['device_uptime']<a['device_uptime'] for a,b in zip(events,events[1:])):
                raise ValueError('Segment device clock regressed')
            parts=self.folder/'parts'; parts.mkdir(exist_ok=False)
            for event in starts:
                name=event['file']; destination=parts/name
                self.journal.device('pull-'+name,['pull',self.remote+'/'+name,str(destination)],timeout=180)
                if not 0<destination.stat().st_size<=256*1024*1024: raise ValueError('Video segment outside byte budget')
                info=self._decode(destination,'part-'+str(event['segment']),self._probe(destination,'probe-'+name))
                snapshot=parts/('ready-'+name)
                self.journal.device('pull-ready-'+name,['pull',self.remote+'/ready-'+name,str(snapshot)],timeout=90)
                info['readiness_snapshot']=reference(snapshot)
                info['first_packet_in_readiness_snapshot']=self._snapshot_contains_packet(snapshot,destination,info)
                self.report['parts'].append(dict(**reference(destination),**info,segment=event['segment'],host_start_event=event)); self._save()
            listing=self.folder/'concat.txt'
            listing.write_text(''.join("file '"+str(local(p['path'])).replace("'","'\\''")+"'\n" for p in self.report['parts']),encoding='utf-8')
            target=self.folder/'native.mp4'
            self.journal.run('concat-video',[str(self.ffmpeg),'-hide_banner','-loglevel','error','-nostdin','-n',
                '-f','concat','-safe','0','-i',str(listing),'-map','0:v:0','-c:v','copy','-an','-movflags','+faststart',str(target)],timeout=300)
            info=self._decode(target,'final',self._probe(target,'verify-final-video'),seek=True)
            if info['frame_count']!=sum(p['frame_count'] for p in self.report['parts']): raise ValueError('Concat dropped/duplicated decoded frames')
            actual_pts=json.loads(local(info['pts_index']['path']).read_text())['frame_pts_seconds']
            expected_pts=[]; offset=0.
            for part in self.report['parts']:
                stamps=json.loads(local(part['pts_index']['path']).read_text())['frame_pts_seconds']
                expected_pts.extend(offset+t-stamps[0] for t in stamps)
                offset+=part['duration_seconds']
            if any(abs(a-b)>.005 for a,b in zip(actual_pts,expected_pts)):
                raise ValueError('Concatenated PTS differs from the registered segment time mapping')
            self.report.update(video=dict(**reference(target),**info),**reference(target),
                decodable_verified=True,seek_verified=True,recording_wall_seconds=self.stop_started-self.started)
            self._timeline(starts,events)
            prefix=self._prefix_verified(); self.report['started_before_reset']=prefix
            checks=dict(first_video_sample_saved_before_reset=prefix,
                natural_terminal_confirmed=bool(natural_terminal and isinstance(native_result,dict) and native_result.get('finalized') is True),
                reset_anchor_present=any(a['phase']=='before_reset' for a in self.anchors),
                post_reset_anchor_present=any(a.get('tick') is not None and a['phase']!='natural_terminal' for a in self.anchors),
                terminal_clock_anchor_present=any(a['phase']=='natural_terminal' and 'device_uptime' in a for a in self.anchors),
                all_segments_closed=bool(len(ends)==len(starts) and all(e.get('exit_code')==0 for e in ends)),
                supervisor_active_until_stop=not exited_before_stop and self.process.returncode==0,
                all_frames_decoded=True,file_seek_decoded=True,concatenation_pts_verified=True,
                measured_segment_gaps_within_budget=self.report['segment_gaps_within_budget'])
            last=self.report['segment_timeline'][-1]['last_frame_device_uptime_range']
            terminal=[a for a in self.anchors if a['phase']=='natural_terminal' and 'device_uptime' in a]
            checks['terminal_frame_boundary_covered']=bool(terminal and
                last[0] >= terminal[0]['device_uptime']+.01)
            self.report.update(completion_checks=checks,recording_complete=all(checks.values()))
            self.report['status']='complete' if self.report['recording_complete'] else 'partial'
            if self.report['recording_complete']:
                self._shell('remove-owned-remote-media',f"set -eu\nrm -rf '{self.remote}'\n")
                self.report['remote_cleanup_verified']=True
        except BaseException as exc:
            self._error(exc)
            if not isinstance(exc,Exception): interrupted=exc
        finally:
            self._release(); self.finished=True
            if self.journal: self.report['commands']=self.journal.records
            self.report['finished_utc']=datetime.now(timezone.utc).isoformat(); self._save()
        if interrupted: raise interrupted
        return deepcopy(self.report)


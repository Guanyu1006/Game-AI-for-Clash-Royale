"""Continuous Android FrameOutput, encoded on the host without a guest codec.

FrameOutput's 20-byte little-endian header has no capture timestamp. The video
uses measured host feed time, never simulation ticks or a fabricated frame rate.
The caller owns the native lease; this module never starts a VM or calls RPC.
"""
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
import queue
import re
import select
import struct
import subprocess
import threading
import time

from royale_phase2.platform_stream import _PROC_START
from royale_phase3.common import ROOT, append, digest, reference, write
from tools.cloud.native_identity import Journal
from tools.cloud.native_media import NativeMediaRecorder, find_media_tool


HEADER = struct.Struct('<5I')
TIMING_SCOPE = ('Host complete-frame arrival/feed to encoded PTS intervals; Android capture PTS, '
                'capture age and pixel presentation latency are unknown')


def frame_header(header, expected_size=None):
    """Validate before allocating a payload. Length excludes its own four bytes."""
    if len(header) != HEADER.size:
        raise ValueError('Incomplete FrameOutput header')
    length, width, height, stride, pixel_format = HEADER.unpack(header)
    if (not 2 <= width <= 1920 or not 2 <= height <= 1920 or width % 2 or height % 2
            or pixel_format != 3 or not width * 3 <= stride <= width * 3 + 4096
            or length != 16 + stride * height or length > 16 * 1024 * 1024):
        raise ValueError('Invalid bounded RGB24 FrameOutput geometry/packet length')
    if expected_size is not None and (width, height) != expected_size:
        raise ValueError('FrameOutput dimensions differ from the requested video size')
    return dict(width=width, height=height, stride_bytes=stride, pixel_format=pixel_format,
                payload_bytes=stride * height, packet_bytes=length + 4)


def read_exact(stream, count, *, allow_eof=False):
    blocks = []
    left = count
    while left:
        block = stream.read(left)
        if not block:
            if allow_eof and left == count:
                return None
            raise ValueError('Truncated FrameOutput packet')
        blocks.append(block)
        left -= len(block)
    return b''.join(blocks)


class NativeFrameRecorder(NativeMediaRecorder):
    """Same inert/start/anchor/stop API as NativeMediaRecorder.

    A bounded queue fails closed instead of dropping frames. Only the recorder's
    PID + /proc start time + exact command may receive SIGINT. All raw frame
    receipts are retained; this is continuous native video, not screenshots.
    """
    def __init__(self, output, *, adb='.cache/native-tools/bin/adb', adb_port=5038,
                 serial='127.0.0.1:15555', root_shell='su0', ffmpeg=None, ffprobe=None,
                 size='360x640', context=None, queue_frames=8, max_frames=216000,
                 terminal_wait_seconds=15, pts_feed_tolerance_seconds=.25,
                 runner=None, popen=None, clock=None, wall_clock=None):
        super().__init__(output, adb=adb, adb_port=adb_port, serial=serial,
                         root_shell=root_shell, ffmpeg=ffmpeg, ffprobe=ffprobe,
                         size=size, context=context, runner=runner, popen=popen, clock=clock)
        if type(queue_frames) is not int or not 1 <= queue_frames <= 32:
            raise ValueError('Frame queue must hold 1..32 frames')
        if type(max_frames) is not int or not 2 <= max_frames <= 216000:
            raise ValueError('Explicit frame receipt budget must be 2..216000')
        if type(terminal_wait_seconds) not in (int, float) or not 0 < terminal_wait_seconds <= 30:
            raise ValueError('Terminal frame wait must be finite and <=30 seconds')
        if (type(pts_feed_tolerance_seconds) not in (int, float)
                or not 0 <= pts_feed_tolerance_seconds <= .5):
            raise ValueError('PTS/feed tolerance must be finite and <=0.5 seconds')
        self.wall_clock = wall_clock or time.time
        self.width, self.height = map(int, size.split('x'))
        self.frames = queue.Queue(maxsize=queue_frames)
        self.max_frames = max_frames
        self.terminal_wait = terminal_wait_seconds
        self.pts_tolerance = pts_feed_tolerance_seconds
        self.reader_done = threading.Event()
        self.feed_done = threading.Event()
        self.condition = threading.Condition()
        self.received, self.fed = [], []
        self.background_errors = []
        self.max_queue_depth = 0
        self.encoder = None
        self.feed_thread = None
        self.encoder_log = None
        self.first_saved_before_reset = False
        self.report = dict(schema='royale-native-frame-video.v1', status='not_started', requested=True,
            source='actual_android_frameoutput', source_kind='original_episode',
            evidence_scope='interface_only' if runner or popen else 'actual_android_recording',
            encoding='host_ffmpeg_vp9_webm', context=self.context, serial=serial,
            adb_port=adb_port, root_shell=root_shell, remote_directory=self.remote,
            requested_size=size, queue_capacity_frames=queue_frames,
            frame_receipt_budget=max_frames, terminal_wait_seconds=terminal_wait_seconds,
            pts_feed_tolerance_seconds=pts_feed_tolerance_seconds,
            source_capture_pts_available=False, source_frame_rate=None,
            capture_age_unknown=True, terminal_pixel_presentation_verified=False,
            exact_frame_tick_binding=False, timing_scope=TIMING_SCOPE,
            native_rpc_calls=0, native_rendered_required=True,
            official_fidelity_verified=False, game_content_visually_verified=False,
            browser_seek_verified=False, decodable_verified=False, seek_verified=False,
            recording_complete=False, remote_cleanup_verified=False,
            mapping_anchors=[], anchors=0, errors=[])

    def _background_error(self, stage, exc):
        with self.condition:
            self.background_errors.append(f'{stage}: {type(exc).__name__}: {exc}')
            self.condition.notify_all()
        self.ready.set()

    def _start_script(self):
        # No text may enter stdout: shell -T disables the PTY, while ADB's
        # shell-v2 transport forwards stdin and separates stdout from stderr.
        # Older ADB exec-out clients do not reliably forward a stdin script.
        return _PROC_START + f'''
set -u
[ "$(id -u)" = 0 ] || exit 31
mkdir '{self.remote}' || exit 32
pid=0; started=0
stop_owned() {{
  : >'{self.remote}/stop'
  if [ "$pid" -gt 1 ] && [ "$(proc_start "$pid" || true)" = "$started" ]; then
    kill -2 "$pid" 2>/dev/null || true
  fi
}}
trap stop_owned HUP INT TERM EXIT
/system/bin/screenrecord --size {self.size} --output-format frames - 2>'{self.remote}/guest.log' < /dev/null &
pid=$!; started=$(proc_start "$pid") || exit 71
printf '%s %s %s\\n' "$pid" "$started" '{self.token}' >'{self.remote}/recorder.pid.tmp'
mv '{self.remote}/recorder.pid.tmp' '{self.remote}/recorder.pid'
if [ -e '{self.remote}/stop' ]; then kill -2 "$pid" 2>/dev/null || true; fi
wait "$pid"; result=$?
printf '%s\\n' "$result" >'{self.remote}/exit-code'
exit "$result"
'''

    def _stop_script(self):
        expected = f'/system/bin/screenrecord --size {self.size} --output-format frames - '
        return _PROC_START + f'''
set -eu
[ "$(id -u)" = 0 ] || exit 31
mkdir -p '{self.remote}'; : >'{self.remote}/stop'
if [ ! -r '{self.remote}/recorder.pid' ]; then
  printf '{{"released":true,"signal_sent":false,"reason":"no_owned_recorder_record"}}\\n'; exit 0
fi
read pid started token <'{self.remote}/recorder.pid'
case "$pid:$started" in *[!0-9:]*|:*|*:) exit 73;; esac
[ "$token" = '{self.token}' ] && [ "$pid" -gt 1 ] || exit 74
current=$(proc_start "$pid" || true)
if [ -z "$current" ] || [ "$current" != "$started" ]; then
  printf '{{"released":true,"signal_sent":false,"reason":"owned_process_already_exited"}}\\n'; exit 0
fi
cmd=$(tr '\\000' ' ' <"/proc/$pid/cmdline")
[ "$cmd" = '{expected}' ] || exit 75
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

    def _encoder_command(self):
        # 1000 is the input clock's millisecond time base, not generated FPS.
        # passthrough preserves all measured wallclock timestamps without fill.
        return [str(self.ffmpeg), '-nostdin', '-hide_banner', '-v', 'warning', '-n',
            '-nofind_stream_info', '-probesize', '32', '-analyzeduration', '0', '-fpsprobesize', '0',
            '-f', 'rawvideo', '-pixel_format', 'rgb24', '-video_size', self.size,
            '-framerate', '1000', '-use_wallclock_as_timestamps', '1', '-i', 'pipe:0',
            '-map', '0:v:0', '-an', '-copyts', '-start_at_zero', '-fps_mode', 'passthrough',
            '-vf', 'setpts=PTS-STARTPTS',
            '-c:v', 'libvpx-vp9', '-deadline', 'realtime', '-cpu-used', '8', '-threads', '1',
            '-row-mt', '1', '-lag-in-frames', '0', '-pix_fmt', 'yuv420p', '-g', '30', '-crf', '32', '-b:v', '0',
            str(self.folder / 'native.webm')]

    def _read_frames(self):
        try:
            while True:
                header = read_exact(self.process.stdout, HEADER.size, allow_eof=True)
                if header is None:
                    break
                try:
                    geometry = frame_header(header, (self.width, self.height))
                except ValueError:
                    diagnostic = self.folder / 'invalid-frame-header.bin'
                    diagnostic.write_bytes(header)
                    self.report['invalid_frame_header'] = dict(**reference(diagnostic),
                        bytes_hex=header.hex(), expected_header_bytes=HEADER.size,
                        received_frame_index=len(self.received))
                    raise
                if len(self.received) >= self.max_frames:
                    raise ValueError('Native frame receipt budget exceeded')
                payload = read_exact(self.process.stdout, geometry['payload_bytes'])
                stamp, utc = self.clock(), self.wall_clock()
                row = dict(index=len(self.received), received_monotonic=stamp,
                           received_unix_seconds=utc, **geometry,
                           source_capture_pts=None, capture_age_seconds=None)
                if geometry['stride_bytes'] != self.width * 3:
                    payload = b''.join(payload[i:i+self.width*3]
                        for i in range(0, len(payload), geometry['stride_bytes']))
                row['rgb24_sha256'] = hashlib.sha256(payload).hexdigest()
                if not self.received:
                    first = self.folder / 'before-reset.rgb'
                    first.write_bytes(payload)
                    self.report['first_source_frame'] = dict(**reference(first), **geometry)
                append(self.folder / 'frames-received.jsonl', row)
                with self.condition:
                    self.received.append(row)
                    self.condition.notify_all()
                try:
                    self.frames.put((row, payload), timeout=.5)
                except queue.Full:
                    raise RuntimeError('Bounded native frame queue overflow; no frames were silently dropped') from None
                self.max_queue_depth = max(self.max_queue_depth, self.frames.qsize())
        except Exception as exc:
            self._background_error('native-reader', exc)
        finally:
            self.reader_done.set()
            with self.condition:
                self.condition.notify_all()

    def _write_frame(self, payload):
        """Bound blocked writes, including a wedged or failed host encoder."""
        descriptor = self.encoder.stdin.fileno()
        view = memoryview(payload)
        deadline = time.monotonic() + 15
        while view:
            if self.encoder.poll() is not None:
                raise RuntimeError('Host video encoder exited while receiving native frames')
            if time.monotonic() >= deadline:
                raise TimeoutError('Host encoder frame feed exceeded 15 seconds')
            _, writable, _ = select.select([], [descriptor], [], .5)
            if not writable:
                continue
            try:
                count = os.write(descriptor, view[:65536])
            except (BlockingIOError, InterruptedError):
                continue
            if count <= 0:
                raise RuntimeError('Host encoder pipe did not accept frame bytes')
            view = view[count:]

    def _feed_frames(self):
        try:
            os.set_blocking(self.encoder.stdin.fileno(), False)
            while not (self.reader_done.is_set() and self.frames.empty()):
                try:
                    source, payload = self.frames.get(timeout=.2)
                except queue.Empty:
                    continue
                before, utc_before = self.clock(), self.wall_clock()
                self._write_frame(payload)
                after, utc_after = self.clock(), self.wall_clock()
                row = dict(index=source['index'], received_monotonic=source['received_monotonic'],
                    feed_monotonic_interval=[before, after], feed_unix_seconds_interval=[utc_before, utc_after],
                    rgb24_sha256=source['rgb24_sha256'])
                append(self.folder / 'frames-fed.jsonl', row)
                with self.condition:
                    self.fed.append(row)
                    self.condition.notify_all()
                self.frames.task_done()
                if len(self.fed) == 1:
                    self.ready.set()
        except Exception as exc:
            self._background_error('host-encoder-feed', exc)
        finally:
            if self.encoder and self.encoder.stdin:
                self.encoder.stdin.close()
            self.feed_done.set()

    def start(self):
        self.folder.mkdir(parents=True, exist_ok=False)
        self.report['status'] = 'preparing'
        self._save()
        try:
            if not self.adb.is_file():
                raise FileNotFoundError('Existing repository-local ADB is required')
            self.ffmpeg = find_media_tool('ffmpeg', self.ffmpeg_arg)
            self.ffprobe = find_media_tool('ffprobe', self.ffprobe_arg)
            # Hash large host binaries before opening the continuous stream.
            self.report['tools'] = {key: reference(value) for key, value in
                dict(adb=self.adb, ffmpeg=self.ffmpeg, ffprobe=self.ffprobe).items()}
            self.journal = Journal(self.folder, str(self.adb), self.serial, self.port, self.runner)
            for name, executable in (('ffmpeg', self.ffmpeg), ('ffprobe', self.ffprobe)):
                self.journal.run(name+'-version', [str(executable), '-version'])
            client = self.journal.run('adb-version', [str(self.adb), 'version'])
            protocol = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', client)
            if not protocol:
                raise ValueError('Cannot verify ADB client version')
            self.journal.client_version = int(protocol[1])
            if self.journal.device('get-state', ['get-state']).strip() != 'device':
                raise RuntimeError('Selected Android is not ready')
            self._shell('capabilities', 'set -eu\n[ "$(id -u)" = 0 ] || exit 31\n'
                '[ "$(getprop sys.boot_completed)" = 1 ] || exit 32\n'
                'test -x /system/bin/screenrecord\n/system/bin/screenrecord --help\n')
            command = self._encoder_command()
            self.report.update(encoder_command=command, input_framerate_parameter_scope=
                '1000 establishes a millisecond time base only; measured wallclock timestamps and passthrough preserve cadence')
            self.encoder_log = (self.folder / 'encoder.stderr.log').open('wb')
            self.encoder = self.popen(command, cwd=ROOT, stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL, stderr=self.encoder_log, bufsize=0, start_new_session=True)
            script = self._start_script()
            (self.folder / 'start.stdin.sh').write_text(script, encoding='utf-8')
            argv = [str(self.adb), '-H', '127.0.0.1', '-P', str(self.port), '-s', self.serial,
                    'shell', '-T', *(['su', '0'] if self.root_shell == 'su0' else []), 'sh', '-s']
            self.report['capture_transport'] = 'adb_shell_no_pty_stdin_script'
            self.report['supervisor_command'] = argv
            self.log = (self.folder / 'supervisor.stderr.log').open('wb')
            self.started = self.clock()
            self.report['started_monotonic'] = self.started
            self.process = self.popen(argv, cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                      stderr=self.log, bufsize=0, start_new_session=True)
            self.thread = threading.Thread(target=self._read_frames, name='native-frame-reader', daemon=True)
            self.feed_thread = threading.Thread(target=self._feed_frames, name='native-frame-encoder', daemon=True)
            self.thread.start()
            self.feed_thread.start()
            self.process.stdin.write(script.encode())
            self.process.stdin.close()
            if (not self.ready.wait(35) or self.background_errors or not self.fed
                    or self.process.poll() is not None or self.encoder.poll() is not None):
                raise RuntimeError('Continuous native frame and active host encoder were not ready before reset')
            self.first_saved_before_reset = True
            self.report.update(status='recording', started_before_reset=True,
                readiness_scope='First complete native RGB frame saved and fed to the active host encoder before reset',
                first_complete_frame_received_monotonic=self.received[0]['received_monotonic'],
                first_complete_frame_fed_monotonic_interval=self.fed[0]['feed_monotonic_interval'],
                readiness_tick=None)
            self.anchor('before_reset')
            self._save()
            return True
        except BaseException as exc:
            self._error(exc)
            self._release()
            if self.journal:
                self.report['commands'] = self.journal.records
            self._save()
            if not isinstance(exc, Exception):
                raise
            return False

    def anchor(self, phase, *, state=None, sequence=None, transition=None):
        if self.started is None or self.finished:
            return
        stamp = self.clock()
        row = dict(schema='royale-native-frame-tick-anchor.v1', phase=str(phase),
            host_monotonic=stamp, host_unix_seconds=self.wall_clock(),
            host_utc=datetime.now(timezone.utc).isoformat(), recording_elapsed_seconds=stamp-self.started,
            tick=state.get('tick') if state else None, generation=state.get('generation') if state else None,
            state_sha256=digest(state) if state else None, sequence_index=sequence,
            source_capture_pts=None, exact_frame_tick_binding=False, timing_scope=TIMING_SCOPE)
        if transition:
            row.update(episode_id=transition.get('episode_id'), planned_tick=transition.get('planned_tick'),
                actual_execution_tick=transition.get('actual_execution_tick'), card_consumed=transition.get('card_consumed'),
                request_ids=[r['registration']['request_id'] for r in transition.get('execution', {}).get('registrations', [])])
        self.anchors.append(row)
        append(self.folder / 'tick-anchors.jsonl', row)
        self.report['anchors'] = len(self.anchors)

    def _release(self):
        if self._release_attempted:
            return
        self._release_attempted = True
        interrupted = None
        try:
            if self.process:
                receipt = json.loads(self._shell('stop-owned-recorder', self._stop_script(), timeout=30))
                self.report['stop_receipt'] = receipt
                if receipt.get('released') is not True:
                    raise RuntimeError('Owned native frame recorder release was not verified')
                self.process.wait(timeout=15)
        except BaseException as exc:
            self.report['guest_release_unverified'] = True
            self.report['errors'].append(f'Guest cleanup: {type(exc).__name__}: {exc}')
            if not isinstance(exc, Exception):
                interrupted = exc
        finally:
            if self.process and self.process.poll() is None:
                self.process.terminate()
                try:
                    self.process.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    self.process.kill()
                    self.process.wait(timeout=5)
            if self.thread:
                self.thread.join(timeout=10)
                if self.thread.is_alive():
                    self.report['reader_release_unverified'] = True
                elif self.process and self.process.stdout:
                    self.process.stdout.close()
            else:
                self.reader_done.set()
            if self.feed_thread:
                self.feed_thread.join(timeout=20)
                if self.feed_thread.is_alive():
                    self.report['encoder_feed_release_unverified'] = True
            if self.encoder:
                if not self.feed_thread and self.encoder.stdin:
                    self.encoder.stdin.close()
                try:
                    self.encoder.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self.report['encoder_release_unverified'] = True
                    self.encoder.terminate()
                    try:
                        self.encoder.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        self.encoder.kill()
                        self.encoder.wait(timeout=5)
            for log in (self.log, self.encoder_log):
                if log:
                    log.close()
        if interrupted:
            raise interrupted

    def _validate_pts(self, info):
        points = json.loads((ROOT / info['pts_index']['path']).read_text())['frame_pts_seconds']
        if len(points) != len(self.fed) or len(self.received) != len(self.fed):
            raise ValueError('Encoded frames differ from complete native frames/feeds; no silent drop or duplication is allowed')
        if len(points) < 2:
            raise ValueError('At least two native frames are required for measured video timing')
        # copyts/start_at_zero alone does not normalize a raw wallclock input
        # when stream-info probing is disabled. Check the *file's* clock as
        # well as inter-frame differences: an epoch-sized duration is unusable.
        if (abs(points[0]) > .002 or abs(info['first_pts_seconds']) > .002
                or abs(info['first_frame_pts_seconds']) > .002):
            raise ValueError('Native video presentation timeline does not begin at zero')
        span = points[-1]-points[0]
        duration = info['duration_seconds']
        packet_tail = info.get('last_packet_duration_seconds')
        # WebM may omit BlockDuration. In that case only a millisecond-scale
        # container tail is accepted; it is not presented as source exposure.
        if packet_tail is not None and not 0 <= packet_tail <= .010:
            raise ValueError('Native video encoded packet tail exceeds its millisecond time base')
        expected_end = span + (packet_tail if packet_tail is not None else 0.)
        if not span-.002 <= duration <= expected_end+.010:
            raise ValueError('Native video container duration differs from its measured frame span/tail')
        self.report['presentation_timeline'] = dict(first_frame_pts_seconds=points[0],
            frame_span_seconds=span, container_duration_seconds=duration,
            observed_container_tail_seconds=duration-span, last_packet_duration_seconds=packet_tail,
            packet_tail_duration_available=packet_tail is not None,
            tail_scope='Encoded packet/container tail only; source frame exposure duration remains unknown',
            zero_based_verified=True, container_duration_verified=True,
            normalization='Subtract the first measured PTS only; preserve every measured frame interval')
        first = self.fed[0]['feed_unix_seconds_interval']
        first_mono = self.fed[0]['feed_monotonic_interval'][0]
        relative = [value-points[0] for value in points]
        comparisons = []
        for row, point in zip(self.fed, relative):
            low, high = row['feed_unix_seconds_interval']
            expected = [low-first[1]-self.pts_tolerance, high-first[0]+self.pts_tolerance]
            mono = row['feed_monotonic_interval']
            clock_error = abs((low-first[0])-(mono[0]-first_mono))
            if high < low or mono[1] < mono[0] or clock_error > .05:
                raise ValueError('Host wall clock regressed or stepped relative to monotonic feed clock')
            if not expected[0] <= point <= expected[1]:
                raise ValueError('Encoded PTS differs from measured host frame feed interval')
            comparisons.append(dict(index=row['index'], video_seconds=point,
                expected_host_feed_relative_seconds_range=expected, host_clock_error_seconds=clock_error))
        path = self.folder / 'pts-feed-checks.json'
        write(path, comparisons)
        self.report.update(pts_feed_checks=reference(path), host_feed_pts_verified=True,
            all_source_frames_encoded_once=True, video_clock='host frame feed wallclock',
            received_frames=len(self.received), encoded_frames=len(self.fed),
            max_queue_depth_frames=self.max_queue_depth)
        mappings = []
        # Bracket the host observation with nearest complete source arrivals.
        # These are navigation intervals only, not pixel/tick correspondence.
        for anchor in self.anchors:
            if type(anchor.get('tick')) is not int:
                continue
            before = [i for i, row in enumerate(self.received) if row['received_monotonic'] <= anchor['host_monotonic']]
            after = [i for i, row in enumerate(self.received) if row['received_monotonic'] >= anchor['host_monotonic']]
            if before and after:
                mappings.append(dict(tick=anchor['tick'], generation=anchor.get('generation'), phase=anchor['phase'],
                    video_seconds_range=[relative[before[-1]], relative[after[0]]],
                    host_arrival_frame_indices=[before[-1], after[0]],
                    capture_age_unknown=True, exact_frame_tick_binding=False, scope=TIMING_SCOPE))
        self.report['mapping_anchors'] = mappings

    def _source_statistics(self):
        hashes = [row['rgb24_sha256'] for row in self.received]
        span = (self.received[-1]['received_monotonic']-self.received[0]['received_monotonic']
                if len(self.received) > 1 else 0.)
        changes = sum(a != b for a, b in zip(hashes, hashes[1:]))
        feed_lag = [row['feed_monotonic_interval'][0]-row['received_monotonic'] for row in self.fed]
        return dict(unique_source_rgb24_hashes=len(set(hashes)), consecutive_source_pixel_changes=changes,
            host_complete_frame_arrival_span_seconds=span,
            host_observed_arrival_rate=(len(hashes)-1)/span if span > 0 else None,
            host_observed_pixel_change_rate=changes/span if span > 0 else None,
            host_arrival_to_feed_delay_seconds_max=max(feed_lag) if feed_lag else None,
            rate_scope='Complete frames/pixel changes per host arrival second; not source capture FPS or freshness evidence',
            source_capture_fps_verified=False)

    def stop(self, *, natural_terminal=False, native_result=None, context=None):
        if self.finished:
            return deepcopy(self.report)
        self.stop_started = self.clock()
        self.report.update(natural_terminal=bool(natural_terminal), native_result=deepcopy(native_result),
                           context={**self.context, **deepcopy(context or {})})
        interrupted = None
        exited_before_stop = bool(self.process and self.process.poll() is not None)
        try:
            if not self.process:
                raise RuntimeError('Native frame recording did not start')
            terminal_after_frame = False
            if natural_terminal and isinstance(native_result, dict) and native_result.get('finalized') is True:
                self.anchor('natural_terminal', state=native_result)
                stamp = self.anchors[-1]['host_monotonic']
                with self.condition:
                    self.condition.wait_for(lambda: self.background_errors or self.reader_done.is_set()
                        or bool(self.received and self.received[-1]['received_monotonic'] > stamp), timeout=self.terminal_wait)
                    terminal_after_frame = bool(self.received and self.received[-1]['received_monotonic'] > stamp)
            exited_before_stop = exited_before_stop or self.process.poll() is not None
            self._release()
            if self.background_errors:
                raise RuntimeError('; '.join(self.background_errors))
            if any(self.report.get(key) for key in ('guest_release_unverified', 'reader_release_unverified',
                    'encoder_feed_release_unverified', 'encoder_release_unverified')):
                raise RuntimeError('Native reader/encoder cleanup did not complete cleanly')
            if self.encoder is None or self.encoder.returncode != 0:
                raise RuntimeError('Host native video encoder failed')
            target = self.folder / 'native.webm'
            if not 0 < target.stat().st_size <= 512 * 1024 * 1024:
                raise ValueError('Native WebM outside the 512 MiB byte budget')
            info = self._decode(target, 'final', self._probe(target, 'verify-final-video'), seek=True)
            self.report.update(video=dict(**reference(target), **info), **reference(target),
                decodable_verified=True, seek_verified=True, recording_wall_seconds=self.clock()-self.started,
                native_frame_receipts=reference(self.folder / 'frames-received.jsonl'),
                encoder_feed_receipts=reference(self.folder / 'frames-fed.jsonl'))
            # A valid video remains reviewable even when its clock/count
            # acceptance fails; _error still forbids recording_complete.
            self._validate_pts(info)
            checks = dict(first_complete_source_frame_saved_before_reset=self.first_saved_before_reset,
                natural_terminal_confirmed=bool(natural_terminal and isinstance(native_result, dict)
                                                and native_result.get('finalized') is True),
                before_reset_anchor_present=any(a['phase']=='before_reset' for a in self.anchors),
                post_reset_anchor_present=any(type(a.get('tick')) is int and a['phase']!='natural_terminal' for a in self.anchors),
                post_terminal_complete_frame_received=terminal_after_frame,
                recorder_active_until_stop=not exited_before_stop,
                guest_supervisor_exit_clean=self.process.returncode == 0,
                owned_guest_process_released=self.report.get('stop_receipt', {}).get('released') is True,
                all_source_frames_encoded_once=True, host_feed_pts_verified=True,
                all_frames_decoded=True, file_seek_decoded=True)
            self.report.update(completion_checks=checks, recording_complete=all(checks.values()))
            self.report['status'] = 'complete' if self.report['recording_complete'] else 'partial'
            self.report['completion_scope'] = ('Continuous original native stream through a host-received post-terminal frame; '
                'source capture age and terminal pixel presentation remain unverified')
        except BaseException as exc:
            self._error(exc)
            if not isinstance(exc, Exception):
                interrupted = exc
        finally:
            try:
                self._release()
            finally:
                self.finished = True
                if self.journal:
                    try:
                        guest_log = self.folder / 'guest.stderr.log'
                        self.journal.device('pull-guest-log', ['pull', self.remote+'/guest.log', str(guest_log)], timeout=30)
                        self.report['guest_log'] = reference(guest_log)
                    except Exception as exc:
                        self.report['guest_log_error'] = f'{type(exc).__name__}: {exc}'
                    if self.report.get('recording_complete'):
                        try:
                            self._shell('remove-owned-remote-media', f"set -eu\nrm -rf '{self.remote}'\n")
                            self.report['remote_cleanup_verified'] = True
                        except Exception as exc:
                            self.report['remote_cleanup_error'] = f'{type(exc).__name__}: {exc}'
                    self.report['commands'] = self.journal.records
                self.report.update(finished_utc=datetime.now(timezone.utc).isoformat(),
                    background_errors=deepcopy(self.background_errors), received_frames=len(self.received),
                    fed_frames=len(self.fed), max_queue_depth_frames=self.max_queue_depth)
                self.report['source_statistics'] = self._source_statistics()
                self._save()
        if interrupted:
            raise interrupted
        return deepcopy(self.report)

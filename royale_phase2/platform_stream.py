"""Bounded native-screen stream for the existing research console.

Construction is inert. Only explicit ``start`` launches one persistent SSH
source and one local FFmpeg decoder. This module never opens a browser, binds
an HTTP listener, or accesses battle RPC. A caller-owned HTTP handler can write
``iter_mjpeg`` chunks; slow readers receive the latest cached frame, not a queue.

JPEG arrival timestamps are wall-clock telemetry. They are not synchronized to
a native battle tick and must not replace separately captured audit PNGs.
"""
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shlex
import subprocess
import threading
import time

from .runtime import ROOT, local


DEFAULT_FFMPEG = '.cache/phase2/viewer/encoder/ffmpeg-win-x86_64-v7.1.exe'
KNOWN_HOSTS = '.cache/phase2/secondary_runtime/known_hosts'

_PROC_START = '''proc_start() {
  proc_line=$(cat "/proc/$1/stat" 2>/dev/null) || return 1
  proc_tail=${proc_line##*) }
  set -- $proc_tail
  [ "$#" -ge 20 ] || return 1
  shift 19
  printf '%s' "$1"
};
'''


def _adb_script(script, trailing=''):
    # The pinned guest adb preserves the sh -c script argv. The actual saved
    # printf probe proves one guest-shell quote works; double quoting makes
    # Android treat the entire script as an executable name (even exit 0).
    return 'adb -s 127.0.0.1:5555 exec-out sh -c ' + shlex.quote(script) + trailing


def _android_ownership_scripts(token, source_size, bitrate, segment_seconds):
    pidfile = '/data/local/tmp/royale-stream-' + token + '.pid'
    stopfile = '/data/local/tmp/royale-stream-' + token + '.stop'
    executable = '/system/bin/screenrecord'
    recorder = (f'{executable} --output-format=h264 --size {source_size} '
                f'--bit-rate {bitrate} --time-limit {segment_seconds} -')
    start_script = _PROC_START + f'''
token='{token}'; pidfile='{pidfile}'; stopfile='{stopfile}'; segment="${{1:-0}}"
[ ! -e "$stopfile" ] || exit 75
started=$(proc_start "$$") || exit 76
case "$started:$segment" in *[!0-9:]*|:*|*:) exit 76;; esac
printf '%s %s %s %s\\n' "$$" "$started" "$token" "$segment" > "$pidfile.tmp" || exit 76
mv "$pidfile.tmp" "$pidfile" || exit 76
[ ! -e "$stopfile" ] || exit 75
exec {recorder}
'''
    # The record is written by the Android process before exec; PID and Linux
    # starttime survive exec. A stop marker prevents a later rotation from
    # starting after cleanup has already inspected the previous segment.
    cleanup_script = _PROC_START + f'''
token='{token}'; pidfile='{pidfile}'; stopfile='{stopfile}'
pid=0; started=0; owner=false; signaled=false
emit() {{
  printf '{{"schema":"royale-native-stream-cleanup.v1","token":"%s","status":"%s","pid":%s,"starttime":%s,"ownership_verified":%s,"signal_sent":%s,"released":%s}}\\n' "$token" "$1" "$pid" "$started" "$owner" "$signaled" "$2"
}}
: > "$stopfile" || {{ emit stop_marker_failed false; exit 2; }}
if [ ! -r "$pidfile" ]; then emit no_owned_pid_record false; exit 2; fi
read pid started recorded_token segment < "$pidfile" || {{ pid=0; started=0; emit invalid_pid_record false; exit 2; }}
case "$pid:$started" in *[!0-9:]*|:*|*:) pid=0; started=0; emit invalid_pid_record false; exit 2;; esac
if [ "$recorded_token" != "$token" ] || [ "$pid" -le 1 ]; then emit ownership_token_mismatch false; exit 2; fi
expected='{recorder} '
i=0
while [ "$i" -lt 15 ]; do
  if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit already_exited true; exit 0; fi
  current=$(proc_start "$pid") || {{
    if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit already_exited true; exit 0; fi
    emit proc_identity_unreadable false; exit 2;
  }}
  if [ "$current" != "$started" ]; then rm -f "$pidfile"; emit recorded_process_already_exited true; exit 0; fi
  cmd=$(tr '\\000' ' ' < "/proc/$pid/cmdline") || {{
    if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit already_exited true; exit 0; fi
    emit cmdline_unreadable false; exit 2;
  }}
  if [ "$cmd" = "$expected" ]; then owner=true; break; fi
  i=$((i + 1)); sleep 0.1
done
if [ "$owner" != true ]; then emit exact_screenrecord_cmdline_mismatch false; exit 2; fi
# Recheck both ownership properties immediately before signaling this PID.
current=$(proc_start "$pid") || {{
  if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit already_exited true; exit 0; fi
  emit proc_identity_unreadable false; exit 2;
}}
if [ "$current" != "$started" ]; then rm -f "$pidfile"; emit recorded_process_already_exited true; exit 0; fi
cmd=$(tr '\\000' ' ' < "/proc/$pid/cmdline") || {{
  if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit already_exited true; exit 0; fi
  emit cmdline_unreadable false; exit 2;
}}
if [ "$current" != "$started" ] || [ "$cmd" != "$expected" ]; then emit ownership_changed_before_signal false; exit 2; fi
kill -2 "$pid" || {{ emit sigint_failed false; exit 2; }}
signaled=true; i=0
while [ "$i" -lt 40 ]; do
  if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit released_after_sigint true; exit 0; fi
  current=$(proc_start "$pid") || {{
    if [ ! -d "/proc/$pid" ]; then rm -f "$pidfile"; emit released_after_sigint true; exit 0; fi
    emit proc_identity_unreadable_after_sigint false; exit 2;
  }}
  if [ "$current" != "$started" ]; then rm -f "$pidfile"; emit released_after_sigint true; exit 0; fi
  i=$((i + 1)); sleep 0.1
done
emit timeout_owned_pid_alive false
exit 2
'''
    return start_script, cleanup_script, pidfile, stopfile


def _integer(value, name, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'{name} must be {low}..{high}')
    return value


def build_stream_commands(ssh_argv, *, ffmpeg=DEFAULT_FFMPEG, fps=12, width=540,
                          segment_seconds=60, bitrate=1500000, stream_token=None):
    """Build argv only; the Android H264-stdout option requires live validation.

    The existing MP4 screenrecord help does not document --output-format=h264.
    An unsupported native option fails into stderr/EOF instead of silently
    falling back to one new SSH screenshot per frame.
    """
    _integer(fps, 'fps', 1, 30); _integer(width, 'width', 64, 1080)
    _integer(segment_seconds, 'segment_seconds', 5, 180)
    _integer(bitrate, 'bitrate', 100000, 10000000)
    if width % 2: raise ValueError('JPEG scale width must be even')
    source_height = 2*((width*16+9)//18)
    source_size = f'{width}x{source_height}'
    token = stream_token if stream_token is not None else secrets.token_hex(12)
    if not isinstance(token, str) or not re.fullmatch(r'[0-9a-f]{24}', token):
        raise ValueError('Stream ownership token must be 24 lowercase hexadecimal characters')
    if (not isinstance(ssh_argv, (list, tuple)) or len(ssh_argv) < 2
            or any(not isinstance(value, str) or '\0' in value for value in ssh_argv)
            or ssh_argv[-1] != 'researcher@127.0.0.1'):
        raise ValueError('Expected project loopback guest SSH argv ending in researcher@127.0.0.1')
    pinned = local(KNOWN_HOSTS)
    if not pinned.is_file(): raise ValueError('Existing project SSH known_hosts pin is required')
    executable = local(ffmpeg)
    if not executable.is_file(): raise ValueError('Project-local FFmpeg executable not found')
    # OpenSSH uses the first occurrence: remove inherited weak values before
    # inserting hard requirements, including combined -oKey=value syntax.
    override = ('stricthostkeychecking', 'userknownhostsfile', 'globalknownhostsfile', 'hostkeyalgorithms',
                'updatehostkeys', 'compression', 'serveraliveinterval', 'serveralivecountmax',
                'controlmaster', 'controlpath', 'controlpersist')
    prefix = [ssh_argv[0]]; index = 1
    def option_name(value):
        return re.split(r'[=\s]+', value.strip(), maxsplit=1)[0].lower()
    while index < len(ssh_argv)-1:
        value = ssh_argv[index]
        if value == '-o' and index+1 < len(ssh_argv)-1:
            option = ssh_argv[index+1]
            if option_name(option) not in override:
                prefix.extend((value, option))
            index += 2; continue
        if value.startswith('-o') and option_name(value[2:]) in override:
            index += 1; continue
        prefix.append(value); index += 1
    prefix.extend(['-T', '-n', '-o', 'StrictHostKeyChecking=yes',
                   '-o', 'HostKeyAlgorithms=ssh-ed25519', '-o', 'UpdateHostKeys=no',
                   '-o', 'UserKnownHostsFile=' + KNOWN_HOSTS,
                   '-o', 'GlobalKnownHostsFile=none', '-o', 'ControlMaster=no',
                   '-o', 'ControlPath=none', '-o', 'ControlPersist=no',
                   '-o', 'Compression=no', '-o', 'ServerAliveInterval=10',
                   '-o', 'ServerAliveCountMax=2', ssh_argv[-1]])
    android_start, android_cleanup, pidfile, stopfile = _android_ownership_scripts(token, source_size, bitrate, segment_seconds)
    stop_text = 'ROYALE_STREAM_STOPPED_' + token
    stop_probe = _adb_script(f"if [ -e '{stopfile}' ]; then printf '%s' '{stop_text}'; fi")
    # One SSH connection remains alive while the bounded recorder rotates.
    # Host stop/reader-finally separately verifies the exact Android PID; merely
    # terminating this supervisor's adb child is not proof of codec release.
    script = (
        'child=""; segment=0; '
        'cleanup() { if [ -n "$child" ]; then kill "$child" 2>/dev/null || :; '
        'wait "$child" 2>/dev/null || :; fi; }; '
        'trap cleanup EXIT; trap "exit 0" HUP INT TERM; '
        'while :; do segment=$((segment + 1)); segment_started=$(date +%s); '
        + _adb_script(android_start, ' royale-stream-' + token + ' "$segment"') + ' & child=$!; '
        'printf "ROYALE_STREAM_SEGMENT_START seq=%s unix=%s pid=%s pid_role=guest_adb\\n" '
        '"$segment" "$(date +%s)" "$child" >&2; '
        'wait "$child"; result=$?; segment_ended=$(date +%s); elapsed=$((segment_ended - segment_started)); '
        'printf "ROYALE_STREAM_SEGMENT_END seq=%s unix=%s pid=%s exit=%s pid_role=guest_adb\\n" '
        '"$segment" "$(date +%s)" "$child" "$result" >&2; child=""; '
        'stop_state=$(' + stop_probe + '); '
        'if [ "$stop_state" = "' + stop_text + '" ]; then exit 0; fi; '
        'if [ "$result" -ne 0 ]; then exit "$result"; fi; '
        'if [ "$elapsed" -lt 5 ]; then '
        'printf "ROYALE_STREAM_SEGMENT_EARLY_END seq=%s elapsed=%s exit=%s action=abort\\n" '
        '"$segment" "$elapsed" "$result" >&2; exit 77; fi; done')
    decoder = [str(executable), '-hide_banner', '-loglevel', 'warning', '-nostdin',
               '-fflags', '+genpts', '-flags', '+low_delay', '-probesize', '32',
               '-analyzeduration', '0', '-fpsprobesize', '0', '-f', 'h264', '-i', 'pipe:0',
               '-an', '-sn', '-dn', '-vf', f'scale={width}:-2:flags=fast_bilinear',
               '-threads', '2', '-c:v', 'mjpeg', '-pix_fmt', 'yuvj420p', '-q:v', '6', '-f', 'image2pipe',
               '-flush_packets', '1', 'pipe:1']
    return {'source': prefix + [script], 'decoder': decoder,
            'remote_cleanup': prefix + [_adb_script(android_cleanup)],
            'android_start_script': android_start, 'android_cleanup_script': android_cleanup,
            'stream_token': token, 'android_pidfile': pidfile, 'android_stopfile': stopfile,
            'fps_requested': fps, 'width_requested': width, 'segment_seconds': segment_seconds,
            'publication_rate_limit': 'monotonic wall clock; decoder preserves input frames; one latest slot',
            'source_size': source_size, 'source_width': width, 'source_height': source_height,
            'abort_segment_shorter_than_seconds': 5,
            'source_connections': 1, 'cache_capacity_frames': 1,
            'remote_h264_stdout_verified_by_command_build': False,
            'native_tick_binding': 'none; asynchronous wall-clock display stream'}


class JPEGParser:
    """Incremental FFmpeg MJPEG parser, bounded even on invalid source data."""
    def __init__(self, max_frame_bytes=4*1024*1024):
        self.limit = _integer(max_frame_bytes, 'max_frame_bytes', 1024, 16*1024*1024)
        self.buffer = bytearray(); self.discarded_bytes = 0

    def feed(self, data):
        if not isinstance(data, (bytes, bytearray)): raise ValueError('JPEG parser needs bytes')
        self.buffer.extend(data); frames = []
        while True:
            start = self.buffer.find(b'\xff\xd8')
            if start < 0:
                keep = 1 if self.buffer.endswith(b'\xff') else 0
                removed = len(self.buffer)-keep
                self.discarded_bytes += removed
                if removed: del self.buffer[:removed]
                break
            if start:
                self.discarded_bytes += start; del self.buffer[:start]
            end = self.buffer.find(b'\xff\xd9', 2)
            if end < 0:
                if len(self.buffer) > self.limit: raise ValueError('JPEG exceeds bounded frame size')
                break
            end += 2
            if end > self.limit: raise ValueError('JPEG exceeds bounded frame size')
            frames.append(bytes(self.buffer[:end])); del self.buffer[:end]
        return frames


class LatestJPEG:
    """One latest slot, with optional wall-clock publication limits.

    Every decoded input replaces the slot immediately. The decoder reader never
    waits for a publication deadline. Readers promote the newest pending frame
    when due, including the final frame if the source sends no further inputs.
    No queue or duplicate filler frames are created.
    """
    def __init__(self, max_fps=None, clock=None):
        if max_fps is not None and (isinstance(max_fps, bool) or not isinstance(max_fps, (int, float))
                                   or not math.isfinite(max_fps) or not 0 < max_fps <= 30):
            raise ValueError('max_fps must be >0..30 or None')
        if clock is not None and not callable(clock): raise ValueError('clock must be callable')
        self.clock = clock if clock is not None else lambda: time.monotonic()
        self.max_fps = max_fps; self.interval = 1/max_fps if max_fps is not None else 0
        self.condition = threading.Condition(); self.frame = None; self.closed = False; self.error = None
        self.counts_received = 0; self.bytes_received = 0; self.unique_frame_count = 0
        self.first_received = None; self.last_received = None
        self.decoded_frames = 0; self.decoded_bytes = 0; self.suppressed_by_rate_limit = 0
        self.first_decoded = None; self.last_decoded = None; self.pending = False
        self.last_published_sha256 = None

    def _publish_due_locked(self, now):
        if not self.pending: return False
        if self.last_received is not None and now < self.last_received+self.interval: return False
        self.counts_received += 1; self.bytes_received += self.frame['bytes']
        if self.last_published_sha256 != self.frame['sha256']: self.unique_frame_count += 1
        self.last_published_sha256 = self.frame['sha256']
        self.first_received = now if self.first_received is None else self.first_received
        self.last_received = now; self.pending = False
        self.frame.update(sequence=self.counts_received, publication_pending=False, published_monotonic=now)
        self.condition.notify_all()
        return True

    def publish(self, data):
        if not isinstance(data, bytes) or not data.startswith(b'\xff\xd8') or not data.endswith(b'\xff\xd9'):
            raise ValueError('Complete JPEG frame required')
        now = self.clock(); fingerprint = hashlib.sha256(data).hexdigest()
        with self.condition:
            if self.closed: return False
            self.decoded_frames += 1; self.decoded_bytes += len(data)
            self.first_decoded = now if self.first_decoded is None else self.first_decoded
            self.last_decoded = now; self.pending = True
            self.frame = {'sequence': self.counts_received, 'jpeg': data, 'bytes': len(data), 'sha256': fingerprint,
                          'received_monotonic': now, 'received_utc': datetime.now(timezone.utc).isoformat(),
                          'publication_pending': True, 'published_monotonic': None,
                          'native_tick': None, 'state_semantic_sha256': None}
            if not self._publish_due_locked(now): self.suppressed_by_rate_limit += 1
            self.condition.notify_all()
        return True

    def latest(self):
        with self.condition:
            if self.pending: self._publish_due_locked(self.clock())
            return dict(self.frame) if self.frame else None

    def wait_next(self, after_sequence=0, timeout=1):
        _integer(after_sequence, 'after_sequence', 0, 2**63-1)
        if not isinstance(timeout, (int, float)) or not 0 <= timeout <= 60: raise ValueError('timeout must be 0..60 seconds')
        # Real monotonic bounds the caller's timeout even with a manually
        # controlled rate clock in deterministic tests.
        timeout_deadline = time.monotonic()+timeout
        with self.condition:
            while True:
                now = self.clock() if self.pending else None
                if self.pending: self._publish_due_locked(now)
                if self.frame and not self.pending and self.frame['sequence'] > after_sequence:
                    return dict(self.frame)
                if self.closed and not self.pending: return None
                remaining = timeout_deadline-time.monotonic()
                if remaining <= 0: return None
                if self.pending and self.last_received is not None:
                    remaining = min(remaining, max(.000001, self.last_received+self.interval-now))
                self.condition.wait(remaining)

    def finish(self, error=None):
        with self.condition:
            self.closed = True
            if error and not self.error: self.error = str(error)
            self.condition.notify_all()

    def status(self):
        with self.condition:
            now = self.clock()
            if self.pending: self._publish_due_locked(now)
            elapsed = (self.last_received-self.first_received) if self.last_received is not None else 0
            age = max(0, now-self.last_received) if self.last_received is not None else None
            decoded_elapsed = (self.last_decoded-self.first_decoded) if self.last_decoded is not None else 0
            return {'counts_received': self.counts_received, 'bytes_received': self.bytes_received,
                    'counts_received_definition': 'published frames, limited by monotonic wall clock',
                    'last_frame_age': age, 'last_frame_age_seconds': age,
                    'last_frame_age_ms': age*1000 if age is not None else None,
                    'effective_fps': (self.counts_received-1)/elapsed if elapsed > 0 else 0,
                    'unique_frame_count': self.unique_frame_count,
                    'unique_frame_count_definition': 'consecutive JPEG content changes, not an unbounded global distinct set',
                    'decoded_frames': self.decoded_frames, 'decoded_bytes': self.decoded_bytes,
                    'decoded_fps': (self.decoded_frames-1)/decoded_elapsed if decoded_elapsed > 0 else 0,
                    'last_decoded_frame_age': max(0, now-self.last_decoded) if self.last_decoded is not None else None,
                    'suppressed_by_rate_limit': self.suppressed_by_rate_limit,
                    'suppressed_by_rate_limit_definition': 'decoded arrivals not immediately published; newest pending frame can publish later',
                    'publication_pending': self.pending, 'max_publication_fps': self.max_fps,
                    'latest_bytes': self.frame['bytes'] if self.frame else 0,
                    'latest_sha256': self.frame['sha256'] if self.frame else None,
                    'cache_capacity_frames': 1, 'closed': self.closed, 'error': self.error,
                    'native_tick_bound': False}

    def iter_mjpeg(self, boundary='royale-frame', stop_event=None, timeout=1):
        if not isinstance(boundary, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,70}', boundary):
            raise ValueError('Invalid multipart boundary')
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 60:
            raise ValueError('MJPEG wait timeout must be >0..60 seconds')
        sequence = 0
        while not (stop_event and stop_event.is_set()):
            frame = self.wait_next(sequence, timeout)
            if frame is None:
                if self.closed and not self.pending: return
                continue
            sequence = frame['sequence']
            yield (f'--{boundary}\r\nContent-Type: image/jpeg\r\nContent-Length: {frame["bytes"]}\r\n'
                   f'X-Frame-Sequence: {sequence}\r\nX-Frame-SHA256: {frame["sha256"]}\r\n\r\n').encode('ascii') + frame['jpeg'] + b'\r\n'


class NativeFrameStream:
    def __init__(self, ssh_argv, *, ffmpeg=DEFAULT_FFMPEG, fps=12, width=540,
                 segment_seconds=60, bitrate=1500000, output=None):
        self.commands = build_stream_commands(ssh_argv, ffmpeg=ffmpeg, fps=fps, width=width,
                                               segment_seconds=segment_seconds, bitrate=bitrate)
        self.folder = local(output or ('outputs/platform_next/streams/' +
                            datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')+'-'+secrets.token_hex(4)))
        self.cache = LatestJPEG(max_fps=fps); self.source = None; self.decoder = None; self.thread = None
        self.stop_requested = threading.Event(); self.cleanup_lock = threading.RLock()
        self.remote_cleanup_lock = threading.Lock(); self.remote_cleanup_report = None
        self.log_handles = []; self.started = False

    def start(self):
        if self.started or self.stop_requested.is_set(): raise RuntimeError('Create a new stream instance for a fresh start')
        self.folder.mkdir(parents=True, exist_ok=False)
        (self.folder/'configuration.json').write_text(json.dumps(self.commands, ensure_ascii=False, indent=2), encoding='utf-8')
        hidden = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        self.started = True
        try:
            source_log = (self.folder/'source.stderr.log').open('wb'); self.log_handles.append(source_log)
            decoder_log = (self.folder/'decoder.stderr.log').open('wb'); self.log_handles.append(decoder_log)
            self.source = subprocess.Popen(self.commands['source'], cwd=ROOT, stdin=subprocess.DEVNULL,
                                           stdout=subprocess.PIPE, stderr=source_log, bufsize=0, creationflags=hidden)
            self.decoder = subprocess.Popen(self.commands['decoder'], cwd=ROOT, stdin=self.source.stdout,
                                            stdout=subprocess.PIPE, stderr=decoder_log, bufsize=0, creationflags=hidden)
            # The decoder owns its inherited read end. Closing this host copy
            # allows producer pipe errors to surface when the decoder stops.
            self.source.stdout.close()
            self.thread = threading.Thread(target=self._read, name='native-mjpeg-latest-frame', daemon=True)
            self.thread.start()
        except Exception as exc:
            self.cache.finish(f'{type(exc).__name__}: {exc}'); self.stop(); raise
        return self

    def _read(self):
        parser = JPEGParser()
        error = None
        try:
            # Keep draining during remote SIGINT cleanup. Stopping this reader
            # first can fill FFmpeg's JPEG pipe and block Android's final flush.
            while True:
                data = self.decoder.stdout.read(65536)
                if not data:
                    if not self.stop_requested.is_set(): error = 'Decoder stream ended; inspect source/decoder stderr logs'
                    break
                for frame in parser.feed(data): self.cache.publish(frame)
        except Exception as exc:
            if not self.stop_requested.is_set(): error = f'{type(exc).__name__}: {exc}'
        finally:
            self.cache.finish(error); self.stop_requested.set()
            try: self._remote_cleanup_once()
            finally: self._cleanup(3)

    def _remote_cleanup_once(self):
        """One strict SSH ownership check; local process exit is not its proof."""
        with self.remote_cleanup_lock:
            if self.remote_cleanup_report is not None: return self.remote_cleanup_report
            if self.source is None:
                self.remote_cleanup_report = {'status': 'source_not_started', 'released': None,
                                              'verified': False, 'cleanup_ssh_calls': 0}
                return self.remote_cleanup_report
            command = self.commands['remote_cleanup']
            (self.folder/'cleanup.command.json').write_text(json.dumps(command, ensure_ascii=False, indent=2), encoding='utf-8')
            started = time.monotonic()
            receipt = {'status': 'failed', 'released': False, 'verified': False, 'cleanup_ssh_calls': 1,
                       'stream_token': self.commands['stream_token'], 'timeout_seconds': 12}
            stdout = b''; stderr = b''
            hidden = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
            try:
                result = subprocess.run(command, cwd=ROOT, stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                        timeout=12, creationflags=hidden)
                stdout = result.stdout; stderr = result.stderr
                receipt['exit_code'] = result.returncode
                payload = json.loads(stdout.decode('utf-8').strip())
                if (not isinstance(payload, dict) or payload.get('schema') != 'royale-native-stream-cleanup.v1'
                        or payload.get('token') != self.commands['stream_token']
                        or type(payload.get('released')) is not bool
                        or type(payload.get('ownership_verified')) is not bool
                        or type(payload.get('signal_sent')) is not bool):
                    raise ValueError('Cleanup response does not match this stream ownership token/schema')
                receipt.update(status=payload.get('status', 'unknown'), released=payload['released'],
                               verified=result.returncode == 0 and payload['released'] is True,
                               android=payload)
            except subprocess.TimeoutExpired as exc:
                stdout = exc.stdout or b''; stderr = exc.stderr or b''
                receipt.update(status='transport_timeout', error='Remote cleanup SSH exceeded 12 seconds; release remains unverified')
            except (ValueError, OSError, UnicodeError) as exc:
                receipt.update(status='cleanup_unverified', error=f'{type(exc).__name__}: {exc}')
            (self.folder/'cleanup.stdout.log').write_bytes(stdout)
            (self.folder/'cleanup.stderr.log').write_bytes(stderr)
            receipt['elapsed_seconds'] = time.monotonic()-started
            self.remote_cleanup_report = receipt
            (self.folder/'cleanup.json').write_text(json.dumps(receipt, ensure_ascii=False, indent=2), encoding='utf-8')
            return receipt

    def _cleanup(self, timeout):
        with self.cleanup_lock:
            processes = [process for process in (self.decoder, self.source) if process is not None]
            for process in processes:
                if process.poll() is None:
                    try: process.terminate()
                    except OSError: pass
            for process in processes:
                try: process.wait(timeout=max(.1, timeout/2))
                except subprocess.TimeoutExpired:
                    process.kill()
                    try: process.wait(timeout=1)
                    except subprocess.TimeoutExpired: self.cache.finish('Child process did not terminate within cleanup deadline')
                for stream in (getattr(process, 'stdout', None), getattr(process, 'stdin', None)):
                    if stream is not None:
                        try: stream.close()
                        except (OSError, ValueError): pass
            for handle in self.log_handles:
                if not handle.closed: handle.close()

    def stop(self, timeout=3):
        """Verify remote release (<=12s transport budget), then stop local pipes."""
        if not isinstance(timeout, (int, float)) or not 0 < timeout <= 10: raise ValueError('stop timeout must be 0..10 seconds')
        self.stop_requested.set(); self.cache.finish()
        try: self._remote_cleanup_once()
        finally: self._cleanup(timeout)
        if self.thread and self.thread is not threading.current_thread():
            self.thread.join(timeout)
            if self.thread.is_alive(): self.cache.finish('JPEG reader did not stop within cleanup deadline')
        if self.started and self.folder.is_dir():
            (self.folder/'status.json').write_text(json.dumps(self.status(), ensure_ascii=False, indent=2), encoding='utf-8')

    def latest(self): return self.cache.latest()
    def wait_next(self, after_sequence=0, timeout=1): return self.cache.wait_next(after_sequence, timeout)
    def iter_mjpeg(self, boundary='royale-frame', stop_event=None, timeout=1):
        return self.cache.iter_mjpeg(boundary, stop_event, timeout)

    def status(self):
        return {**self.cache.status(), 'started': self.started,
                'running': bool(self.started and not self.stop_requested.is_set()),
                'source_exit_code': self.source.poll() if self.source else None,
                'decoder_exit_code': self.decoder.poll() if self.decoder else None,
                'folder': str(self.folder.relative_to(ROOT)),
                'fps_requested': self.commands['fps_requested'], 'width_requested': self.commands['width_requested'],
                'source_size': self.commands['source_size'],
                'stream_token': self.commands['stream_token'],
                'android_pidfile': self.commands['android_pidfile'],
                'remote_cleanup_verified': bool(self.remote_cleanup_report and self.remote_cleanup_report.get('verified')),
                'remote_cleanup': dict(self.remote_cleanup_report) if self.remote_cleanup_report else None,
                'source_connections_planned': 1, 'remote_capability_live_verified_by_this_module': False,
                'timing': 'Asynchronous wall-clock stream; no exact native tick/state binding'}

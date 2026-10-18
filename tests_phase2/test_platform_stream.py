"""Offline stream checks: local recorded media and fake child lifecycles only.

Never invoke SSH, ADB, VM, battle RPC, an HTTP listener, or a browser.
"""
import io
import json
import os
from pathlib import Path
import shutil
import shlex
import subprocess
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from royale_phase2.platform_stream import DEFAULT_FFMPEG, JPEGParser, LatestJPEG, NativeFrameStream, build_stream_commands, _adb_script
from royale_phase2.runtime import ROOT, local
from royale_phase2.viewer import AndroidBridge


EVIDENCE = ROOT/'outputs/platform_next/fixes/20261018_human_stream'
JPEG = b'\xff\xd8explicit-parser-fixture\xff\xd9'


class FakeProcess:
    def __init__(self, stdout=None):
        self.stdout = stdout if stdout is not None else io.BytesIO()
        self.stdin = None; self.returncode = None; self.terminated = False
    def poll(self): return self.returncode
    def terminate(self):
        self.terminated = True; self.returncode = -15
        if hasattr(self.stdout, 'release'): self.stdout.release()
    def kill(self): self.terminate()
    def wait(self, timeout=None):
        if self.returncode is None: self.returncode = 0
        return self.returncode


class BlockingPipe:
    def __init__(self): self.event = threading.Event(); self.closed = False
    def read(self, size):
        self.event.wait(3)
        return b''
    def release(self): self.event.set()
    def close(self): self.closed = True; self.release()


class PlatformStreamTests(unittest.TestCase):
    def setUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.scratch = Path(tempfile.mkdtemp(prefix='offline-', dir=EVIDENCE)).resolve()
        self.scratch.relative_to(EVIDENCE.resolve())
    def tearDown(self):
        self.scratch.resolve().relative_to(EVIDENCE.resolve())
        shutil.rmtree(self.scratch)

    @staticmethod
    def cleanup_result(stream, *, token=None, released=True):
        payload = {'schema': 'royale-native-stream-cleanup.v1', 'token': token or stream.commands['stream_token'],
                   'status': 'released_after_sigint' if released else 'exact_screenrecord_cmdline_mismatch',
                   'pid': 123, 'starttime': 456, 'ownership_verified': released,
                   'signal_sent': released, 'released': released}
        return subprocess.CompletedProcess([], 0 if released else 2, stdout=json.dumps(payload).encode(), stderr=b'')

    def test_commands_use_one_hardened_guest_connection_and_project_decoder(self):
        inherited = AndroidBridge().ssh
        self.assertIn('StrictHostKeyChecking=no', inherited)
        commands = build_stream_commands(inherited)
        source = commands['source']; decoder = commands['decoder']
        self.assertIn('StrictHostKeyChecking=yes', source)
        self.assertNotIn('StrictHostKeyChecking=no', source)
        self.assertIn('UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts', source)
        self.assertIn('GlobalKnownHostsFile=none', source)
        self.assertIn('ControlMaster=no', source)
        self.assertIn('--time-limit 60', source[-1])
        self.assertIn('--size 540x960 ', source[-1])
        self.assertEqual(commands['source_size'], '540x960')
        self.assertEqual((commands['source_width'], commands['source_height']), (540, 960))
        smaller = build_stream_commands(inherited, width=360)
        self.assertEqual(smaller['source_size'], '360x640')
        odd_height_ratio = build_stream_commands(inherited, width=64)
        self.assertEqual(odd_height_ratio['source_height'] % 2, 0)
        self.assertIn('--output-format=h264', source[-1])
        self.assertIn('while :;', source[-1])
        self.assertIn('trap cleanup EXIT', source[-1])
        self.assertIn('segment=$((segment + 1))', source[-1])
        self.assertIn('ROYALE_STREAM_SEGMENT_START seq=%s unix=%s pid=%s', source[-1])
        self.assertIn('ROYALE_STREAM_SEGMENT_END seq=%s unix=%s pid=%s exit=%s', source[-1])
        self.assertIn('pid_role=guest_adb', source[-1])
        self.assertEqual(source[-1].count('>&2'), 3)
        self.assertIn('ROYALE_STREAM_SEGMENT_EARLY_END', source[-1])
        self.assertIn('if [ "$elapsed" -lt 5 ]', source[-1])
        self.assertIn('exit 77', source[-1])
        self.assertLess(source[-1].index('if [ "$stop_state"'), source[-1].index('if [ "$elapsed"'))
        self.assertIn('scale=540:-2:flags=fast_bilinear', decoder)
        self.assertNotIn('fps=', decoder[decoder.index('-vf')+1])
        self.assertIn('monotonic wall clock', commands['publication_rate_limit'])
        self.assertEqual(local(decoder[0]), local(DEFAULT_FFMPEG))
        self.assertEqual(commands['source_connections'], 1)
        self.assertFalse(commands['remote_h264_stdout_verified_by_command_build'])
        spaced = inherited[:-1]+['-oStrictHostKeyChecking accept-new', '-o', 'UserKnownHostsFile /outside/pin', inherited[-1]]
        hardened = build_stream_commands(spaced)['source']
        self.assertNotIn('-oStrictHostKeyChecking accept-new', hardened)
        self.assertNotIn('UserKnownHostsFile /outside/pin', hardened)

    def test_paths_modes_and_numeric_bounds_reject_invalid_configuration(self):
        ssh = AndroidBridge().ssh
        for kwargs in ({'fps': 0}, {'fps': True}, {'width': 541}, {'segment_seconds': 181},
                       {'bitrate': 0}, {'ffmpeg': ROOT.parent/'outside.exe'}, {'stream_token': '../../other'}):
            with self.assertRaises(ValueError): build_stream_commands(ssh, **kwargs)
        with self.assertRaises(ValueError): build_stream_commands(ssh[:-1]+['unrelated.example'])
        with self.assertRaises(ValueError): NativeFrameStream(ssh, output=ROOT.parent/'outside')

    def test_constructor_is_inert_and_does_not_create_folder_or_start_process(self):
        output = self.scratch/'not-started'
        with patch('royale_phase2.platform_stream.subprocess.Popen') as popen:
            stream = NativeFrameStream(AndroidBridge().ssh, output=output)
            self.assertFalse(output.exists())
            self.assertFalse(stream.status()['started'])
            self.assertEqual(stream.status()['counts_received'], 0)
            popen.assert_not_called()
            stream.stop()

    def test_incremental_parser_handles_fragmentation_multiple_frames_and_garbage(self):
        parser = JPEGParser()
        self.assertEqual(parser.feed(b'not-a-frame\xff'), [])
        self.assertEqual(parser.feed(b'\xd8one\xff'), [])
        self.assertEqual(parser.feed(b'\xd9'+JPEG+b'junk'), [b'\xff\xd8one\xff\xd9', JPEG])
        self.assertLessEqual(len(parser.buffer), 1)
        self.assertGreater(parser.discarded_bytes, 0)

    def test_invalid_frame_size_is_bounded(self):
        parser = JPEGParser(1024)
        parser.feed(b'x'*100000)
        self.assertLessEqual(len(parser.buffer), 1)
        with self.assertRaisesRegex(ValueError, 'bounded frame size'):
            parser.feed(b'\xff\xd8'+b'x'*1024)

    def test_cache_is_latest_only_and_slow_reader_drops_backlog(self):
        cache = LatestJPEG(); iterator = cache.iter_mjpeg()
        cache.publish(JPEG)
        first = next(iterator)
        self.assertIn(b'X-Frame-Sequence: 1\r\n', first)
        for index in range(2, 101): cache.publish(b'\xff\xd8'+str(index).encode()+b'\xff\xd9')
        last = next(iterator)
        self.assertIn(b'X-Frame-Sequence: 100\r\n', last)
        self.assertEqual(cache.status()['cache_capacity_frames'], 1)
        self.assertEqual(cache.status()['counts_received'], 100)
        self.assertEqual(cache.latest()['sequence'], 100)
        self.assertIsNone(cache.latest()['native_tick'])
        cache.finish()
        with self.assertRaises(StopIteration): next(iterator)

    def test_cache_metrics_are_wall_clock_and_content_changes_are_explicit(self):
        cache = LatestJPEG()
        with patch('royale_phase2.platform_stream.time.monotonic', side_effect=[10.0, 10.1, 10.2]):
            cache.publish(JPEG); cache.publish(JPEG); status = cache.status()
        self.assertEqual(status['counts_received'], 2)
        self.assertEqual(status['unique_frame_count'], 1)
        self.assertAlmostEqual(status['effective_fps'], 10)
        self.assertAlmostEqual(status['last_frame_age'], .1)
        self.assertFalse(status['native_tick_bound'])
        self.assertEqual(status['bytes_received'], 2*len(JPEG))
        frame = cache.latest(); frame['sequence'] = 9
        self.assertEqual(cache.latest()['sequence'], 2)

    def test_rate_limit_uses_injected_wall_clock_keeps_latest_and_never_fills_duplicates(self):
        now = [100.0]
        cache = LatestJPEG(max_fps=10, clock=lambda: now[0])
        cache.publish(JPEG)
        first = cache.wait_next(0, 0)
        self.assertEqual(first['sequence'], 1)
        # An arbitrarily fast decoder replaces one slot without sleeping or
        # building a backlog. None of these arrivals may publish before .1s.
        with patch('royale_phase2.platform_stream.time.sleep', side_effect=AssertionError('reader must not sleep')):
            for index in range(1, 101):
                now[0] = 100+index*.0001
                cache.publish(b'\xff\xd8'+str(index).encode()+b'\xff\xd9')
        status = cache.status()
        self.assertEqual((status['decoded_frames'], status['counts_received']), (101, 1))
        self.assertEqual(status['suppressed_by_rate_limit'], 100)
        self.assertEqual(status['unique_frame_count'], 1)
        self.assertTrue(status['publication_pending'])
        self.assertEqual(cache.latest()['jpeg'], b'\xff\xd8100\xff\xd9')
        self.assertIsNone(cache.wait_next(1, 0))
        # No new decoder input is required to publish the newest pending frame.
        now[0] = 100.1
        last = cache.wait_next(1, 0)
        self.assertEqual((last['sequence'], last['jpeg']), (2, b'\xff\xd8100\xff\xd9'))
        self.assertFalse(last['publication_pending'])
        now[0] = 200
        self.assertIsNone(cache.wait_next(2, 0))
        self.assertEqual(cache.status()['counts_received'], 2)
        self.assertEqual(cache.status()['unique_frame_count'], 2)
        self.assertEqual(cache.status()['cache_capacity_frames'], 1)

    def test_final_pending_frame_survives_finish_and_publishes_once_when_due(self):
        now = [10.0]
        cache = LatestJPEG(max_fps=2, clock=lambda: now[0])
        cache.publish(JPEG)
        now[0] = 10.1
        final = b'\xff\xd8last-decoded-frame\xff\xd9'
        cache.publish(final); cache.finish()
        self.assertEqual(cache.latest()['jpeg'], final)
        self.assertTrue(cache.latest()['publication_pending'])
        self.assertFalse(cache.publish(JPEG))
        self.assertIsNone(cache.wait_next(1, 0))
        now[0] = 10.5
        frame = cache.wait_next(1, 0)
        self.assertEqual((frame['sequence'], frame['jpeg']), (2, final))
        self.assertIsNone(cache.wait_next(2, 0))
        status = cache.status()
        self.assertEqual((status['decoded_frames'], status['counts_received'], status['unique_frame_count']), (2, 2, 2))
        self.assertAlmostEqual(status['effective_fps'], 2)
        self.assertAlmostEqual(status['decoded_fps'], 10)
        self.assertEqual(status['bytes_received'], len(JPEG)+len(final))
        self.assertEqual(status['decoded_bytes'], len(JPEG)+len(final))

    def test_wall_clock_wait_flushes_last_pending_frame_without_new_decoder_input(self):
        # A Condition deadline, rather than a publisher thread or decoder
        # sleep, wakes a real reader even after its source stops producing.
        cache = LatestJPEG(max_fps=30)
        cache.publish(JPEG); cache.publish(b'\xff\xd8pending\xff\xd9')
        frame = cache.wait_next(1, .5)
        self.assertIsNotNone(frame)
        self.assertEqual(frame['sequence'], 2)
        self.assertGreaterEqual(frame['published_monotonic']-cache.first_received, 1/30)
        self.assertIsNone(cache.wait_next(2, 0))
        for invalid in (0, -1, True, float('nan'), float('inf'), 31):
            with self.assertRaises(ValueError): LatestJPEG(max_fps=invalid)
        with self.assertRaises(ValueError): LatestJPEG(clock=42)

    def test_waiters_unblock_on_stop_and_multipart_boundary_is_validated(self):
        cache = LatestJPEG(); answer = []
        thread = threading.Thread(target=lambda: answer.append(cache.wait_next(0, 2)))
        thread.start(); cache.finish(); thread.join(1)
        self.assertFalse(thread.is_alive()); self.assertEqual(answer, [None])
        with self.assertRaises(ValueError): next(cache.iter_mjpeg('invalid\r\nboundary'))
        with self.assertRaises(ValueError): next(cache.iter_mjpeg(timeout=0))
        self.assertFalse(cache.publish(JPEG))

    def test_fake_start_has_two_processes_hidden_and_cleanup_releases_pipes(self):
        source = FakeProcess(); decoder = FakeProcess(BlockingPipe())
        stream = NativeFrameStream(AndroidBridge().ssh, output=self.scratch/'fake-live')
        with patch('royale_phase2.platform_stream.subprocess.Popen', side_effect=[source, decoder]) as popen, \
             patch('royale_phase2.platform_stream.subprocess.run', return_value=self.cleanup_result(stream)) as cleanup:
            stream.start()
            self.assertEqual(popen.call_count, 2)
            self.assertIs(popen.call_args_list[1].kwargs['stdin'], source.stdout)
            if os.name == 'nt':
                self.assertEqual(popen.call_args_list[0].kwargs['creationflags'], subprocess.CREATE_NO_WINDOW)
            stream.stop(1)
            stream.stop(1)
            cleanup.assert_called_once()
        self.assertTrue(source.terminated); self.assertTrue(decoder.terminated)
        self.assertFalse(stream.thread.is_alive())
        self.assertTrue(decoder.stdout.closed)
        self.assertTrue(all(handle.closed for handle in stream.log_handles))
        self.assertFalse(stream.status()['running'])
        self.assertTrue(stream.status()['remote_cleanup_verified'])
        self.assertTrue((stream.folder/'cleanup.json').is_file())

    def test_decoder_start_failure_cleans_up_source_without_launching_real_process(self):
        source = FakeProcess()
        stream = NativeFrameStream(AndroidBridge().ssh, output=self.scratch/'failure')
        with patch('royale_phase2.platform_stream.subprocess.Popen', side_effect=[source, OSError('explicit simulated failure')]), \
             patch('royale_phase2.platform_stream.subprocess.run', return_value=self.cleanup_result(stream)):
            with self.assertRaises(OSError): stream.start()
        self.assertTrue(source.terminated)
        self.assertTrue(all(handle.closed for handle in stream.log_handles))
        self.assertIn('simulated failure', stream.status()['error'])

    def test_android_pid_ownership_is_unique_exact_and_stop_prevents_new_segments(self):
        first = build_stream_commands(AndroidBridge().ssh)
        second = build_stream_commands(AndroidBridge().ssh)
        self.assertNotEqual(first['stream_token'], second['stream_token'])
        self.assertNotEqual(first['android_pidfile'], second['android_pidfile'])
        start = first['android_start_script']; cleanup = first['android_cleanup_script']
        self.assertEqual(start.count('[ ! -e "$stopfile" ]'), 2)
        self.assertIn('"$$" "$started" "$token" "$segment"', start)
        self.assertIn('mv "$pidfile.tmp" "$pidfile"', start)
        self.assertIn('exec /system/bin/screenrecord', start)
        self.assertIn('/proc/$pid/cmdline', cleanup)
        self.assertIn('recorded_token', cleanup)
        self.assertIn('"$current" != "$started"', cleanup)
        self.assertIn('"$cmd" != "$expected"', cleanup)
        self.assertLess(cleanup.index(': > "$stopfile"'), cleanup.index('read pid started recorded_token'))
        self.assertLess(cleanup.index('ownership_changed_before_signal'), cleanup.index('kill -2 "$pid"'))
        self.assertIn('timeout_owned_pid_alive', cleanup)
        self.assertNotIn('killall', cleanup)
        self.assertNotIn('pidof', cleanup)
        # The actual guest printf probe establishes that exactly one guest
        # shell parse must recover the complete Android sh -c argument.
        guest = shlex.split(first['remote_cleanup'][-1])
        self.assertEqual(guest[guest.index('exec-out')+1:], ['sh', '-c', cleanup])

    def test_adb_script_uses_actual_measured_single_quote_probe(self):
        probe_path = ROOT/'outputs/platform_next/fixes/20261018_manual_play/adb_quote_probe.json'
        probe = json.loads(probe_path.read_text(encoding='utf-8'))
        single = next(row for row in probe['rows'] if row['kind'] == 'single_quote')
        double = next(row for row in probe['rows'] if row['kind'] == 'double_quote')
        self.assertEqual(single['stdout'], 'ROYAL_QUOTE_OK:hello\nSECOND_LINE\n')
        self.assertEqual(single['exit_code'], double['exit_code'])
        self.assertIn('inaccessible or not found', double['stdout'])
        argv = shlex.split(single['command'])
        self.assertEqual(_adb_script(argv[-1]), single['command'])

    def test_actual_local_shell_cleanup_handles_disappearing_proc_but_never_kills_unknown_identity(self):
        # Execute the actual generated cleanup logic in local Git Bash. Only
        # fixture /proc and token files under project scratch are accessible;
        # kill is replaced by an audit log and sleep by a no-op. No Android,
        # SSH, ADB, real /proc access, or actual signal is involved.
        bash = Path('C:/Program Files/Git/bin/bash.exe')
        if not bash.is_file(): self.skipTest('Local offline Git Bash not installed')
        hidden = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        environment = dict(os.environ, HOME=str(self.scratch), TMP=str(self.scratch), TEMP=str(self.scratch))
        probe = subprocess.run([str(bash), '--noprofile', '--norc', '-c', 'printf ROYALE_OFFLINE_SHELL_READY'],
                               cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               timeout=10, creationflags=hidden)
        if b'NtCreateDirectoryObject' in probe.stderr and b'fatal error' in probe.stderr:
            self.skipTest('Git Bash MSYS initialization blocked by current sandbox; see initial shell sandbox failure log')
        self.assertEqual(probe.stdout, b'ROYALE_OFFLINE_SHELL_READY', probe.stderr.decode(errors='replace'))
        config = build_stream_commands(AndroidBridge().ssh, stream_token='e'*24)
        results = []
        def shell_path(path):
            path = path.resolve()
            path.relative_to(self.scratch)
            return '/'+path.drive[0].lower()+path.as_posix()[2:]
        cases = [('first_identity', 'proc', 1), ('first_cmdline', 'tr', 1),
                 ('before_signal_identity', 'proc', 2), ('before_signal_cmdline', 'tr', 2),
                 ('after_signal_identity', 'proc', 3)]
        for label, failure_kind, fail_at in cases:
            for disappears in (True, False):
                with self.subTest(position=label, disappears=disappears):
                    folder = self.scratch/(label+('-gone' if disappears else '-unknown'))
                    proc = folder/'proc/123'; proc.mkdir(parents=True)
                    (proc/'cmdline').write_bytes(b'fixture never executed')
                    (folder/'owned.pid').write_text('123 456 '+config['stream_token']+' 1\n', encoding='ascii')
                    fixture_root = shell_path(folder)
                    stubs = f'''
case_root={shlex.quote(fixture_root)}
fake_disappear() {{
  exec 0<&-
  rm -f "$case_root/proc/123/cmdline"
  rmdir "$case_root/proc/123"
}}
proc_start() {{
  n=$(cat "$case_root/proc.calls" 2>/dev/null || printf 0)
  n=$((n + 1)); printf '%s' "$n" > "$case_root/proc.calls"
  if [ '{failure_kind}' = proc ] && [ "$n" -eq {fail_at} ]; then
    {'fake_disappear' if disappears else ':'}
    return 1
  fi
  printf 456
}}
tr() {{
  n=$(cat "$case_root/tr.calls" 2>/dev/null || printf 0)
  n=$((n + 1)); printf '%s' "$n" > "$case_root/tr.calls"
  if [ '{failure_kind}' = tr ] && [ "$n" -eq {fail_at} ]; then
    {'fake_disappear' if disappears else ':'}
    return 1
  fi
  printf '%s' "$expected"
}}
kill() {{ printf '%s\\n' "$*" >> "$case_root/signals.log"; }}
sleep() {{ :; }}
'''
                    script = config['android_cleanup_script']
                    script = script.replace('/proc/', fixture_root+'/proc/')
                    script = script.replace(config['android_pidfile'], fixture_root+'/owned.pid')
                    script = script.replace(config['android_stopfile'], fixture_root+'/owned.stop')
                    script = script.replace('\ntoken=', '\n'+stubs+'\ntoken=', 1)
                    self.assertNotIn('"/proc/', script)
                    environment = dict(os.environ, HOME=str(folder), TMP=str(folder), TEMP=str(folder))
                    hidden = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
                    result = subprocess.run([str(bash), '--noprofile', '--norc', '-s'], input=script.encode('utf-8'),
                                            cwd=ROOT, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                            timeout=10, creationflags=hidden)
                    if b'NtCreateDirectoryObject' in result.stderr and b'fatal error' in result.stderr:
                        # Optional local POSIX shell cannot initialize in some
                        # Windows sandboxes. Do not request elevated execution
                        # or confuse host initialization with cleanup logic.
                        self.skipTest('Git Bash MSYS initialization blocked by current sandbox; see initial shell sandbox failure log')
                    self.assertTrue(result.stdout.strip(), result.stderr.decode(errors='replace'))
                    payload = json.loads(result.stdout.decode('utf-8').strip())
                    after_signal = label == 'after_signal_identity'
                    signal_log = (folder/'signals.log').read_text().splitlines() if (folder/'signals.log').exists() else []
                    self.assertEqual(signal_log, ['-2 123'] if after_signal else [])
                    self.assertEqual(result.returncode, 0 if disappears else 2)
                    self.assertEqual(payload['released'], disappears)
                    self.assertEqual(payload['signal_sent'], after_signal)
                    expected_status = ('released_after_sigint' if after_signal else 'already_exited') if disappears else (
                        'proc_identity_unreadable_after_sigint' if after_signal else (
                            'cmdline_unreadable' if failure_kind == 'tr' else 'proc_identity_unreadable'))
                    self.assertEqual(payload['status'], expected_status)
                    results.append({'position': label, 'directory_disappears_on_read_failure': disappears,
                                    'exit_code': result.returncode, 'payload': payload,
                                    'mock_signals': signal_log, 'stderr': result.stderr.decode(errors='replace')})
        (EVIDENCE/'cleanup_proc_race_offline.json').write_text(json.dumps({
            'schema': 'royale-stream-cleanup-proc-race-offline.v1', 'status': 'pass',
            'shell': str(bash), 'actual_signals': 0, 'ssh_calls': 0, 'adb_calls': 0,
            'method': 'Production cleanup script; project fixture proc and token paths; stubbed identity reads and signals',
            'cases': results}, ensure_ascii=False, indent=2), encoding='utf-8')

    def test_local_stop_does_not_claim_remote_release_on_wrong_token_or_timeout(self):
        for condition in ('wrong_token', 'timeout'):
            with self.subTest(condition=condition):
                source = FakeProcess(); decoder = FakeProcess(BlockingPipe())
                stream = NativeFrameStream(AndroidBridge().ssh, output=self.scratch/condition)
                response = self.cleanup_result(stream, token='f'*24)
                run_kwargs = {'return_value': response} if condition == 'wrong_token' else {
                    'side_effect': subprocess.TimeoutExpired(['explicit-fake-cleanup'], 12, output=b'partial', stderr=b'fake timeout')}
                with patch('royale_phase2.platform_stream.subprocess.Popen', side_effect=[source, decoder]), \
                     patch('royale_phase2.platform_stream.subprocess.run', **run_kwargs) as cleanup:
                    stream.start(); stream.stop(1)
                    cleanup.assert_called_once()
                self.assertFalse(stream.status()['remote_cleanup_verified'])
                self.assertFalse(stream.status()['remote_cleanup']['released'])
                self.assertFalse(stream.thread.is_alive())
                self.assertTrue(source.terminated and decoder.terminated)

    def test_unexpected_decoder_exit_performs_one_remote_cleanup_in_finally(self):
        source = FakeProcess(); decoder = FakeProcess(io.BytesIO(JPEG))
        stream = NativeFrameStream(AndroidBridge().ssh, output=self.scratch/'eof')
        with patch('royale_phase2.platform_stream.subprocess.Popen', side_effect=[source, decoder]), \
             patch('royale_phase2.platform_stream.subprocess.run', return_value=self.cleanup_result(stream)) as cleanup:
            stream.start(); stream.thread.join(2); stream.stop(1)
            cleanup.assert_called_once()
        self.assertEqual(stream.status()['counts_received'], 1)
        self.assertIn('Decoder stream ended', stream.status()['error'])
        self.assertTrue(stream.status()['remote_cleanup_verified'])

    def test_actual_project_ffmpeg_decodes_saved_local_video_h264_to_jpeg(self):
        ffmpeg = local(DEFAULT_FFMPEG)
        video = ROOT/'outputs/platform_next/sessions/20261018T041121.705498Z-agent/part-001.mp4'
        self.assertTrue(video.is_file())
        # Both subprocesses are the local project FFmpeg binary. No ssh/adb
        # command is run. Media output stays in pipes or this project log.
        hidden = getattr(subprocess, 'CREATE_NO_WINDOW', 0) if os.name == 'nt' else 0
        demux = subprocess.run([str(ffmpeg), '-hide_banner', '-loglevel', 'error', '-i', str(video), '-t', '4',
                               '-map', '0:v:0', '-c:v', 'copy', '-bsf:v', 'h264_mp4toannexb', '-f', 'h264', 'pipe:1'],
                              cwd=ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=30, creationflags=hidden)
        self.assertEqual(demux.returncode, 0, demux.stderr.decode(errors='replace'))
        command = build_stream_commands(AndroidBridge().ssh)['decoder']
        decode_started = time.monotonic()
        decoded = subprocess.run(command, input=demux.stdout, cwd=ROOT, stdout=subprocess.PIPE,
                                 stderr=subprocess.PIPE, timeout=30, creationflags=hidden)
        decode_seconds = time.monotonic()-decode_started
        (EVIDENCE/'ffmpeg_decode.stderr.log').write_bytes(decoded.stderr)
        self.assertEqual(decoded.returncode, 0, decoded.stderr.decode(errors='replace'))
        frames = JPEGParser().feed(decoded.stdout)
        self.assertGreater(len(frames), 2)
        self.assertTrue(all(frame.startswith(b'\xff\xd8') and frame.endswith(b'\xff\xd9') for frame in frames))
        sof = frames[0].find(b'\xff\xc0')
        self.assertGreaterEqual(sof, 0)
        width = int.from_bytes(frames[0][sof+7:sof+9], 'big')
        height = int.from_bytes(frames[0][sof+5:sof+7], 'big')
        self.assertEqual((width, height), (540, 960))
        report = {'schema': 'royale-platform-stream-offline-codec.v1', 'status': 'pass', 'ssh_calls': 0, 'adb_calls': 0,
                  'source_video': str(video.relative_to(ROOT)), 'h264_bytes': len(demux.stdout),
                  'decoded_jpegs': len(frames), 'jpeg_bytes': sum(map(len, frames)), 'width_requested': 540,
                  'actual_jpeg_width': width, 'actual_jpeg_height': height,
                  'offline_decode_seconds': decode_seconds,
                  'offline_decode_frames_per_second': len(frames)/decode_seconds,
                  'decoder': command,
                  'limitations': ['Local saved-video codec check only; no claim of guest H264 stdout support or measured live latency.']}
        (EVIDENCE/'offline_codec.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    unittest.main()

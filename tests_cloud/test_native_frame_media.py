"""Synthetic FrameOutput + real host VP9/decode fixtures; no Android evidence."""
from copy import deepcopy
import io
import json
from pathlib import Path
import signal
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

from royale_phase3.common import ROOT, write
from tools.cloud.native_frame_media import NativeFrameRecorder, frame_header, read_exact, TIMING_SCOPE
from tools.cloud.native_media import find_media_tool, video_interval_for_tick


class NativeFrameTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='frame-media-test-')
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)

    def recorder(self, **kwargs):
        return NativeFrameRecorder(self.base / 'recording', **kwargs)

    def test_constructor_is_inert_and_source_time_is_unknown(self):
        with patch('subprocess.Popen') as process, patch('socket.create_connection') as network:
            recorder = self.recorder()
        process.assert_not_called()
        network.assert_not_called()
        self.assertFalse(recorder.report['source_capture_pts_available'])
        self.assertTrue(recorder.report['capture_age_unknown'])
        self.assertFalse(recorder.report['recording_complete'])
        self.assertIsNone(video_interval_for_tick(recorder.report, 120))

    def test_header_matches_measured_android_format_and_rejects_unbounded_allocations(self):
        actual = frame_header(struct.pack('<5I', 172816, 180, 320, 540, 3), (180, 320))
        self.assertEqual(actual['payload_bytes'], 172800)
        self.assertEqual(actual['packet_bytes'], 172820)
        for values in [(172815, 180, 320, 540, 3), (172816, 180, 320, 540, 4),
                       (172816, 181, 320, 540, 3), (16+16*1024*1024, 180, 320, 52428, 3),
                       (16+539*320, 180, 320, 539, 3)]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                frame_header(struct.pack('<5I', *values))
        with self.assertRaises(ValueError):
            frame_header(struct.pack('<5I', 172816, 180, 320, 540, 3), (360, 640))

    def test_partial_stream_reads_and_truncated_packets(self):
        class Fragmented(io.BytesIO):
            def read(self, count):
                return super().read(min(count, 3))
        self.assertEqual(read_exact(Fragmented(b'12345678'), 8), b'12345678')
        self.assertIsNone(read_exact(io.BytesIO(), 20, allow_eof=True))
        with self.assertRaisesRegex(ValueError, 'Truncated'):
            read_exact(io.BytesIO(b'1234'), 20, allow_eof=True)

    def test_invalid_header_is_preserved_as_bounded_binary_and_hex(self):
        recorder = self.recorder()
        recorder.folder.mkdir()
        invalid = b'not a frame header!!'
        self.assertEqual(len(invalid), 20)
        recorder.process = Mock(stdout=io.BytesIO(invalid))
        recorder._read_frames()
        self.assertIn('Invalid bounded RGB24', recorder.background_errors[0])
        self.assertEqual(recorder.report['invalid_frame_header']['bytes_hex'], invalid.hex())
        self.assertEqual((recorder.folder/'invalid-frame-header.bin').read_bytes(), invalid)
        self.assertFalse(recorder.report['recording_complete'])

    def test_reader_removes_row_padding_and_records_every_received_hash(self):
        recorder = self.recorder(size='64x96')
        recorder.folder.mkdir()
        stride = 196
        payload = (b'\x10\x20\x30' * 64 + b'pad!') * 96
        recorder.process = Mock(stdout=io.BytesIO(struct.pack('<5I', 16+stride*96, 64, 96, stride, 3)+payload))
        recorder._read_frames()
        self.assertTrue(recorder.reader_done.is_set())
        self.assertEqual(recorder.background_errors, [])
        row, rgb = recorder.frames.get_nowait()
        self.assertEqual(rgb, b'\x10\x20\x30' * (64*96))
        self.assertEqual(row['stride_bytes'], 196)
        self.assertIsNone(row['source_capture_pts'])
        self.assertEqual((recorder.folder/'before-reset.rgb').read_bytes(), rgb)

    def test_queue_overflow_and_incomplete_packet_fail_without_dropping_silently(self):
        packet = struct.pack('<5I', 16+64*96*3, 64, 96, 64*3, 3)+b'\0'*(64*96*3)
        recorder = self.recorder(size='64x96', queue_frames=1)
        recorder.folder.mkdir()
        recorder.process = Mock(stdout=io.BytesIO(packet*2))
        recorder._read_frames()
        self.assertEqual(len(recorder.received), 2)
        self.assertEqual(recorder.frames.qsize(), 1)
        self.assertIn('queue overflow', recorder.background_errors[0])
        other = NativeFrameRecorder(self.base/'truncated', size='64x96')
        other.folder.mkdir()
        other.process = Mock(stdout=io.BytesIO(packet[:-1]))
        other._read_frames()
        self.assertIn('Truncated', other.background_errors[0])
        self.assertFalse(other.report['recording_complete'])

    def test_navigation_retains_host_arrival_scope_and_does_not_bind_tick_to_pixel(self):
        report = dict(timing_scope=TIMING_SCOPE, mapping_anchors=[dict(tick=100, video_seconds_range=[1., 1.5])])
        result = video_interval_for_tick(report, 110)
        self.assertEqual(result['timing_scope'], TIMING_SCOPE)
        self.assertEqual(result['tick_distance'], 10)
        self.assertFalse(result['exact_frame_tick_binding'])

    def test_measured_feed_pts_reject_drop_clock_step_and_fixed_rate_retiming(self):
        recorder = self.recorder()
        recorder.folder.mkdir()
        points = recorder.folder/'pts.json'
        write(points, dict(frame_pts_seconds=[0., .3, .9]))
        recorder.received = [dict(received_monotonic=10.+delta) for delta in [0, .3, .9]]
        recorder.fed = [dict(index=i, feed_unix_seconds_interval=[100.+delta, 100.+delta+.001],
                            feed_monotonic_interval=[10.+delta, 10.+delta+.001])
                        for i, delta in enumerate([0, .3, .9])]
        recorder.anchors = [dict(tick=90, host_monotonic=10.5, phase='reset_returned')]
        info = dict(pts_index=dict(path=points.relative_to(ROOT).as_posix()), first_pts_seconds=0.,
                    first_frame_pts_seconds=0., duration_seconds=.9, last_packet_duration_seconds=None)
        recorder._validate_pts(info)
        self.assertTrue(recorder.report['host_feed_pts_verified'])
        self.assertEqual(recorder.report['mapping_anchors'][0]['video_seconds_range'], [.3, .9])
        original = deepcopy(recorder.fed)
        recorder.fed.pop()
        with self.assertRaisesRegex(ValueError, 'Encoded frames differ'):
            recorder._validate_pts(info)
        recorder.fed = original
        recorder.fed[-1]['feed_unix_seconds_interval'] = [110., 110.001]
        with self.assertRaisesRegex(ValueError, 'clock'):
            recorder._validate_pts(info)
        recorder.fed = original
        recorder.fed[-1]['feed_unix_seconds_interval'] = [100.9, 100.901]
        write(points, dict(frame_pts_seconds=[0., .2, .4]))
        with self.assertRaisesRegex(ValueError, 'PTS differs'):
            info['duration_seconds'] = .4
            recorder._validate_pts(info)

    def test_epoch_clock_and_oversized_container_duration_cannot_pass_relative_pts_checks(self):
        recorder = self.recorder()
        recorder.folder.mkdir()
        path = recorder.folder/'pts.json'
        recorder.received = [dict(received_monotonic=20.+i) for i in range(3)]
        recorder.fed = [dict(index=i, feed_unix_seconds_interval=[100.+i, 100.+i+.001],
            feed_monotonic_interval=[20.+i, 20.+i+.001]) for i in range(3)]
        epoch = 1794033980.833
        write(path, dict(frame_pts_seconds=[epoch+i for i in range(3)]))
        info = dict(pts_index=dict(path=path.relative_to(ROOT).as_posix()), first_pts_seconds=epoch,
            first_frame_pts_seconds=epoch, duration_seconds=epoch+2, last_packet_duration_seconds=None)
        with self.assertRaisesRegex(ValueError, 'begin at zero'):
            recorder._validate_pts(info)
        write(path, dict(frame_pts_seconds=[0., 1., 2.]))
        info.update(first_pts_seconds=0., first_frame_pts_seconds=0.)
        with self.assertRaisesRegex(ValueError, 'container duration'):
            recorder._validate_pts(info)
        info['duration_seconds'] = 2.
        recorder._validate_pts(info)
        self.assertTrue(recorder.report['presentation_timeline']['zero_based_verified'])
        self.assertIsNone(recorder.report['presentation_timeline']['last_packet_duration_seconds'])
        info.update(duration_seconds=3., last_packet_duration_seconds=1.)
        with self.assertRaisesRegex(ValueError, 'packet tail'):
            recorder._validate_pts(info)

    def test_encoder_command_has_measured_variable_timestamps_and_single_thread(self):
        recorder = self.recorder()
        recorder.ffmpeg = Path('/fixture/ffmpeg')
        command = recorder._encoder_command()
        self.assertEqual(command[command.index('-use_wallclock_as_timestamps')+1], '1')
        self.assertEqual(command[command.index('-fps_mode')+1], 'passthrough')
        self.assertEqual(command[command.index('-threads')+1], '1')
        self.assertNotIn('-r', command)
        self.assertEqual(command[command.index('-vf')+1], 'setpts=PTS-STARTPTS')
        self.assertIn('-nofind_stream_info', command)
        self.assertNotIn('nobuffer', command)
        self.assertIn('libvpx-vp9', command)

    def test_owned_cleanup_is_idempotent_and_failure_remains_explicit(self):
        recorder = self.recorder()
        recorder.process = Mock()
        recorder.process.poll.return_value = None
        with patch.object(recorder, '_shell', side_effect=RuntimeError('transport lost')) as shell:
            recorder._release()
            recorder._release()
        self.assertEqual(shell.call_count, 1)
        self.assertTrue(recorder.report['guest_release_unverified'])
        self.assertIn('proc_start', recorder._stop_script())
        self.assertIn('cmdline', recorder._stop_script())
        self.assertNotIn('pkill', recorder._stop_script())
        recorder.process.terminate.assert_called_once()

    def test_rejects_unsafe_or_nonfinite_configuration(self):
        for kwargs in ({'serial': 'a; rm'}, {'queue_frames': True}, {'max_frames': 999999},
                       {'terminal_wait_seconds': float('nan')}, {'pts_feed_tolerance_seconds': float('inf')},
                       {'size': '1x1'}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                self.recorder(**kwargs)

    def test_guest_cleanup_failure_still_closes_the_owned_host_encoder(self):
        for error in (RuntimeError('guest link lost'), KeyboardInterrupt()):
            with self.subTest(error=type(error).__name__):
                recorder = self.recorder()
                recorder.process = Mock()
                recorder.process.poll.return_value = None
                recorder.encoder = Mock()
                with patch.object(recorder, '_shell', side_effect=error):
                    if isinstance(error, KeyboardInterrupt):
                        with self.assertRaises(KeyboardInterrupt):
                            recorder._release()
                    else:
                        recorder._release()
                recorder.encoder.stdin.close.assert_called_once()
                recorder.encoder.wait.assert_called_once()
                recorder.process.terminate.assert_called_once()
                self.assertTrue(recorder.report['guest_release_unverified'])

    def test_preparing_failure_closes_encoder_without_starting_a_world(self):
        recorder = self.recorder()
        recorder.folder.mkdir()
        recorder.encoder = Mock()
        with patch.object(recorder, '_save'):
            receipt = recorder.stop(natural_terminal=True, native_result=dict(finalized=True, tick=999))
        self.assertEqual(receipt['status'], 'failed')
        self.assertFalse(receipt['recording_complete'])
        recorder.encoder.stdin.close.assert_called_once()
        recorder.encoder.wait.assert_called_once()
        self.assertEqual(receipt['native_rpc_calls'], 0)

    def test_real_vp9_lifecycle_with_synthetic_variable_cadence_frames(self):
        try:
            ffmpeg, ffprobe = find_media_tool('ffmpeg'), find_media_tool('ffprobe')
        except FileNotFoundError as exc:
            self.skipTest(str(exc))
        adb = self.base/'fixture-adb'
        adb.write_text('fixture only')
        native = []
        # Frames are explicitly synthetic. Source process stays active through
        # the natural-terminal fixture and handles only its owned SIGINT.
        program = '''import os, signal, struct, time
running = True
def stop(*unused):
 global running
 running = False
signal.signal(signal.SIGINT, stop)
i = 0
while running:
 rgb = bytes((i % 250, (i*31)%250, 80)) * (64*96)
 packet = struct.pack('<5I',16+len(rgb),64,96,192,3)+rgb
 view = memoryview(packet)
 while view:
  count = os.write(1, view)
  view = view[count:]
 i += 1
 time.sleep([.12,.22,.16][i%3])
'''
        def popen(argv, **kwargs):
            if argv[0] == str(adb):
                value = subprocess.Popen([sys.executable, '-u', '-c', program], **kwargs)
                native.append(value)
                return value
            return subprocess.Popen(argv, **kwargs)
        def runner(label, argv, script, timeout):
            if label == 'adb-version':
                result = b'Android Debug Bridge version 1.0.41\n'
            elif label.startswith('server-version-'):
                result = b'0029'
            elif label == 'get-state':
                result = b'device\n'
            elif label == 'capabilities':
                result = b'--output-format frames\n'
            elif label == 'stop-owned-recorder':
                native[0].send_signal(signal.SIGINT)
                result = b'{"released":true,"ownership_verified":true}'
            elif label == 'pull-guest-log':
                Path(argv[-1]).write_text('Synthetic fixture; no Android access')
                result = b''
            elif label == 'remove-owned-remote-media':
                result = b''
            else:
                return subprocess.run(argv, capture_output=True, timeout=timeout, cwd=ROOT)
            return subprocess.CompletedProcess(argv, 0, result, b'')
        recorder = self.recorder(adb=str(adb), size='64x96', ffmpeg=str(ffmpeg), ffprobe=str(ffprobe),
                                 runner=runner, popen=popen)
        self.addCleanup(lambda: native and native[0].poll() is None and native[0].kill())
        self.assertTrue(recorder.start(), recorder.report)
        try:
            command = recorder.report['supervisor_command']
            self.assertIn('shell', command)
            self.assertEqual(command[command.index('shell')+1], '-T')
            self.assertNotIn('exec-out', command)
            self.assertGreaterEqual(len(recorder.received), 1)
            self.assertTrue((recorder.folder/'before-reset.rgb').is_file())
            recorder.anchor('reset_returned', state=dict(tick=90, generation=1))
            with recorder.condition:
                self.assertTrue(recorder.condition.wait_for(lambda: len(recorder.fed) >= 5, timeout=5))
            receipt = recorder.stop(natural_terminal=True, native_result=dict(finalized=True, tick=120, generation=1))
        finally:
            if not recorder.finished:
                recorder.stop()
        self.assertEqual(receipt['evidence_scope'], 'interface_only')
        self.assertEqual(receipt['status'], 'complete', receipt.get('errors'))
        self.assertTrue(receipt['recording_complete'])
        self.assertTrue(receipt['host_feed_pts_verified'])
        self.assertLessEqual(abs(receipt['video']['first_pts_seconds']), .002)
        self.assertLessEqual(abs(receipt['video']['first_frame_pts_seconds']), .002)
        self.assertAlmostEqual(receipt['video']['duration_seconds'], receipt['video']['frame_span_seconds'], delta=.010)
        self.assertTrue(receipt['presentation_timeline']['container_duration_verified'])
        self.assertEqual(receipt['video']['frame_count'], receipt['received_frames'])
        self.assertGreaterEqual(receipt['video']['unique_decoded_pixel_hashes'], 5)
        self.assertTrue(receipt['seek_verified'])
        self.assertFalse(receipt['terminal_pixel_presentation_verified'])
        self.assertFalse(receipt['game_content_visually_verified'])
        self.assertTrue(receipt['capture_age_unknown'])
        self.assertGreaterEqual(receipt['source_statistics']['unique_source_rgb24_hashes'], 5)
        self.assertFalse(receipt['source_statistics']['source_capture_fps_verified'])
        self.assertEqual(recorder.stop(), receipt)


if __name__ == '__main__':
    unittest.main()

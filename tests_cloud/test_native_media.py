"""Media contracts and real local decode/seek fixtures; never Android evidence."""
from copy import deepcopy
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

from royale_phase3.common import ROOT, reference
from tools.cloud.native_identity import Journal
from tools.cloud.native_media import NativeMediaRecorder, find_media_tool, inspect_probe, video_interval_for_tick


def probe_fixture():
    return dict(streams=[dict(codec_type='video',codec_name='h264',width=64,height=96)],
        format=dict(duration='1.6'),
        packets=[dict(pts_time=str(i*.2),dts_time=str(i*.2),pos=str(48+i*10),size='10') for i in range(8)],
        frames=[dict(best_effort_timestamp_time=str(i*.2),key_frame=int(i in (0,5))) for i in range(8)])


class MediaContractTests(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='native-media-test-')
        self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name)

    def recorder(self,**kwargs):
        return NativeMediaRecorder(self.base/'recording',**kwargs)

    def test_inert_constructor_and_no_invented_tick_mapping(self):
        with patch('subprocess.Popen') as process,patch('socket.create_connection') as network:
            recorder=self.recorder()
        process.assert_not_called(); network.assert_not_called()
        self.assertFalse(recorder.report['recording_complete'])
        self.assertIsNone(video_interval_for_tick(recorder.report,90))

    def test_decoded_frames_and_keyframes_are_required(self):
        value=inspect_probe(probe_fixture())
        self.assertEqual(value['frame_count'],8)
        self.assertAlmostEqual(value['measured_encoded_fps'],5)
        self.assertEqual(value['keyframe_count'],2)
        self.assertFalse(value['fresh_frame_rate_verified'])
        for mutate in ('missing_frames','bad_pts','missing_keyframe','invalid_packet'):
            fixture=probe_fixture()
            if mutate=='missing_frames': fixture.pop('frames')
            elif mutate=='bad_pts': fixture['frames'][2]['best_effort_timestamp_time']='nan'
            elif mutate=='missing_keyframe': fixture['frames'][0]['key_frame']=0
            else: fixture['packets'][0]['size']='0'
            with self.subTest(mutate=mutate),self.assertRaises((ValueError,TypeError)):
                inspect_probe(fixture)

    def test_b_frame_packet_pts_can_regress_but_display_pts_cannot(self):
        fixture=probe_fixture()
        fixture['packets'][1]['pts_time'],fixture['packets'][2]['pts_time']='.4','.2'
        self.assertFalse(inspect_probe(fixture)['packet_pts_monotonic'])
        fixture['frames'][2]['best_effort_timestamp_time']='0'
        with self.assertRaises(ValueError): inspect_probe(fixture)

    def test_gap_measurement_preserves_missing_time_and_nearest_anchor_interval(self):
        recorder=self.recorder()
        part=dict(duration_seconds=1.6,frame_span_seconds=1.4,first_packet_in_readiness_snapshot=True)
        recorder.report['parts']=[deepcopy(part),deepcopy(part)]
        starts=[dict(segment=1,device_uptime=100.),dict(segment=2,device_uptime=105.)]
        events=[dict(event='segment_ready',segment=1,device_uptime=100.1),
                dict(event='segment_end',segment=1,device_uptime=101.6),
                dict(event='segment_ready',segment=2,device_uptime=105.1),
                dict(event='segment_end',segment=2,device_uptime=106.6)]
        recorder.anchors=[dict(tick=90,phase='reset_returned',device_uptime=100.5,generation=1)]
        recorder._timeline(starts,events)
        self.assertFalse(recorder.report['segment_gaps_within_budget'])
        self.assertGreater(recorder.report['segment_gaps'][0]['missing_time_seconds_range'][0],3)
        self.assertEqual(recorder.report['segment_timeline'][1]['video_start_seconds'],1.6)
        value=video_interval_for_tick(recorder.report,95)
        self.assertEqual(value['anchor_tick'],90)
        self.assertEqual(value['tick_distance'],5)
        self.assertGreater(value['uncertainty_seconds'],0)
        self.assertLess(value['video_seconds_range'][1],1)
        self.assertFalse(value['exact_frame_tick_binding'])
        recorder.report['parts'][0]['first_packet_in_readiness_snapshot']=False
        with self.assertRaises(ValueError): recorder._timeline(starts,events)

    def test_pre_reset_output_is_verified_against_actual_packet_payload(self):
        recorder=self.recorder()
        recorder.folder.mkdir()
        original=recorder.folder/'original.mp4'; snapshot=recorder.folder/'snapshot.mp4'
        original.write_bytes(b'headerABCpayload-tail')
        snapshot.write_bytes(b'headerABCpayload')
        info=dict(first_packet=dict(position=9,size=7))
        self.assertTrue(recorder._snapshot_contains_packet(snapshot,original,info))
        snapshot.write_bytes(b'headerABCpay')
        self.assertFalse(recorder._snapshot_contains_packet(snapshot,original,info))
        snapshot.write_bytes(b'headerABCxxxxxxx')
        self.assertFalse(recorder._snapshot_contains_packet(snapshot,original,info))

    def test_supervisor_start_event_is_not_recording_readiness(self):
        recorder=self.recorder(); recorder.folder.mkdir()
        events=[dict(event='segment_start',segment=1,file='part-00001.mp4',device_uptime=100.)]
        recorder.process=Mock(stdout=io.BytesIO((''.join(json.dumps(e)+'\n' for e in events)).encode()))
        recorder._read_events()
        self.assertFalse(recorder.ready.is_set())
        events.append(dict(event='segment_ready',segment=1,file='part-00001.mp4',device_uptime=100.1))
        recorder.process.stdout=io.BytesIO((''.join(json.dumps(e)+'\n' for e in events)).encode())
        recorder._read_events()
        self.assertTrue(recorder.ready.is_set())

    def test_failed_start_closes_its_owned_process_without_global_kill(self):
        recorder=self.recorder()
        process=Mock()
        process.poll.return_value=None
        recorder.process=process
        with patch.object(recorder,'_shell',return_value='{"released":true}') as shell:
            recorder._release()
            recorder._release()
        self.assertEqual(shell.call_count,1)
        process.wait.assert_called()
        # No trust in caller-supplied finalized flag without recording/timing.
        with patch.object(recorder,'_save'):
            result=recorder.stop(natural_terminal=True,native_result={'finalized':True,'tick':999})
        self.assertFalse(result['recording_complete'])

    def test_failed_device_cleanup_is_explicit_and_idempotent(self):
        recorder=self.recorder(); recorder.folder.mkdir()
        process=Mock(); process.poll.return_value=None
        recorder.process=process
        with patch.object(recorder,'_shell',side_effect=RuntimeError('transport interrupted')):
            recorder._release(); recorder._release()
        self.assertTrue(recorder.report['guest_release_unverified'])
        self.assertFalse(recorder.report['recording_complete'])
        process.terminate.assert_called_once()

    def test_validation_rejects_nonfinite_gap_budget_and_unsafe_device(self):
        for kwargs in ({'max_segment_gap_seconds':float('nan')},{'max_segment_gap_seconds':True},
                       {'serial':'host:5555 --tcpip'},{'segment_seconds':181},
                       {'encoder_probe_seconds':1},{'encoder_probe_seconds':True}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError): self.recorder(**kwargs)

    def test_probe_duration_is_explicit_and_guest_log_is_pulled_without_retry(self):
        recorder=self.recorder(); recorder.folder.mkdir(); recorder.journal=Mock()
        def pull(label,args,timeout):
            Path(args[-1]).write_bytes(b'codec diagnostic' if label.endswith('-log') else b'mp4 bytes')
        recorder.journal.device.side_effect=pull
        with patch.object(recorder,'_shell',return_value='') as shell:
            self.assertEqual(recorder._encoder_probe(),recorder.folder/'encoder-probe.mp4')
        self.assertEqual(shell.call_count,1)
        self.assertIn('--time-limit 10',shell.call_args.args[1])
        self.assertIn('--verbose',shell.call_args.args[1])
        self.assertEqual(recorder.report['encoder_probe_seconds'],10)
        self.assertEqual(recorder.report['encoder_probe_automatic_retries'],0)
        self.assertTrue(recorder.report['encoder_probe_log']['path'].endswith('/encoder-probe.guest.log'))

    def test_probe_error_keeps_original_failure_and_still_fetches_guest_log(self):
        recorder=self.recorder(encoder_probe_seconds=5); recorder.folder.mkdir(); recorder.journal=Mock()
        recorder.journal.device.side_effect=lambda label,args,timeout: Path(args[-1]).write_text('codec initialization failed')
        with patch.object(recorder,'_shell',side_effect=RuntimeError('encoder exited')) as shell:
            with self.assertRaisesRegex(RuntimeError,'encoder exited'): recorder._encoder_probe()
        self.assertEqual(shell.call_count,1)
        self.assertEqual(recorder.journal.device.call_count,1)
        self.assertEqual((recorder.folder/'encoder-probe.guest.log').read_text(),'codec initialization failed')


class RealLocalMediaTests(unittest.TestCase):
    """Synthetic host fixtures prove decoder behavior, never Android pixels."""
    @classmethod
    def setUpClass(cls):
        try:
            cls.ffmpeg=find_media_tool('ffmpeg'); cls.ffprobe=find_media_tool('ffprobe')
        except FileNotFoundError as exc:
            raise unittest.SkipTest(str(exc))
        (ROOT/'.tmp').mkdir(exist_ok=True)
        cls.temp=tempfile.TemporaryDirectory(dir=ROOT/'.tmp',prefix='native-media-decode-test-')
        cls.base=Path(cls.temp.name)
        cls.video=cls.base/'synthetic-host-fixture.mp4'
        subprocess.run([str(cls.ffmpeg),'-v','error','-nostdin','-n','-f','lavfi','-i',
            'testsrc2=size=64x96:rate=5','-t','1.6','-c:v','mpeg4','-g','5','-bf','0',str(cls.video)],
            cwd=ROOT,check=True,capture_output=True,timeout=30)

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(dir=self.base,prefix='case-')
        self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name)

    def recorder(self):
        recorder=NativeMediaRecorder(self.folder/'recording',runner=lambda *a:None)
        recorder.folder.mkdir()
        recorder.ffmpeg=self.ffmpeg; recorder.ffprobe=self.ffprobe
        recorder.journal=Journal(recorder.folder,'unused-adb','127.0.0.1:15555',5038)
        return recorder

    def test_actual_full_decode_and_begin_middle_end_seek(self):
        recorder=self.recorder()
        with patch('socket.create_connection',side_effect=AssertionError('No network allowed')):
            info=recorder._probe(self.video,'host-fixture')
            value=recorder._decode(self.video,'host-fixture',info,seek=True)
        self.assertTrue(value['full_decode_verified'])
        self.assertEqual(value['decoded_frames'],8)
        self.assertEqual(len(value['seek_checks']),3)
        self.assertTrue(value['seek_verified'])
        self.assertEqual(recorder.report['evidence_scope'],'interface_only')
        self.assertFalse(recorder.report['browser_seek_verified'])

    def test_truncated_video_cannot_pass_full_probe_or_decode(self):
        recorder=self.recorder()
        corrupt=self.folder/'truncated.mp4'
        corrupt.write_bytes(self.video.read_bytes()[:100])
        with self.assertRaises((ValueError,RuntimeError)):
            info=recorder._probe(corrupt,'corrupt')
            recorder._decode(corrupt,'corrupt',info)

    def test_complete_stop_needs_real_decode_packet_prefix_and_terminal_anchors(self):
        recorder=self.recorder()
        recorder.started=0.
        snapshot=recorder.folder/'before-reset-part-00001.mp4'
        shutil.copyfile(self.video,snapshot)
        recorder.report['readiness_snapshot']=reference(snapshot)
        recorder.anchors=[dict(phase='before_reset',device_uptime=100.1),
            dict(phase='reset_returned',tick=90,generation=1,device_uptime=100.2),
            dict(phase='natural_terminal',tick=110,generation=1,device_uptime=101.2)]
        recorder.events=[
            dict(event='segment_start',segment=1,file='part-00001.mp4',device_uptime=100.),
            dict(event='segment_ready',segment=1,file='part-00001.mp4',device_uptime=100.1),
            dict(event='segment_end',segment=1,file='part-00001.mp4',device_uptime=101.6,exit_code=0)]
        process=Mock(); process.poll.return_value=None; process.returncode=0; recorder.process=process
        def release():
            process.poll.return_value=0
            recorder._release_attempted=True
        def pull(label,args,script=None,timeout=30):
            shutil.copyfile(self.video,args[-1]); return ''
        with patch.object(recorder,'_release',side_effect=release),patch.object(recorder,'anchor'), \
             patch.object(recorder,'_shell',return_value=''),patch.object(recorder.journal,'device',side_effect=pull):
            result=recorder.stop(natural_terminal=True,native_result=dict(finalized=True,tick=110),
                context=dict(owner=1,checkpoint_sha256='a'*64))
        self.assertEqual(result['status'],'complete')
        self.assertTrue(result['recording_complete'])
        self.assertTrue(result['decodable_verified'])
        self.assertTrue(result['seek_verified'])
        self.assertTrue(result['started_before_reset'])
        self.assertEqual(result['context']['owner'],1)
        self.assertTrue(result['path'].endswith('/native.mp4'))
        self.assertTrue(all(result['completion_checks'].values()))
        self.assertEqual(recorder.stop(),result)


if __name__=='__main__':
    unittest.main()


"""Native-loop interface tests with synthetic transport; zero real native RPC.

The mock clock/receipts below do not implement battle physics. Passing these
tests proves recording/export/training contracts, never Android execution.
"""
from contextlib import ExitStack, contextmanager, nullcontext
from copy import deepcopy
import importlib.util
from pathlib import Path
import tempfile
import sys
from types import ModuleType
import unittest
from unittest.mock import patch

from royale_phase3.common import ROOT, read, reference, rows, write
from royale_phase3.data_contract import load_dataset
from royale_phase3 import native_loop as loop
from tests_cloud.test_lifecycle import MockNativeBackend


class SyntheticFinalizingBackend(MockNativeBackend):
    def reset(self, initialization):
        result = super().reset(initialization)
        self.terminal_tick = 100
        # Seed-dependent synthetic HUD ensures duplicate detection can be
        # tested without pretending seed changes alone prove independence.
        self.state['players'][0]['elixir'] -= initialization.get('seed', 0) % 100
        return deepcopy(self.state)


class NativeLoopInterface(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(prefix='native-loop-', dir=ROOT / '.tmp')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)

    def batch(self, name='batch', games=5, max_tick=100, paired=False,
              backend=SyntheticFinalizingBackend, lease=None, policy='cycle', opponent='wait',
              record_backend='screenrecord', **kwargs):
        with ExitStack() as stack:
            stack.enter_context(patch.object(loop, 'native_lock', return_value=lease or nullcontext()))
            stack.enter_context(patch.object(loop, 'runtime_preflight', return_value={
                'ready': True, 'status': 'ready_assets', 'native_execution_verified': False,
                'evidence_scope': 'interface_only',
                'scope': 'synthetic interface fixture; zero native RPC'}))
            stack.enter_context(patch('royale_phase2.backend.NativeBattleBackend', backend))
            stack.enter_context(patch('royale_phase2.backend._read_locked_elixir_rates', return_value={}))
            stack.enter_context(patch('royale_phase3.native_env.match',
                                      side_effect=lambda seed: {'battle': {}, 'seed': seed}))
            return loop.run_batch(self.root / name, games=games, max_tick=max_tick,
                                  policy=policy, opponent=opponent, seed=700, paired=paired,
                                  record_backend=record_backend, **kwargs)

    def recorder(self, events, *, start_ok=True, stop_error=False, anchor_error=False, stop_interrupt=False):
        """Protocol fixture only: never contacts ADB or claims decodable pixels."""
        class Recorder:
            def __init__(self, output, **kwargs):
                self.folder = Path(output); self.context = kwargs['context']
                self.report = {'status': 'pending', 'recording_complete': False,
                               'evidence_scope': 'interface_only', 'context': self.context}
                events.append(('construct_recorder', self.context['index'], kwargs))

            def start(self):
                events.append(('start', self.context['index']))
                self.folder.mkdir()
                self.report['status'] = 'recording' if start_ok else 'failed'
                write(self.folder / 'recording.json', self.report)
                return start_ok

            def anchor(self, phase, **kwargs):
                events.append(('anchor', self.context['index'], phase, kwargs))
                if anchor_error:
                    raise OSError('synthetic media anchor failure')

            def stop(self, *, natural_terminal, native_result, context):
                events.append(('stop', self.context['index'], natural_terminal, native_result, context))
                if stop_interrupt:
                    raise KeyboardInterrupt('synthetic cancellation during media finalization')
                if stop_error:
                    raise OSError('synthetic media stop failure')
                complete = bool(start_ok and natural_terminal and native_result.get('finalized'))
                self.report.update(status='complete' if complete else 'partial', recording_complete=complete)
                write(self.folder / 'recording.json', self.report)
                return deepcopy(self.report)

        return Recorder

    def test_recording_selection_is_checked_before_touching_runtime(self):
        for index, arguments in enumerate(({'record_video': True},
                {'rendered': True, 'record_video': True, 'record_game_indices': '2'},
                {'rendered': True, 'record_video': True, 'record_game_indices': '0,0'},
                {'rendered': True, 'record_video': True, 'record_game_indices': ''},
                {'rendered': True, 'record_video': True, 'record_game_indices': [True]},
                {'rendered': True, 'record_video': True, 'record_backend': 'unknown'},
                {'record_game_indices': '0'})):
            with self.subTest(arguments=arguments), patch.object(loop, 'runtime_preflight') as preflight:
                with self.assertRaises(ValueError):
                    loop.run_batch(self.root / f'bad-media-{index}', games=2, **arguments)
                preflight.assert_not_called()

    def test_selected_recording_starts_before_environment_and_owns_same_lease(self):
        events = []

        @contextmanager
        def lease():
            events.append(('lease_acquired',))
            try:
                yield
            finally:
                events.append(('lease_released',))

        class Backend(SyntheticFinalizingBackend):
            def __init__(self, **kwargs):
                events.append(('construct_environment', kwargs['rendered']))
                super().__init__(**kwargs)

            def reset(self, initialization):
                events.append(('reset', initialization['seed']))
                return super().reset(initialization)

        with patch('tools.cloud.native_media.NativeMediaRecorder', self.recorder(events)):
            report = self.batch(games=2, rendered=True, record_video=True, backend=Backend, lease=lease())
        self.assertEqual([event[0] for event in events[:4]],
                         ['lease_acquired', 'construct_recorder', 'start', 'construct_environment'])
        self.assertEqual(events[-1], ('lease_released',))
        self.assertEqual([event for event in events if event[0] == 'construct_environment'],
                         [('construct_environment', True), ('construct_environment', True)])
        stops = [event for event in events if event[0] == 'stop']
        self.assertEqual(len(stops), 1)
        self.assertTrue(stops[0][2]); self.assertTrue(stops[0][3]['finalized'])
        phases = [event[2] for event in events if event[0] == 'anchor']
        self.assertEqual(phases, ['reset_returned', 'transition', 'natural_terminal'])
        self.assertEqual(report['games'][0]['media']['status'], 'complete')
        self.assertEqual(report['games'][1]['media']['status'], 'not_requested')
        self.assertEqual(report['record_game_indices'], [0])
        self.assertTrue(report['media_complete'])
        self.assertFalse(report['native_execution_verified'])

    def test_failed_recording_start_never_resets_a_game(self):
        events = []
        with patch('tools.cloud.native_media.NativeMediaRecorder', self.recorder(events, start_ok=False)), \
                patch('royale_phase3.native_env.NativePhase3Env') as environment:
            report = self.batch(games=1, rendered=True, record_video=True)
        environment.assert_not_called()
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['natural_terminal_games'], 0)
        self.assertFalse(report['media_complete'])
        self.assertEqual(report['stop_reason'], 'media_failure_before_reset')

    def test_media_failure_keeps_completed_numeric_results(self):
        for name, options in (('stop', {'stop_error': True}), ('anchor', {'anchor_error': True})):
            events = []
            with self.subTest(failure=name), patch('tools.cloud.native_media.NativeMediaRecorder',
                    self.recorder(events, **options)):
                report = self.batch(name=f'media-{name}', games=2, rendered=True, record_video=True,
                                    record_game_indices='0,1')
            self.assertEqual(report['status'], 'incomplete')
            self.assertEqual(report['natural_terminal_games'], 2)
            self.assertEqual(report['statuses'], {'completed': 2})
            self.assertEqual(report['consumed'], 2)
            self.assertFalse(report['media_complete'])
            self.assertTrue(all(not game['media']['recording_complete'] for game in report['games']))

    def test_interrupted_recording_is_stopped_and_retains_partial_receipt(self):
        events = []

        class InterruptedBackend(SyntheticFinalizingBackend):
            def advance_exact_ticks(self, ticks):
                raise KeyboardInterrupt('synthetic media cancellation; no native RPC')

        with patch('tools.cloud.native_media.NativeMediaRecorder', self.recorder(events)):
            with self.assertRaises(KeyboardInterrupt):
                self.batch(games=1, rendered=True, record_video=True, backend=InterruptedBackend)
        report = read(self.root / 'batch/report.json')
        active = read(report['active_game_ref'])
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual(active['status'], 'interrupted')
        self.assertFalse(active['media']['recording_complete'])
        stop = [event for event in events if event[0] == 'stop'][0]
        self.assertFalse(stop[2]); self.assertTrue(stop[4]['interrupted'])

    def test_cancellation_during_media_stop_preserves_terminal_game_and_closes_environment(self):
        events = []

        class Backend(SyntheticFinalizingBackend):
            def close(self):
                events.append(('environment_closed',))
                super().close()

        @contextmanager
        def lease():
            try:
                yield
            finally:
                events.append(('lease_released',))

        with patch('tools.cloud.native_media.NativeMediaRecorder', self.recorder(events, stop_interrupt=True)):
            with self.assertRaisesRegex(KeyboardInterrupt, 'during media finalization'):
                self.batch(games=1, rendered=True, record_video=True, backend=Backend, lease=lease())
        report = read(self.root / 'batch/report.json')
        result = read(self.root / 'batch/game-000/result.json')
        self.assertEqual(report['status'], 'interrupted')
        self.assertEqual(report['natural_terminal_games'], 1)
        self.assertEqual(report['consumed'], 1)
        self.assertEqual(report['games'][0]['status'], 'completed')
        self.assertEqual(result['status'], 'completed')
        self.assertTrue(result['natural_terminal'])
        self.assertEqual(result['winner_owner'], 0)
        self.assertFalse(result['media']['recording_complete'])
        self.assertIn('KeyboardInterrupt', result['media']['stop_error'])
        self.assertEqual(events[-2:], [('environment_closed',), ('lease_released',)])
        self.assertTrue(list(rows(result['transitions']['path']))[0]['terminated'])
        self.assertFalse(read(self.root / 'batch/game-000/media/recording.json')['recording_complete'])

    def test_sparse_media_anchors_keep_consumption_and_terminal_without_changing_decisions(self):
        events = []

        class LongBackend(SyntheticFinalizingBackend):
            def reset(self, initialization):
                result = super().reset(initialization)
                self.terminal_tick = 360
                return result

        actions = self.root / 'sparse-actions.jsonl'
        actions.write_text('{"game_index":0,"tick":140,"action":'
                           '{"kind":"play_card","slot":0,"x":3500,"y":11000}}\n')
        with patch('tools.cloud.native_media.NativeMediaRecorder', self.recorder(events)):
            report = self.batch(games=1, max_tick=400, policy='manual', actions=actions,
                                rendered=True, record_video=True, backend=LongBackend)
        game = report['games'][0]
        anchors = [event for event in events if event[0] == 'anchor']
        transition_ticks = [event[3]['state']['tick'] for event in anchors if event[2] == 'transition']
        self.assertEqual(game['decisions'], 27)
        self.assertEqual(game['consumed'], 1)
        self.assertEqual(transition_ticks, [100, 150, 200, 300, 360])
        self.assertEqual([event[3]['state']['tick'] for event in anchors
                          if event[2] == 'natural_terminal'], [360])
        configuration = read(self.root / 'batch/configuration.json')
        self.assertEqual(configuration['control_ticks'], 10)
        self.assertEqual(configuration['recording']['anchor_interval_ticks'], 100)

    def test_frameoutput_selection_uses_native_frame_recorder_and_host_encoding_metadata(self):
        events = []
        fixture_module = ModuleType('tools.cloud.native_frame_media')
        fixture_module.NativeFrameRecorder = self.recorder(events)
        with patch.dict(sys.modules, {'tools.cloud.native_frame_media': fixture_module}), \
                patch('tools.cloud.native_media.NativeMediaRecorder') as android_encoder:
            report = self.batch(games=1, rendered=True, record_video=True, record_backend='frameoutput')
        android_encoder.assert_not_called()
        self.assertTrue(report['media_complete'])
        self.assertEqual([event[0] for event in events[:2]], ['construct_recorder', 'start'])
        configuration = read(self.root / 'batch/configuration.json')
        self.assertEqual(configuration['recording']['backend'], 'frameoutput')
        self.assertEqual(configuration['recording']['capture_source'], 'android_native_frameoutput_rgb')
        self.assertEqual(configuration['recording']['encoding'], 'host_ffmpeg')
        options = events[0][2]
        self.assertNotIn('segment_seconds', options)
        self.assertNotIn('size', options)
        self.assertNotIn('bitrate', options)

    def test_cli_defaults_to_frameoutput_recording(self):
        from tools import native_loop as cli
        with patch.object(cli, 'configure'), patch.object(cli, 'run_batch', return_value={'status': 'completed'}) as batch, \
                patch.object(cli, 'print'):
            self.assertEqual(cli.main(['evaluate', '--output', str(self.root / 'default-backend'),
                '--checkpoint', 'saved.json', '--rendered', '--record-video']), 0)
        self.assertEqual(batch.call_args.kwargs['record_backend'], 'frameoutput')

    def test_cli_passes_explicit_media_options_to_evaluation_only(self):
        from tools import native_loop as cli
        with patch.object(cli, 'configure'), patch.object(cli, 'run_batch', return_value={'status': 'completed'}) as batch, \
                patch.object(cli, 'print'):
            code = cli.main(['evaluate', '--output', str(self.root / 'cli'), '--checkpoint', 'saved.json',
                '--rendered', '--record-video', '--record-game-indices', '0,1', '--record-adb', 'adb',
                '--record-backend', 'screenrecord',
                '--record-adb-port', '5039', '--record-serial', '127.0.0.1:15556',
                '--record-root-shell', 'adbd', '--record-ffmpeg', 'ffmpeg', '--record-ffprobe', 'ffprobe'])
        self.assertEqual(code, 0)
        arguments = batch.call_args.kwargs
        self.assertTrue(arguments['rendered']); self.assertTrue(arguments['record_video'])
        self.assertTrue(arguments['paired']); self.assertEqual(arguments['record_game_indices'], '0,1')
        self.assertEqual(arguments['record_backend'], 'screenrecord')
        self.assertEqual(arguments['record_adb_port'], 5039)
        self.assertEqual(arguments['record_serial'], '127.0.0.1:15556')

    def test_missing_runtime_never_constructs_an_environment(self):
        with patch.object(loop, 'runtime_preflight', return_value={
                'ready': False, 'status': 'unavailable', 'reason': 'synthetic missing assets'}), \
                patch.object(loop, 'native_lock', return_value=nullcontext()), \
                patch('royale_phase3.native_env.NativePhase3Env') as env:
            report = loop.run_batch(self.root / 'missing', games=1)
        env.assert_not_called()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(report['native_execution_verified'])
        self.assertEqual(report['games'], [])

    def test_pipeline_gate_cannot_report_native_success(self):
        with patch.object(loop, 'runtime_preflight', return_value={
                'ready': False, 'status': 'unavailable', 'reason': 'synthetic missing assets'}), \
                patch.object(loop, '_child') as child:
            report = loop.run_loop(self.root / 'pipeline')
        child.assert_not_called()
        self.assertEqual(report['status'], 'unavailable')
        self.assertFalse(report['native_closed_loop_verified'])

    def test_actual_before_and_after_inputs_and_consumption_are_preserved(self):
        report = self.batch(games=1)
        game = report['games'][0]
        transition = list(rows(game['transitions']['path']))[0]
        self.assertEqual(transition['actor_input']['tick'], 90)
        self.assertEqual(transition['next_actor_input']['tick'], 100)
        self.assertEqual(transition['planned_tick'], 91)
        self.assertEqual(transition['actual_execution_tick'], 91)
        self.assertTrue(transition['card_consumed'])
        self.assertTrue(transition['terminated'])
        self.assertFalse(transition['truncated'])
        self.assertEqual(transition['reward'], 1.)
        self.assertIsNone(transition['actual_deployment_point'])
        self.assertEqual(transition['actor_input']['previous_action'], 0)
        self.assertGreater(transition['next_actor_input']['previous_action'], 0)
        self.assertFalse(report['native_execution_verified'])
        self.assertEqual(report['evidence_scope'], 'interface_only')

    def test_rejected_request_is_never_a_supervised_wait(self):
        actions = self.root / 'manual.jsonl'
        actions.write_text('{"game_index":0,"tick":90,"action":{"action_index":5}}\n')
        with patch.object(loop, 'runtime_preflight', return_value={'ready': True}), \
                patch.object(loop, 'native_lock', return_value=nullcontext()), \
                patch('royale_phase2.backend.NativeBattleBackend', SyntheticFinalizingBackend), \
                patch('royale_phase2.backend._read_locked_elixir_rates', return_value={}), \
                patch('royale_phase3.native_env.match', return_value={'battle': {}}):
            report = loop.run_batch(self.root / 'manual', games=1, policy='manual',
                                    actions=actions, max_tick=100, opponent='wait')
        transition = list(rows(report['games'][0]['transitions']['path']))[0]
        self.assertEqual(transition['execution']['action'], {'kind': 'wait'})
        self.assertIsNotNone(transition['execution']['boundary_rejection'])
        self.assertFalse(transition['loss_mask'])
        self.assertFalse(transition['card_consumed'])

    def test_watchdog_is_not_a_natural_game(self):
        report = self.batch(games=1, max_tick=97)
        self.assertEqual(report['status'], 'incomplete')
        self.assertEqual(report['natural_terminal_games'], 0)
        self.assertFalse(report['native_execution_verified'])
        game = report['games'][0]
        transition = list(rows(game['transitions']['path']))[0]
        self.assertFalse(transition['terminated'])
        self.assertTrue(transition['truncated'])
        self.assertEqual(transition['transition_dt_ticks'], 7)

    def test_external_interrupt_preserves_count_or_time_bounded_partial_progress(self):
        for interruption_step, clock_increment, expected_decisions in ((26, 0., 25), (2, 6., 1)):
            with self.subTest(interruption_step=interruption_step):
                clock = [0.]

                class InterruptedBackend(MockNativeBackend):
                    def reset(self, initialization):
                        self.calls = 0
                        return super().reset(initialization)

                    def advance_exact_ticks(self, ticks):
                        # NativePhase3Env observes the injected tick, then the
                        # rest of the control interval as a second call.
                        if ticks == 1:
                            self.calls += 1
                            if self.calls == interruption_step:
                                raise KeyboardInterrupt('synthetic external interruption; zero native RPC')
                            clock[0] += clock_increment
                        return super().advance_exact_ticks(ticks)

                name = f'interrupted-{interruption_step}'
                with patch.object(loop.time, 'monotonic', side_effect=lambda: clock[0]):
                    with self.assertRaises(KeyboardInterrupt):
                        self.batch(name=name, games=1, max_tick=7200, backend=InterruptedBackend)
                report = read(self.root / name / 'report.json')
                self.assertEqual(report['status'], 'running')
                self.assertEqual(report['games'], [])
                self.assertFalse(report['native_execution_verified'])
                result = read(report['active_game_ref'])
                transitions = list(rows(self.root / name / 'game-000/transitions.jsonl'))
                self.assertEqual(result['decisions'], expected_decisions)
                self.assertEqual(len(transitions), expected_decisions)
                self.assertEqual(result['last_tick'], transitions[-1]['next_actor_input']['tick'])
                self.assertEqual(result['consumed'], sum(row['card_consumed'] for row in transitions))
                self.assertEqual(result['wall_seconds'], expected_decisions * clock_increment)
                self.assertEqual(result['status'], 'running')
                self.assertFalse(result['natural_terminal'])
                self.assertNotIn('finished', result)

    def test_final_report_removes_active_reference_and_counts_one_game_once(self):
        class LongFinalizingBackend(SyntheticFinalizingBackend):
            def reset(self, initialization):
                result = super().reset(initialization)
                self.terminal_tick = 1100
                return result

        with patch.object(loop, 'print') as printed:
            report = self.batch(games=1, max_tick=1100, backend=LongFinalizingBackend)
        self.assertEqual(report['status'], 'completed')
        self.assertNotIn('active_game_ref', report)
        self.assertNotIn('active_game_ref', read(self.root / 'batch/report.json'))
        self.assertEqual(report['attempted_games'], 1)
        self.assertEqual(report['natural_terminal_games'], 1)
        self.assertEqual(report['win_denominator'], 1)
        self.assertEqual(len(report['games']), 1)
        game = report['games'][0]
        trajectory = list(rows(game['transitions']['path']))
        self.assertEqual(game['decisions'], 101)
        self.assertEqual(game['last_tick'], 1100)
        self.assertEqual(report['consumed'], sum(row['card_consumed'] for row in trajectory))
        progress = [call for call in printed.call_args_list if call.args[0]['status'] == 'running']
        self.assertEqual(len(progress), 1)
        self.assertEqual(progress[0].args[0]['decisions'], 100)
        self.assertEqual(progress[0].args[0]['last_tick'], 1090)
        self.assertEqual(progress[0].args[0]['consumed'], sum(row['card_consumed'] for row in trajectory[:100]))
        self.assertTrue(progress[0].kwargs['flush'])

    def test_export_whole_games_and_physically_isolates_heldout_rows(self):
        self.batch()
        exported = loop.export_trajectories(self.root / 'batch', self.root / 'dataset')
        all_rows, meta = load_dataset(exported['metadata']['path'])
        training_rows, training_meta = load_dataset(exported['training_metadata']['path'])
        self.assertEqual(len(all_rows), 5)
        self.assertEqual(len(training_rows), 4)
        self.assertEqual({r['split'] for r in training_rows}, {'train', 'validation'})
        self.assertEqual(training_meta['split_groups']['test'], [])
        self.assertEqual(len(meta['split_groups']['test']), 1)
        self.assertEqual(meta['feature_spec']['schema'], 'royale-phase3-player-vector.v1')
        self.assertEqual(meta['ticks_per_second'], 20)
        self.assertEqual(sum(r['action_index'] > 0 for r in all_rows), 5)
        self.assertTrue(all('reward' in r and 'discount' in r for r in all_rows))
        from tools.cloud.replay import ReplaySession
        replay=ReplaySession(metadata_path=exported['metadata']['path'])
        self.assertEqual(replay.catalog()['provenance']['source_scope'],'interface_only')
        self.assertTrue(replay.state()['observation']['hand'])
        self.assertEqual(replay.state()['native_rpc_calls'],0)

    def test_export_rejects_changed_trajectory_artifact(self):
        report = self.batch(games=1)
        target = ROOT / report['games'][0]['transitions']['path']
        target.write_text(target.read_text() + '\n')
        with self.assertRaisesRegex(ValueError, 'artifact changed'):
            loop.export_trajectories(self.root / 'batch', self.root / 'dataset')

    def test_semantically_duplicate_games_do_not_inflate_validation(self):
        report = self.batch(games=4)
        first = [item for item in report['games'][0]['artifacts']
                 if Path(item['path']).name == 'states.jsonl'][0]
        duplicate = report['games'][3]
        for index, item in enumerate(duplicate['artifacts']):
            if Path(item['path']).name == 'states.jsonl':
                target = ROOT / item['path']
                target.write_bytes((ROOT / first['path']).read_bytes())
                duplicate['artifacts'][index] = reference(target)
        report['games'][3]['semantic_trajectory_hash'] = report['games'][0]['semantic_trajectory_hash']
        write(self.root / 'batch/report.json', report)
        exported = loop.export_trajectories(self.root / 'batch', self.root / 'dataset')
        self.assertEqual(exported['counts']['validation'], {})
        self.assertEqual(exported['excluded_games'], [{'index': 3, 'reason': 'duplicate_semantic_game'}])
        with self.assertRaisesRegex(ValueError, 'Independent eligible'):
            loop.train_validation_view(exported['metadata']['path'], self.root / 'bad-training')

    def test_same_seed_swapped_seats_remain_in_one_split(self):
        self.batch(games=8, paired=True)
        exported = loop.export_trajectories(self.root / 'batch', self.root / 'dataset')
        data, meta = load_dataset(exported['metadata']['path'])
        self.assertEqual(len(data), 8)
        self.assertEqual(len(meta['split_groups']['validation']), 2)
        self.assertEqual(exported['counts']['validation']['rows'], 2)

    def test_export_rejects_unbacked_semantic_hash_and_mixed_version(self):
        report = self.batch(games=1)
        report['games'][0]['semantic_trajectory_hash'] = 'incorrect-hash'
        write(self.root / 'batch/report.json', report)
        with self.assertRaisesRegex(ValueError, 'semantic trajectory hash'):
            loop.export_trajectories(self.root / 'batch', self.root / 'bad-hash')
        report = self.batch(name='other', games=1)
        game = report['games'][0]
        transition = list(rows(game['transitions']['path']))[0]
        transition['version_fingerprint'] = 'different-native-build'
        path = ROOT / game['transitions']['path']
        path.write_text('')
        loop.append(path, transition)
        game['transitions'] = reference(path)
        write(self.root / 'other/report.json', report)
        with self.assertRaisesRegex(ValueError, 'version differs'):
            loop.export_trajectories(self.root / 'other', self.root / 'bad-version')

    def test_stage_exception_has_a_terminal_pipeline_report(self):
        with patch.object(loop, 'runtime_preflight', return_value={'ready': True}), \
                patch.object(loop, '_child', return_value={'returncode': 0}), \
                patch.object(loop, 'export_trajectories', side_effect=ValueError('synthetic invalid export')):
            report = loop.run_loop(self.root / 'pipeline-failed')
        self.assertEqual(report['status'], 'failed')
        failure = read(report['failure']['path'])
        self.assertIn('synthetic invalid export', failure['error'])
        self.assertFalse(report['native_closed_loop_verified'])

    def test_native_lease_failure_has_a_terminal_batch_report(self):
        with patch.object(loop, 'native_lock', side_effect=RuntimeError('synthetic second controller')):
            report = loop.run_batch(self.root / 'lease-failed', games=1)
        self.assertEqual(report['status'], 'failed')
        self.assertIn('second controller', read(report['failure']['path'])['error'])

    def test_transition_validation_rejects_cross_episode_and_clock_mismatch(self):
        report = self.batch(games=1)
        transition = list(rows(report['games'][0]['transitions']['path']))[0]
        corrupt = deepcopy(transition)
        corrupt['next_actor_input']['episode_id'] = 'different-game'
        with self.assertRaisesRegex(ValueError, 'episode'):
            loop.validate_transition(corrupt)
        corrupt = deepcopy(transition)
        corrupt['actual_dt_seconds'] = .25
        with self.assertRaisesRegex(ValueError, 'clock'):
            loop.validate_transition(corrupt)

    def test_bad_budgets_rejected_before_touching_runtime(self):
        for arguments in ({'games': 0}, {'max_tick': 90}, {'wall_seconds': float('nan')}, {'seed': True}):
            with patch.object(loop, 'runtime_preflight') as preflight:
                with self.assertRaises(ValueError):
                    loop.run_batch(self.root / 'bad', **arguments)
                preflight.assert_not_called()

    @unittest.skipUnless(importlib.util.find_spec('torch'), 'Optional CPU Torch not installed')
    def test_real_tiny_optimization_uses_no_heldout_rows_or_native_rpc(self):
        self.batch()
        exported = loop.export_trajectories(self.root / 'batch', self.root / 'dataset')
        with patch.object(loop, 'runtime_preflight') as preflight:
            report = loop.train_offline(exported['metadata']['path'], self.root / 'model',
                                        epochs=1, hidden=4)
        preflight.assert_not_called()
        self.assertTrue(report['completed'])
        self.assertEqual(report['test_rows_supplied_to_learner'], 0)
        self.assertEqual(report['fresh_native_environment_steps'], 0)
        self.assertFalse(report['native_online_training_verified'])
        self.assertEqual(report['dataset_counts'], {'train': 3, 'validation': 1})
        from royale_phase3.learning import StatefulPolicy
        model = StatefulPolicy.load(report['checkpoint']['path'], 'offline-mock', 'fair')
        data, _ = load_dataset(report['physically_separate_training_metadata']['path'])
        self.assertIn(model.predict(data[0])['action_index'], range(5))


if __name__ == '__main__':
    unittest.main()

"""Research queue and HTTP contracts; fixtures make zero native calls."""
import http.client
import json
from pathlib import Path
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from royale_phase3 import research
from royale_phase3.common import ROOT, read, reference, write
from tools.cloud import research_app


class ResearchQueueTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='research-test-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.metadata = self.folder / 'metadata.json'
        self.payload = self.folder / 'rows.jsonl'
        self.payload.write_text('{}\n')
        write(self.metadata, {'fixture': True})
        self.data = {'path': str(self.metadata.relative_to(ROOT)),
                     'metadata': reference(self.metadata), 'rows': reference(self.payload),
                     'version_fingerprint_sha256': 'fixture-version', 'offline_only': False}
        self.service = research.ResearchService(self.folder / 'runs', lock_path=self.folder / 'queue.lock')
        self.addCleanup(self.service.close)

    def test_recording_selection_rejected_before_execution(self):
        with patch.object(research, '_dataset', return_value=self.data), \
                patch.object(self.service, '_run_child') as child:
            for indices in ([2], [0, 0], [True], '0', []):
                with self.subTest(indices=indices), self.assertRaises(ValueError):
                    self.service._resolve({'mode': 'full', 'record_game_indices': indices})
            config = self.service._resolve({'record_video': False})
            offline_default_recording = self.service._resolve({'mode': 'offline', 'record_video': True})
        self.assertEqual(config['record_game_indices'], [])
        self.assertFalse(config['rendered'])
        self.assertFalse(offline_default_recording['rendered'])
        child.assert_not_called()

    def test_portable_dataset_cannot_enter_native_evaluation(self):
        with patch.object(research, '_dataset', return_value={**self.data, 'offline_only': True}):
            with self.assertRaisesRegex(ValueError, 'offline-only'):
                self.service._resolve({'mode': 'full'})

    def test_disabled_recording_does_not_inherit_indices_outside_reduced_budget(self):
        with patch.dict(research.DEFAULTS, {'games': 4, 'record_game_indices': [3]}), \
                patch.object(research, '_dataset', return_value=self.data):
            config = self.service._resolve({'mode': 'offline', 'games': 2, 'record_video': False})
            self.assertEqual(config['record_game_indices'], [])
            with self.assertRaisesRegex(ValueError, 'record_game_indices'):
                self.service._resolve({'games': 2, 'record_video': False, 'record_game_indices': [3]})

    def test_numerical_evaluation_uses_headless_and_recording_uses_renderer(self):
        checkpoint = {'path': 'fixture-checkpoint', 'checkpoint_sha256': 'fixture'}
        arguments_seen = []

        def child(run_id, stage, arguments, env=None, allowed_returncodes=(0,)):
            folder = self.service._path(run_id)
            if stage == 'train':
                write(folder / 'model/report.json', {'completed': True, 'test_evaluated': False,
                                                     'checkpoint': checkpoint})
            elif stage == 'offline_validation':
                write(folder / 'offline_validation.json', {'status': 'completed', 'checkpoint': checkpoint,
                                                         'metrics': {'fixture': True}})
            elif stage == 'evaluate':
                arguments_seen.append(arguments)
                media = folder / 'fixture.webm'; media.write_bytes(b'not a real video; wiring fixture only')
                write(folder / 'evaluation/report.json', {'status': 'completed',
                    'natural_terminal_games': 2, 'native_execution_verified': True, 'media_complete': True})
                write(folder / 'evaluation/game-000/result.json', {'index': 0, 'owner': 0,
                    'status': 'completed', 'media': {'status': 'complete', 'recording_complete': True,
                                                    'path': str(media.relative_to(ROOT))}})
            else:
                self.fail('Unexpected fixture stage')

        with patch.object(research, '_dataset', return_value=self.data), \
                patch.object(research, '_checkpoint', return_value=checkpoint), \
                patch.object(self.service, '_run_child', side_effect=child), \
                patch('tools.cloud.research_runtime.ensure_runtime', return_value={
                    'connection': str(self.metadata), 'version_fingerprint': 'fixture-version'}) as prepare:
            with self.assertRaisesRegex(ValueError, 'record_video must be true'):
                self.service._resolve({'mode': 'full', 'record_video': False})
            for mode, recording in (('evaluate', False), ('evaluate', True), ('full', True)):
                with self.subTest(mode=mode, recording=recording):
                    request = {'mode': mode, 'record_video': recording}
                    if mode == 'evaluate':
                        request['checkpoint'] = 'fixture'
                    run = self.service.submit(request)
                    deadline = time.monotonic() + 5
                    while time.monotonic() < deadline:
                        value = self.service.get_run(run['run_id'])
                        if value['status'] in research.TERMINAL:
                            break
                        time.sleep(.02)
                    self.assertEqual(value['status'], 'completed', value.get('error'))
                    self.assertEqual(value['config']['rendered'], recording)
                    self.assertEqual('--rendered' in arguments_seen[-1], recording)
                    self.assertEqual('--record-video' in arguments_seen[-1], recording)
                    self.assertEqual(prepare.call_args.kwargs['required_mode'], None if recording else 'headless')

    def test_restart_marks_work_interrupted_without_reexecution(self):
        self.service.close()
        run_id = 'run-20261107t000000000000z-12345678'
        folder = self.folder / 'runs' / run_id
        write(folder / 'run.json', {'run_id': run_id, 'created_at': 'fixture', 'status': 'running'})
        self.service = research.ResearchService(self.folder / 'runs', lock_path=self.folder / 'queue.lock')
        self.addCleanup(self.service.close)
        record = read(folder / 'run.json')
        self.assertEqual(record['status'], 'interrupted')
        self.assertIn('no automatic retry', record['error'])
        self.assertIsNone(self.service._active)

    def test_media_failure_preserves_natural_numerical_result(self):
        checkpoint = {'path': 'fixture-checkpoint', 'checkpoint_sha256': 'fixture'}

        def child(run_id, stage, arguments, env=None, allowed_returncodes=(0,)):
            folder = self.service._path(run_id)
            if stage == 'offline_validation':
                write(folder / 'offline_validation.json', {'status': 'completed', 'checkpoint': checkpoint,
                                                         'metrics': {'fixture': True}})
            elif stage == 'evaluate':
                self.assertEqual(allowed_returncodes, (0, 2))
                write(folder / 'evaluation/report.json', {'status': 'incomplete',
                    'natural_terminal_games': 2, 'native_execution_verified': True})
                write(folder / 'evaluation/game-000/result.json', {'index': 0, 'owner': 0,
                    'status': 'completed', 'media': {'recording_complete': False, 'status': 'failed'}})
            else:
                self.fail('Unexpected fixture stage')

        with patch.object(research, '_dataset', return_value=self.data), \
                patch.object(research, '_checkpoint', return_value=checkpoint), \
                patch.object(self.service, '_run_child', side_effect=child), \
                patch('tools.cloud.research_runtime.ensure_runtime', return_value={
                    'connection': str(self.metadata), 'version_fingerprint': 'fixture-version'}):
            run = self.service.submit({'mode': 'evaluate', 'checkpoint': 'fixture'})
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                value = self.service.get_run(run['run_id'])
                if value['status'] in research.TERMINAL:
                    break
                time.sleep(.02)
        self.assertEqual(value['status'], 'failed')
        self.assertTrue(value['native_execution_verified'])
        self.assertFalse(value['native_media_recording_complete'])
        self.assertIn('video did not complete', value['error'])
        self.assertEqual(value['summary']['native_evaluation']['natural_terminal_games'], 2)


class ResearchApplicationIdentityTests(unittest.TestCase):
    def test_changed_backend_rejects_live_service_with_old_source_identity(self):
        # Isolate source edits and the handshake: do not contact a live portal or
        # modify the implementation files currently used by another executor.
        (ROOT / '.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='research-identity-') as temporary:
            fixture_root = Path(temporary)
            for name in research_app.IMPLEMENTATION_SOURCES:
                source = fixture_root / name
                source.parent.mkdir(parents=True, exist_ok=True)
                source.write_bytes(b'identity fixture\n')
            with patch.object(research_app, 'ROOT', fixture_root):
                before = research_app.application_identity('outputs/research')
                health = {**before, 'pid': 123, 'instance_id': 'fixture-instance',
                          'process_starttime': '456', 'status': 'ready'}
                with patch.object(research_app, 'client_request', return_value=health), \
                        patch.object(research_app, 'process_identity', return_value='456'):
                    self.assertEqual(research_app.probe_existing(8770, 'outputs/research'), health)
                    backend = fixture_root / 'royale_phase3/research.py'
                    backend.write_bytes(b'changed research executor fixture\n')
                    after = research_app.application_identity('outputs/research')
                    self.assertNotEqual(before['assets_sha256'], after['assets_sha256'])
                    self.assertEqual(before['workspace_fingerprint'], after['workspace_fingerprint'])
                    with self.assertRaisesRegex(ValueError, '同版本研究服务'):
                        research_app.probe_existing(8770, 'outputs/research')


class ResearchHttpTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='research-http-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.video = self.folder / 'run-fixture' / 'video.mp4'
        self.video.parent.mkdir()
        self.video.write_bytes(b'0123456789')  # Byte-range fixture, never a real video claim.
        video = self.video

        class Service:
            mutations = 0
            def catalog(self): return {'fixture': True}
            def list_runs(self): return []
            def get_run(self, run_id):
                return {'media': [{'id': 'video-000', 'path': str(video.relative_to(ROOT))}]}
            def submit(self, body):
                self.mutations += 1
                return {'run_id': 'run-fixture'}

        self.service = Service()
        self.server = research_app.make_server(self.service, port=0,
                                              run_root=str(self.folder.relative_to(ROOT)))
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.cleanup_server)

    def cleanup_server(self):
        self.server.shutdown(); self.server.server_close(); self.thread.join(timeout=3)

    def request(self, method, path, data=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        try:
            body = None if data is None else json.dumps(data)
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def test_readonly_range_seek_and_head(self):
        route = '/api/research/runs/run-fixture/media/video-000'
        status, headers, body = self.request('GET', route, headers={'Range': 'bytes=3-6'})
        self.assertEqual((status, body), (206, b'3456'))
        self.assertEqual(headers['Content-Range'], 'bytes 3-6/10')
        self.assertEqual(self.request('HEAD', route)[2], b'')
        self.assertEqual(self.request('GET', route, headers={'Range': 'bytes=99-'})[0], 416)
        self.assertEqual(self.request('GET', route, headers={'Range': 'bytes=-3'})[2], b'789')
        self.assertEqual(self.service.mutations, 0)

    def test_session_token_and_origin_before_mutation(self):
        route = '/api/research/runs'
        self.assertEqual(self.request('POST', route, {})[0], 403)
        session = json.loads(self.request('GET', '/api/research/session')[2])
        headers = {'X-Research-Token': session['csrf_token'], 'Content-Type': 'application/json',
                   'Origin': 'https://invalid.example'}
        self.assertEqual(self.request('POST', route, {}, headers)[0], 403)
        self.assertEqual(self.service.mutations, 0)
        headers.pop('Origin')
        self.assertEqual(self.request('POST', route, {}, headers)[0], 202)
        self.assertEqual(self.service.mutations, 1)

    def test_media_symlink_is_rejected(self):
        original = self.video.with_name('original.mp4')
        self.video.rename(original)
        self.video.symlink_to(original.name)
        self.assertEqual(self.request('GET', '/api/research/runs/run-fixture/media/video-000')[0], 400)


if __name__ == '__main__':
    unittest.main()

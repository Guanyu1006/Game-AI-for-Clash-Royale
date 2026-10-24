"""Actual loopback HTTP transport with a fake, inert native session only."""
import http.client
import base64
import json
from pathlib import Path
import struct
import tempfile
import threading
import unittest
from unittest.mock import patch
import zlib

from tools.cloud import native_browser as browser


def fixture_png():
    """Tiny synthetic transport fixture, never a native render fallback."""
    def chunk(kind, value):
        return (struct.pack('>I', len(value)) + kind + value
                + struct.pack('>I', zlib.crc32(kind + value) & 0xffffffff))
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(b'\0\x80\x40\x20\xff')) + chunk(b'IEND', b''))


class FakeNativeSession:
    """Queue submissions never execute; HTTP cannot touch an engine fixture."""
    csrf_token = 'test-native-session-token'

    def __init__(self, folder):
        self.folder = folder
        self.png = fixture_png()
        self.submissions = []
        self.frame_requests = []
        self.archive_lookups = []
        self.raise_on_submit = None
        self.frame_error = None
        self.known = folder / 'archive-fixture'
        self.known.mkdir()
        (self.known / 'animation.png').write_bytes(self.png)

    def public(self):
        return {'csrf_token': self.csrf_token, 'ready': True, 'busy': False, 'mode': 'live',
                'tick': 90, 'generation': 1,
                'jobs': {f'job-{i + 1}': {'command': row[0], 'status': 'queued'}
                         for i, row in enumerate(self.submissions)}}

    def latest_frame(self):
        if self.frame_error:
            raise self.frame_error
        return self.png

    def archives(self):
        return [{'archive_id': 'archive-fixture', 'frame_count': 2}]

    def _archive(self, archive_id):
        self.archive_lookups.append(archive_id)
        if archive_id != 'archive-fixture':
            raise ValueError('没有此档案')
        return self.known

    def manifest(self, archive_id):
        self._archive(archive_id)
        return {'archive_id': archive_id, 'frame_count': 2,
                'frames': [{'index': 0, 'tick': 90}, {'index': 1, 'tick': 100}]}

    def archive_frame(self, archive_id, index):
        self._archive(archive_id)
        self.frame_requests.append((archive_id, index))
        if type(index) is not int or not 0 <= index < 2:
            raise ValueError('帧序号超出范围')
        return self.png

    def submit(self, command, data):
        if self.raise_on_submit:
            raise self.raise_on_submit
        self.submissions.append((command, data))
        return f'job-{len(self.submissions)}'


class CloudNativeHTTPTests(unittest.TestCase):
    def setUp(self):
        base = browser.local('.tmp/cloud-native-http')
        base.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=base)
        self.session = FakeNativeSession(Path(self.temp.name))
        self.sources = Path(self.temp.name) / 'sources'
        self.server = browser.make_server(self.session, port=0, source_root=self.sources)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=3)
        self.temp.cleanup()

    def request(self, path, *, method='GET', data=None, body=None, headers=None):
        connection = http.client.HTTPConnection('127.0.0.1', self.server.server_port, timeout=5)
        values = {} if headers is None else dict(headers)
        if data is not None:
            body = json.dumps(data, ensure_ascii=False).encode('utf-8')
            values.setdefault('Content-Type', 'application/json')
        try:
            connection.request(method, path, body=body, headers=values)
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def post(self, path, data=None, *, headers=None, body=None):
        values = {'X-Native-Token': self.session.csrf_token, 'Content-Type': 'application/json'}
        values.update(headers or {})
        return self.request(path, method='POST', data=data, body=body, headers=values)

    def test_read_routes_png_bytes_and_cache_headers(self):
        for path in ('/', '/native'):
            status, headers, payload = self.request(path)
            self.assertEqual(status, 200)
            self.assertIn('原生对局', payload.decode())
            self.assertTrue(headers['Content-Type'].startswith('text/html'))
        status, headers, payload = self.request('/api/native/frame.png')
        self.assertEqual((status, payload), (200, self.session.png))
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(headers['Content-Length'], str(len(self.session.png)))
        self.assertEqual(headers['Cache-Control'], 'no-store')
        self.assertEqual(headers['X-Content-Type-Options'], 'nosniff')
        self.assertNotIn('Access-Control-Allow-Origin', headers)
        status, _, payload = self.request('/api/native/state')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['tick'], 90)
        self.assertEqual(self.session.submissions, [])

    def test_all_mutations_are_post_only_and_queued_once(self):
        routes = {'reset': 'reset', 'play': 'play', 'step': 'step', 'run': 'run',
                  'pause': 'pause', 'save': 'save', 'replay/open': 'replay_open', 'replay/seek': 'replay_seek'}
        for route, command in routes.items():
            path = '/api/native/' + route
            status, _, _ = self.request(path)
            self.assertEqual(status, 404)
            before = len(self.session.submissions)
            payload = {'fixture': route, 'tick': 90}
            status, _, raw = self.post(path, payload)
            response = json.loads(raw)
            self.assertEqual(status, 202, response)
            self.assertTrue(response['accepted'])
            self.assertEqual(len(self.session.submissions), before + 1)
            self.assertEqual(self.session.submissions[-1], (command, payload))
            self.assertEqual(response['state']['tick'], 90)
            self.assertEqual(response['state']['jobs'][response['job_id']]['status'], 'queued')
        for method in ('PUT', 'DELETE', 'PATCH', 'OPTIONS', 'HEAD'):
            before = len(self.session.submissions)
            status, _, _ = self.request('/api/native/reset', method=method)
            self.assertIn(status, (405, 501))
            self.assertEqual(len(self.session.submissions), before)

    def test_public_source_import_and_open_use_saved_catalog_only(self):
        raw = '{ "battle": {}, "events": [] }\n'
        def imported(payload, output, **options):
            self.assertEqual(payload, {'battle': {}, 'events': []})
            self.assertEqual(options['source_bytes'], raw.encode())
            self.assertEqual(options['filename'], 'example.json')
            output.mkdir(parents=True)
            (output / 'plan.json').write_text('{}')
            report = {'status': 'imported', 'source_summary': {'event_count': 0}, 'assumptions': []}
            (output / 'report.json').write_text(json.dumps(report))
            return report
        with patch('royale_phase2.public_replay.import_public_replay', side_effect=imported):
            status, _, payload = self.post('/api/native/source/import',
                {'text': raw, 'filename': '../example.json'})
        self.assertEqual(status, 201, payload)
        source_id = json.loads(payload)['source_id']
        status, _, payload = self.request('/api/native/sources')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['sources'][0]['source_id'], source_id)
        self.assertEqual(self.session.submissions, [], 'Import is file-only, never a native reset')
        status, _, payload = self.post('/api/native/source/open', {'source_id': source_id})
        self.assertEqual(status, 202, payload)
        command, data = self.session.submissions[-1]
        self.assertEqual(command, 'source_open')
        self.assertEqual(data['plan_path'], (self.sources / source_id / 'plan.json').relative_to(browser.ROOT).as_posix())
        self.assertEqual(self.post('/api/native/source/seek', {'tick': 100})[0], 202)
        self.assertEqual(self.session.submissions[-1], ('source_seek', {'tick': 100}))

    def test_public_source_import_limits_and_path_boundary(self):
        self.assertEqual(self.post('/api/native/source/import', {'text': '{}'},
                                  headers={'X-Native-Token': ''})[0], 403)
        self.assertEqual(self.post('/api/native/source/import', {'text': 'invalid'})[0], 400)
        self.assertEqual(self.post('/api/native/source/import', {'text': ' ' * (2*1024*1024+1)})[0], 400)
        for source_id in ('../README.md', '/README.md', 'missing', None):
            self.assertEqual(self.post('/api/native/source/open', {'source_id': source_id})[0], 400)
        self.assertEqual(self.session.submissions, [])

    def test_public_source_binary_upload_preserves_original_bom_and_newlines(self):
        raw = b'\xef\xbb\xbf{ "events": [] }\r\n'
        with patch('royale_phase2.public_replay.import_public_replay',
                   return_value={'status': 'imported'}) as imported:
            status, _, payload = self.post('/api/native/source/import',
                {'raw_base64': base64.b64encode(raw).decode(), 'filename': 'bom.json'})
        self.assertEqual(status, 201, payload)
        self.assertEqual(imported.call_args.args[0], {'events': []})
        self.assertEqual(imported.call_args.kwargs['source_bytes'], raw)
        self.assertEqual(self.session.submissions, [])
        self.assertEqual(self.post('/api/native/source/import', {'raw_base64': 'not base64!'})[0], 400)

    def test_mutation_requires_token_origin_json_object_and_bounded_length(self):
        cases = [
            ({'X-Native-Token': ''}, b'{}', 403),
            ({'X-Native-Token': 'wrong'}, b'{}', 403),
            ({'Origin': 'https://attacker.invalid'}, b'{}', 403),
            ({'Origin': 'null'}, b'{}', 403),
            ({'Content-Type': 'text/plain'}, b'{}', 415),
            ({}, b'', 400),
            ({}, b'not json', 400),
            ({}, b'[]', 400),
            ({}, b'null', 400),
            ({}, b'"text"', 400),
            ({}, b'x' * 16385, 400),
            ({'Content-Length': '-1'}, b'', 400),
        ]
        for headers, body, expected in cases:
            with self.subTest(headers=headers, length=len(body)):
                status, _, payload = self.post('/api/native/reset', headers=headers, body=body)
                self.assertEqual(status, expected, payload)
        self.assertEqual(self.session.submissions, [])
        # A CLI without an Origin remains supported when it has the session token.
        self.assertEqual(self.post('/api/native/step', {})[0], 202)

    def test_same_origin_and_exact_codespaces_proxy_origin(self):
        origin = f'http://127.0.0.1:{self.server.server_port}'
        self.assertEqual(self.post('/api/native/step', {}, headers={'Origin': origin})[0], 202)
        with patch.dict('os.environ', {'CODESPACES': 'true', 'CODESPACE_NAME': 'fixture-workspace',
                                       'GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN': 'app.github.dev'}):
            forwarded = f'https://fixture-workspace-{self.server.server_port}.app.github.dev'
            self.assertEqual(self.post('/api/native/step', {}, headers={'Origin': forwarded})[0], 202)
            before = len(self.session.submissions)
            for bad in (forwarded.replace('fixture-workspace', 'another-workspace'),
                        forwarded + '.attacker.invalid', forwarded.replace('https:', 'http:')):
                self.assertEqual(self.post('/api/native/step', {}, headers={'Origin': bad})[0], 403)
            self.assertEqual(len(self.session.submissions), before)

    def test_host_boundary_accepts_local_and_exact_proxy_host_only(self):
        for host in (f'localhost:{self.server.server_port}', f'127.0.0.1:{self.server.server_port}'):
            self.assertEqual(self.request('/api/native/state', headers={'Host': host})[0], 200)
        for host in ('localhost', '127.0.0.1:1', f'localhost.attacker.invalid:{self.server.server_port}',
                     f'attacker@127.0.0.1:{self.server.server_port}', '127.0.0.1:invalid'):
            with self.subTest(host=host):
                self.assertEqual(self.request('/api/native/state', headers={'Host': host})[0], 403)
        with patch.dict('os.environ', {'CODESPACES': 'true', 'CODESPACE_NAME': 'fixture-workspace',
                                       'GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN': 'app.github.dev'}):
            host = f'fixture-workspace-{self.server.server_port}.app.github.dev'
            origin = 'https://' + host
            self.assertEqual(self.request('/api/native/state', headers={'Host': host})[0], 200)
            self.assertEqual(self.post('/api/native/save', {}, headers={'Host': host, 'Origin': origin})[0], 202)
            self.assertEqual(self.request('/api/native/state', headers={'Host': host + '.attacker.invalid'})[0], 403)

    def test_foreign_host_cannot_read_token_or_submit_even_with_matching_origin(self):
        status, _, _ = self.request('/api/native/state', headers={'Host': 'attacker.invalid'})
        self.assertEqual(status, 403, 'Foreign Host must not read a native session token')
        status, _, _ = self.post('/api/native/reset', {},
                                  headers={'Host': 'attacker.invalid', 'Origin': 'http://attacker.invalid'})
        self.assertEqual(status, 403, 'Matching attacker Host/Origin must not bypass the local boundary')
        self.assertEqual(self.session.submissions, [])

    def test_archives_manifest_frame_and_apng_urls(self):
        status, _, payload = self.request('/api/native/archives')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['archives'][0]['archive_id'], 'archive-fixture')
        status, _, payload = self.request('/api/native/archive/archive-fixture/manifest')
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)['frames'][-1]['tick'], 100)
        status, headers, payload = self.request('/api/native/archive/archive-fixture/frames/1.png')
        self.assertEqual((status, payload), (200, self.session.png))
        self.assertEqual(headers['Content-Type'], 'image/png')
        self.assertEqual(self.session.frame_requests, [('archive-fixture', 1)])
        status, headers, payload = self.request('/api/native/archive/archive-fixture/animation.png')
        self.assertEqual((status, payload), (200, self.session.png))
        self.assertEqual(headers['Content-Type'], 'image/apng')
        (self.session.known / 'animation.png').unlink()
        self.assertEqual(self.request('/api/native/archive/archive-fixture/animation.png')[0], 404)

    def test_unknown_routes_invalid_indices_and_repository_paths_never_expose_files(self):
        cases = {
            '/README.md': 404,
            '/api/native/README.md': 404,
            '/api/native/archive/archive-fixture/../../README.md': 404,
            '/api/native/archive/%2e%2e/manifest': 404,
            '/api/native/archive/archive-fixture/frames/../../README.md': 404,
            '/api/native/archive/archive-fixture/frames/-1.png': 404,
            '/api/native/archive/archive-fixture/frames/nope.png': 404,
            '/api/native/archive/archive-fixture/frames/999999999999999999999.png': 400,
            '/api/native/archive/unknown/manifest': 400,
            '/api/native/not-a-route': 404,
        }
        for path, expected in cases.items():
            with self.subTest(path=path):
                status, _, payload = self.request(path)
                self.assertEqual(status, expected, payload)
                self.assertIn('error', json.loads(payload))
        # A query cannot turn a fixed frame endpoint into arbitrary path serving.
        status, _, payload = self.request('/api/native/frame.png?path=../../README.md')
        self.assertEqual((status, payload), (200, self.session.png))
        self.assertEqual(self.post('/README.md', {})[0], 404)
        self.assertEqual(self.post('/api/native/state', {})[0], 404)
        self.assertEqual(self.session.submissions, [])

    def test_animation_symlink_cannot_serve_files_outside_its_archive(self):
        outside = self.session.folder / 'outside-archive.txt'
        outside.write_bytes(b'fixture sentinel outside the registered media archive')
        animation = self.session.known / 'animation.png'
        animation.unlink()
        animation.symlink_to(outside)
        status, _, payload = self.request('/api/native/archive/archive-fixture/animation.png')
        self.assertIn(status, (400, 403, 404))
        self.assertNotIn(outside.read_bytes(), payload)

    def test_worker_busy_invalid_command_and_capture_failure_return_errors_without_retries(self):
        self.session.raise_on_submit = ValueError('上一个原生操作仍在执行')
        status, _, raw = self.post('/api/native/play', {'slot': 0})
        self.assertEqual(status, 400)
        self.assertIn('仍在执行', json.loads(raw)['error'])
        self.assertEqual(self.session.submissions, [])
        self.session.frame_error = ValueError('尚未取得 Android 原生画面')
        self.assertEqual(self.request('/api/native/frame.png')[0], 400)
        self.session.frame_error = RuntimeError('fixture transport failed')
        status, _, raw = self.request('/api/native/frame.png')
        self.assertEqual(status, 503)
        self.assertIn('fixture transport failed', json.loads(raw)['error'])


if __name__ == '__main__':
    unittest.main()

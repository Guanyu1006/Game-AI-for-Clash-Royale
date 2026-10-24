"""Small local fixtures for pinned content recovery; no external HTTP or native RPC."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

from tools.cloud import native_content as content


class FakeResponse(io.BytesIO):
    def __init__(self, body, url):
        super().__init__(body)
        self.status = 200
        self.url = url
        self.headers = {'Content-Length': str(len(body))}
    def geturl(self):
        return self.url


class NativeContentTests(unittest.TestCase):
    def setUp(self):
        (content.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='native-content-', dir=content.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bodies = {}
        self.assets = []

    def manifest(self, count=1, **changes):
        self.assets = []
        self.bodies = {}
        for index in range(count):
            body = f'local interface fixture {index}'.encode()
            name = f'csv_logic/fixture-{index}.csv'
            asset = {'path': content.PREFIX + name, 'role': 'paired_content',
                     'bytes': len(body), 'sha256': content.sha256(body)}
            self.assets.append(asset)
            self.bodies[f'{content.BASE}/{content.CONTENT}/{name}'] = body
        data = {'schema': 'royale-phase2-version.v1', 'content_version': '15.535.86',
                'content_manifest_sha1': content.CONTENT, 'assets': self.assets, **changes}
        (self.root / 'manifest.json').write_text(json.dumps(data))
        return content.ContentRestorer('manifest.json', root=self.root)

    def opener(self, bodies=None):
        bodies = self.bodies if bodies is None else bodies
        return Mock(open=Mock(side_effect=lambda req, timeout: FakeResponse(bodies[req.full_url], req.full_url)))

    def target(self, index=0):
        return self.root / self.assets[index]['path']

    def test_inspect_missing_and_verified_files_never_constructs_transport(self):
        restorer = self.manifest()
        with patch('urllib.request.build_opener') as opener:
            report = restorer.inspect()
            self.assertEqual(report['missing'], 1)
            path = self.target(); path.parent.mkdir(parents=True); path.write_bytes(next(iter(self.bodies.values())))
            report = restorer.inspect()
        opener.assert_not_called()
        self.assertEqual(report['status'], 'ready_content')
        self.assertEqual(report['network_requests'], 0)
        self.assertFalse(report['native_execution_verified'])

    def test_fetch_cached_resource_requires_exact_hash_and_zero_requests(self):
        restorer = self.manifest()
        path = self.target(); path.parent.mkdir(parents=True); path.write_bytes(next(iter(self.bodies.values())))
        opener = self.opener()
        report = restorer.fetch('evidence', opener=opener)
        opener.open.assert_not_called()
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['network_requests'], 0)

    def test_matching_download_is_atomically_published_and_reopened(self):
        restorer = self.manifest(count=5)
        opener = self.opener()
        report = restorer.fetch('evidence', workers=2, opener=opener)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(report['verified'], 5)
        self.assertEqual(report['network_requests'], 5)
        self.assertEqual(restorer.inspect()['verified'], 5)
        self.assertTrue((self.root / 'evidence/report.json').is_file())

    def test_different_existing_file_is_preserved_without_requests(self):
        restorer = self.manifest(count=2)
        path = self.target(); path.parent.mkdir(parents=True); path.write_bytes(b'preserve original')
        opener = self.opener()
        report = restorer.fetch('evidence', opener=opener)
        self.assertEqual(report['status'], 'existing_conflict_preserved')
        self.assertEqual(path.read_bytes(), b'preserve original')
        opener.open.assert_not_called()
        self.assertFalse(self.target(1).exists())

    def test_hash_mismatch_is_retained_and_never_published(self):
        restorer = self.manifest()
        url, body = next(iter(self.bodies.items()))
        report = restorer.fetch('evidence', opener=self.opener({url: b'x' * len(body)}))
        self.assertNotEqual(report['status'], 'completed')
        self.assertFalse(self.target().exists())
        failed = report['records'][0]
        self.assertIn('SHA256', failed['error'])
        self.assertTrue(failed['retained_files'])

    def test_two_connection_failures_stop_before_any_bulk_transfer(self):
        restorer = self.manifest(count=12)
        opener = Mock(open=Mock(side_effect=urllib.error.URLError('local transport fixture')))
        report = restorer.fetch('evidence', opener=opener)
        self.assertEqual(report['status'], 'stopped_after_failures')
        self.assertEqual(opener.open.call_count, 2)
        self.assertEqual(report['network_requests'], 2)
        self.assertEqual(restorer.inspect()['verified'], 0)
        self.assertFalse(report['native_execution_verified'])

    def test_output_reuse_budget_and_worker_limits_fail_before_requests(self):
        restorer = self.manifest()
        (self.root / 'existing').mkdir()
        for output, options in [('existing', {}), ('one', {'max_mib': 161}),
                                ('two', {'workers': 3}), ('three', {'max_mib': True})]:
            opener = self.opener()
            with self.subTest(options=options):
                with self.assertRaises((ValueError, FileExistsError)):
                    restorer.fetch(output, opener=opener, **options)
                opener.open.assert_not_called()

    def test_unsafe_manifest_paths_and_missing_sha256_are_rejected(self):
        self.manifest()
        original = json.loads((self.root / 'manifest.json').read_text())
        for changed in ({'path': content.PREFIX + '../escape'},
                        {'sha256': 'not-a-strong-lock'},
                        {'bytes': content.MAX_FILE + 1}):
            data = {**original, 'assets': [{**original['assets'][0], **changed}]}
            (self.root / 'manifest.json').write_text(json.dumps(data))
            with self.subTest(changed=changed):
                with self.assertRaises(ValueError):
                    content.ContentRestorer('manifest.json', root=self.root)

    def test_fingerprint_representation_preserves_values_and_requires_both_locks(self):
        # Tiny representation fixture; production always uses fixed 3be42f/e0cb2f locks.
        source = {'files': [{'file': 'a/b', 'sha': 'fixture-sha1'}], 'version': '15.535.86', 'sha': content.CONTENT}
        derived = json.dumps(source, ensure_ascii=False, separators=(',', ':')).encode()
        raw = derived.replace(b'/', b'\\/')
        lock = content.FingerprintLock(content.sha256(raw), content.sha256(derived), len(derived), files=1)
        self.assertEqual(content.derive_fingerprint(raw, lock), derived)
        with self.assertRaisesRegex(ValueError, 'Raw fingerprint SHA256'):
            content.derive_fingerprint(raw + b'\n', lock)
        with self.assertRaisesRegex(ValueError, 'Derived fingerprint'):
            content.derive_fingerprint(raw, content.FingerprintLock(content.sha256(raw), '0' * 64, len(derived), files=1))
        with self.assertRaisesRegex(ValueError, 'Raw fingerprint SHA256'):
            content.derive_fingerprint(raw)  # Tiny fixtures cannot pass the production raw lock.

    def test_redirect_is_rejected_before_following_another_endpoint(self):
        with self.assertRaisesRegex(ValueError, 'redirects'):
            content.RejectRedirect().redirect_request(None, None, 302, None, None, 'http://other.invalid')


if __name__ == '__main__':
    unittest.main()

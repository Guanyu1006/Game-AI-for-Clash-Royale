"""Resource transport/identity tests use bytes fixtures, never game physics."""
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch
import zipfile

from tools.cloud import native_resources as resources


class Response(io.BytesIO):
    def __init__(self, body, status=200, headers=None):
        super().__init__(body)
        self.status = status
        self.headers = headers or {}
        self.url = 'https://example.invalid/pinned-resource'


class NativeResourcesTests(unittest.TestCase):
    def setUp(self):
        parent = resources.local('.tmp/native-resource-tests')
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.folder = Path(temporary.name)
        self.body = b'pinned resource bytes'
        self.destination = self.folder / 'artifact.bin'
        self.spec = {'url': 'https://example.invalid/pinned-resource',
                     'path': str(self.destination.relative_to(resources.ROOT)),
                     'bytes': len(self.body), 'sha256': hashlib.sha256(self.body).hexdigest()}
        self.partial = self.destination.with_name('artifact.bin.partial')

    def fetch(self, body=None, **options):
        response = Response(self.body if body is None else body, **options)
        opener = Mock(return_value=response)
        report = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        return report, opener

    def test_download_roundtrip_and_cached_validation(self):
        report, _ = self.fetch()
        self.assertTrue(report['success'])
        self.assertEqual(self.destination.read_bytes(), self.body)
        self.assertFalse(report['native_execution_verified'])
        opener = Mock(side_effect=AssertionError('cached download made network request'))
        cached = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        self.assertTrue(cached['success'])
        self.assertTrue(cached['cached'])
        self.destination.write_bytes(b'different')
        rejected = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        self.assertFalse(rejected['success'])
        self.assertEqual(self.destination.read_bytes(), b'different')
        opener.assert_not_called()

    def test_budget_containment_and_url_checked_before_network(self):
        opener = Mock(side_effect=AssertionError('preflight made network request'))
        for spec, budget in ((self.spec, len(self.body) - 1),
                             ({**self.spec, 'path': '../outside-native-resource.bin'}, len(self.body)),
                             ({**self.spec, 'url': 'http://example.invalid/file'}, len(self.body)),
                             ({**self.spec, 'url': 'https://user:secret@example.invalid/file'}, len(self.body))):
            with self.subTest(spec=spec):
                self.assertFalse(resources.fetch_resource(spec, budget, opener=opener)['success'])
        opener.assert_not_called()
        self.assertFalse(self.destination.exists())

    def test_range_resumption_verifies_offset_and_exact_identity(self):
        self.partial.write_bytes(self.body[:7])
        report, opener = self.fetch(self.body[7:], status=206,
                                    headers={'Content-Range': f'bytes 7-{len(self.body)-1}/{len(self.body)}'})
        self.assertTrue(report['success'])
        self.assertEqual(report['resume_mode'], 'http-range')
        self.assertEqual(opener.call_args.args[0].get_header('Range'), 'bytes=7-')
        self.assertEqual(self.destination.read_bytes(), self.body)

    def test_ignored_range_rechecks_prefix_and_keeps_different_partial(self):
        self.partial.write_bytes(self.body[:7])
        report, _ = self.fetch()
        self.assertTrue(report['success'])
        self.assertEqual(report['resume_mode'], 'verified-http-200-prefix')
        self.destination.unlink()
        self.partial.write_bytes(b'changed')
        report, _ = self.fetch()
        self.assertFalse(report['success'])
        self.assertEqual(self.partial.read_bytes(), b'changed')
        self.assertFalse(self.destination.exists())

    def test_wrong_range_total_rejected_without_append(self):
        self.partial.write_bytes(self.body[:7])
        for value in ('bytes 6-20/21', 'bytes 7-20/99', ''):
            with self.subTest(value=value):
                report, _ = self.fetch(self.body[7:], status=206, headers={'Content-Range': value})
                self.assertFalse(report['success'])
                self.assertEqual(self.partial.read_bytes(), self.body[:7])

    def test_hash_mismatch_stays_partial_and_is_not_redownloaded(self):
        report, _ = self.fetch(b'x' * len(self.body))
        self.assertFalse(report['success'])
        self.assertFalse(self.destination.exists())
        self.assertEqual(self.partial.read_bytes(), b'x' * len(self.body))
        opener = Mock(side_effect=AssertionError('complete mismatching partial redownloaded'))
        again = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        self.assertFalse(again['success'])
        opener.assert_not_called()

    def test_truncation_can_resume_but_oversize_cannot_be_promoted(self):
        truncated, _ = self.fetch(self.body[:7])
        self.assertFalse(truncated['success'])
        self.assertEqual(self.partial.read_bytes(), self.body[:7])
        resumed, _ = self.fetch()
        self.assertTrue(resumed['success'])
        self.destination.unlink()
        oversized, _ = self.fetch(self.body + b'extra')
        self.assertFalse(oversized['success'])
        self.assertFalse(self.destination.exists())

    def test_empty_partial_from_interrupted_request_can_retry(self):
        self.partial.write_bytes(b'')
        report, _ = self.fetch()
        self.assertTrue(report['success'])
        self.assertEqual(self.destination.read_bytes(), self.body)
        self.assertFalse(self.partial.exists())

    def test_concurrent_partial_change_is_preserved_without_overwrite(self):
        self.partial.write_bytes(b'')
        def changed_during_request(*args, **kwargs):
            self.partial.write_bytes(b'concurrent writer')
            return Response(self.body)
        report = resources.fetch_resource(self.spec, len(self.body), opener=changed_during_request)
        self.assertFalse(report['success'])
        self.assertIn('concurrently', report['error'])
        self.assertEqual(self.partial.read_bytes(), b'concurrent writer')
        self.assertFalse(self.destination.exists())

    def test_destination_symlink_cannot_escape_repository(self):
        self.destination.symlink_to('/tmp/outside-native-resource-test')
        opener = Mock(side_effect=AssertionError('escaped destination made network request'))
        result = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        self.assertFalse(result['success'])
        opener.assert_not_called()

    def test_partial_symlink_cannot_move_another_project_file(self):
        other = self.folder / 'keep-original.bin'
        other.write_bytes(self.body)
        self.partial.symlink_to(other)
        opener = Mock(side_effect=AssertionError('symlink partial made network request'))
        report = resources.fetch_resource(self.spec, len(self.body), opener=opener)
        self.assertFalse(report['success'])
        self.assertEqual(other.read_bytes(), self.body)
        self.assertTrue(self.partial.is_symlink())
        self.assertFalse(self.destination.exists())
        opener.assert_not_called()

    def test_component_restore_requires_each_hash_and_preserves_existing(self):
        archive = self.folder / 'unapproved-container.zip'
        base = 'third_party/phase2/firstlight/user-provided/base-assets/'
        destinations = [self.folder / name for name in ('matched', 'mismatch', 'preserved')]
        # The fixture remaps only local() for these synthetic locked paths.
        manifest = {'assets': [
            {'path': base + 'assets/' + name, 'role': 'pinned_logic_resource',
             'bytes': len(self.body), 'sha256': hashlib.sha256(self.body).hexdigest()}
            for name in ('matched', 'mismatch', 'preserved')]}
        manifest_path = self.folder / 'manifest.json'
        manifest_path.write_text(json.dumps(manifest))
        with zipfile.ZipFile(archive, 'w') as output:
            output.writestr('assets/matched', self.body)
            output.writestr('assets/mismatch', b'x' * len(self.body))
            output.writestr('assets/preserved', self.body)
        destinations[2].write_bytes(b'keep existing file')
        original_local = resources.local
        mapping = dict(zip((row['path'] for row in manifest['assets']), destinations))
        def fixture_local(path):
            return mapping.get(str(path), original_local(path))
        with patch.object(resources, 'local', side_effect=fixture_local):
            report = resources.extract_locked_apk_components(str(archive), manifest_path=str(manifest_path))
        self.assertEqual(report['matched_components'], 1)
        self.assertFalse(report['success'])
        self.assertFalse(report['archive_whole_identity_matches'])
        self.assertFalse(report['native_execution_verified'])
        self.assertEqual(destinations[0].read_bytes(), self.body)
        self.assertFalse(destinations[1].exists())
        self.assertEqual(destinations[2].read_bytes(), b'keep existing file')
        with self.assertRaisesRegex(ValueError, 'budget'):
            resources.extract_locked_apk_components(str(archive), manifest_path=str(manifest_path), max_bytes=1)

    def test_builtin_game_identities_match_locked_manifest(self):
        manifest = json.loads(resources.local(resources.MANIFEST).read_text())
        locked = {row['path']: row for row in manifest['assets']}
        for spec in resources.RESOURCES.values():
            self.assertEqual(spec['sha256'], locked[spec['path']]['sha256'])
            self.assertEqual(spec['bytes'], locked[spec['path']]['bytes'])

    def test_adb_budget_fails_before_network_or_extraction(self):
        with patch.object(resources, 'fetch_resource') as fetch:
            with self.assertRaisesRegex(ValueError, 'budget'):
                resources.install_adb(max_bytes=1)
        fetch.assert_not_called()


if __name__ == '__main__':
    unittest.main()

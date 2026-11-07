"""Static inventory parses source without executing imports or inspecting secrets."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from tools.cloud import research_inventory as inventory


class ResearchInventoryTests(unittest.TestCase):
    def setUp(self):
        parent = inventory.ROOT / '.tmp'; parent.mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent, prefix='inventory-test-')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)

    def put(self, path, content):
        target = self.root / path; target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding='utf-8')
        return path

    def test_static_graph_profiles_assets_and_secret_exclusions_without_execution(self):
        paths = [self.put('pkg/__init__.py', ''),
                 self.put('pkg/helper.py', 'raise RuntimeError("must never execute")\n'),
                 self.put('pkg/worker.py', 'from .helper import thing\nimport json\nimport numpy\n'
                          'import importlib\nimportlib.import_module("optional_backend")\n'
                          'import subprocess\nsubprocess.run(["python", "tools/task.py"])\n'),
                 self.put('tools/task.py', 'raise RuntimeError("must never execute")\n'),
                 self.put('requirements.lock', 'numpy==2.5.4\n'),
                 self.put('requirements-codespaces.txt', 'numpy==2.2.6\n'
                          'custom @ https://user:sensitive-token@example.invalid/custom.whl\n'),
                 self.put('pyproject.toml', '[project]\nname="fixture"\n'
                          'dependencies=["numpy>=2.2,<3"]\n'
                          '[project.optional-dependencies]\nresearch=["torch==2.6.0"]\n'),
                 self.put('pkg/credentials.py', 'raise RuntimeError("sensitive-token")\n'),
                 self.put('outputs/generated.py', 'raise RuntimeError("sensitive-token")\n'),
                 self.put('research/data/generated.py', 'raise RuntimeError("sensitive-token")\n'),
                 self.put('shared_assets/project/copy.py', 'raise RuntimeError("sensitive-token")\n')]
        self.put('third_party/asset.bin', 'fixed test asset')
        self.put('third_party/asset-no-size.bin', 'fixed test asset')
        self.put('research/runtime/version.json', json.dumps({'fingerprint': 'fixture-version',
                 'abi': 'arm64-v8a', 'ticks_per_second': 20, 'assets': [
                     {'path': 'third_party/asset.bin', 'role': 'fixture', 'bytes': 16, 'sha256': 'f' * 64},
                     {'path': 'third_party/asset-no-size.bin', 'role': 'fixture', 'sha256': 'e' * 64}]}))
        self.put('research/system.json', json.dumps({'schema': 'fixture-system',
                 'paths': {'native_manifest': 'research/runtime/version.json', 'runs': 'research/runs'},
                 'datasets': {}, 'reference_checkpoints': []}))
        (self.root / 'research/runs').mkdir()
        with patch.object(inventory, 'git_paths', return_value=paths), \
                patch.object(inventory, 'version', return_value='fixture-only'):
            result = inventory.build_inventory(self.root)
        self.assertEqual(result['summary']['source_files'], 4)
        graph = result['python']
        self.assertIn({'from': 'pkg/worker.py', 'to': 'pkg/helper.py'}, graph['local_edges'])
        worker = next(item for item in graph['modules'] if item['path'] == 'pkg/worker.py')
        self.assertEqual(worker['dynamic_imports'][0]['module_literal'], 'optional_backend')
        self.assertEqual(worker['process_calls'][0]['literal_author_script_targets'], ['tools/task.py'])
        self.assertEqual([item['name'] for item in graph['external_import_roots']], ['numpy'])
        self.assertTrue(any(item['name'] == 'json' for item in graph['stdlib_import_roots']))
        self.assertNotIn('sensitive-token', json.dumps(result))
        native = result['native_assets']
        self.assertEqual(native['asset_count'], 2)
        self.assertEqual(native['existing_files'], 2)
        self.assertEqual(native['assets'][0]['declared_sha256'], 'f' * 64)
        self.assertFalse(native['assets'][0]['content_hash_verified'])
        self.assertTrue(native['assets'][0]['size_matches_declared'])
        self.assertIsNone(native['assets'][1]['size_matches_declared'])
        self.assertEqual(native['declared_size_count'], 1)
        self.assertEqual(native['declared_bytes'], 16)
        self.assertEqual(native['known_stat_bytes'], 32)
        self.assertEqual(result['system']['paths']['runs']['kind'], 'directory')
        profiles = {item['path']: item for item in result['dependency_profiles']}
        self.assertEqual(profiles['requirements.lock']['scope'], 'historical_windows_independent')
        self.assertEqual(profiles['pyproject.toml']['optional_dependencies']['research'][0]['specifier'], '==2.6.0')

    def test_missing_current_manifest_never_searches_old_outputs(self):
        self.put('research/system.json', json.dumps({'paths': {'native_manifest': 'research/runtime/version.json'}}))
        self.put('outputs/old/version.json', json.dumps({'assets': [{'path': 'old.bin'}]}))
        result = inventory.configured_resources(self.root)['native_assets']
        self.assertEqual(result['status'], 'missing')
        self.assertFalse(result['historical_fallback_used'])
        self.assertEqual(result['assets'], [])

    def test_stdlib_name_is_not_confused_with_unique_project_module(self):
        result = inventory.analyze_python({'pkg/platform.py': b'', 'main.py': b'import platform\n'})
        imported = next(item for item in result['modules'] if item['path'] == 'main.py')['imports'][0]
        self.assertEqual(imported['classification'], 'stdlib')
        self.assertEqual(result['local_edges'], [])

    def test_output_containment_refusal_and_owned_refresh(self):
        current = {'schema': inventory.SCHEMA, 'value': 1}
        self.assertEqual(inventory.save_inventory(self.root, 'review/map.json', current), 'created')
        self.assertEqual(inventory.save_inventory(self.root, 'review/map.json', current), 'unchanged')
        with self.assertRaises(FileExistsError):
            inventory.save_inventory(self.root, 'review/map.json', {**current, 'value': 2})
        self.assertEqual(inventory.save_inventory(self.root, 'review/map.json', {**current, 'value': 2}, True), 'refreshed')
        self.put('other.json', '{"schema":"other-tool"}')
        with self.assertRaisesRegex(ValueError, 'own inventory schema'):
            inventory.save_inventory(self.root, 'other.json', current, True)
        for path in ('../outside.json', '/tmp/outside.json', '.git/map.json', '.codex/map.json',
                     '.venv-cloud/map.json', 'review/.env.secret.json'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                inventory.save_inventory(self.root, path, current)
        link = self.root / 'linked'; link.symlink_to(self.root.parent, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'Symlinked'):
            inventory.save_inventory(self.root, 'linked/map.json', current)

    def test_git_discovery_includes_staged_and_untracked_without_shell(self):
        with patch.object(inventory.subprocess, 'run') as run:
            run.return_value.stdout = b'b.py\0a.py\0b.py\0'
            self.assertEqual(inventory.git_paths(self.root), ['a.py', 'b.py'])
        self.assertEqual(run.call_args.args[0],
                         ['git', 'ls-files', '--cached', '--others', '--exclude-standard', '-z'])
        self.assertNotIn('shell', run.call_args.kwargs)


if __name__ == '__main__':
    unittest.main()

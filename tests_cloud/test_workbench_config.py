"""Current-profile configuration isolation and containment; no Android calls."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from royale_phase3.common import ROOT
from royale_phase3 import workbench_config as config


class WorkbenchConfigTests(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / '.tmp', prefix='workbench-config-')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder / 'system.json'
        self.document = config.load_system()

    def save(self, value):
        self.path.write_text(json.dumps(value), encoding='utf-8')
        return self.path

    def test_current_profile_has_one_explicit_artifact_root(self):
        self.assertEqual(config.DEFAULT_RUN_ROOT, 'research/runs')
        self.assertEqual(config.NATIVE_MANIFEST, 'research/runtime/version.json')
        self.assertEqual(config.NATIVE_LOG_ROOT, 'research/vm')
        self.assertEqual(config.DATASETS['native-run-002']['path'], 'research/data/native/metadata.json')
        self.assertEqual(config.DATASETS['portable-pilot']['path'], 'research/data/portable-pilot/metadata.json')
        self.assertEqual(config.REFERENCE_CHECKPOINTS, ['research/models/gru-c/reference.json'])
        self.assertEqual(config.EXPERIMENT_DEFAULTS['hidden'], 32)
        self.assertEqual(config.EXPERIMENT_DEFAULTS['record_game_indices'], [0])
        loaded = config.load_system()
        loaded['paths']['runs'] = 'other-runs'
        self.assertEqual(config.DEFAULT_RUN_ROOT, 'research/runs')

    def test_missing_config_never_searches_a_historical_location(self):
        with self.assertRaisesRegex(FileNotFoundError, 'configuration is missing'):
            config.load_system(self.folder / 'missing.json')

    def test_unknown_or_missing_fields_and_duplicate_json_keys_are_rejected(self):
        for section in (None, 'paths', 'experiment_defaults'):
            with self.subTest(section=section):
                value = deepcopy(self.document)
                target = value if section is None else value[section]
                target['typo'] = True
                with self.assertRaisesRegex(ValueError, 'unknown keys'):
                    config.load_system(self.save(value))
        value = deepcopy(self.document); del value['paths']['runs']
        with self.assertRaisesRegex(ValueError, 'missing keys'):
            config.load_system(self.save(value))
        self.path.write_text('{"schema":"first","schema":"second"}', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'Duplicate configuration key'):
            config.load_system(self.path)

    def test_all_configured_paths_are_relative_normalized_and_contained(self):
        fields = (lambda value: value['paths'].__setitem__('runs', self.bad_path),
                  lambda value: value['datasets']['native-run-002'].__setitem__('path', self.bad_path),
                  lambda value: value.__setitem__('reference_checkpoints', [self.bad_path]))
        for bad_path in ('../outside', '/tmp/absolute', 'C:/absolute', r'C:\absolute',
                         'research/../old', 'research//runs', 'research/./runs', '.', ''):
            for update in fields:
                with self.subTest(path=bad_path):
                    self.bad_path = bad_path; value = deepcopy(self.document); update(value)
                    with self.assertRaises(ValueError):
                        config.load_system(self.save(value))
        link = self.folder / 'outside-link'; link.symlink_to(ROOT.parent, target_is_directory=True)
        value = deepcopy(self.document)
        value['paths']['runs'] = link.relative_to(ROOT).as_posix() + '/escape'
        with self.assertRaisesRegex(ValueError, 'escapes the project'):
            config.load_system(self.save(value))

    def test_unsupported_models_bad_budgets_and_dataset_registration_are_rejected(self):
        for name, item in (('model', 'transformer'), ('dataset', 'missing'), ('epochs', True),
                           ('games', 3), ('wall_seconds', float('inf')), ('play_weight', 0),
                           ('record_video', 1), ('record_game_indices', [True]),
                           ('record_game_indices', [0, 0])):
            with self.subTest(field=name, value=item):
                value = deepcopy(self.document); value['experiment_defaults'][name] = item
                with self.assertRaises(ValueError):
                    config.load_system(self.save(value))
        value = deepcopy(self.document)
        value['datasets']['native-run-002']['typo'] = True
        with self.assertRaisesRegex(ValueError, 'unknown keys'):
            config.load_system(self.save(value))

    def test_missing_selected_resources_are_explicitly_unavailable_without_fallback(self):
        from royale_phase3 import research
        dataset = {'label': 'missing fixture', 'path': (self.folder / 'missing-data.json').relative_to(ROOT).as_posix(),
                   'offline_only': False}
        checkpoint = (self.folder / 'missing-model.json').relative_to(ROOT).as_posix()
        with patch.object(research, 'DATASETS', {'fixture': dataset}), \
                patch.object(research, 'REFERENCE_CHECKPOINTS', [checkpoint]):
            with self.assertRaises(FileNotFoundError):
                research._dataset('fixture')
            service = research.ResearchService(self.folder / 'runs', lock_path=self.folder / 'service.lock')
            try:
                catalog = service.catalog()
            finally:
                service.close()
        self.assertEqual(catalog['checkpoints'], [])
        self.assertEqual([item['path'] for item in catalog['checkpoint_errors']], [checkpoint])
        self.assertFalse(catalog['datasets'][0]['available'])
        self.assertIn('FileNotFoundError', catalog['datasets'][0]['error'])


if __name__ == '__main__':
    unittest.main()

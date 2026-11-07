"""Strict current-workbench configuration; no historical-path discovery.

Edit research/system.json and restart the portal to change datasets, current
artifact locations or supported experiment defaults. This loader validates the
configuration without importing Torch or requiring Android. Consumers verify
the selected files and their content hashes before using them; missing files
are errors or explicitly unavailable catalog entries, never fallback sources.
"""
from copy import deepcopy
import json
import math
from pathlib import PurePosixPath, PureWindowsPath
import re

from .common import ROOT, local

SYSTEM_PATH = 'research/system.json'
SCHEMA = 'royale-research-system.v1'
_ROOT_KEYS = {'schema', 'paths', 'datasets', 'reference_checkpoints', 'experiment_defaults'}
_PATH_KEYS = {'runs', 'native_manifest', 'native_cache', 'native_logs'}
_DEFAULT_KEYS = {'mode', 'dataset', 'model', 'epochs', 'hidden', 'seed', 'play_weight',
                 'games', 'max_tick', 'wall_seconds', 'record_video', 'record_game_indices'}
_DATASET_ID = re.compile(r'^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$')


def _keys(value, expected, label):
    if not isinstance(value, dict):
        raise ValueError(f'{label} must be a JSON object')
    missing, unknown = expected - value.keys(), value.keys() - expected
    if missing or unknown:
        raise ValueError(f'{label}: missing keys {sorted(missing)}; unknown keys {sorted(unknown)}')


def _path(value, label):
    if not isinstance(value, str) or not value or value == '.' or '\\' in value or '\x00' in value:
        raise ValueError(f'{label} must be a project-relative POSIX path')
    parsed = PurePosixPath(value)
    if (parsed.is_absolute() or PureWindowsPath(value).drive or
            str(parsed) != value or any(part in ('.', '..') for part in parsed.parts)):
        raise ValueError(f'{label} must be a normalized project-relative path')
    try:
        local(value).relative_to(ROOT)
    except (ValueError, OSError, RuntimeError):
        raise ValueError(f'{label} escapes the project, including through a symlink') from None
    return value


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'Duplicate configuration key: {key}')
        result[key] = value
    return result


def load_system(path=None):
    """Load one explicit repository-contained config, or research/system.json.

    Resource existence is checked when the selected dataset/model/native runtime
    is used so an offline experiment never requires an installed Android guest.
    The configuration file itself is mandatory and has no fallback path.
    """
    source = local(SYSTEM_PATH if path is None else path)
    if not source.is_file():
        raise FileNotFoundError(f'Current research configuration is missing: {source.relative_to(ROOT)}')
    with source.open(encoding='utf-8-sig') as stream:
        value = json.load(stream, object_pairs_hook=_unique_object,
                          parse_constant=lambda item: (_ for _ in ()).throw(
                              ValueError(f'Nonfinite configuration value: {item}')))
    _keys(value, _ROOT_KEYS, 'research system')
    if value['schema'] != SCHEMA:
        raise ValueError('Unsupported research system schema')
    _keys(value['paths'], _PATH_KEYS, 'paths')
    for name, item in value['paths'].items():
        _path(item, f'paths.{name}')
    datasets = value['datasets']
    if not isinstance(datasets, dict) or not datasets:
        raise ValueError('datasets must contain at least one registered dataset')
    for name, item in datasets.items():
        if not _DATASET_ID.fullmatch(name):
            raise ValueError('Invalid dataset id: ' + name)
        _keys(item, {'label', 'path', 'offline_only'}, 'datasets.' + name)
        if not isinstance(item['label'], str) or not item['label'].strip():
            raise ValueError('Dataset label must be nonempty: ' + name)
        if type(item['offline_only']) is not bool:
            raise ValueError('Dataset offline_only must be boolean: ' + name)
        _path(item['path'], f'datasets.{name}.path')
    checkpoints = value['reference_checkpoints']
    if not isinstance(checkpoints, list):
        raise ValueError('reference_checkpoints must be a list')
    for item in checkpoints:
        _path(item, 'reference_checkpoints')
    if len(checkpoints) != len(set(checkpoints)):
        raise ValueError('reference_checkpoints must be distinct')
    defaults = value['experiment_defaults']
    _keys(defaults, _DEFAULT_KEYS, 'experiment_defaults')
    if defaults['mode'] not in ('offline', 'evaluate', 'full') or defaults['model'] != 'gru-c':
        raise ValueError('Defaults require mode offline/evaluate/full and supported model gru-c')
    if not isinstance(defaults['dataset'], str) or defaults['dataset'] not in datasets:
        raise ValueError('Default dataset is not registered')
    for name, low, high in (('epochs', 1, 50), ('hidden', 4, 512),
                            ('seed', 0, 2**31 - 10001), ('games', 2, 20),
                            ('max_tick', 91, 1_000_000)):
        if type(defaults[name]) is not int or not low <= defaults[name] <= high:
            raise ValueError(f'Default {name} must be an integer in {low}..{high}')
    if defaults['games'] % 2:
        raise ValueError('Default games must use paired owners (an even count)')
    for name, low, high in (('play_weight', .001, 1000), ('wall_seconds', 1, 3600)):
        item = defaults[name]
        if type(item) not in (int, float) or not math.isfinite(item) or not low <= item <= high:
            raise ValueError(f'Default {name} must be finite in {low}..{high}')
    if type(defaults['record_video']) is not bool:
        raise ValueError('Default record_video must be boolean')
    indices = defaults['record_game_indices']
    if (not isinstance(indices, list) or
            any(type(item) is not int or not 0 <= item < defaults['games'] for item in indices) or
            len(indices) != len(set(indices))):
        raise ValueError('Default record_game_indices must contain distinct requested game indices')
    if defaults['mode'] != 'offline':
        if datasets[defaults['dataset']]['offline_only']:
            raise ValueError('Default native mode cannot use an offline-only dataset')
        if defaults['record_video'] and not indices:
            raise ValueError('Default native recording requires a selected game')
    if defaults['mode'] == 'full' and not defaults['record_video']:
        raise ValueError('Default full mode requires native recording')
    return deepcopy(value)


SYSTEM = load_system()
DEFAULT_RUN_ROOT = SYSTEM['paths']['runs']
NATIVE_MANIFEST = SYSTEM['paths']['native_manifest']
NATIVE_CACHE = SYSTEM['paths']['native_cache']
NATIVE_LOG_ROOT = SYSTEM['paths']['native_logs']
DATASETS = deepcopy(SYSTEM['datasets'])
REFERENCE_CHECKPOINTS = list(SYSTEM['reference_checkpoints'])
EXPERIMENT_DEFAULTS = deepcopy(SYSTEM['experiment_defaults'])

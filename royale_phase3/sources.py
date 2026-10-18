"""Versioned, provenance-preserving adapters for public replay samples.

No adapter asserts that a historical dataset is current native dynamics.  The
object-NPY reader below is a data interpreter, NOT pickle.load/Unpickler: GLOBAL
names become inert enum values and REDUCE/BUILD are decoded by exact schemas.
No callable named in a pickle is imported or invoked.
"""
from __future__ import annotations

import ast
from collections import Counter, defaultdict
from dataclasses import dataclass
import hashlib
from html.parser import HTMLParser
import json
import lzma
import math
from pathlib import Path
import pickletools
import struct
from typing import Any

from .common import ROOT, digest, local, reference, rows, sha, write

SCHEMA = 'phase3.external_record.v1'
COCHON_REV = 'bee52be3d0d6463f6e4826da314ca2b6ec7ad007'
TV_REV = 'f3a750881e008e0daf237afd45736231b51a955a'
WTY_REV = 'ce86e10bedcf97762c3d207633f035cd12be8936'
EXTERNAL = Path('data/phase3/external')


@dataclass
class _DType:
    code: str
    endian: str = '='


@dataclass
class _Array:
    shape: tuple = ()
    dtype: _DType | None = None
    data: Any = None


_GLOBALS = {
    'numpy.core.multiarray _reconstruct': 'array_constructor',
    'numpy._core.multiarray _reconstruct': 'array_constructor',
    'numpy ndarray': 'array_type',
    'numpy dtype': 'dtype_constructor',
}
_NUMERIC = {'f2': 'e', 'f4': 'f', 'f8': 'd', 'i1': 'b', 'i2': 'h',
            'i4': 'i', 'i8': 'q', 'u1': 'B', 'u2': 'H', 'u4': 'I',
            'u8': 'Q', 'b1': '?'}


def _array_build(target, state):
    if not isinstance(state, tuple) or len(state) != 5:
        raise ValueError('unsupported ndarray BUILD state')
    version, shape, dtype, fortran, data = state
    if version != 1 or not isinstance(shape, tuple) or len(shape) > 8:
        raise ValueError('unsupported ndarray shape/version')
    if not isinstance(dtype, _DType) or fortran is not False:
        raise ValueError('unsupported ndarray dtype/Fortran order')
    count = 1
    for size in shape:
        if type(size) is not int or not 0 <= size <= 1_000_000:
            raise ValueError('invalid ndarray dimension')
        count *= size
    if count > 1_000_000:
        raise ValueError('array element budget exceeded')
    if dtype.code in ('O8', 'O4', 'O'):
        if not isinstance(data, list) or len(data) != count:
            raise ValueError('object array length mismatch')
        values = data
    else:
        if dtype.code not in _NUMERIC or not isinstance(data, bytes):
            raise ValueError('unsupported array payload')
        fmt = _NUMERIC[dtype.code]
        # '=' is encoded in these historical samples as native little-endian.
        # Refuse an unknown endianness rather than guessing.
        endian = '<' if dtype.endian in ('<', '=', '|') else '>' if dtype.endian == '>' else None
        if endian is None or len(data) != count * struct.calcsize(endian + fmt):
            raise ValueError('numeric array byte size/endianness mismatch')
        values = [x[0] for x in struct.iter_unpack(endian + fmt, data)]
    target.shape, target.dtype, target.data = shape, dtype, values


def _plain(value, active=None, depth=0):
    if depth > 100:
        raise ValueError('nested data budget exceeded')
    if value is None or type(value) in (bool, int, str):
        return value
    if type(value) is float:
        return value if math.isfinite(value) else None
    active = set() if active is None else active
    identity = id(value)
    if identity in active:
        raise ValueError('cyclic pickle data is forbidden')
    active.add(identity)
    try:
        if isinstance(value, _Array):
            if value.data is None:
                raise ValueError('unbuilt array')
            payload = _plain(value.data, active, depth + 1)
            if value.shape == ():
                return payload[0]
            # Keep dtype/shape, including image/crop arrays, explicit in the
            # quarantine conversion. Training adapters select fields below.
            return {'array_dtype': value.dtype.code, 'shape': list(value.shape), 'values': payload}
        if isinstance(value, (list, tuple)):
            return [_plain(x, active, depth + 1) for x in value]
        if isinstance(value, dict):
            if not all(type(k) in (str, int) for k in value):
                raise ValueError('unsupported mapping key')
            return {str(k): _plain(v, active, depth + 1) for k, v in value.items()}
        raise ValueError(f'unsupported retained type {type(value).__name__}')
    finally:
        active.remove(identity)


def interpret_object_npy_xz(path, max_unpacked=12_000_000):
    """Decode a bounded NPY scalar with a deliberately small pickle VM.

    The allowed reducer names are inert strings.  Constructors are not called;
    supported ndarray/dtype records are manually materialized as Python data.
    Any other GLOBAL, reducer, extension, persistent ID or object opcode fails.
    """
    compressed = local(path).read_bytes()
    if len(compressed) > 4_000_000:
        raise ValueError('compressed sample budget exceeded')
    decoder = lzma.LZMADecompressor(memlimit=128_000_000)
    payload = decoder.decompress(compressed, max_length=max_unpacked + 1)
    if len(payload) > max_unpacked or not decoder.eof or decoder.unused_data:
        raise ValueError('unpacked/trailing-stream budget violated')
    if payload[:8] != b'\x93NUMPY\x01\x00':
        raise ValueError('only inspected NPY v1 is supported')
    n = struct.unpack('<H', payload[8:10])[0]
    if n > 8192:
        raise ValueError('NPY header budget exceeded')
    header = ast.literal_eval(payload[10:10 + n].decode('ascii'))
    if header != {'descr': '|O', 'fortran_order': False, 'shape': ()}:
        raise ValueError('expected a single object NPY scalar')
    stream = payload[10 + n:]
    stack, memo, mark = [], {}, object()
    op_counts, seen_globals = Counter(), set()
    stopped = False

    def marked():
        for i in range(len(stack) - 1, -1, -1):
            if stack[i] is mark:
                tail = stack[i + 1:]
                del stack[i:]
                return tail
        raise ValueError('pickle MARK missing')

    for index, (op, arg, position) in enumerate(pickletools.genops(stream)):
        if index > 1_000_000 or len(stack) > 100_000 or len(memo) > 100_000:
            raise ValueError('pickle instruction/memory budget exceeded')
        name = op.name
        op_counts[name] += 1
        if name == 'PROTO':
            if arg != 3:
                raise ValueError('only inspected pickle protocol 3 supported')
        elif name == 'GLOBAL':
            if arg not in _GLOBALS:
                raise ValueError(f'forbidden GLOBAL {arg!r}')
            seen_globals.add(arg)
            stack.append(('symbol', _GLOBALS[arg]))
        elif name in ('BINPUT', 'LONG_BINPUT'):
            if not stack or not 0 <= arg <= 100_000:
                raise ValueError('invalid pickle memo store')
            memo[arg] = stack[-1]
        elif name in ('BINGET', 'LONG_BINGET'):
            stack.append(memo[arg])
        elif name == 'MARK': stack.append(mark)
        elif name == 'NONE': stack.append(None)
        elif name == 'NEWTRUE': stack.append(True)
        elif name == 'NEWFALSE': stack.append(False)
        elif name in ('BININT', 'BININT1', 'BININT2', 'LONG1', 'LONG4', 'BINFLOAT', 'BINUNICODE', 'SHORT_BINBYTES', 'BINBYTES'):
            stack.append(arg)
        elif name == 'EMPTY_TUPLE': stack.append(())
        elif name == 'EMPTY_LIST': stack.append([])
        elif name == 'EMPTY_DICT': stack.append({})
        elif name == 'TUPLE': stack.append(tuple(marked()))
        elif name in ('TUPLE1', 'TUPLE2', 'TUPLE3'):
            size = int(name[-1]); items = stack[-size:]; del stack[-size:]; stack.append(tuple(items))
        elif name in ('APPEND', 'APPENDS'):
            items = marked() if name == 'APPENDS' else [stack.pop()]
            if type(stack[-1]) is not list: raise ValueError('APPEND target not list')
            stack[-1].extend(items)
        elif name == 'SETITEMS':
            items = marked()
            if type(stack[-1]) is not dict or len(items) % 2: raise ValueError('invalid SETITEMS')
            for k, v in zip(items[::2], items[1::2]):
                if type(k) not in (str, int): raise ValueError('forbidden mapping key')
                stack[-1][k] = v
        elif name == 'REDUCE':
            args, symbol = stack.pop(), stack.pop()
            if not isinstance(args, tuple): raise ValueError('REDUCE args not tuple')
            if symbol == ('symbol', 'array_constructor'):
                if args != (('symbol', 'array_type'), (0,), b'b'):
                    raise ValueError('unsupported ndarray constructor parameters')
                stack.append(_Array())
            elif symbol == ('symbol', 'dtype_constructor'):
                if len(args) != 3 or type(args[0]) is not str or args[1:] != (False, True):
                    raise ValueError('unsupported dtype constructor')
                if args[0] not in set(_NUMERIC) | {'O', 'O4', 'O8'}:
                    raise ValueError('forbidden dtype')
                stack.append(_DType(args[0]))
            else:
                raise ValueError('unsupported REDUCE symbol')
        elif name == 'BUILD':
            state, target = stack.pop(), stack[-1]
            if isinstance(target, _DType):
                if (not isinstance(state, tuple) or len(state) != 8 or state[0] != 3
                    or state[1] not in ('<', '>', '=', '|') or state[2:5] != (None, None, None)
                    or state[5:7] != (-1, -1) or type(state[7]) is not int):
                    raise ValueError('unsupported dtype BUILD state')
                target.endian = state[1]
            elif isinstance(target, _Array):
                _array_build(target, state)
            else: raise ValueError('forbidden BUILD target')
        elif name == 'STOP':
            if len(stack) != 1 or position != len(stream) - 1:
                raise ValueError('pickle trailing/stack mismatch')
            stopped = True
            break
        else:
            raise ValueError(f'forbidden pickle opcode {name}')
    if not stopped:
        raise ValueError('pickle STOP missing')
    data = _plain(stack[0])
    return data, {'reader': 'inert_numpy_pickle_vm.v1', 'globals_executed': 0,
                  'reducer_functions_called': 0, 'allowed_symbol_names': sorted(seen_globals),
                  'opcode_counts': dict(op_counts), 'compressed_bytes': len(compressed),
                  'unpacked_bytes': len(payload), 'npy_header': str(header)}


def _base(source, revision, group, index, source_ref, quality):
    return {'schema': SCHEMA, 'source': source, 'revision': revision,
        'group_id': f'{source}:{group}', 'source_index': index, 'source_ref': source_ref,
        'game_version': None, 'current_native_compatible': False,
        'player_view': {'orientation': 'source_original', 'actor': None},
        'time': {'source_value': None, 'source_unit': 'unknown', 'engine_tick': None,
                 'seconds': None, 'dt_seconds': None, 'exact_dt_known': False},
        'coordinates': {'source_x': None, 'source_y': None, 'source_space': 'unknown',
                        'canonical_x': None, 'canonical_y': None, 'valid': False,
                        'transform': 'unconverted_unknown.v1'},
        'action': {'kind': 'unknown', 'card': None, 'slot': None, 'semantics': 'unknown',
                   'consumed': None},
        'observation': {},
        'reward': {'value': None, 'semantics': 'unknown', 'rtg_actor_input': False},
        'end': {'terminated': None, 'truncated': None, 'semantics': 'unknown'},
        'quality': {'level': quality, 'weight': 0.25, 'reasons': []},
        'actor_exclusions': ['future_events', 'outcome', 'reward', 'rtg', 'source_ref', 'group_id']}


class ReplayHTML(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tags = []
    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if any(k in attrs for k in ('data-card', 'data-c', 'data-x', 'data-tag')):
            self.tags.append({'tag': tag, **attrs})


def parse_cochon_raw(value):
    """Return explicit schemas; never manufacture state from card-play markers."""
    if value.get('schema_version') == 1 and isinstance(value.get('payload'), dict):
        payload = value['payload']
        if payload.get('success') is not True or not isinstance(payload.get('html'), str):
            raise ValueError('cochon raw unsuccessful/non-HTML payload')
        parser = ReplayHTML(); parser.feed(payload['html'])
        return {'schema_kind': 'raw_html_v1', 'tags': parser.tags,
                'events': [x for x in parser.tags if 'data-card' in x and 'data-t' in x],
                'markers': [x for x in parser.tags if 'data-x' in x and 'data-y' in x]}
    if isinstance(value.get('events'), list):
        return {'schema_kind': 'json_events_unknown_version', 'events': value['events'], 'markers': []}
    raise ValueError('unrecognized Cochon schema; snapshot must be audited independently')


def cochon_record(event, source_ref, subset='cleaned-legacy'):
    out = _base('cochon', COCHON_REV, event['battle_id'], event.get('sequence'), source_ref, 'public_event_weak_state')
    out['source_subset'] = subset
    out['player_view']['actor'] = event.get('side')
    ticks = event.get('ticks')
    if type(ticks) in (int, float) and ticks >= 0:
        out['time'].update(source_value=ticks, source_unit='source_20hz_tick', seconds=ticks / 20,
                           exact_dt_known=False, source_seconds_conversion='dataset_documented_ticks_div_20')
    x, y = event.get('x'), event.get('y')
    valid = type(x) in (int, float) and type(y) in (int, float) and 0 <= x <= 18000 and 0 <= y <= 32000
    out['coordinates'].update(source_x=x, source_y=y, source_space='source_arena_18000x32000',
        canonical_x=x / 1000 if valid else None, canonical_y=y / 1000 if valid else None,
        valid=valid, transform='cochon_source_units_to_tile.v1')
    kind = event.get('event_type', 'unknown')
    out['action'].update(kind=kind, card=event.get('card'), semantics='public_replay_event_not_native_receipt')
    out['quality']['reasons'] = ['no_continuous_entity_or_hp_state', 'balance_version_unknown',
        'not_a_current_native_dynamics_label', 'ability_branch_unsupported' if kind != 'card_play' else 'ordinary_card_event_only']
    out['quality']['ghost_state'] = 'not_generated'
    out['quality']['tower_hp'] = 'unavailable_not_zero'
    return out


def tv_record(item, source_ref, subset):
    replay = str(item.get('replay', '01600528-75d9-429a-bb4b-7c5f062600a5'))
    out = _base('tv', TV_REV, replay, item.get('source_row_in_group'), source_ref, 'processed_visual_weak_action')
    out['source_subset'] = subset
    out['time'].update(source_value=item.get('frame', item.get('frame_id')), source_unit='deduplicated_frame_index')
    out['quality']['reasons'] = ['deduplicated_frames_have_no_exact_timestamps', 'balance_version_unknown',
                                 'processed_action_pre_post_alignment_unverified', 'not_native_consumption']
    if subset == 'raw_frame_metadata':
        out['observation'] = {'frame_hash': item.get('hash'), 'image_downloaded': False}
        out['quality']['level'] = 'raw_frame_metadata_only'
        return out
    x, y = item.get('x'), item.get('y')
    # Upstream field names alone do not establish whether these are pixels,
    # tiles or a click projection. Retain raw values without native conversion.
    present = type(x) in (int, float) and type(y) in (int, float) and x >= 0 and y >= 0
    out['coordinates'].update(source_x=x, source_y=y, source_space='processed_xy_unverified', valid=False)
    out['coordinates']['present'] = present
    card = item.get('card')
    out['action'].update(kind='visual_card_label' if card else 'unknown', card=card,
                         semantics='processed_visual_label_alignment_unverified')
    out['observation'] = {'hand_source': item.get('hand'), 'elixir_visual': item.get('elixir'),
                          'source_offset': item.get('offset'), 'image_downloaded': False}
    if not present: out['quality']['reasons'].append('missing_or_sentinel_position_masked')
    return out


def _values(value):
    return value['values'] if isinstance(value, dict) and 'array_dtype' in value else value


def wty_records(data, source_ref, group):
    state, action, reward = data.get('state'), data.get('action'), _values(data.get('reward'))
    if not isinstance(state, list) or not isinstance(action, list) or not isinstance(reward, list):
        raise ValueError('wty requires state/action/reward lists')
    if not len(state) == len(action) == len(reward):
        raise ValueError('wty state/action/reward length mismatch')
    previous_time = None
    for i, (s, a, r) in enumerate(zip(state, action, reward)):
        out = _base('wty', WTY_REV, group, i, source_ref, 'vision_inferred_state_action_reward')
        out['source_subset'] = 'fast_hog_2.6_small_sample'
        t = s.get('time')
        out['time'].update(source_value=t, source_unit='ocr_game_clock_seconds',
                           seconds=t, dt_seconds=None if previous_time is None or t is None else t - previous_time)
        previous_time = t
        xy = _values(a.get('xy'))
        if isinstance(xy, list) and len(xy) == 2:
            out['coordinates'].update(source_x=xy[0], source_y=xy[1], source_space='katacr_cell_18x32',
                canonical_x=xy[0], canonical_y=xy[1], valid=0 <= xy[0] < 18 and 0 <= xy[1] < 32,
                transform='katacr_source_cell_identity_not_native_projection.v1')
        card_id = a.get('card_id')
        out['action'].update(kind='wait_visual' if card_id == 0 else 'play_visual', slot=card_id,
            semantics='vision_inferred_backward_offset_adjusted_by_upstream')
        units = []
        for unit in s.get('unit_infos', []):
            units.append({'xy_source': _values(unit.get('xy')), 'class_source': unit.get('cls'),
                'side_source': unit.get('bel'), 'hp': None,
                'visual_crops': {key: None if unit.get(key) is None else
                    {'shape': unit[key].get('shape'), 'dtype': unit[key].get('array_dtype'),
                     'content_sha256': digest(unit[key]), 'pixels_omitted_from_actor': True}
                    for key in ('body', 'bar1', 'bar2')}})
        out['observation'] = {'cards_source': s.get('cards'), 'elixir_visual': s.get('elixir'),
                              'unit_infos_visual': units, 'source_offset': a.get('offset')}
        out['reward'].update(value=r, semantics='upstream_visual_hp_change_plus_tower_elixir_shaping')
        out['end'].update(semantics='file_end_only_no_true_terminal_label',
                          file_boundary=i == len(state) - 1)
        out['quality']['reasons'] = ['upstream_has_no_explicit_terminated', 'ocr_clock_not_exact_engine_dt',
             'offset_removed_before_saved_dataset', 'unknown_data_license', 'historical_version_not_native_truth',
             'reward_uses_vision_hp_and_shaping_not_official_score']
        out['quality']['weight'] = 0.1
        yield out


def validate_record(record):
    errors = []
    for name in ('schema', 'source', 'revision', 'group_id', 'source_ref', 'time', 'coordinates', 'action', 'quality', 'end'):
        if name not in record: errors.append(f'missing:{name}')
    if errors: return errors
    if record['schema'] != SCHEMA: errors.append('schema_version')
    if record['current_native_compatible'] is not False: errors.append('unverified_native_compatibility')
    if record['source'] == 'tv' and any(record['time'].get(k) is not None for k in ('seconds', 'dt_seconds', 'engine_tick')):
        errors.append('tv_frame_index_is_not_timestamp')
    if record['source'] == 'wty' and record['end']['terminated'] is not None: errors.append('wty_unproved_terminal')
    if record['action'].get('consumed') is not None: errors.append('external_native_receipt_claim')
    if any(x not in record['actor_exclusions'] for x in ('future_events', 'outcome', 'reward', 'rtg')):
        errors.append('actor_exclusions')
    return errors


def sequence_fingerprint(records, relative_time=False):
    """Content, not player names or file paths; usable for derived-match grouping."""
    events = [r for r in records if r['action']['kind'] in ('card_play', 'play_visual', 'visual_card_label')]
    first = next((r['time']['seconds'] for r in events if r['time']['seconds'] is not None), 0)
    items = []
    for r in events:
        t = r['time']['seconds']
        items.append((r['action']['card'], None if t is None else round(t - first if relative_time else t, 1),
                      r['coordinates']['canonical_x'], r['coordinates']['canonical_y']))
    return digest(items)

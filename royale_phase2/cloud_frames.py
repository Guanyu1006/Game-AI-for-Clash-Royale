"""Actual local-ADB PNG capture and lossless, tick-timed APNG archives.

Construction never starts ADB or contacts Android. PNG validity/content checks
are diagnostics, not proof that a frame depicts a particular native tick. The
caller owns the native lock and must pause and bind state before/after capture.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import time
import zlib

from .runtime import ROOT, local

PNG_SIGNATURE = b'\x89PNG\r\n\x1a\n'
MAX_PNG_BYTES = 32 * 1024 * 1024
MAX_DECODED_BYTES = 64 * 1024 * 1024


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode()).hexdigest()


def _chunk(kind, data):
    return (struct.pack('>I', len(data)) + kind + data
            + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff))


def _chunks(png):
    if not isinstance(png, bytes) or not png.startswith(PNG_SIGNATURE):
        raise ValueError('Actual PNG bytes with the PNG signature are required')
    if len(png) > MAX_PNG_BYTES:
        raise ValueError('PNG exceeds the bounded capture budget')
    result = []
    offset = len(PNG_SIGNATURE)
    while offset < len(png):
        if offset + 12 > len(png):
            raise ValueError('Truncated PNG chunk')
        size, kind = struct.unpack_from('>I4s', png, offset)
        end = offset + 12 + size
        if end > len(png) or not all(65 <= c <= 90 or 97 <= c <= 122 for c in kind):
            raise ValueError('Invalid PNG chunk length/type')
        data = png[offset + 8:end - 4]
        crc = struct.unpack_from('>I', png, end - 4)[0]
        if zlib.crc32(kind + data) & 0xffffffff != crc:
            raise ValueError('PNG chunk CRC mismatch: ' + kind.decode('ascii'))
        result.append((kind, data))
        offset = end
        if kind == b'IEND':
            if size or offset != len(png):
                raise ValueError('PNG has invalid IEND or trailing bytes')
            break
    if (not result or result[0][0] != b'IHDR' or len(result[0][1]) != 13
            or result[-1][0] != b'IEND' or not any(k == b'IDAT' for k, _ in result)
            or sum(k == b'IHDR' for k, _ in result) != 1
            or any(k in (b'acTL', b'fcTL', b'fdAT') for k, _ in result)):
        raise ValueError('Expected one complete, non-animated PNG image')
    idat_seen = False
    idat_ended = False
    for kind, _ in result:
        if kind == b'IDAT':
            if idat_ended:
                raise ValueError('PNG IDAT chunks must be consecutive')
            idat_seen = True
        elif idat_seen:
            idat_ended = True
    return result


def _unfilter(compressed, width, height, channels):
    stride = width * channels
    expected = (stride + 1) * height
    if expected > MAX_DECODED_BYTES:
        raise ValueError('PNG decoded pixels exceed the bounded budget')
    decoder = zlib.decompressobj()
    try:
        source = decoder.decompress(compressed, expected + 1)
    except zlib.error as exc:
        raise ValueError('PNG IDAT cannot be decompressed') from exc
    if (len(source) != expected or not decoder.eof or decoder.unused_data
            or decoder.unconsumed_tail):
        raise ValueError('PNG IDAT decoded length does not match IHDR')
    prior = bytearray(stride)
    rows = []
    for y in range(height):
        offset = y * (stride + 1)
        method = source[offset]
        row = bytearray(source[offset + 1:offset + 1 + stride])
        if method > 4:
            raise ValueError('Unsupported PNG row filter')
        if method:
            for x in range(stride):
                left = row[x - channels] if x >= channels else 0
                above = prior[x]
                corner = prior[x - channels] if x >= channels else 0
                if method == 1:
                    predictor = left
                elif method == 2:
                    predictor = above
                elif method == 3:
                    predictor = (left + above) // 2
                else:
                    base = left + above - corner
                    a, b, c = abs(base - left), abs(base - above), abs(base - corner)
                    predictor = left if a <= b and a <= c else above if b <= c else corner
                row[x] = (row[x] + predictor) & 255
        rows.append(row)
        prior = row
    return rows


def _numpy_metrics(rows, width, height, color, transparency):
    """Optional RGB/RGBA8 reductions; retain the fallback's exact arithmetic."""
    if color not in (2, 6) or transparency:
        return None
    try:
        import numpy as np
    except ImportError:
        return None
    channels = 4 if color == 6 else 3
    pixels = np.frombuffer(b''.join(rows), dtype=np.uint8).reshape(height, width, channels)
    if color == 6:
        rgba = pixels
    else:
        rgba = np.empty((height, width, 4), dtype=np.uint8)
        rgba[:, :, :3] = pixels
        rgba[:, :, 3] = 255
    values = rgba[:, :, :3]
    alpha = rgba[:, :, 3:4]
    if not np.all(alpha == 255):
        # uint16 prevents overflow before the exact floor division by 255.
        values = values.astype(np.uint16) * alpha.astype(np.uint16) // 255
    nonblack = int(np.count_nonzero(np.any(values, axis=2)))
    total = int(np.sum(values, dtype=np.uint64))
    square = int(np.sum(np.square(values, dtype=np.uint32), dtype=np.uint64))
    count = width * height * 3
    return dict(pixel_diagnostics_available=True, non_black=bool(nonblack), non_black_pixels=nonblack,
                non_black_fraction=nonblack / (width * height),
                pixel_sha256=hashlib.sha256(rgba.tobytes(order='C')).hexdigest(),
                mean_channel_value=total / count,
                pixel_variance=max(0, square / count - (total / count) ** 2),
                min_channel_value=int(np.min(values)), max_channel_value=int(np.max(values)))


def frame_info(png, *, use_numpy=True):
    """Validate all chunk CRCs and diagnose common 8-bit screencap PNG pixels.

    ``non_black`` being true is necessary but insufficient for a game frame.
    Unsupported valid pixel formats return unknown diagnostics, never success.
    Transparent pixels are composited onto black only for brightness metrics.
    NumPy is optional and accelerates common RGB/RGBA8 metrics without changing
    PNG bytes, pixel hashes, or result fields. ``use_numpy=False`` forces the
    portable implementation for comparisons and environments without NumPy.
    """
    chunks = _chunks(png)
    width, height, depth, color, compression, filtering, interlace = struct.unpack('>IIBBBBB', chunks[0][1])
    if not 1 <= width <= 8192 or not 1 <= height <= 8192:
        raise ValueError('PNG dimensions exceed the bounded capture budget')
    if compression != 0 or filtering != 0 or interlace not in (0, 1):
        raise ValueError('Invalid PNG IHDR methods')
    if color not in (0, 2, 3, 4, 6) or depth not in ({0: (1, 2, 4, 8, 16), 2: (8, 16),
                                                  3: (1, 2, 4, 8), 4: (8, 16), 6: (8, 16)}[color]):
        raise ValueError('Invalid PNG bit depth/color combination')
    info = {'bytes': len(png), 'sha256': hashlib.sha256(png).hexdigest(),
            'width': width, 'height': height, 'bit_depth': depth, 'color_type': color,
            'interlace': interlace, 'png_crc_valid': True, 'pixel_diagnostics_available': False,
            'non_black': None, 'non_black_pixels': None, 'pixel_sha256': None,
            'pixel_variance': None, 'game_content_verified': False,
            'native_tick_binding_verified': False}
    if depth != 8 or interlace:
        info['diagnostic_limitation'] = 'Pixel diagnostics support non-interlaced 8-bit PNG only'
        return info
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    rows = _unfilter(b''.join(v for k, v in chunks if k == b'IDAT'), width, height, channels)
    palette = next((v for k, v in chunks if k == b'PLTE'), b'')
    transparency = next((v for k, v in chunks if k == b'tRNS'), b'')
    if ((palette and (len(palette) % 3 or len(palette) > 768))
            or (transparency and (color in (4, 6) or
                color == 0 and len(transparency) != 2 or color == 2 and len(transparency) != 6))):
        raise ValueError('Invalid PNG palette/transparency metadata')
    if color == 3 and (not palette or len(palette) % 3):
        raise ValueError('Indexed PNG lacks a valid palette')
    metrics = _numpy_metrics(rows, width, height, color, transparency) if use_numpy else None
    if metrics is not None:
        info.update(metrics)
        return info
    fingerprint = hashlib.sha256()
    nonblack = 0
    total = square = 0
    minimum, maximum = 255, 0
    for row in rows:
        rgba = bytearray()
        for x in range(width):
            at = x * channels
            if color == 6:
                r, g, b, alpha = row[at:at + 4]
            elif color == 2:
                r, g, b = row[at:at + 3]
                alpha = 0 if transparency and struct.unpack('>HHH', transparency) == (r, g, b) else 255
            elif color == 3:
                index = row[at]
                if index * 3 + 3 > len(palette):
                    raise ValueError('Indexed PNG pixel is outside the palette')
                r, g, b = palette[index * 3:index * 3 + 3]
                alpha = transparency[index] if index < len(transparency) else 255
            else:
                r = g = b = row[at]
                alpha = row[at + 1] if color == 4 else 255
                if color == 0 and transparency and struct.unpack('>H', transparency)[0] == r:
                    alpha = 0
            rgba.extend((r, g, b, alpha))
            values = (r * alpha // 255, g * alpha // 255, b * alpha // 255)
            nonblack += bool(any(values))
            total += sum(values)
            square += sum(v * v for v in values)
            minimum = min(minimum, *values)
            maximum = max(maximum, *values)
        fingerprint.update(rgba)
    count = width * height * 3
    info.update(pixel_diagnostics_available=True, non_black=bool(nonblack), non_black_pixels=nonblack,
                non_black_fraction=nonblack / (width * height), pixel_sha256=fingerprint.hexdigest(),
                mean_channel_value=total / count, pixel_variance=max(0, square / count - (total / count) ** 2),
                min_channel_value=minimum, max_channel_value=maximum)
    return info


def compare_frame_info(current, previous):
    """Pixel changes are independent of PNG encoder/chunk differences."""
    comparable = bool(previous and current.get('pixel_sha256') and previous.get('pixel_sha256'))
    return {'pixel_comparison_available': comparable,
            'pixels_changed': (current['pixel_sha256'] != previous['pixel_sha256'] or
                               (current['width'], current['height']) != (previous['width'], previous['height']))
            if comparable else None,
            'png_bytes_changed': current['sha256'] != previous['sha256'] if previous else None}


class CloudAndroidBridge:
    """One explicit local ADB capture; no daemon start, retry or device mutation."""
    def __init__(self, adb='.cache/native-tools/bin/adb', adb_port=5038,
                 serial='127.0.0.1:15555', root_shell='su0', timeout=60, *, runner=None):
        self.adb = local(adb)
        if not self.adb.is_file():
            raise ValueError('Repository-local ADB executable is required')
        if type(adb_port) is not int or not 1 <= adb_port <= 65535:
            raise ValueError('Invalid ADB server port')
        if not isinstance(serial, str) or not serial or serial.startswith('-') or any(c.isspace() for c in serial):
            raise ValueError('An explicit ADB device serial is required')
        if root_shell not in ('adbd', 'su0'):
            raise ValueError('root_shell must be adbd or su0')
        if type(timeout) not in (int, float) or not 0 < timeout <= 600:
            raise ValueError('Capture timeout must be in (0, 600] seconds')
        self.adb_port, self.serial, self.root_shell, self.timeout = adb_port, serial, root_shell, timeout
        self.runner = runner or subprocess.run
        self.last_capture = None
        self.previous_info = None

    def capture(self):
        command = [str(self.adb), '-H', '127.0.0.1', '-P', str(self.adb_port), '-s', self.serial,
                   'exec-out', *(['su', '0'] if self.root_shell == 'su0' else []), 'screencap', '-p']
        started = time.monotonic()
        record = {'command': command, 'started_utc': datetime.now(timezone.utc).isoformat(),
                  'source': 'Actual Android screencap; PNG bytes are not modified', 'success': False}
        self.last_capture = record
        try:
            result = self.runner(command, cwd=ROOT, capture_output=True, timeout=self.timeout)
            record['exit_code'] = result.returncode
            record['stderr'] = result.stderr.decode(errors='replace')[:4000]
            if result.returncode:
                raise RuntimeError('ADB screencap failed: ' + record['stderr'])
            info = frame_info(result.stdout)
            record.update(info=info, **compare_frame_info(info, self.previous_info), success=True)
            self.previous_info = info
            return result.stdout
        except Exception as exc:
            record['error'] = type(exc).__name__ + ': ' + str(exc)
            raise
        finally:
            record['elapsed_seconds'] = time.monotonic() - started


def _state_bindings(states_path):
    bindings = set()
    origin = None
    for line in local(states_path).read_text(encoding='utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        raw = row.get('raw_capture', row.get('state', row.get('raw_state')))
        if not isinstance(raw, dict) or type(raw.get('tick')) is not int:
            raise ValueError('State log row lacks an actual raw state/tick')
        direct = _digest(raw)
        semantic = None
        if 'all_observed_card_events' in row:
            if origin is None:
                origin = raw['next'] - 1
            packet = deepcopy(raw)
            packet.pop('generation')
            packet['next'] -= origin
            packet['plays'] = deepcopy(row['all_observed_card_events'])
            for event in packet['plays']:
                event[0] -= origin
            semantic = _digest(packet)
        declared = row.get('semantic_sha256', row.get('native_semantic_sha256', row.get('state_sha256')))
        if declared is not None and declared not in (direct, semantic):
            raise ValueError('State log hash does not match its reconstructed raw/semantic state')
        bindings.add((raw['tick'], direct))
        if semantic:
            bindings.add((raw['tick'], semantic))
    return bindings


def validate_frame_index(index_path, *, states_path=None, expected_source_sha256=None):
    """Re-read PNGs and optionally rebuild their tick/state bindings.

    Index rows use repository-relative ``path``, ``sha256``, ``tick`` and
    ``native_semantic_sha256`` (or ``state_sha256``). A source hash is only
    checked if explicitly supplied; it cannot attest a running process.
    """
    index = local(index_path)
    raw_index = index.read_bytes()
    bindings = _state_bindings(states_path) if states_path is not None else None
    rows = []
    for line in raw_index.decode('utf-8').splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if type(row.get('tick')) is not int or row['tick'] < 0:
            raise ValueError('Frame tick must be a nonnegative native integer')
        path = local(row['path'])
        png = path.read_bytes()
        info = frame_info(png)
        if row.get('sha256') != info['sha256'] or ('bytes' in row and row['bytes'] != len(png)):
            raise ValueError('Frame file differs from its index receipt: ' + row['path'])
        state_hash = row.get('native_semantic_sha256', row.get('state_sha256'))
        if bindings is not None and (row['tick'], state_hash) not in bindings:
            raise ValueError('Frame tick/state hash is not bound to the supplied raw states')
        if expected_source_sha256 is not None and row.get('source_sha256') != expected_source_sha256:
            raise ValueError('Frame source hash differs from the requested source')
        rows.append({'tick': row['tick'], 'path': str(path.relative_to(ROOT)), **info,
                     'native_semantic_sha256': state_hash})
    if not rows:
        raise ValueError('Frame index has no actual images')
    return {'schema': 'royale-cloud-native-frame-validation.v1', 'status': 'pass',
            'index_path': str(index.relative_to(ROOT)), 'index_sha256': hashlib.sha256(raw_index).hexdigest(),
            'frames': rows, 'frame_count': len(rows),
            'non_black_frames': sum(r['non_black'] is True for r in rows),
            'unique_pixel_hashes': len({r['pixel_sha256'] for r in rows if r['pixel_sha256']}),
            'state_bindings_verified': bindings is not None,
            'source_hash_verified': expected_source_sha256 is not None,
            'game_content_verified': False,
            'timing_limitation': 'Paused-state binding does not prove zero native display presentation latency'}


def export_apng(index_path, output_path, *, ticks_per_second=20, states_path=None,
                expected_source_sha256=None, last_frame_ticks=1, plays=0):
    """Copy true PNG IDATs into APNG; no interpolation or synthesized pixels.

    Repeated ticks use the last saved frame. Backward ticks require separate
    archive segments. The final frame holds ``last_frame_ticks`` explicitly.
    """
    if type(ticks_per_second) is not int or not 1 <= ticks_per_second <= 65535:
        raise ValueError('Invalid native ticks per second')
    if type(last_frame_ticks) is not int or not 1 <= last_frame_ticks <= 65535:
        raise ValueError('Invalid final-frame hold duration')
    if type(plays) is not int or not 0 <= plays <= 0xffffffff:
        raise ValueError('Invalid animation play count')
    report = validate_frame_index(index_path, states_path=states_path,
                                  expected_source_sha256=expected_source_sha256)
    output = local(output_path)
    if output.exists():
        raise ValueError('APNG output already exists; choose a new artifact path')
    frames = []
    for row in report['frames']:
        if frames and row['tick'] < frames[-1]['tick']:
            raise ValueError('Backward frame ticks require separate animation segments')
        if frames and row['tick'] == frames[-1]['tick']:
            frames[-1] = row
        else:
            frames.append(row)
    first_chunks = _chunks(local(frames[0]['path']).read_bytes())
    ihdr = first_chunks[0][1]
    shared = []
    for kind, data in first_chunks[1:]:
        if kind == b'IDAT':
            break
        shared.append((kind, data))
    # Palette/color metadata must agree: IDAT data is deliberately unchanged.
    for row in frames[1:]:
        chunks = _chunks(local(row['path']).read_bytes())
        before = []
        for kind, data in chunks[1:]:
            if kind == b'IDAT':
                break
            before.append((kind, data))
        if chunks[0][1] != ihdr or before != shared:
            raise ValueError('APNG frames require identical IHDR and pre-IDAT metadata')
    width, height = struct.unpack_from('>II', ihdr)
    timings = []
    for index, row in enumerate(frames):
        ticks = frames[index + 1]['tick'] - row['tick'] if index + 1 < len(frames) else last_frame_ticks
        duration = Fraction(ticks, ticks_per_second)
        if duration.numerator > 65535 or duration.denominator > 65535:
            raise ValueError('Frame delay exceeds APNG rational timing range')
        timings.append({'tick': row['tick'], 'duration_ticks': ticks, 'source_path': row['path'],
                        'source_sha256': row['sha256'], 'delay_numerator': duration.numerator,
                        'delay_denominator': duration.denominator})
    output.parent.mkdir(parents=True, exist_ok=True)
    fingerprint = hashlib.sha256()
    written = 0
    with output.open('xb') as stream:
        def write(data):
            nonlocal written
            stream.write(data)
            fingerprint.update(data)
            written += len(data)
        write(PNG_SIGNATURE)
        write(_chunk(b'IHDR', ihdr))
        for kind, data in shared:
            write(_chunk(kind, data))
        write(_chunk(b'acTL', struct.pack('>II', len(frames), plays)))
        sequence = 0
        for index, (row, timing) in enumerate(zip(frames, timings)):
            control = struct.pack('>IIIIIHHBB', sequence, width, height, 0, 0,
                                  timing['delay_numerator'], timing['delay_denominator'], 0, 0)
            write(_chunk(b'fcTL', control))
            sequence += 1
            # At most one bounded source PNG is held at once, even for a long game.
            png = local(row['path']).read_bytes()
            if hashlib.sha256(png).hexdigest() != row['sha256']:
                raise ValueError('Source PNG changed during animation export')
            for kind, data in _chunks(png):
                if kind != b'IDAT':
                    continue
                if index == 0:
                    write(_chunk(b'IDAT', data))
                else:
                    write(_chunk(b'fdAT', struct.pack('>I', sequence) + data))
                    sequence += 1
        write(_chunk(b'IEND', b''))
    result = {'schema': 'royale-cloud-native-apng-export.v1', 'status': 'pass',
              'path': str(output.relative_to(ROOT)), 'sha256': fingerprint.hexdigest(),
              'bytes': written, 'width': width, 'height': height, 'frame_count': len(frames),
              'input_frames': len(report['frames']), 'same_tick_frames_omitted': len(report['frames']) - len(frames),
              'ticks_per_second': ticks_per_second, 'last_frame_ticks': last_frame_ticks,
              'source_index_sha256': report['index_sha256'],
              'state_bindings_verified': report['state_bindings_verified'],
              'source_hash_verified': report['source_hash_verified'], 'frames': timings,
              'pixels': 'Original PNG IDAT payloads copied without re-encoding or interpolation',
              'duration_seconds': sum(t['duration_ticks'] for t in timings) / ticks_per_second}
    return result

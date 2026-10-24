"""PNG fixtures only; these tests never contact ADB, Android, or native RPC."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import zlib

from royale_phase2 import cloud_frames as frames


def fixture_png(rows=None, *, width=2, color=6, filters=None, compression=6):
    """Encode tiny explicitly synthetic test pixels; never runtime fallbacks."""
    rows = rows or [bytes((255, 0, 0, 255, 0, 255, 0, 255)),
                    bytes((0, 0, 255, 255, 0, 0, 0, 255))]
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[color]
    source = bytearray()
    prior = bytes(width * channels)
    for index, row in enumerate(rows):
        method = filters[index] if filters else 0
        source.append(method)
        for x, value in enumerate(row):
            left = row[x - channels] if x >= channels else 0
            above = prior[x]
            corner = prior[x - channels] if x >= channels else 0
            if method == 0:
                predictor = 0
            elif method == 1:
                predictor = left
            elif method == 2:
                predictor = above
            elif method == 3:
                predictor = (left + above) // 2
            else:
                base = left + above - corner
                a, b, c = abs(base - left), abs(base - above), abs(base - corner)
                predictor = left if a <= b and a <= c else above if b <= c else corner
            source.append((value - predictor) & 255)
        prior = row
    header = struct.pack('>IIBBBBB', width, len(rows), 8, color, 0, 0, 0)
    return (frames.PNG_SIGNATURE + frames._chunk(b'IHDR', header)
            + frames._chunk(b'IDAT', zlib.compress(bytes(source), compression))
            + frames._chunk(b'IEND', b''))


def archive_chunks(data):
    result = []
    offset = 8
    while offset < len(data):
        size, kind = struct.unpack_from('>I4s', data, offset)
        body = data[offset + 8:offset + 8 + size]
        crc = struct.unpack_from('>I', data, offset + 8 + size)[0]
        if crc != zlib.crc32(kind + body) & 0xffffffff:
            raise AssertionError('Exported APNG CRC mismatch')
        result.append((kind, body))
        offset += size + 12
    return result


class NativePNGTests(unittest.TestCase):
    def test_optional_numpy_and_python_metrics_match_every_field(self):
        # Alpha extremes and partial opacity detect arithmetic/pixel-hash drift.
        rgba_rows = [bytes((0, 255, 80, 0, 255, 240, 128, 1, 90, 160, 250, 127,
                            255, 255, 255, 255, 3, 4, 5, 254)) for _ in range(5)]
        rgb_rows = [bytes((0, 255, 80, 255, 240, 128, 90, 160, 250,
                           255, 255, 255, 3, 4, 5)) for _ in range(5)]
        for color, rows in ((6, rgba_rows), (2, rgb_rows)):
            png = fixture_png(rows, width=5, color=color, filters=[0, 1, 2, 3, 4])
            with self.subTest(color=color):
                self.assertEqual(frames.frame_info(png), frames.frame_info(png, use_numpy=False))
        black = fixture_png([bytes((0, 0, 0, 255))], width=1)
        self.assertEqual(frames.frame_info(black), frames.frame_info(black, use_numpy=False))

    def test_missing_numpy_falls_back_without_changing_diagnostics(self):
        png = fixture_png()
        expected = frames.frame_info(png, use_numpy=False)
        with patch.dict('sys.modules', {'numpy': None}):
            self.assertEqual(frames.frame_info(png), expected)

    def test_transparency_metadata_and_grayscale_keep_portable_results(self):
        rgb = fixture_png([bytes((20, 40, 80, 255, 255, 255))], width=2, color=2)
        chunks = frames._chunks(rgb)
        png = (frames.PNG_SIGNATURE + frames._chunk(*chunks[0])
               + frames._chunk(b'tRNS', struct.pack('>HHH', 20, 40, 80))
               + b''.join(frames._chunk(k, v) for k, v in chunks[1:]))
        self.assertEqual(frames.frame_info(png), frames.frame_info(png, use_numpy=False))
        self.assertEqual(frames.frame_info(png)['non_black_pixels'], 1)
        for color, values in ((0, bytes((255, 0))), (4, bytes((255, 127, 40, 0)))):
            image = fixture_png([values], width=2, color=color)
            self.assertEqual(frames.frame_info(image), frames.frame_info(image, use_numpy=False))

    def test_real_pixel_diagnostics_decode_every_png_filter(self):
        rows = [bytes(((11 + i * 33) % 256 for i in range(8))) for _ in range(5)]
        plain = frames.frame_info(fixture_png(rows))
        filtered = frames.frame_info(fixture_png(rows, filters=[0, 1, 2, 3, 4]))
        self.assertEqual(filtered['pixel_sha256'], plain['pixel_sha256'])
        self.assertEqual((filtered['width'], filtered['height']), (2, 5))
        self.assertTrue(filtered['png_crc_valid'])
        self.assertTrue(filtered['non_black'])
        self.assertGreater(filtered['pixel_variance'], 0)
        self.assertFalse(filtered['game_content_verified'])
        self.assertFalse(filtered['native_tick_binding_verified'])

    def test_black_and_transparent_images_are_not_claimed_game_content(self):
        black = fixture_png([bytes((0, 0, 0, 255))], width=1)
        invisible_red = fixture_png([bytes((255, 0, 0, 0))], width=1)
        for png in (black, invisible_red):
            info = frames.frame_info(png)
            self.assertFalse(info['non_black'])
            self.assertEqual(info['non_black_fraction'], 0)
            self.assertEqual(info['mean_channel_value'], 0)

    def test_encoder_byte_change_is_separate_from_pixel_change(self):
        first = frames.frame_info(fixture_png(compression=0))
        second = frames.frame_info(fixture_png(compression=9))
        changed = frames.compare_frame_info(second, first)
        self.assertTrue(changed['png_bytes_changed'])
        self.assertFalse(changed['pixels_changed'])
        blue = frames.frame_info(fixture_png([bytes((0, 0, 255, 255))], width=1))
        self.assertTrue(frames.compare_frame_info(blue, first)['pixels_changed'])

    def test_gray_rgb_and_alpha_pixels_share_canonical_pixel_hash(self):
        rgba = frames.frame_info(fixture_png([bytes((80, 80, 80, 255))], width=1))
        for color, row in ((0, bytes((80,))), (2, bytes((80, 80, 80))), (4, bytes((80, 255)))):
            self.assertEqual(frames.frame_info(fixture_png([row], width=1, color=color))['pixel_sha256'],
                             rgba['pixel_sha256'])

    def test_corrupt_truncated_and_bomb_pngs_fail_closed(self):
        png = fixture_png()
        corrupt = bytearray(png)
        corrupt[-1] ^= 1
        header = frames._chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 8, 6, 0, 0, 0))
        bomb = frames.PNG_SIGNATURE + header + frames._chunk(b'IDAT', zlib.compress(b'\0' * 100000)) + frames._chunk(b'IEND', b'')
        for value in (b'not png', bytes(corrupt), png[:-2], png + b'extra', bomb):
            with self.subTest(length=len(value)), self.assertRaises(ValueError):
                frames.frame_info(value)

    def test_unsupported_valid_pixel_format_remains_unknown(self):
        header = frames._chunk(b'IHDR', struct.pack('>IIBBBBB', 1, 1, 16, 0, 0, 0, 0))
        png = frames.PNG_SIGNATURE + header + frames._chunk(b'IDAT', zlib.compress(b'\0\x80\0')) + frames._chunk(b'IEND', b'')
        info = frames.frame_info(png)
        self.assertTrue(info['png_crc_valid'])
        self.assertFalse(info['pixel_diagnostics_available'])
        self.assertIsNone(info['non_black'])


class CloudCaptureAndArchiveTests(unittest.TestCase):
    def setUp(self):
        (frames.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='cloud-native-frames-', dir=frames.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.adb = self.folder / 'adb'
        self.adb.write_bytes(b'fixture executable never launched')

    def test_local_adb_capture_preserves_bytes_and_uses_only_explicit_device(self):
        png = fixture_png()
        commands = []
        def runner(command, **kwargs):
            commands.append(command)
            self.assertEqual(kwargs['cwd'], frames.ROOT)
            return subprocess.CompletedProcess(command, 0, png, b'fixture warning')
        bridge = frames.CloudAndroidBridge(adb=self.adb, adb_port=5038, serial='fixture:15555',
                                           root_shell='su0', runner=runner)
        self.assertEqual(commands, [])
        self.assertIs(bridge.capture(), png)
        self.assertIs(bridge.capture(), png)
        self.assertEqual(len(commands), 2)
        self.assertEqual(commands[0], [str(self.adb), '-H', '127.0.0.1', '-P', '5038', '-s', 'fixture:15555',
                                       'exec-out', 'su', '0', 'screencap', '-p'])
        self.assertGreaterEqual(bridge.last_capture['elapsed_seconds'], 0)
        self.assertFalse(bridge.last_capture['pixels_changed'])
        prohibited = {'start-server', 'root', 'connect', 'install', 'forward', 'push'}
        self.assertFalse(set(commands[0]) & prohibited)

    def test_capture_failure_and_timeout_are_not_retried(self):
        for failure in (subprocess.CompletedProcess([], 1, b'', b'device offline'),
                        subprocess.TimeoutExpired('fixture', 1)):
            calls = []
            def runner(command, **kwargs):
                calls.append(command)
                if isinstance(failure, Exception):
                    raise failure
                return failure
            bridge = frames.CloudAndroidBridge(adb=self.adb, root_shell='adbd', runner=runner)
            with self.assertRaises((RuntimeError, subprocess.TimeoutExpired)):
                bridge.capture()
            self.assertEqual(len(calls), 1)
            self.assertFalse(bridge.last_capture['success'])
            self.assertIn('error', bridge.last_capture)

    def prepare_index(self, *, ticks=(90, 100, 120)):
        rows = []
        states = []
        for i, tick in enumerate(ticks):
            png = fixture_png([bytes((i * 60, 80, 160, 255))], width=1)
            path = self.folder / f'frame-{i}.png'
            path.write_bytes(png)
            state = {'tick': tick, 'generation': 1, 'next': 1, 'plays': [], 'public_fixture': i}
            sha = frames._digest(state)
            states.append({'state': state, 'state_sha256': sha})
            rows.append({'tick': tick, 'path': str(path.relative_to(frames.ROOT)),
                         'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png),
                         'state_sha256': sha, 'source_sha256': 'fixture-source'})
        index = self.folder / 'index.jsonl'
        index.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        state_path = self.folder / 'states.jsonl'
        state_path.write_text('\n'.join(json.dumps(r) for r in states) + '\n')
        return index, state_path, rows

    def test_validate_rebuilds_bindings_and_detects_file_state_source_tampering(self):
        index, states, rows = self.prepare_index()
        valid = frames.validate_frame_index(index, states_path=states, expected_source_sha256='fixture-source')
        self.assertTrue(valid['state_bindings_verified'])
        self.assertTrue(valid['source_hash_verified'])
        self.assertEqual(valid['unique_pixel_hashes'], 3)
        variants = [dict(rows[0], tick=91), dict(rows[0], sha256='0' * 64),
                    dict(rows[0], source_sha256='different'), dict(rows[0], state_sha256='0' * 64)]
        for row in variants:
            index.write_text(json.dumps(row) + '\n')
            with self.assertRaises(ValueError):
                frames.validate_frame_index(index, states_path=states, expected_source_sha256='fixture-source')
        index.write_text('\n'.join(json.dumps(r) for r in rows) + '\n')
        state_rows = [json.loads(line) for line in states.read_text().splitlines()]
        state_rows[0]['state']['public_fixture'] = 'changed'
        states.write_text('\n'.join(json.dumps(r) for r in state_rows) + '\n')
        with self.assertRaisesRegex(ValueError, 'reconstructed'):
            frames.validate_frame_index(index, states_path=states)

    def test_semantic_binding_reconstructs_generation_and_event_origin(self):
        index, states, rows = self.prepare_index(ticks=(90,))
        raw = {'tick': 90, 'generation': 5, 'next': 8, 'plays': []}
        packet = deepcopy(raw)
        packet.pop('generation')
        packet['next'] = 1
        packet['plays'] = []
        sha = frames._digest(packet)
        states.write_text(json.dumps({'raw_capture': raw, 'all_observed_card_events': [], 'semantic_sha256': sha}) + '\n')
        rows[0]['state_sha256'] = sha
        index.write_text(json.dumps(rows[0]) + '\n')
        self.assertTrue(frames.validate_frame_index(index, states_path=states)['state_bindings_verified'])

    def test_lossless_apng_keeps_idats_and_exact_tick_delays(self):
        index, states, rows = self.prepare_index()
        output = self.folder / 'actual-frames.apng'
        report = frames.export_apng(index, output, states_path=states, expected_source_sha256='fixture-source')
        self.assertEqual(report['duration_seconds'], 31 / 20)
        self.assertEqual(report['sha256'], hashlib.sha256(output.read_bytes()).hexdigest())
        chunks = archive_chunks(output.read_bytes())
        self.assertEqual([struct.unpack('>II', v) for k, v in chunks if k == b'acTL'], [(3, 0)])
        controls = [struct.unpack('>IIIIIHHBB', v) for k, v in chunks if k == b'fcTL']
        self.assertEqual([(v[5], v[6]) for v in controls], [(1, 2), (1, 1), (1, 20)])
        original_idats = [b''.join(v for k, v in frames._chunks(frames.local(row['path']).read_bytes()) if k == b'IDAT')
                          for row in rows]
        copied_idats = [v if k == b'IDAT' else v[4:] for k, v in chunks if k in (b'IDAT', b'fdAT')]
        self.assertEqual(copied_idats, original_idats)
        sequences = [struct.unpack_from('>I', v)[0] for k, v in chunks if k in (b'fcTL', b'fdAT')]
        self.assertEqual(sequences, list(range(len(sequences))))
        with self.assertRaisesRegex(ValueError, 'already exists'):
            frames.export_apng(index, output)

    def test_animation_same_tick_deduplicates_and_backward_time_is_rejected(self):
        index, _, _ = self.prepare_index(ticks=(90, 90, 100))
        report = frames.export_apng(index, self.folder / 'dedup.apng')
        self.assertEqual(report['frame_count'], 2)
        self.assertEqual(report['same_tick_frames_omitted'], 1)
        index, _, _ = self.prepare_index(ticks=(90, 100, 95))
        with self.assertRaisesRegex(ValueError, 'Backward'):
            frames.export_apng(index, self.folder / 'invalid.apng')
        self.assertFalse((self.folder / 'invalid.apng').exists())


if __name__ == '__main__':
    unittest.main()

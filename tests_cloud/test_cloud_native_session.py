"""Browser/native session integration contracts with synthetic transport only.

The fixtures return generated diagnostic PNGs and a toy clock, never gameplay
pixels or native physics. These tests do not attest Android or visual fidelity.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import struct
import tempfile
import threading
import time
import unittest
from unittest.mock import Mock, patch
import zlib

from royale_phase2.cloud_native import CloudNativeSession
from royale_phase2.cloud_frames import validate_frame_index
from royale_phase3.actions import DECK
from royale_phase3.common import ROOT, read, reference, rows, write


def diagnostic_png(value):
    def chunk(kind, data):
        return struct.pack('>I', len(data)) + kind + data + struct.pack('>I', zlib.crc32(kind + data) & 0xffffffff)
    scanline = b'\0' + bytes((value % 255 + 1, 100, 200)) * 2
    return (b'\x89PNG\r\n\x1a\n' + chunk(b'IHDR', struct.pack('>IIBBBBB', 2, 2, 8, 2, 0, 0, 0))
            + chunk(b'IDAT', zlib.compress(scanline * 2)) + chunk(b'IEND', b''))


class SyntheticSessionEngine:
    def __init__(self, **kwargs):
        self.version = {'fingerprint': 'synthetic-session-version'}
        self.trace_path = kwargs.get('trace_path')
        self.resets = 0
        self.calls = []
        self.valid = False
        self.fail_next_advance = False
        self.block_next_advance = None
        self.closed = False
        self.terminal_tick = 110

    def _call(self, method):
        self.calls.append((method, threading.get_ident()))

    def _state_required(self):
        if not self.valid:
            from royale_phase2.backend import NativeStateInvalid
            raise NativeStateInvalid('Synthetic invalid state; explicitly reset')

    def reset(self, match, warmup_ticks=90):
        self._call('reset')
        self.resets += 1
        self.valid = True
        self.match = deepcopy(match)
        self.actions = []
        self._plays = {}
        self.origin = self.resets * 1000
        self.state = {'schema': 'cr-minimal.v4', 'ok': True, 'generation': self.resets,
            'tick': warmup_ticks, 'next': self.origin + 1, 'plays': [], 'entities': [],
            'crowns': [0, 0], 'result': None, 'finalized': False,
            'players': [{'owner': owner, 'elixir': 100000, 'cycle': list(DECK[4:]),
                'hand': [[slot, card, 3, card, card] for slot, card in enumerate(DECK[:4])],
                'evolution': [], 'abilities': []} for owner in (0, 1)]}
        return deepcopy(self.state)

    def observe_visible(self, owner):
        self._call('observe_visible')
        self._state_required()
        player = self.state['players'][owner]
        return {'tick': self.state['tick'], 'generation': self.state['generation'],
            'native_owner': owner, 'own_elixir_raw': player['elixir'], 'towers': [],
            'hand': [{'slot': row[0], 'card': row[1], 'cost': row[2]} for row in player['hand']]}

    def debug_state(self):
        self._call('debug_state')
        self._state_required()
        return deepcopy(self.state)

    def _capture(self, generation, cursor):
        self._call('capture')
        self._state_required()
        if generation != self.state['generation']:
            raise RuntimeError('generation changed')
        self.state['plays'] = [deepcopy(v) for key, v in self._plays.items() if key >= cursor]
        return deepcopy(self.state)

    def schedule_card(self, owner, card, x, y, tick, request_id):
        self._call('schedule_card')
        self._state_required()
        result = {'request_id': request_id, 'kind': 'play_card', 'owner': owner, 'card_id': card,
                  'x': x, 'y': y, 'tick': tick, 'generation': self.state['generation'],
                  'registration_receipt': {'registeredAtTick': self.state['tick'], 'executeTick': tick,
                                           'sequence': self.origin + len(self.actions) + 1},
                  'before_registration': {'tick': self.state['tick']}}
        self.actions.append(result)
        return deepcopy(result)

    def advance_exact_ticks(self, ticks):
        self._call('advance')
        self._state_required()
        if self.block_next_advance is not None:
            entered, release = self.block_next_advance
            self.block_next_advance = None
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Test coordination timeout')
        if self.fail_next_advance:
            self.fail_next_advance = False
            self.valid = False
            from royale_phase2.backend import NativeStateInvalid
            raise NativeStateInvalid('Synthetic unknown mutation outcome')
        before = self.state['tick']
        tick = min(before + ticks, self.terminal_tick)
        new_events = []
        for action in self.actions:
            if before < action['tick'] <= tick:
                event = [self.origin + len(self._plays) + 1, action['tick'], action['owner'], action['card_id'], 3]
                self._plays[event[0]] = event
                new_events.append(event)
                action['after_execution'] = {'tick': tick}
        self.state.update(tick=tick, plays=deepcopy(new_events), next=self.origin + len(self._plays) + 1)
        if tick == self.terminal_tick:
            self.state.update(finalized=True, result=0, crowns=[1, 0])
        return deepcopy(self.state)

    def execution_feedback(self, request_ids=None):
        self._call('execution_feedback')
        self._state_required()
        return [{**deepcopy(a), 'native_injected': self.state['tick'] >= a['tick'],
                 'card_consumed': self.state['tick'] >= a['tick'],
                 'actual_execution_tick': a['tick'] if self.state['tick'] >= a['tick'] else None,
                 'consumption_status': 'confirmed_native_card_event' if self.state['tick'] >= a['tick'] else 'not_observed'}
                for a in self.actions if request_ids is None or a['request_id'] in request_ids]

    def export_journal(self):
        self._call('export_journal')
        return {'schema': 'royale-phase2-native-journal.v1',
                'version_fingerprint': self.version['fingerprint'],
                'identity_receipt_sha256': 'synthetic-no-identity-proof',
                'match': deepcopy(getattr(self, 'match', None)), 'initialization_tick': 90,
                'actions': deepcopy(getattr(self, 'actions', [])),
                'actual_card_events': list(deepcopy(getattr(self, '_plays', {})).values())}

    def result(self):
        self._call('result')
        self._state_required()
        return {k: deepcopy(self.state[k]) for k in ('tick', 'generation', 'finalized', 'result', 'crowns')}

    def close(self):
        self._call('close')
        self.closed = True
        self.valid = False


class SyntheticBridge:
    def __init__(self, engines):
        self.engines = engines
        self.threads = []

    def capture(self):
        self.threads.append(threading.get_ident())
        return diagnostic_png(self.engines[0].state['tick'])


class CloudNativeSessionContracts(unittest.TestCase):
    def setUp(self):
        (ROOT / '.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='cloud-native-session-', dir=ROOT / '.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.engines = []
        self.leases = []
        self.session = None
        self.addCleanup(self._close)

    def _close(self):
        if self.session:
            self.session.close(timeout=8)

    def start(self, *, capture_ticks=2, max_tick=120):
        @contextmanager
        def lease():
            self.leases.append(('enter', threading.get_ident()))
            try:
                yield
            finally:
                self.leases.append(('exit', threading.get_ident()))
        def factory(**kwargs):
            engine = SyntheticSessionEngine(**kwargs)
            self.engines.append(engine)
            return engine
        self.session = CloudNativeSession(self.folder/'session', archive_root=self.folder/'archives',
            engine_factory=factory, bridge=SyntheticBridge(self.engines), lease_factory=lease,
            frame_wait=0, capture_ticks=capture_ticks, max_tick=max_tick)
        self.wait(lambda: self.session.public()['ready'] and not self.session.public()['busy'])
        return self.session

    def wait(self, condition, timeout=8):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return
            time.sleep(.01)
        self.fail('Timed out waiting for synthetic worker: ' + str(self.session.public() if self.session else None))

    def command(self, name, data=None, *, success=True):
        job = self.session.submit(name, data)
        self.wait(lambda: self.session.public()['jobs'].get(job, {}).get('status') in ('done', 'failed')
                  or self.session.closed.is_set())
        result = self.session.public()['jobs'].get(job, {})
        if success:
            self.assertEqual(result.get('status'), 'done', result)
        else:
            self.assertEqual(result.get('status'), 'failed', result)
        return result

    def reset(self):
        self.command('reset', {'owner': 1, 'seed': 17, 'opponent': 'wait'})

    def play(self):
        state = self.session.public()
        card = state['hand'][0]
        return self.command('play', {'generation': state['generation'], 'expected_tick': state['tick'],
            'slot': card['slot'], 'card_id': card['card_id'], 'x': 3500, 'y': 21000})

    def test_worker_owns_all_rpc_and_pngs_are_bound_to_rebuilt_raw_states(self):
        session = self.start()
        self.assertEqual(self.engines[0].resets, 0)
        self.assertIsNone(session.public()['tick'])
        self.reset()
        self.play()
        self.assertTrue(session.public()['last_action']['card_consumed'])
        self.assertTrue(session.public()['feedback'][-1]['card_consumed'])
        self.command('step', {'ticks': 3})
        self.command('save')
        source = session.active_dir
        report = validate_frame_index(source/'frames/index.jsonl', states_path=source/'native/states-bound.jsonl')
        self.assertTrue(report['state_bindings_verified'])
        self.assertFalse(report['game_content_verified'])
        worker_id = session.thread.ident
        self.assertEqual({tid for _, tid in self.engines[0].calls}, {worker_id})
        self.assertEqual(set(session.bridge.threads), {worker_id})
        before = len(self.engines[0].calls)
        session.public(); session.latest_frame(); session.archives()
        self.assertEqual(len(self.engines[0].calls), before)
        self.assertEqual(self.leases, [('enter', worker_id)])

    def test_capture_every_tick_honors_requested_cadence_even_for_multi_tick_steps(self):
        session = self.start(capture_ticks=1)
        self.reset()
        self.command('step', {'ticks': 4})
        captured = [row['tick'] for row in rows(session.active_dir/'frames/index.jsonl')]
        self.assertEqual(sorted(set(captured)), [90, 91, 92, 93, 94])

    def test_dense_tick_samples_do_not_claim_fresh_twenty_hz_rendered_frames(self):
        session = self.start(capture_ticks=1)
        self.reset()
        self.command('step', {'ticks': 20})
        manifest = read(session.active_dir/'manifest.json')
        self.assertTrue(manifest['natural_terminal'])
        self.assertTrue(manifest['complete_frame_coverage'])
        self.assertTrue(manifest['full_20hz_samples'])
        self.assertFalse(manifest['full_20hz_frames'])
        self.assertFalse(manifest['fresh_frame_rate_verified'])

    def test_pause_during_advance_stops_at_completed_boundary_then_can_continue(self):
        session = self.start()
        self.reset()
        entered, release = threading.Event(), threading.Event()
        self.engines[0].block_next_advance = entered, release
        self.command('run', {'ticks_per_step': 2})
        self.assertTrue(entered.wait(5))
        pause_job = session.submit('pause')
        release.set()
        self.wait(lambda: session.public()['jobs'][pause_job]['status'] == 'done')
        paused = session.public()['tick']
        self.assertFalse(session.public()['running'])
        time.sleep(.15)
        self.assertEqual(session.public()['tick'], paused)
        self.command('step', {'ticks': 2})
        self.assertEqual(session.public()['tick'], paused + 2)
        self.assertEqual(self.engines[0].resets, 1)

    def test_transport_failure_never_resets_implicitly_and_explicit_reset_can_recover(self):
        session = self.start()
        self.reset()
        self.engines[0].fail_next_advance = True
        self.command('step', {'ticks': 2}, success=False)
        self.assertEqual(self.engines[0].resets, 1)
        self.assertFalse(session.public()['running'])
        self.assertFalse(session.closed.is_set(), 'Recoverable invalid episode must preserve its controller for explicit reset')
        self.command('reset', {'owner': 1, 'seed': 18, 'opponent': 'wait'})
        self.assertEqual(self.engines[0].resets, 2)
        self.assertEqual(session.public()['tick'], 90)

    def test_continuous_failure_preserves_controller_for_explicit_reset(self):
        session = self.start()
        self.reset()
        self.engines[0].fail_next_advance = True
        self.command('run', {'ticks_per_step': 2})
        self.wait(lambda: bool(session.public()['error']))
        self.assertEqual(self.engines[0].resets, 1)
        self.assertFalse(session.public()['running'])
        self.assertFalse(session.closed.is_set(), 'Continuous and manual mutation failures need the same explicit-reset recovery')
        self.command('reset', {'owner': 1, 'seed': 18, 'opponent': 'wait'})
        self.assertEqual(self.engines[0].resets, 2)

    def test_replay_backward_seek_separates_archives_and_preserves_consumption_feedback(self):
        session = self.start(capture_ticks=1)
        self.reset()
        self.play()
        self.command('step', {'ticks': 19})
        self.command('save')
        archive_id = session.archive_id
        original_folder = session.active_dir
        original_journal = (original_folder/'native/journal.json').read_bytes()
        self.command('replay_open', {'archive_id': archive_id})
        first_replay = session.active_dir
        self.command('replay_seek', {'tick': 100})
        self.assertTrue(session.public()['feedback'], 'Only executed prefix feedback should appear')
        self.command('replay_seek', {'tick': 90})
        self.assertNotEqual(session.active_dir, first_replay)
        self.assertEqual(session.public()['feedback'], [])
        self.command('replay_seek', {'tick': 110})
        verification = read(session.active_dir/'replay-verification.json')
        self.assertTrue(verification['saved_state_trajectory_verified'], verification)
        self.assertTrue(verification['natural_terminal_verified'])
        self.assertEqual((original_folder/'native/journal.json').read_bytes(), original_journal)
        events = read(session.active_dir/'native/execution_feedback.json')
        self.assertEqual(len(events), 1)
        self.assertTrue(events[0]['card_consumed'])
        saved_ticks = [row['tick'] for row in rows(session.active_dir/'native/states.jsonl')]
        self.assertEqual(saved_ticks[0], 90)
        self.assertEqual(saved_ticks, sorted(saved_ticks))

    def test_out_of_range_backward_seek_does_not_create_an_empty_archive(self):
        session = self.start()
        self.reset()
        self.command('step', {'ticks': 4})
        self.command('save')
        archive_id = session.archive_id
        self.command('replay_open', {'archive_id': archive_id})
        self.command('replay_seek', {'tick': 94})
        active = session.active_dir
        before_reset_count = self.engines[0].resets
        self.command('replay_seek', {'tick': 0}, success=False)
        self.assertEqual(session.active_dir, active)
        self.assertEqual(self.engines[0].resets, before_reset_count)

    def test_modified_registered_source_is_rejected_before_native_reset(self):
        session = self.start()
        self.reset()
        self.command('step', {'ticks': 4})
        self.command('save')
        source_id = session.archive_id
        source_folder = session.active_dir
        # Seal the source by creating a different live game before tampering.
        self.command('reset', {'owner': 1, 'seed': 18, 'opponent': 'wait'})
        journal_path = source_folder/'native/journal.json'
        journal = read(journal_path)
        journal['match']['rndSeed'] += 1
        write(journal_path, journal)
        write(source_folder/'native/initialization.json', journal['match'])
        resets = self.engines[0].resets
        self.command('replay_open', {'archive_id': source_id}, success=False)
        self.assertEqual(self.engines[0].resets, resets)

    def test_partial_source_run_stops_at_source_end_without_spinning_or_resetting(self):
        session = self.start()
        self.reset()
        self.command('step', {'ticks': 5})
        self.command('save')
        source_id = session.archive_id
        self.command('replay_open', {'archive_id': source_id})
        self.command('run', {'ticks_per_step': 2})
        self.wait(lambda: session.public()['tick'] == 95)
        self.wait(lambda: not session.public()['running'])
        advance_count = sum(method == 'advance' for method, _ in self.engines[0].calls)
        time.sleep(.15)
        self.assertEqual(sum(method == 'advance' for method, _ in self.engines[0].calls), advance_count)
        self.assertEqual(self.engines[0].resets, 2)

    def test_misdirected_native_directory_cannot_bypass_registered_source_hashes(self):
        session = self.start()
        self.reset()
        self.command('step', {'ticks': 4})
        self.command('save')
        source_id, source_folder = session.archive_id, session.active_dir
        self.command('reset', {'owner': 1, 'seed': 18, 'opponent': 'wait'})
        self.command('step', {'ticks': 6})
        self.command('save')
        other_directory = (session.active_dir/'native').relative_to(ROOT).as_posix()
        manifest_path = source_folder/'manifest.json'
        manifest = read(manifest_path)
        manifest['native_directory'] = other_directory
        write(manifest_path, manifest)
        resets = self.engines[0].resets
        self.command('replay_open', {'archive_id': source_id}, success=False)
        self.assertEqual(self.engines[0].resets, resets)

    def test_stale_generation_or_tick_cannot_register_or_consume_an_action(self):
        session = self.start()
        self.reset()
        old = session.public()
        self.command('step', {'ticks': 4})
        for generation, tick in ((old['generation'], old['tick']),
                                 (old['generation'] + 1, session.public()['tick'])):
            self.command('play', {'generation': generation, 'expected_tick': tick, 'slot': 0,
                'card_id': DECK[0], 'x': 3500, 'y': 21000}, success=False)
        self.assertFalse(self.engines[0].actions)
        self.assertEqual(self.engines[0].resets, 1)

    def test_watchdog_truncation_cannot_register_a_future_play(self):
        session = self.start(max_tick=100)
        self.reset()
        self.command('step', {'ticks': 10})
        state = session.public()
        self.assertFalse(state['terminal'])
        self.assertTrue(state['truncated'])
        self.command('play', {'generation': state['generation'], 'expected_tick': state['tick'],
            'slot': 0, 'card_id': DECK[0], 'x': 3500, 'y': 21000}, success=False)
        self.assertFalse(self.engines[0].actions)

    def test_clean_close_releases_lease_and_persists_report_without_session_token(self):
        session = self.start()
        self.reset()
        worker = session.thread.ident
        session.close(timeout=8)
        self.assertFalse(session.thread.is_alive())
        self.assertTrue(self.engines[0].closed)
        self.assertEqual(self.leases, [('enter', worker), ('exit', worker)])
        report = read(session.folder/'report.json')
        self.assertTrue(report['closed'])
        self.assertEqual(report['csrf_token'], 'omitted')
        self.assertNotIn(session.csrf_token, (session.folder/'report.json').read_text())

    def test_import_registers_parent_native_folder_without_rpc_or_invented_frames(self):
        from tools.cloud.native_browser import import_archive
        session = self.start()
        self.reset()
        self.command('step', {'ticks': 3})
        self.command('save')
        original = session.active_dir
        original_bytes = (original/'native/journal.json').read_bytes()
        calls = len(self.engines[0].calls)
        imported = import_archive(original, session.archive_root)
        self.assertEqual(len(self.engines[0].calls), calls)
        self.assertEqual(imported['native_directory'], (original/'native').relative_to(ROOT).as_posix())
        self.assertEqual(imported['frame_count'], 0)
        self.assertFalse(imported['complete_frame_coverage'])
        self.assertEqual(imported['journal']['sha256'], hashlib.sha256(original_bytes).hexdigest())
        self.assertEqual((original/'native/journal.json').read_bytes(), original_bytes)

    def test_offline_session_has_no_worker_engine_bridge_or_lease_and_rejects_control(self):
        lease = Mock(side_effect=AssertionError('Offline must not acquire native lease'))
        with patch('royale_phase2.backend.NativeBattleBackend') as engine, \
                patch('royale_phase2.cloud_frames.CloudAndroidBridge') as bridge, \
                patch('royale_phase2.cloud_native.threading.Thread') as worker:
            self.session = CloudNativeSession(self.folder/'offline', archive_root=self.folder/'archives',
                connection='missing-connection.json', adb='missing-adb', offline=True,
                lease_factory=lease)
            self.assertIsNone(self.session.thread)
            self.assertIsNone(self.session.engine)
            self.assertIsNone(self.session.bridge)
            self.assertTrue(self.session.public()['offline'])
            self.assertEqual(self.session.public()['mode'], 'archive')
            self.assertFalse(self.session.public()['busy'])
            for command in ('reset', 'play', 'step', 'run', 'pause', 'save',
                            'replay_open', 'replay_seek', 'shutdown'):
                with self.subTest(command=command), self.assertRaisesRegex(ValueError, '离线'):
                    self.session.submit(command)
            self.assertTrue(self.session.queue.empty())
            self.session.close()
            engine.assert_not_called()
            bridge.assert_not_called()
            worker.assert_not_called()
            lease.assert_not_called()
        report = read(self.session.folder/'report.json')
        self.assertTrue(report['closed'])
        self.assertTrue(report['offline'])
        self.assertEqual(report['native_rpc_calls'], 0)
        self.assertEqual(report['csrf_token'], 'omitted')
        self.assertNotIn(self.session.csrf_token, (self.session.folder/'report.json').read_text())

    def test_offline_archive_reads_saved_pngs_and_rejects_tampered_or_escaped_frames(self):
        archive_root = self.folder/'archives'
        archive = archive_root/'diagnostic-fixture'
        (archive/'frames').mkdir(parents=True)
        original = diagnostic_png(90)
        image = archive/'frames/000000.png'
        image.write_bytes(original)
        record = {'tick': 90, 'path': image.relative_to(ROOT).as_posix(),
                  'sha256': hashlib.sha256(original).hexdigest()}
        index = archive/'frames/index.jsonl'
        index.write_text(json.dumps(record) + '\n')
        write(archive/'manifest.json', {'archive_id': archive.name, 'kind': 'synthetic_diagnostic',
            'frame_count': 1, 'initial_tick': 90, 'end_tick': 90, 'natural_terminal': False,
            'complete_frame_coverage': False, 'native_directory': None})
        self.session = CloudNativeSession(self.folder/'offline', archive_root=archive_root, offline=True)
        self.assertEqual(self.session.archives()[0]['archive_id'], archive.name)
        self.assertEqual(self.session.manifest(archive.name)['frames'][0]['index'], 0)
        self.assertEqual(self.session.archive_frame(archive.name, 0), original)
        image.write_bytes(diagnostic_png(91))
        with self.assertRaisesRegex(ValueError, '哈希'):
            self.session.archive_frame(archive.name, 0)
        record['path'] = (ROOT/'README.md').relative_to(ROOT).as_posix()
        record['sha256'] = hashlib.sha256((ROOT/'README.md').read_bytes()).hexdigest()
        index.write_text(json.dumps(record) + '\n')
        with self.assertRaises(ValueError):
            self.session.archive_frame(archive.name, 0)
        self.assertIsNone(self.session.engine)
        self.assertIsNone(self.session.thread)

    def index_fixture(self, *, sealed=True):
        archive_root = self.folder/'archives'
        archive = archive_root/'index-fixture'
        (archive/'frames').mkdir(parents=True)
        png = diagnostic_png(90)
        image = archive/'frames/000000.png'
        image.write_bytes(png)
        record = {'tick': 90, 'path': image.relative_to(ROOT).as_posix(),
                  'sha256': hashlib.sha256(png).hexdigest()}
        index = archive/'frames/index.jsonl'
        index.write_text(json.dumps(record) + '\n')
        manifest = {'archive_id': archive.name, 'kind': 'synthetic_diagnostic', 'sealed': sealed,
            'frame_count': 1, 'initial_tick': 90, 'end_tick': 90,
            'frame_index': reference(index) if sealed else
                {'path': index.relative_to(ROOT).as_posix(), 'mutable_recording': True}}
        write(archive/'manifest.json', manifest)
        self.session = CloudNativeSession(self.folder/'offline', archive_root=archive_root, offline=True)
        return archive, index, record

    def test_sealed_index_tampering_is_rejected_before_reading_a_saved_frame(self):
        archive, index, record = self.index_fixture()
        self.assertEqual(self.session.manifest(archive.name)['frames'][0]['tick'], 90)
        previous = index.stat()
        record['tick'] = 91
        index.write_text(json.dumps(record) + '\n')
        os.utime(index, ns=(previous.st_atime_ns, previous.st_mtime_ns + 1000000))
        with self.assertRaisesRegex(ValueError, '索引哈希'):
            self.session.manifest(archive.name)
        with self.assertRaisesRegex(ValueError, '索引哈希'):
            self.session.archive_frame(archive.name, 0)

    def test_frame_index_cache_key_includes_expected_sha_even_when_stat_is_unchanged(self):
        archive, index, record = self.index_fixture()
        original_stat = index.stat()
        self.assertEqual(self.session.manifest(archive.name)['frames'][0]['tick'], 90)
        manifest = read(archive/'manifest.json')
        manifest['frame_index']['sha256'] = '0' * 64
        write(archive/'manifest.json', manifest)
        with self.assertRaisesRegex(ValueError, '索引哈希'):
            self.session.manifest(archive.name)
        record['tick'] = 91
        index.write_text(json.dumps(record) + '\n')
        self.assertEqual(index.stat().st_size, original_stat.st_size)
        os.utime(index, ns=(original_stat.st_atime_ns, original_stat.st_mtime_ns))
        manifest['frame_index'] = reference(index)
        write(archive/'manifest.json', manifest)
        self.assertEqual(self.session.manifest(archive.name)['frames'][0]['tick'], 91)

    def test_mutable_partial_index_tail_is_ignored_until_complete_but_sealed_tail_is_strict(self):
        archive, index, record = self.index_fixture(sealed=False)
        first = index.read_bytes()
        second = json.dumps({**record, 'tick': 95}).encode()
        split = len(second) // 2
        index.write_bytes(first + second[:split])
        self.assertEqual(len(self.session.manifest(archive.name)['frames']), 1)
        with index.open('ab') as stream:
            stream.write(second[split:] + b'\n')
        self.assertEqual([row['tick'] for row in self.session.manifest(archive.name)['frames']], [90, 95])
        with index.open('ab') as stream:
            stream.write(b'{"tick":')
        manifest = read(archive/'manifest.json')
        manifest.update(sealed=True, frame_index=reference(index))
        write(archive/'manifest.json', manifest)
        with self.assertRaises(ValueError):
            self.session.manifest(archive.name)


if __name__ == '__main__':
    unittest.main()

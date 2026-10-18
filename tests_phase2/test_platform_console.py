"""Offline console/API tests. Native RPC and VM startup are never invoked."""
from copy import deepcopy
import hashlib
import http.client
import json
from pathlib import Path
import random
import re
import shutil
import tempfile
import threading
from types import ModuleType
import unittest
from unittest.mock import patch

from royale_phase2.battlefield import build_battlefield_observation
from royale_phase2.observability import build_observation
from royale_phase2.platform import ArchiveSession, LiveSession, make_server, plugin, research_action
from royale_phase2.runtime import ROOT, local
from royale_phase2.viewer import digest, semantic_packet
from royale_phase2.visibility import project_actor, TowerAttestation


EVIDENCE = ROOT / 'outputs/platform_next/console'
NATIVE = ROOT / 'outputs/phase2/fixtures/native-v1/hero_ability_readiness_and_activation/states.jsonl'


class ConsoleTests(unittest.TestCase):
    def setUp(self):
        EVIDENCE.mkdir(parents=True, exist_ok=True)
        self.scratch = Path(tempfile.mkdtemp(prefix='console-test-', dir=EVIDENCE)).resolve()
        self.scratch.relative_to(EVIDENCE.resolve())
        self.native = json.loads(NATIVE.read_text(encoding='utf-8').splitlines()[0])

    def tearDown(self):
        self.scratch.resolve().relative_to(EVIDENCE.resolve())
        shutil.rmtree(self.scratch)

    def archive_fixture(self):
        folder = self.scratch / 'archive'
        (folder / 'frames').mkdir(parents=True)
        origin = self.native['next'] - 1
        states = []
        frames = []
        # The PNG bytes are a format/header fixture; this is not a new native render.
        for index in range(2):
            png = b'\x89PNG\r\n\x1a\nexplicit-test-bytes' + bytes([index])
            raw = deepcopy(self.native)
            raw['tick'] += index
            if index: raw['entities'][0][5] -= 10
            row = {'raw_capture': raw, 'all_observed_card_events': [],
                   'semantic_sha256': digest(semantic_packet(raw, [], origin))}
            states.append(row)
            path = folder / 'frames' / f'{index}.png'
            path.write_bytes(png)
            frames.append({'tick': raw['tick'], 'path': str(path.relative_to(ROOT)),
                           'sha256': hashlib.sha256(png).hexdigest(), 'bytes': len(png),
                           'native_semantic_sha256': row['semantic_sha256']})
        self.write_lines(folder / 'states.jsonl', states)
        self.write_lines(folder / 'frames/index.jsonl', frames)
        return folder, states, frames

    @staticmethod
    def write_lines(path, rows):
        path.write_text(''.join(json.dumps(row) + '\n' for row in rows), encoding='utf-8')

    def test_real_archive_first_and_last_native_frames(self):
        session = ArchiveSession()
        self.assertTrue(session.public()['ready'])
        self.assertGreater(session.public()['archive_count'], 2)
        session.submit('seek', index=len(session.frames)-1)
        state = session.public()
        self.assertEqual(state['tick'], state['observation']['tick'])
        self.assertTrue(state['terminal'])
        self.assertTrue(session.latest_frame.startswith(b'\x89PNG\r\n\x1a\n'))

    def test_real_archive_random_and_backward_seek_has_current_battlefield_history_only(self):
        folder = ROOT/'outputs/platform_next/sessions/20261018T035759.956813Z-replay'
        session = ArchiveSession(folder)
        all_final_events = session.rows[max(session.rows)]['all_observed_card_events']
        indices = [len(session.frames)-1, 0]
        indices += random.Random(20261018).sample(range(len(session.frames)), 8)
        indices += [len(session.frames)-1, 0]
        for index in indices:
            session.submit('seek', index=index)
            public = session.public(); tick = public['tick']; battlefield = public['battlefield']
            raw = session.rows[tick]['raw_capture']
            expected_events = session.rows[tick]['all_observed_card_events']
            tower_ids = {row['entity_id'] for row in battlefield['tower_identities']}
            self.assertEqual(public['actor']['tick'], tick)
            self.assertEqual(battlefield['tick'], tick)
            self.assertEqual([row['entity_id'] for row in battlefield['entities']],
                             [row[0] for row in raw['entities'] if row[0] not in tower_ids])
            self.assertFalse(tower_ids & {row['entity_id'] for row in battlefield['entities']})
            self.assertEqual({row['sequence'] for row in battlefield['history']}, {row[0] for row in expected_events})
            self.assertTrue(all(row['tick'] <= tick for row in battlefield['history']))
            self.assertFalse({row[0] for row in all_final_events if row[1] > tick} &
                             {row['sequence'] for row in battlefield['history']})
            self.assertFalse(battlefield['visibility_fairness_verified'])
            def keys(value):
                if isinstance(value, dict):
                    return set(value) | set().union(*(keys(child) for child in value.values()))
                if isinstance(value, list): return set().union(*(keys(child) for child in value))
                return set()
            self.assertFalse(keys(battlefield) & {'raw', 'players', 'cycle', 'abilities', 'journal',
                                                  'source_actions', 'canonical_journal',
                                                  'target_entity_id', 'attack_cooldown_ms'})
        self.assertEqual(session.public()['battlefield']['history'], [])
        detached = session.public()
        detached['battlefield']['hand'][0]['card'] = 1
        self.assertNotEqual(session.public()['battlefield']['hand'][0]['card'], 1)

    def test_archive_controls_cached_copy_and_snapshot_deltas(self):
        folder, _, _ = self.archive_fixture()
        session = ArchiveSession(folder)
        before = session.public()
        before['observation']['raw']['tick'] = -1
        self.assertEqual(session.public()['tick'], self.native['tick'])
        session.submit('step')
        delta = session.public()['observation']['deltas']
        self.assertTrue(delta['comparable'])
        self.assertEqual(delta['tick_gap'], 1)
        self.assertEqual(delta['entity_changes'][0]['fields']['hp']['after'], self.native['entities'][0][5]-10)
        session.submit('step')
        self.assertEqual(session.index, 1)
        session.submit('seek', index=0)
        self.assertEqual(session.public()['observation']['deltas']['reason'], 'tick_regressed')
        for index in (-1, True, 2, None):
            with self.assertRaises(ValueError): session.submit('seek', index=index)
        with self.assertRaises(ValueError): session.submit('play')
        session.submit('shutdown')
        self.assertTrue(session.closed.is_set())

    def test_archive_raw_state_tampering_is_detected(self):
        folder, states, _ = self.archive_fixture()
        states[0]['raw_capture']['entities'][0][5] -= 1
        self.write_lines(folder / 'states.jsonl', states)
        with self.assertRaisesRegex(ValueError, 'raw state semantic'):
            ArchiveSession(folder)

    def test_archive_frame_hash_and_binding_are_checked(self):
        folder, _, frames = self.archive_fixture()
        image = ROOT / frames[0]['path']
        image.write_bytes(image.read_bytes() + b'tampered')
        with self.assertRaisesRegex(ValueError, 'image SHA256'):
            ArchiveSession(folder)
        image.write_bytes(b'\x89PNG\r\n\x1a\nexplicit-test-bytes' + bytes([0]))
        frames[0]['native_semantic_sha256'] = '0' * 64
        self.write_lines(folder / 'frames/index.jsonl', frames)
        with self.assertRaisesRegex(ValueError, 'Frame/state binding'):
            ArchiveSession(folder)

    def test_archive_frame_tick_and_png_are_checked(self):
        folder, _, frames = self.archive_fixture()
        frames[0]['tick'] = 999999
        self.write_lines(folder / 'frames/index.jsonl', frames)
        with self.assertRaisesRegex(ValueError, 'matching native tick'):
            ArchiveSession(folder)
        frames[0]['tick'] = self.native['tick']
        path = ROOT / frames[0]['path']
        path.write_bytes(b'not png')
        frames[0]['sha256'] = hashlib.sha256(path.read_bytes()).hexdigest()
        self.write_lines(folder / 'frames/index.jsonl', frames)
        with self.assertRaisesRegex(ValueError, 'not a PNG'):
            ArchiveSession(folder)

    def test_path_boundaries_reject_project_and_frame_escapes(self):
        for path in ('../outside.py', ROOT.parent / 'outside.py'):
            with self.assertRaises(ValueError): local(path)
            with self.assertRaises(ValueError): plugin(path)
            with self.assertRaises(ValueError): ArchiveSession(path)
        folder, _, frames = self.archive_fixture()
        frames[0]['path'] = 'CODEX_PHASE2_PROMPT.txt'
        self.write_lines(folder / 'frames/index.jsonl', frames)
        with self.assertRaisesRegex(ValueError, 'frames folder'):
            ArchiveSession(folder)

    def test_conflicting_duplicate_state_tick_rejected(self):
        folder, states, _ = self.archive_fixture()
        duplicate = deepcopy(states[0])
        duplicate['raw_capture']['entities'][0][5] -= 10
        duplicate['semantic_sha256'] = digest(semantic_packet(duplicate['raw_capture'], [], self.native['next']-1))
        states.append(duplicate)
        self.write_lines(folder / 'states.jsonl', states)
        with self.assertRaisesRegex(ValueError, 'Conflicting archive states'):
            ArchiveSession(folder)

    def test_research_action_contract_is_current_slot_budget_and_bounds(self):
        actor = project_actor(self.native, 0)
        card = actor['hand'][0]
        chosen = research_action({'kind': 'play_card', 'slot': card['slot'], 'x': 3500, 'y': 11000}, actor)
        self.assertEqual(chosen['card'], card['card'])
        self.assertEqual(research_action({'kind': 'wait'}, actor), {'kind': 'wait'})
        invalid = [None, [], {'kind': 'ability'}, {'kind': 'play_card', 'slot': True, 'x': 0, 'y': 0},
                   {'kind': 'play_card', 'slot': 0, 'x': -1, 'y': 0},
                   {'kind': 'play_card', 'slot': 0, 'x': 18000, 'y': 0},
                   {'kind': 'play_card', 'slot': 0, 'x': 0, 'y': 32000},
                   {'kind': 'play_card', 'slot': 0, 'x': 0, 'y': False}]
        for action in invalid:
            with self.assertRaises(ValueError): research_action(action, actor)
        actor['own_elixir_raw'] = 0
        with self.assertRaisesRegex(ValueError, 'Insufficient'):
            research_action({'kind': 'play_card', 'slot': 0, 'x': 0, 'y': 0}, actor)
        actor['hand'] = []
        with self.assertRaisesRegex(ValueError, 'slot missing'):
            research_action({'kind': 'play_card', 'slot': 0, 'x': 0, 'y': 0}, actor)

    def test_example_plugin_handles_actual_player_and_oracle_inputs(self):
        decide, receipt = plugin('examples/platform_policy.py')
        self.assertEqual(receipt['sha256'], hashlib.sha256((ROOT / receipt['path']).read_bytes()).hexdigest())
        actor = project_actor(self.native, 0)
        oracle = build_observation(self.native)
        battlefield = build_battlefield_observation(self.native, actor, [])
        self.assertEqual(research_action(decide(actor), actor), research_action(decide(oracle), actor))
        self.assertEqual(research_action(decide(actor), actor), research_action(decide(battlefield), actor))
        actor['own_elixir_raw'] = 0
        self.assertEqual(decide(actor), {'kind': 'wait'})
        no_decide = self.scratch / 'bad_policy.py'
        no_decide.write_text('value = 3\n', encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'export decide'): plugin(no_decide)
        with self.assertRaisesRegex(ValueError, 'Python file'): plugin('CODEX_PHASE2_PROMPT.txt')

    def test_live_custom_policy_receives_selected_current_information_mode_and_logs_it(self):
        saved = [json.loads(line) for line in NATIVE.read_text(encoding='utf-8').splitlines() if line.strip()]
        raw = saved[-1]; attested = TowerAttestation.from_reset(saved[0])
        actors = {owner: project_actor(raw, owner, attested) for owner in (0, 1)}
        initial_actor = project_actor(saved[0], 0, attested)
        initial_battlefield = build_battlefield_observation(saved[0], initial_actor, [])
        actual = {event[0]: event for state in saved for event in state['plays']}
        battlefield = build_battlefield_observation(raw, actors[0], list(actual.values()), previous=initial_battlefield)
        self.assertTrue(battlefield['entities'])
        self.assertTrue(battlefield['history'])
        oracle = build_observation(raw)
        class FakeEngine:
            def __init__(self): self.registrations = []
            def observe_visible(self, owner): return deepcopy(actors[owner])
            def schedule_card(self, owner, card, x, y, tick, request_id):
                row = {'owner': owner, 'card_id': card, 'x': x, 'y': y, 'tick': tick, 'request_id': request_id}
                self.registrations.append(row)
                return deepcopy(row)
        for mode, expected in (('battlefield', battlefield), ('player', actors[0]), ('oracle', oracle)):
            with self.subTest(mode=mode):
                received = []
                affordable = [card for card in actors[0]['hand'] if card['cost']*10000 <= actors[0]['own_elixir_raw']]
                card = min(affordable, key=lambda row: (row['cost'], row['slot']))
                def decide(observation):
                    received.append(deepcopy(observation))
                    return {'kind': 'play_card', 'slot': card['slot'], 'x': 3500, 'y': 11000}
                session = object.__new__(LiveSession)
                session.folder = self.scratch/mode; session.folder.mkdir()
                session.engine = FakeEngine(); session.information_mode = mode; session.custom = decide
                session.observation = deepcopy(oracle); session.battlefield = deepcopy(battlefield)
                session.proof = {'explicit_test_stub': True}; session.policy_info = {'information_mode': mode}
                session.lock = threading.Lock(); session.decisions = []; session.decision_index = 0
                mask = {'schema': 'explicit-offline-test-mask', 'values': [True, False, False, False, False]}
                # Core-only tests have no optional NumPy/Gym dependency. The
                # custom path needs only the opponent hook; no environment or
                # native backend is instantiated by this explicit module stub.
                fake_agent_env = ModuleType('royale_phase2.agent_env')
                fake_agent_env.public_heuristic = lambda actor, index: {'kind': 'wait'}
                def unexpected_bc_validation(*args):
                    self.fail('Custom policy must not enter saved-BC validation')
                fake_agent_env.validate_policy_action = unexpected_bc_validation
                with patch.dict('sys.modules', {'royale_phase2.agent_env': fake_agent_env}), \
                     patch('royale_phase2.trajectories.subspace_mask', return_value=mask) as mocked_mask:
                    session._decide()
                mocked_mask.assert_called_once_with(actors[0], True, session.proof)
                self.assertEqual(received, [expected])
                self.assertEqual(session.decisions[0]['policy_input'], expected)
                self.assertEqual(session.decisions[0]['policy_information_mode'], mode)
                logged = json.loads((session.folder/'decisions.jsonl').read_text(encoding='utf-8').splitlines()[0])
                self.assertEqual(logged['policy_input'], received[0])
                self.assertEqual(logged['model_action']['card'], card['card'])
                self.assertEqual(len(session.engine.registrations), 1)
                self.assertEqual(session.engine.registrations[0]['tick'], raw['tick']+1)
                self.assertEqual(session.next_decision, raw['tick']+20)
                self.assertEqual(session.battlefield, battlefield)

    def test_battlefield_example_targets_entities_and_respects_actual_history_and_budget(self):
        path = ROOT/'outputs/phase2/fixtures/native-v1/spell_air_and_ground/states.jsonl'
        saved = [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]
        attested = TowerAttestation.from_reset(saved[0]); previous = None; actual = {}; by_tick = {}
        actors = {}
        for raw in saved:
            for event in raw['plays']: actual[event[0]] = event
            actor = project_actor(raw, 0, attested)
            previous = build_battlefield_observation(raw, actor, list(actual.values()), previous=previous)
            by_tick[raw['tick']] = previous; actors[raw['tick']] = actor
        decide, _ = plugin('examples/platform_battlefield_policy.py')
        baseline = by_tick[116]
        before = research_action(decide(baseline), actors[116])
        self.assertEqual(before['kind'], 'play_card')
        self.assertTrue(28000000 <= before['card'] < 29000000)
        enemies = [row for row in baseline['entities'] if row['owner'] == 1 and (row['hp'] or 0) > 0]
        self.assertEqual(len(enemies), 1)
        # Explicit coordinate perturbation of a real captured unit. Only its
        # position changes, so a different target demonstrates actual use.
        moved = deepcopy(baseline)
        enemy = next(row for row in moved['entities'] if row['entity_id'] == enemies[0]['entity_id'])
        enemy['x_raw'] = 13777; enemy['y_raw'] = 17777
        after = research_action(decide(moved), actors[116])
        self.assertEqual((after['x'], after['y']), (13777, 17777))
        self.assertNotEqual((before['x'], before['y']), (after['x'], after['y']))
        # Tick138 contains an actual native owner0 Arrows consumption from the
        # saved fixture, rather than an invented submitted command.
        recent = by_tick[138]
        self.assertTrue(any(row['owner'] == 0 and 0 <= recent['tick']-row['tick'] < 40 for row in recent['history']))
        self.assertEqual(decide(recent), {'kind': 'wait'})
        without_recent_history = deepcopy(recent)
        without_recent_history['history'] = [row for row in recent['history'] if row['owner'] != 0]
        self.assertEqual(decide(without_recent_history)['kind'], 'play_card')
        empty_budget = deepcopy(baseline); empty_budget['own_elixir_raw'] = 0
        self.assertEqual(decide(empty_budget), {'kind': 'wait'})

    def test_live_auto_information_mode_defaults_without_starting_any_worker(self):
        custom_path = self.scratch/'auto-custom'
        bc_path = self.scratch/'auto-bc'
        rejected_path = self.scratch/'bc-battlefield-rejected'
        with patch.object(threading.Thread, 'start') as start:
            custom = LiveSession('agent', custom_path, policy_file='examples/platform_battlefield_policy.py', information_mode='auto')
            self.assertEqual(custom.information_mode, 'battlefield')
            self.assertFalse(custom.thread.is_alive())
            self.assertIsNone(custom.engine)
            bc = LiveSession('agent', bc_path, information_mode='auto')
            self.assertEqual(bc.information_mode, 'player')
            self.assertFalse(bc.thread.is_alive())
            self.assertIsNone(bc.engine)
            self.assertEqual(start.call_count, 2)
            with self.assertRaisesRegex(ValueError, 'Saved BC supports player only'):
                LiveSession('agent', rejected_path, information_mode='battlefield')
            self.assertEqual(start.call_count, 2)
            self.assertFalse(rejected_path.exists())

    def test_live_command_queue_validation_without_starting_worker(self):
        import queue
        session = object.__new__(LiveSession)
        session.lock = threading.Lock(); session.closed = threading.Event()
        session.status = {'jobs': {}}; session.queue = queue.Queue()
        ticket = session.submit('advance', ticks=20)
        self.assertEqual(session.queue.get_nowait(), (ticket, 'advance', {'ticks': 20}))
        for ticks in (0, 10001, True, None):
            with self.assertRaises(ValueError): session.submit('advance', ticks=ticks)
        with self.assertRaises(ValueError): session.submit('reset')
        session.closed.set()
        with self.assertRaisesRegex(ValueError, 'Worker closed'): session.submit('step')

    def test_http_loopback_host_token_json_and_dynamic_binding(self):
        folder, _, _ = self.archive_fixture()
        session = ArchiveSession(folder)
        class Pending:
            def public(self): return {'ready': False, 'placeholder': True}
        server = make_server(Pending(), 0)
        self.assertEqual(server.server_address[0], '127.0.0.1')
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        def request(method, path, body=None, headers=None):
            connection = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
            try:
                connection.request(method, path, body=body, headers=headers or {})
                response = connection.getresponse()
                return response.status, response.read(), dict(response.getheaders())
            finally: connection.close()
        try:
            status, page, headers = request('GET', '/')
            self.assertEqual(status, 200)
            self.assertIn('frame-ancestors', headers['Content-Security-Policy'])
            self.assertEqual(headers['Cache-Control'], 'no-store')
            token = re.search(rb'const\s+(?:TOKEN|token)\s*=\s*("[^"]+")', page)
            self.assertIsNotNone(token)
            token = json.loads(token.group(1))
            self.assertTrue(json.loads(request('GET', '/api/state')[1])['placeholder'])
            server.session = session
            self.assertEqual(json.loads(request('GET', '/api/state')[1])['mode'], 'archive')
            self.assertEqual(request('GET', '/api/frame')[0], 200)
            first_sha = session.frames[0]['sha256']
            first_image = session.latest_frame
            self.assertEqual(request('GET', '/missing')[0], 404)
            self.assertEqual(request('GET', '/api/state', headers={'Host': 'evil.example'})[0], 403)
            self.assertEqual(request('POST', '/api/control', '{"command":"step"}')[0], 403)
            auth = {'X-Platform-Token': token, 'Content-Type': 'application/json'}
            self.assertEqual(request('POST', '/api/control', '{"command":"step"}', auth)[0], 200)
            self.assertEqual(session.index, 1)
            self.assertNotEqual(session.latest_frame, first_image)
            status, saved_image, _ = request('GET', '/api/frame?v=' + first_sha)
            self.assertEqual(status, 200)
            self.assertEqual(saved_image, first_image)
            self.assertEqual(hashlib.sha256(saved_image).hexdigest(), first_sha)
            self.assertEqual(request('GET', '/api/frame?v=' + '0' * 64)[0], 404)
            self.assertEqual(request('GET', '/api/frame?v=../../CODEX_PHASE2_PROMPT.txt')[0], 409)
            for body in ('[]', 'null', '1', '"x"', '{', '{}', '{"command":3}'):
                self.assertEqual(request('POST', '/api/control', body, auth)[0], 400)
            self.assertEqual(request('POST', '/api/control', 'x' * 8193, auth)[0], 400)
            self.assertEqual(request('POST', '/api/control', '{"command":"play"}', auth)[0], 400)
            self.assertEqual(request('POST', '/api/control', '{"command":"step"}', {**auth, 'Host': 'evil.example'})[0], 403)
            self.assertEqual(request('POST', '/api/control', '{"command":"step"}', {**auth, 'X-Platform-Token': 'wrong'})[0], 403)
            self.assertEqual(request('POST', '/missing', '{}', auth)[0], 404)
        finally:
            server.shutdown(); thread.join(5); server.server_close()


if __name__ == '__main__':
    unittest.main()

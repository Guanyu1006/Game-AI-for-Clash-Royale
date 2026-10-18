"""Explicit synthetic negative controls for the offline session auditor."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import struct
import tempfile
import unittest

from royale_phase2.observability import build_observation
from royale_phase2.battlefield import build_battlefield_observation
from royale_phase2.runtime import ROOT
from royale_phase2.viewer import digest, inspect_mp4, semantic_packet
from royale_phase2.visibility import TowerAttestation, project_actor
from tools.phase2_platform_audit import audit_media, audit_session


class ArtifactAuditTests(unittest.TestCase):
    def setUp(self):
        base = ROOT/'outputs/platform_next/console'
        base.mkdir(parents=True, exist_ok=True)
        self.folder = Path(tempfile.mkdtemp(prefix='artifact-negative-', dir=base)).resolve()
        self.folder.relative_to(base.resolve())
        (self.folder/'frames').mkdir()
        fixture = ROOT/'outputs/phase2/fixtures/native-v1/hero_ability_readiness_and_activation/states.jsonl'
        initial = json.loads(fixture.read_text(encoding='utf-8').splitlines()[0])
        self.states = []; self.observations = []; self.frames = []; self.wire = []
        origin = initial['next']-1
        previous = None
        for index, tick in enumerate((90, 1000, 2000, 3000, 3648)):
            raw = deepcopy(initial); raw['tick'] = tick
            raw['finalized'] = index == 4; raw['result'] = 1 if index == 4 else None
            packet = semantic_packet(raw, [], origin)
            self.states.append({'raw_capture': raw, 'all_observed_card_events': [], 'semantic_sha256': digest(packet)})
            self.observations.append(build_observation(raw, previous)); previous = raw
            self.wire.append({'command': 'minimal 1 0', 'response': raw})
            path = self.folder/'frames'/f'{index}.png'
            data = b'\x89PNG\r\n\x1a\nexplicit-synthetic-control' + bytes([index])
            path.write_bytes(data)
            self.frames.append({'tick': tick, 'path': str(path.relative_to(ROOT)), 'sha256': hashlib.sha256(data).hexdigest(),
                                'bytes': len(data), 'native_semantic_sha256': digest(packet)})
        final = self.states[-1]['raw_capture']
        self.report = {'schema': 'phase2-research-platform-run.v1', 'mode': 'agent',
                       'native_result': {key: final[key] for key in ('finalized', 'result', 'crowns', 'tick', 'generation')},
                       'complete_native_terminal': True, 'truncated': False, 'frames': 5, 'decisions': 0,
                       'actual_card_events': 0, 'submitted_actions': 0, 'consumed_actions': 0}
        self.journal = {'actions': [], 'actual_card_events': []}
        self.feedback = []
        self.sealed = {'error': None, 'tick': final['tick'], 'observation': self.observations[-1]}
        self.flush()

    def tearDown(self):
        self.folder.resolve().relative_to((ROOT/'outputs/platform_next/console').resolve())
        shutil.rmtree(self.folder)

    def dump(self, name, value):
        (self.folder/name).write_text(json.dumps(value), encoding='utf-8')

    def linefile(self, name, values):
        (self.folder/name).write_text(''.join(json.dumps(value)+'\n' for value in values), encoding='utf-8')

    def flush(self):
        for name, values in (('states.jsonl', self.states), ('observations.jsonl', self.observations),
                             ('wire.jsonl', self.wire), ('frames/index.jsonl', self.frames)):
            self.linefile(name, values)
        for name, value in (('session.json', self.sealed), ('report.json', self.report),
                            ('journal.json', self.journal), ('execution_feedback.json', self.feedback)):
            self.dump(name, value)

    def battlefield_log(self):
        towers = TowerAttestation.from_reset(self.states[0]['raw_capture'])
        previous = None; rows = []
        for state in self.states:
            raw = state['raw_capture']
            observation = build_battlefield_observation(raw, project_actor(raw, 0, towers),
                state['all_observed_card_events'], self.journal['actions'], previous)
            rows.append(observation); previous = observation
        self.linefile('battlefield_observations.jsonl', rows)
        self.sealed['battlefield'] = deepcopy(rows[-1]); self.dump('session.json', self.sealed)
        return rows

    def later_native_event(self):
        """Synthetic later wire event: it must not enter an earlier history."""
        event = [101, 1000, 1, 26000001, 3]
        origin = self.states[0]['raw_capture']['next']-1
        previous = None
        for index, state in enumerate(self.states):
            raw = state['raw_capture']
            raw['plays'] = [deepcopy(event)] if index == 1 else []
            cumulative = [deepcopy(event)] if index >= 1 else []
            if index >= 1: raw['next'] = 102
            state['all_observed_card_events'] = cumulative
            state['semantic_sha256'] = digest(semantic_packet(raw, cumulative, origin))
            self.observations[index] = build_observation(raw, previous); previous = raw
            self.wire[index]['response'] = deepcopy(raw)
            self.frames[index]['native_semantic_sha256'] = state['semantic_sha256']
        self.journal['actual_card_events'] = [event]
        self.report['actual_card_events'] = 1
        self.sealed['observation'] = deepcopy(self.observations[-1])
        self.flush()
        return event

    def human_fixture(self, *, ai_play=False, pointer=False):
        """Explicit file-only human/AI fixture; no native execution claim."""
        raw = self.states[0]['raw_capture']
        towers = TowerAttestation.from_reset(raw)
        actor = project_actor(raw, 1, towers)
        card = next(card for card in actor['hand'] if card['cost']*10000 <= actor['own_elixir_raw'])
        chosen = {'kind': 'play_card', 'slot': card['slot'], 'card': card['card'], 'x': 3500, 'y': 21500}
        source = {'x': 3500, 'y': 21500}
        if pointer:
            source = {'u': (111+3500*856/18000)/1080, 'v': (436+21500*1230/32000)/1920}
        self.report.update(mode='human', human_actions=1, human_owner=1, ai_owner=0, decisions=1)
        self.human_rows = []

        def register(owner, action, request_id, sequence):
            receipt = {'ok': True, 'sequence': sequence, 'registeredAtTick': 90, 'executeTick': 91}
            player = next(player for player in raw['players'] if player['owner'] == owner)
            before = {'tick': 90, 'hand': deepcopy(player['hand']), 'cycle': deepcopy(player['cycle']), 'elixir': player['elixir']}
            row = {'request_id': request_id, 'kind': 'play_card', 'owner': owner, 'card_id': action['card'],
                   'x': action['x'], 'y': action['y'], 'tick': 91, 'generation': raw['generation'],
                   'registration_receipt': receipt, 'before_registration': before}
            status = {'ok': True, 'state': 'succeeded', 'sequence': sequence}
            self.wire.extend([
                {'command': f'replay-schedule-card {owner} {action["card"]} {action["x"]} {action["y"]} 91', 'response': receipt},
                {'command': f'replay-schedule-status {sequence}', 'response': status}])
            self.journal['actions'].append(deepcopy(row))
            self.feedback.append({**deepcopy(row), 'scheduler_feedback': status, 'card_consumed': False,
                                  'consumption_status': 'not_observed'})
            return row

        registration = register(1, chosen, 'platform:human:0', 1)
        self.human_rows.append({'human_action_index': 0, 'tick': 90, 'actor_before': actor, 'action': chosen,
                               'registration': registration, 'placement_source': source})
        self.report['submitted_actions'] = 1
        rows = self.battlefield_log()
        decision = {'decision_index': 0, 'tick': 90, 'policy_information_mode': 'battlefield',
                    'policy_input': deepcopy(rows[0]), 'model_action': {'kind': 'wait'},
                    'opponent_action': {'kind': 'wait'}, 'registrations': []}
        if ai_play:
            ai_actor = project_actor(raw, 0, towers)
            ai_card = next(card for card in ai_actor['hand'] if card['cost']*10000 <= ai_actor['own_elixir_raw'])
            ai_action = {'kind': 'play_card', 'slot': ai_card['slot'], 'card': ai_card['card'], 'x': 3500, 'y': 10500}
            decision['model_action'] = ai_action
            decision['registrations'] = [{'controller': 'model', 'registration': register(0, ai_action, 'platform:model:0', 2)}]
            self.report['submitted_actions'] += 1
        self.flush()
        self.linefile('human_actions.jsonl', self.human_rows)
        self.linefile('decisions.jsonl', [decision])
        return decision

    def test_synthetic_consistent_files_pass_without_claiming_native_execution(self):
        result = audit_session(self.folder, require_terminal=True)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['pngs_verified'], 5)
        self.assertEqual(result['rpc_calls'], 0)
        self.assertFalse(result['battlefield_observations_verified'])
        self.assertEqual(result['battlefield_observations'], 0)

    def test_human_and_ai_requests_are_independently_bound_and_unknown_stays_unknown(self):
        self.human_fixture(ai_play=True, pointer=True)
        # Native display stream is asynchronous; intermediate states need not
        # have synchronous PNG labels, but first/last evidence remain required.
        self.frames = [self.frames[0], self.frames[-1]]; self.report['frames'] = 2
        self.flush()
        result = audit_session(self.folder)
        self.assertEqual(result['human_actions_verified'], 1)
        self.assertEqual(result['decision_registrations_verified'], 1)
        self.assertEqual(result['registered_by_controller'], {'model': 1, 'human': 1})
        self.assertEqual(result['confirmed_consumptions_cross_checked'], 0)
        self.assertEqual(result['unconfirmed_consumptions']['count'], 2)
        self.assertEqual(result['feedback_statuses'], {'not_observed': 2})
        self.assertIn('human_actions.jsonl', result['input_sha256'])

    def test_human_empty_action_file_is_optional_only_without_unrecorded_requests(self):
        self.battlefield_log()
        self.report.update(mode='human', human_actions=0, human_owner=1, ai_owner=0)
        self.dump('report.json', self.report)
        self.assertEqual(audit_session(self.folder)['human_actions_verified'], 0)
        self.human_fixture(); (self.folder/'human_actions.jsonl').unlink()
        with self.assertRaisesRegex(ValueError, 'no recorded decision or human request'):
            audit_session(self.folder)

    def test_human_actor_before_cannot_include_other_owner_or_future_state(self):
        self.human_fixture()
        raw = self.states[1]['raw_capture']
        self.human_rows[0]['actor_before'] = project_actor(raw, 1, TowerAttestation.from_reset(self.states[0]['raw_capture']))
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'conservative owner1 raw projection'):
            audit_session(self.folder)

    def test_human_current_slot_is_rechecked(self):
        self.human_fixture()
        self.human_rows[0]['action']['card'] += 1
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'normalized current-slot play'):
            audit_session(self.folder)

    def test_human_budget_cannot_be_invented_in_actor_or_request(self):
        self.human_fixture()
        raw = self.states[0]['raw_capture']
        next(player for player in raw['players'] if player['owner'] == 1)['elixir'] = 0
        origin = raw['next']-1; previous = None
        for index, state in enumerate(self.states):
            state['semantic_sha256'] = digest(semantic_packet(state['raw_capture'], state['all_observed_card_events'], origin))
            self.observations[index] = build_observation(state['raw_capture'], previous); previous = state['raw_capture']
            self.frames[index]['native_semantic_sha256'] = state['semantic_sha256']
        self.human_rows[0]['actor_before'] = project_actor(raw, 1, TowerAttestation.from_reset(raw))
        for action in (self.human_rows[0]['registration'], self.journal['actions'][0], self.feedback[0]):
            action['before_registration']['elixir'] = 0
        self.flush(); self.battlefield_log()
        self.linefile('human_actions.jsonl', self.human_rows)
        # Keep the AI input matched too, so rejection is specifically budget.
        decisions = [json.loads(line) for line in (self.folder/'decisions.jsonl').read_text(encoding='utf-8').splitlines()]
        decisions[0]['policy_input'] = json.loads((self.folder/'battlefield_observations.jsonl').read_text(encoding='utf-8').splitlines()[0])
        self.linefile('decisions.jsonl', decisions)
        with self.assertRaisesRegex(ValueError, 'Insufficient current elixir'):
            audit_session(self.folder)

    def test_human_ordinary_card_cannot_claim_upper_half_demo_placement(self):
        self.human_fixture()
        actor = self.human_rows[0]['actor_before']
        card = next(card for card in actor['hand'] if card['card'] < 28000000 and card['cost']*10000 <= actor['own_elixir_raw'])
        self.human_rows[0]['action'].update(slot=card['slot'], card=card['card'], y=10500)
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'demo lower-half placement restriction'):
            audit_session(self.folder)

    def test_human_pointer_must_match_requested_coordinates_and_remain_in_arena(self):
        self.human_fixture(pointer=True)
        self.human_rows[0]['placement_source']['u'] = .9
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'differs from its saved placement source'):
            audit_session(self.folder)
        self.human_rows[0]['placement_source']['u'] = 0
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'outside the measured arena'):
            audit_session(self.folder)

    def test_human_registration_requires_next_tick_and_exact_wire_command(self):
        self.human_fixture()
        self.human_rows[0]['registration']['tick'] = 92
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'next native tick'):
            audit_session(self.folder)
        self.human_rows[0]['registration']['tick'] = 91
        self.linefile('human_actions.jsonl', self.human_rows)
        self.wire[-2]['command'] += ' changed'
        self.linefile('wire.jsonl', self.wire)
        with self.assertRaisesRegex(ValueError, 'exact raw wire pair'):
            audit_session(self.folder)

    def test_human_ai_cannot_silently_use_public_heuristic_or_player_input(self):
        decision = self.human_fixture()
        decision['opponent_action'] = deepcopy(self.human_rows[0]['action'])
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'automated opponent action'):
            audit_session(self.folder)
        decision['opponent_action'] = {'kind': 'wait'}
        decision['policy_information_mode'] = 'player'
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'must use battlefield'):
            audit_session(self.folder)

    def test_human_registration_cannot_relabel_owner_or_journal_position(self):
        self.human_fixture()
        self.human_rows[0]['registration']['owner'] = 0
        self.linefile('human_actions.jsonl', self.human_rows)
        with self.assertRaisesRegex(ValueError, 'controller/owner/kind differs'):
            audit_session(self.folder)
        self.human_rows[0]['registration']['owner'] = 1
        self.linefile('human_actions.jsonl', self.human_rows)
        self.journal['actions'][0]['x'] += 1000
        self.feedback[0]['x'] += 1000
        self.flush()
        with self.assertRaisesRegex(ValueError, 'Action/journal registration differs: x'):
            audit_session(self.folder)

    def test_human_missing_final_png_and_forged_feedback_are_rejected(self):
        self.human_fixture()
        original_frames = deepcopy(self.frames)
        self.frames = self.frames[:-1]; self.report['frames'] -= 1; self.flush()
        with self.assertRaisesRegex(ValueError, 'Frames do not cover'):
            audit_session(self.folder)
        self.frames = original_frames; self.report['frames'] += 1; self.flush()
        self.feedback[-1]['card_consumed'] = True
        self.dump('execution_feedback.json', self.feedback)
        with self.assertRaisesRegex(ValueError, 'no native wire event'):
            audit_session(self.folder)

    def test_battlefield_log_rebuild_and_same_tick_capture_coverage(self):
        rows = self.battlefield_log()
        # Screenshot commands can add another raw state without another _accept.
        self.states.insert(2, deepcopy(self.states[1]))
        self.linefile('states.jsonl', self.states)
        result = audit_session(self.folder, require_terminal=True)
        self.assertTrue(result['battlefield_observations_verified'])
        self.assertEqual(result['battlefield_observations'], len(rows))
        self.assertEqual(result['state_rows'], len(rows)+1)
        self.assertEqual(result['battlefield_final']['tick'], 3648)
        self.assertEqual(result['battlefield_final']['history_events'], 0)
        self.assertEqual(result['battlefield_final']['actual_positions_observed'], 0)
        self.assertFalse(result['battlefield_final']['visibility_fairness_verified'])
        self.assertIn('battlefield_observations.jsonl', result['input_sha256'])

    def test_battlefield_entity_cannot_be_added_without_raw_evidence(self):
        rows = self.battlefield_log()
        rows[1]['entities'].append({'entity_id': 999, 'owner': 1, 'x_raw': 500, 'y_raw': 500})
        self.linefile('battlefield_observations.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'independent raw/event reconstruction'):
            audit_session(self.folder)

    def test_battlefield_json_types_cannot_masquerade_as_native_integers(self):
        rows = self.battlefield_log()
        self.assertEqual(rows[1]['native_owner'], 0)
        rows[1]['native_owner'] = False  # Python equality alone treats False == 0.
        self.linefile('battlefield_observations.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'independent raw/event reconstruction'):
            audit_session(self.folder)

    def test_battlefield_log_cannot_omit_a_native_state(self):
        rows = self.battlefield_log()
        self.linefile('battlefield_observations.jsonl', rows[:2]+rows[3:])
        with self.assertRaisesRegex(ValueError, 'no reconstructed battlefield'):
            audit_session(self.folder)

    def test_battlefield_history_cannot_import_a_later_real_wire_event(self):
        self.later_native_event()
        rows = self.battlefield_log()
        self.assertEqual(rows[0]['history'], [])
        self.assertEqual(rows[1]['history'][0]['tick'], 1000)
        self.assertEqual(audit_session(self.folder)['status'], 'pass')
        rows[0]['history'] = deepcopy(rows[1]['history'])
        self.linefile('battlefield_observations.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'independent raw/event reconstruction'):
            audit_session(self.folder)

    def test_battlefield_requested_position_cannot_be_labeled_actual(self):
        self.later_native_event()
        rows = self.battlefield_log()
        rows[1]['history'][0]['actual_position'] = {'x': 500, 'y': 500}
        self.linefile('battlefield_observations.jsonl', rows)
        with self.assertRaisesRegex(ValueError, 'independent raw/event reconstruction'):
            audit_session(self.folder)

    def test_battlefield_policy_input_is_bound_to_its_decision_tick(self):
        self.later_native_event()
        rows = self.battlefield_log()
        decision = {'decision_index': 0, 'tick': 90, 'policy_information_mode': 'battlefield',
                    'policy_input': deepcopy(rows[0]), 'model_action': {'kind': 'wait'},
                    'opponent_action': {'kind': 'wait'}, 'registrations': []}
        self.report['decisions'] = 1; self.dump('report.json', self.report)
        self.linefile('decisions.jsonl', [decision])
        self.assertEqual(audit_session(self.folder)['battlefield_policy_decisions'], 1)
        # This later input is valid on its own but leaks the next native event.
        decision['policy_input'] = deepcopy(rows[1])
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'Battlefield policy input differs'):
            audit_session(self.folder)

    def test_battlefield_policy_requires_a_reconstructible_log(self):
        raw = self.states[0]['raw_capture']
        offered = build_battlefield_observation(raw, project_actor(raw, 0, TowerAttestation.from_reset(raw)), [])
        decision = {'decision_index': 0, 'tick': 90, 'policy_information_mode': 'battlefield',
                    'policy_input': offered, 'model_action': {'kind': 'wait'},
                    'opponent_action': {'kind': 'wait'}, 'registrations': []}
        self.report['decisions'] = 1; self.dump('report.json', self.report)
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'requires its saved observation log'):
            audit_session(self.folder)

    def test_battlefield_final_seal_and_file_are_both_required(self):
        self.battlefield_log()
        self.sealed['battlefield']['own_elixir_raw'] += 1
        self.dump('session.json', self.sealed)
        with self.assertRaisesRegex(ValueError, 'Sealed battlefield differs'):
            audit_session(self.folder)
        (self.folder/'battlefield_observations.jsonl').unlink()
        with self.assertRaisesRegex(ValueError, 'Sealed battlefield requires'):
            audit_session(self.folder)

    def test_raw_changed_and_rehashed_but_no_matching_wire_fails(self):
        self.states[2]['raw_capture'] = deepcopy(self.states[2]['raw_capture'])
        self.states[2]['raw_capture']['entities'][0][5] -= 1
        self.states[2]['semantic_sha256'] = digest(semantic_packet(self.states[2]['raw_capture'], [], self.states[0]['raw_capture']['next']-1))
        self.linefile('states.jsonl', self.states)
        with self.assertRaisesRegex(ValueError, 'exact raw wire'):
            audit_session(self.folder)

    def test_png_tampering_fails(self):
        image = ROOT/self.frames[2]['path']; image.write_bytes(image.read_bytes()+b'tampered')
        with self.assertRaisesRegex(ValueError, 'PNG SHA256'):
            audit_session(self.folder)

    def test_lossless_observation_tampering_fails(self):
        self.observations[1]['entities'][0]['hp'] -= 1
        self.linefile('observations.jsonl', self.observations)
        with self.assertRaisesRegex(ValueError, 'lossless reproducible'):
            audit_session(self.folder)

    def test_unsealed_session_is_rejected(self):
        (self.folder/'session.json').unlink()
        with self.assertRaises(OSError): audit_session(self.folder)

    def test_report_count_tampering_fails(self):
        self.report['frames'] += 1; self.dump('report.json', self.report)
        with self.assertRaisesRegex(ValueError, 'Report count'):
            audit_session(self.folder)

    def test_cumulative_event_invention_fails(self):
        self.states[2]['all_observed_card_events'] = [[999, 2000, 0, 28000001, 3]]
        self.linefile('states.jsonl', self.states)
        with self.assertRaisesRegex(ValueError, 'Cumulative events'):
            audit_session(self.folder)

    def test_decision_registration_is_bound_to_chosen_next_tick_action(self):
        raw = self.states[0]['raw_capture']
        actor = project_actor(raw, 0, TowerAttestation.from_reset(raw))
        card = actor['hand'][0]
        chosen = {'kind': 'play_card', 'slot': card['slot'], 'card': card['card'], 'x': 3500, 'y': 11000}
        receipt = {'ok': True, 'sequence': 1, 'registeredAtTick': 90, 'executeTick': 91}
        action = {'request_id': 'explicit-fixture', 'kind': 'play_card', 'owner': 0,
                  'card_id': card['card'], 'x': 3500, 'y': 11000, 'tick': 91,
                  'generation': raw['generation'], 'registration_receipt': receipt}
        decision = {'decision_index': 0, 'tick': 90, 'policy_information_mode': 'player',
                    'policy_input': actor, 'model_action': chosen, 'opponent_action': {'kind': 'wait'},
                    'registrations': [{'controller': 'model', 'registration': deepcopy(action)}]}
        scheduler = {'ok': True, 'state': 'failed', 'sequence': 1}
        self.wire.extend([{'response': receipt}, {'response': scheduler}])
        self.journal['actions'] = [action]
        self.feedback = [{**deepcopy(action), 'scheduler_feedback': scheduler, 'card_consumed': False,
                          'consumption_status': 'not_observed'}]
        self.report.update(decisions=1, submitted_actions=1)
        self.flush(); self.linefile('decisions.jsonl', [decision])
        self.assertEqual(audit_session(self.folder)['decision_registrations_verified'], 1)
        decision['registrations'][0]['registration']['tick'] = 92
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'next native tick'):
            audit_session(self.folder)

    def test_oracle_plugin_input_and_explicit_truncation_are_supported(self):
        final = self.states[-1]['raw_capture']
        final['finalized'] = False; final['result'] = None
        self.states[-1]['semantic_sha256'] = digest(semantic_packet(final, [], self.states[0]['raw_capture']['next']-1))
        self.frames[-1]['native_semantic_sha256'] = self.states[-1]['semantic_sha256']
        self.observations[-1] = build_observation(final, self.states[-2]['raw_capture'])
        self.report['native_result'] = {key: final[key] for key in ('finalized', 'result', 'crowns', 'tick', 'generation')}
        self.report.update(complete_native_terminal=False, truncated=True, decisions=1)
        self.sealed.update(observation=self.observations[-1])
        decision = {'decision_index': 0, 'tick': 90, 'policy_information_mode': 'oracle',
                    'policy_input': deepcopy(self.observations[0]), 'model_action': {'kind': 'wait'},
                    'opponent_action': {'kind': 'wait'}, 'registrations': []}
        self.flush(); self.linefile('decisions.jsonl', [decision])
        result = audit_session(self.folder)
        self.assertFalse(result['native_terminal'])
        self.assertTrue(result['truncated'])
        with self.assertRaisesRegex(ValueError, 'Natural native terminal required'):
            audit_session(self.folder, require_terminal=True)
        decision['policy_input']['entities'][0]['hp'] -= 1
        self.linefile('decisions.jsonl', [decision])
        with self.assertRaisesRegex(ValueError, 'lossless reconstructed observation'):
            audit_session(self.folder)

    def media_fixture(self):
        # An explicit container-parser fixture; no codec/pixel fidelity claim.
        def box(kind, content):
            return struct.pack('>I4s', len(content)+8, kind)+content
        moov = b'mvhd'+bytes(12)+struct.pack('>II', 1000, 10000)+b'avc1'+bytes(8)
        data = box(b'ftyp', b'isom'+bytes(8))+box(b'mdat', bytes(10))+box(b'moov', moov)
        path = self.folder/'part-001.mp4'; path.write_bytes(data)
        part = {'index': 1, 'path': str(path.relative_to(ROOT)), 'start_tick': 90, 'end_tick': 3648,
                **inspect_mp4(data)}
        recording = {'parts': [part], 'error': None}
        self.report['recording'] = deepcopy(recording)
        self.dump('recording.json', recording); self.dump('report.json', self.report)
        return path, recording

    def test_media_receipt_hash_validity_and_png_binding(self):
        path, _ = self.media_fixture()
        result = audit_media(self.folder)
        self.assertEqual(result['status'], 'pass')
        self.assertEqual(result['png_count'], 5)
        self.assertEqual(result['mp4_part_count'], 1)
        path.write_bytes(path.read_bytes()+b'tampered')
        with self.assertRaisesRegex(ValueError, 'MP4 receipt differs'):
            audit_media(self.folder)

    def test_media_tick_coverage_cannot_claim_a_missing_terminal_interval(self):
        _, recording = self.media_fixture()
        recording['parts'][0]['end_tick'] = 3000
        self.report['recording'] = deepcopy(recording)
        self.dump('recording.json', recording); self.dump('report.json', self.report)
        with self.assertRaisesRegex(ValueError, 'full saved native interval'):
            audit_media(self.folder)

    def test_five_point_replay_reference_compares_all_semantic_fields(self):
        self.report['mode'] = 'replay'; self.dump('report.json', self.report)
        anchors = {'version_fingerprint': 'explicit-synthetic-control', 'initialization_sha256': 'fixture',
                   'journal_sha256': 'fixture', 'expected_anchors': [row['raw_capture']['tick'] for row in self.states], 'anchors': {}}
        origin = self.states[0]['raw_capture']['next']-1
        for row in self.states:
            packet = semantic_packet(row['raw_capture'], [], origin)
            anchors['anchors'][str(row['raw_capture']['tick'])] = {**deepcopy(row), 'semantic_packet': packet}
        self.dump('anchors.json', anchors); self.dump('reference.json', anchors)
        result = audit_session(self.folder, self.folder/'reference.json', require_terminal=True)
        self.assertTrue(result['replay_reference']['five_points_equal'])
        anchors['anchors']['2000']['semantic_packet']['entities'][0][5] -= 1
        self.dump('reference.json', anchors)
        with self.assertRaisesRegex(ValueError, 'independent saved headless'):
            audit_session(self.folder, self.folder/'reference.json')

    def test_bounded_replay_compares_reached_anchors_without_claiming_five(self):
        origin = self.states[0]['raw_capture']['next']-1
        reference = {'version_fingerprint': 'explicit-synthetic-control', 'initialization_sha256': 'fixture',
                     'journal_sha256': 'fixture', 'expected_anchors': [row['raw_capture']['tick'] for row in self.states],
                     'anchors': {}}
        for row in self.states:
            reference['anchors'][str(row['raw_capture']['tick'])] = {
                **deepcopy(row), 'semantic_packet': semantic_packet(row['raw_capture'], [], origin)}
        self.dump('reference.json', reference)
        stored = deepcopy(reference)
        stored['anchors'] = {tick: row for tick, row in stored['anchors'].items() if int(tick) <= 1000}
        self.dump('anchors.json', stored)
        self.states = self.states[:2]; self.observations = self.observations[:2]
        self.frames = self.frames[:2]; self.wire = self.wire[:2]
        last = self.states[-1]['raw_capture']
        self.report.update(mode='replay', frames=2, complete_native_terminal=False, truncated=True,
            native_result={key: last[key] for key in ('finalized', 'result', 'crowns', 'tick', 'generation')})
        self.sealed.update(tick=last['tick'], observation=self.observations[-1])
        self.flush()
        result = audit_session(self.folder, self.folder/'reference.json')
        self.assertFalse(result['native_terminal'])
        self.assertFalse(result['replay_reference']['five_points_equal'])
        self.assertEqual(result['replay_reference']['compared_anchors'], [90, 1000])
        self.assertEqual(result['replay_reference']['not_yet_reached_anchors'], [2000, 3000, 3648])
        self.assertEqual(result['replay_reference']['comparison_scope'], 'bounded_partial_reference_comparison')
        with self.assertRaisesRegex(ValueError, 'Natural native terminal required'):
            audit_session(self.folder, self.folder/'reference.json', require_terminal=True)
        stored['anchors']['1000']['semantic_packet']['entities'][0][5] -= 1
        self.dump('anchors.json', stored)
        with self.assertRaisesRegex(ValueError, 'Replay anchor packet/hash differs'):
            audit_session(self.folder, self.folder/'reference.json')


if __name__ == '__main__':
    unittest.main()

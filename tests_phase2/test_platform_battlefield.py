"""Saved native examples plus explicit privacy/future/ambiguity controls; no RPC."""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from royale_phase2.battlefield import build_battlefield_observation, SCHEMA
from royale_phase2.visibility import project_actor, TowerAttestation


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT/'outputs/phase2/fixtures/native-v1'


def states(name):
    return [json.loads(line) for line in (FIXTURES/name/'states.jsonl').read_text(encoding='utf-8').splitlines() if line.strip()]


class BattlefieldTests(unittest.TestCase):
    def setUp(self):
        self.initial = states('hero_ability_readiness_and_activation')[0]
        self.towers = TowerAttestation.from_reset(self.initial)

    def at(self, tick=120):
        raw = deepcopy(self.initial); raw['tick'] = tick
        actor = project_actor(raw, 0, self.towers)
        return raw, actor

    def base_previous(self):
        return build_battlefield_observation(self.initial, project_actor(self.initial, 0, self.towers), [])

    def test_real_saved_states_preserve_all_non_tower_rows_and_real_events(self):
        count = 0
        for fixture in ('hero_ability_readiness_and_activation', 'attack_damage_and_cooldown',
                        'spell_air_and_ground', 'tombstone_death_spawn'):
            saved = states(fixture); attested = TowerAttestation.from_reset(saved[0]); previous = None; actual = {}
            tower_ids = {identity.entity_id for identity in attested.identities}
            for raw in saved:
                for event in raw['plays']: actual[event[0]] = event
                actor = project_actor(raw, 0, attested)
                observation = build_battlefield_observation(raw, actor, list(actual.values()), previous=previous)
                expected = [row for row in raw['entities'] if row[0] not in tower_ids]
                self.assertEqual([row['entity_id'] for row in observation['entities']], [row[0] for row in expected])
                for entity, row in zip(observation['entities'], expected):
                    self.assertEqual([entity[key] for key in ('owner', 'card_id_raw', 'x_raw', 'y_raw', 'hp', 'max_hp', 'native_kind', 'deployment_remaining_ms')],
                                     [row[index] for index in (1, 2, 3, 4, 5, 6, 9, 10)])
                    self.assertEqual(entity['visibility'], 'unknown')
                self.assertEqual(len(observation['history']), len(actual))
                self.assertFalse(observation['visibility_fairness_verified'])
                json.dumps(observation, allow_nan=False)
                previous = observation; count += 1
        self.assertGreater(count, 15)

    def test_enemy_private_target_cooldown_and_extra_actor_fields_do_not_leak(self):
        raw, actor = self.at(90)
        expected = build_battlefield_observation(raw, actor, [])
        changed = deepcopy(raw)
        changed['players'][1].update(elixir='SECRET_ELIXIR', hand=['SECRET_HAND'], cycle=['SECRET_CYCLE'], abilities=['SECRET_ABILITY'])
        changed['players'][0]['cycle'] = ['SECRET_OWN_CYCLE']
        for entity in changed['entities']:
            entity[7] = {'hidden_target': 'SECRET_TARGET'}; entity[8] = 'SECRET_COOLDOWN'
        actor['secret'] = 'SECRET_ACTOR'
        actor['hand'][0]['raw'] = 'SECRET_HAND_RAW'
        actor['crowns']['extra'] = 'SECRET_CROWNS'
        for tower in actor['towers']: tower['raw'] = 'SECRET_TOWER_RAW'
        observation = build_battlefield_observation(changed, actor, [])
        self.assertEqual(observation, expected)
        self.assertNotIn('SECRET', json.dumps(observation))
        self.assertNotIn('raw', observation)

    def test_future_journal_never_creates_history_or_changes_output(self):
        raw, actor = self.at()
        expected = build_battlefield_observation(raw, actor, [], previous=self.base_previous())
        future = [{'kind': 'play_card', 'owner': 1, 'tick': 9000, 'card_id': 26000001,
                   'x': 3500, 'y': 24000, 'private_future_command': 'DO_NOT_LEAK'}]
        got = build_battlefield_observation(raw, actor, [], journal=future, previous=self.base_previous())
        self.assertEqual(got, expected)
        self.assertEqual(got['history'], [])
        self.assertNotIn('DO_NOT_LEAK', json.dumps(got))

    def test_unique_exact_event_annotates_requested_but_not_actual_position(self):
        raw, actor = self.at()
        actual = [[101, 100, 1, 26000001, 3]]
        action = {'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 3500, 'y': 24000}
        obs = build_battlefield_observation(raw, actor, actual, {'actions': [action]}, self.base_previous())
        row = obs['history'][0]
        self.assertEqual(row['requested_position'], {'x': 3500, 'y': 24000, 'units': 'native_requested_coordinate'})
        self.assertIsNone(row['actual_position'])
        self.assertEqual(row['position_reason'], 'unique_owner_tick_exact_native_card_id_request')
        self.assertEqual(row['native_card_id'], 26000001)

    def test_opaque_alias_and_ambiguous_requests_keep_positions_unknown(self):
        raw, actor = self.at()
        cases = [
            ([[101, 100, 1, 0, 3]], [{'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 1, 'y': 1}], 'opaque_native_card_id'),
            ([[101, 100, 1, 13000001, 3]], [{'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 1, 'y': 1}], 'native_card_identity_differs_from_request_no_alias_inference'),
            ([[101, 100, 1, 26000001, 3]], [{'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 1, 'y': 1}]*2, 'ambiguous_request_owner_tick'),
            ([[101, 100, 1, 26000001, 3], [102, 100, 1, 26000000, 3]], [{'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 1, 'y': 1}], 'ambiguous_native_owner_tick')]
        for events, requests, reason in cases:
            obs = build_battlefield_observation(raw, actor, events, requests, self.base_previous())
            self.assertIsNone(obs['history'][0]['requested_position'])
            self.assertEqual(obs['history'][0]['position_reason'], reason)

    def test_previous_history_retained_and_sequence_duplicates_deduplicated(self):
        raw, actor = self.at()
        events = [[101, 100, 1, 26000001, 3]]
        action = {'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 3500, 'y': 24000}
        previous = build_battlefield_observation(raw, actor, events+events, [action], self.base_previous())
        self.assertEqual(len(previous['history']), 1)
        raw2, actor2 = self.at(130)
        retained = build_battlefield_observation(raw2, actor2, [], previous=previous)
        self.assertEqual(retained['history'], previous['history'])
        with self.assertRaisesRegex(ValueError, 'Conflicting native event sequence'):
            build_battlefield_observation(raw2, actor2, [[101, 100, 1, 26000000, 3]], previous=previous)

    def test_later_ambiguity_invalidates_previous_requested_position(self):
        raw, actor = self.at()
        previous = build_battlefield_observation(raw, actor, [[101, 100, 1, 26000001, 3]],
            [{'kind': 'play_card', 'owner': 1, 'tick': 100, 'card_id': 26000001, 'x': 1, 'y': 1}], self.base_previous())
        got = build_battlefield_observation(raw, actor, [[102, 100, 1, 26000000, 3]], previous=previous)
        self.assertIsNone(got['history'][0]['requested_position'])
        self.assertEqual(got['history'][0]['position_reason'], 'ambiguous_native_owner_tick')

    def test_reset_discards_previous_epoch_and_reused_sequence(self):
        raw, actor = self.at()
        previous = build_battlefield_observation(raw, actor, [[101, 100, 1, 26000001, 3]], previous=self.base_previous())
        reset = deepcopy(self.initial); reset['generation'] += 1
        reset_actor = project_actor(reset, 0, TowerAttestation.from_reset(reset))
        new = build_battlefield_observation(reset, reset_actor, [[101, 90, 0, 28000001, 3]], previous=previous)
        self.assertTrue(new['history_reset'])
        self.assertEqual(len(new['history']), 1)
        self.assertEqual(new['history'][0]['native_card_id'], 28000001)
        self.assertEqual(new['schema'], SCHEMA)

    def test_future_events_bad_schema_time_regression_and_sequence_order_fail(self):
        raw, actor = self.at()
        for events in ([[101, 121, 0, 26000001, 3]], [[True, 100, 0, 26000001, 3]],
                       [[101, 110, 0, 26000001, 3], [102, 100, 0, 26000001, 3]], [[1, 2]]):
            with self.assertRaises(ValueError): build_battlefield_observation(raw, actor, events)
        previous = build_battlefield_observation(raw, actor, [], previous=self.base_previous())
        old_raw, old_actor = self.at(119)
        with self.assertRaisesRegex(ValueError, 'regressed'):
            build_battlefield_observation(old_raw, old_actor, [], previous=previous)
        bad = deepcopy(raw); bad['schema'] = 'future-raw'
        with self.assertRaises(ValueError): build_battlefield_observation(bad, actor, [])
        with self.assertRaises(ValueError): build_battlefield_observation(raw, {**actor, 'tick': 119}, [])

    def test_late_first_capture_preserves_unattested_objects_and_unknown_visibility(self):
        raw, actor = self.at()
        raw['entities'].append([999, 1, 26000000, 3000, 8000, 100, 200, 123, 999, 'troop', 500])
        got = build_battlefield_observation(raw, actor, [])
        self.assertTrue(got['field_coverage']['may_include_unattested_towers'])
        self.assertEqual(len(got['entities']), len(raw['entities']))
        non_tower = got['entities'][-1]
        self.assertNotIn('target_entity_id', non_tower)
        self.assertNotIn('attack_cooldown_ms', non_tower)
        self.assertEqual(non_tower['visibility'], 'unknown')
        self.assertFalse(got['visibility_fairness_verified'])

    def test_input_values_and_previous_are_not_mutated(self):
        raw, actor = self.at(); previous = self.base_previous()
        original = deepcopy((raw, actor, previous))
        got = build_battlefield_observation(raw, actor, [], previous=previous)
        self.assertEqual((raw, actor, previous), original)
        got['hand'][0]['card'] = 1
        self.assertEqual((raw, actor, previous), original)


if __name__ == '__main__':
    unittest.main()

"""Oracle interpretation checks against saved native v4 captures; no RPC/VM.

Mutation cases are explicit negative controls, not native runtime evidence.
"""
from copy import deepcopy
import json
from pathlib import Path
import unittest

from royale_phase2.observability import build_observation, SCHEMA, ENTITY_FIELDS
from royale_phase2.visibility import project_actor


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / 'outputs/phase2/fixtures/native-v1'


def captures(name):
    path = FIXTURES / name / 'states.jsonl'
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines() if line.strip()]


class PlatformObservabilityTests(unittest.TestCase):
    def test_all_saved_fixture_states_are_losslessly_preserved(self):
        count = 0
        for folder in ('hero_ability_readiness_and_activation', 'attack_damage_and_cooldown',
                       'shield_absorbs_controlled_hit', 'spell_air_and_ground',
                       'ground_bridge_path', 'tombstone_death_spawn'):
            previous = None
            for state in captures(folder):
                obs = build_observation(state, previous)
                self.assertEqual(obs['raw'], state)
                self.assertEqual([row['raw'] for row in obs['entities']], state['entities'])
                self.assertEqual(len(obs['players']), 2)
                self.assertEqual(json.loads(json.dumps(obs, allow_nan=False))['raw'], state)
                self.assertFalse(obs['field_coverage']['complete_game_state'])
                count += 1
                previous = obs
        self.assertGreaterEqual(count, 20)

    def test_both_private_player_states_and_runtime_columns(self):
        states = captures('hero_ability_readiness_and_activation')
        raw = next(state for state in states if any(p['abilities'] for p in state['players']))
        obs = build_observation(raw)
        self.assertFalse(obs['actor_safe'])
        for i, player in enumerate(obs['players']):
            self.assertEqual(player['elixir_raw'], raw['players'][i]['elixir'])
            self.assertEqual(player['elixir'], raw['players'][i]['elixir'] / 10000)
            self.assertEqual(player['cycle'], raw['players'][i]['cycle'])
            self.assertEqual([card['raw'] for card in player['hand']], raw['players'][i]['hand'])
            self.assertEqual([ability['raw'] for ability in player['abilities']], raw['players'][i]['abilities'])
            self.assertEqual([evo['raw'] for evo in player['evolution']], raw['players'][i]['evolution'])
        ability = next(p['abilities'][0] for p in obs['players'] if p['abilities'])
        self.assertIsNone(ability['ability_identity'])
        self.assertIsNone(ability['button_state_meaning'])

    def test_column_provenance_and_unknown_are_explicit(self):
        raw = captures('hero_ability_readiness_and_activation')[0]
        obs = build_observation(raw)
        row = obs['entities'][0]
        for index, field in enumerate(ENTITY_FIELDS):
            self.assertEqual(row[field], raw['entities'][0][index])
            self.assertEqual(row['sources'][field], f'/entities/0/{index}')
        self.assertIsNone(row['semantic_type'])
        self.assertIsNone(row['name'])
        self.assertEqual(obs['players'][0]['hand'][0]['sources']['packed_card_parameter'], '/players/0/hand/0/3')
        self.assertTrue(obs['field_coverage']['not_exported'])

    def test_native_events_are_preserved_and_deduplicated(self):
        states = captures('hero_ability_readiness_and_activation')
        raw = next(state for state in states if state['plays'])
        obs = build_observation(raw)
        self.assertEqual([play['raw'] for play in obs['native_consumption_events']], raw['plays'])
        self.assertEqual(obs['native_consumption_events'][0]['kind'], 'native_card_consumption')
        self.assertIsNone(obs['native_consumption_events'][0]['x_raw'])
        self.assertEqual(build_observation(raw, obs)['deltas']['native_consumption_events'], [])

    def test_actual_differences_are_sample_presence_and_hp_changes_only(self):
        states = captures('attack_damage_and_cooldown')
        observations = [build_observation(state, states[i-1] if i else None) for i, state in enumerate(states)]
        changed = [obs for obs in observations if obs['deltas']['entity_changes']]
        self.assertTrue(changed)
        self.assertTrue(any('hp' in change['fields'] for obs in changed for change in obs['deltas']['entity_changes']))
        self.assertTrue(any(obs['deltas']['added_entities'] for obs in observations))
        for obs in observations:
            self.assertIsNone(obs['deltas']['causal_combat_events'])
            self.assertNotIn('damage_source', json.dumps(obs['deltas']))

    def test_unknown_optional_fields_stay_null_not_zero(self):
        raw = captures('hero_ability_readiness_and_activation')[0]
        del raw['players'][1]['elixir']
        del raw['players'][1]['abilities']
        raw['entities'][0][5:9] = [None, None, None, None]
        raw['entities'][0][10] = None
        obs = build_observation(raw)
        self.assertIsNone(obs['players'][1]['elixir_raw'])
        self.assertIsNone(obs['players'][1]['elixir'])
        self.assertIsNone(obs['players'][1]['abilities'])
        self.assertIsNone(obs['entities'][0]['hp'])
        self.assertTrue(any(item['path'] == '/players/1/elixir' for item in obs['field_coverage']['unknown']))

    def test_unknown_native_kind_is_retained_without_semantic_guess(self):
        raw = captures('hero_ability_readiness_and_activation')[0]
        raw['entities'][0][9] = 'future_unmapped_class'
        obs = build_observation(raw)
        self.assertEqual(obs['entities'][0]['native_kind'], 'future_unmapped_class')
        self.assertIsNone(obs['entities'][0]['semantic_type'])

    def test_generation_or_tick_boundary_never_infers_spawn_or_death(self):
        raw = captures('hero_ability_readiness_and_activation')[0]
        previous = deepcopy(raw)
        previous['generation'] += 1
        obs = build_observation(raw, previous)
        self.assertFalse(obs['deltas']['comparable'])
        self.assertEqual(obs['deltas']['reason'], 'generation_changed')
        self.assertEqual(obs['deltas']['added_entities'], [])
        previous = deepcopy(raw)
        previous['tick'] += 1
        self.assertEqual(build_observation(raw, previous)['deltas']['reason'], 'tick_regressed')

    def test_nonjson_schema_bad_rows_and_ambiguous_ids_fail_closed(self):
        native = captures('hero_ability_readiness_and_activation')[0]
        bad = []
        raw = deepcopy(native); raw['schema'] = 'guessed-schema'; bad.append(raw)
        raw = deepcopy(native); raw['ok'] = False; bad.append(raw)
        raw = deepcopy(native); raw['tick'] = True; bad.append(raw)
        raw = deepcopy(native); raw['unknown_extra'] = float('nan'); bad.append(raw)
        raw = deepcopy(native); raw['entities'][0].append(123); bad.append(raw)
        raw = deepcopy(native); raw['entities'].append(raw['entities'][0]); bad.append(raw)
        raw = deepcopy(native); raw['players'][1]['owner'] = 0; bad.append(raw)
        raw = deepcopy(native); raw['players'][0]['hand'][0][0] = 9; bad.append(raw)
        raw = deepcopy(native); raw['players'][0]['abilities'] = [[1, 2]]; bad.append(raw)
        raw = deepcopy(native); raw['next'] = False; bad.append(raw)
        for raw in bad:
            with self.subTest(case=raw):
                with self.assertRaises(ValueError):
                    build_observation(raw)

    def test_no_mutation_and_no_implicit_actor_change(self):
        raw = captures('hero_ability_readiness_and_activation')[0]
        original = deepcopy(raw)
        actor = project_actor(raw, 0)
        obs = build_observation(raw)
        self.assertEqual(raw, original)
        self.assertEqual(project_actor(raw, 0), actor)
        obs['raw']['players'][0]['elixir'] = 0
        self.assertEqual(raw, original)
        self.assertEqual(obs['schema'], SCHEMA)


if __name__ == '__main__':
    unittest.main()

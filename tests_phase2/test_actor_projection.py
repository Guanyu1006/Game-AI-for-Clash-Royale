"""Privacy perturbation tests on explicit synthetic capture fixtures.

These validate the projection boundary, never native visibility or game rules.
"""
from copy import deepcopy
import unittest
from royale_phase2.visibility import TowerAttestation, TowerIdentity, affordability_mask, project_actor


def capture():
    entities = [[10+i, owner, -1, x, y, 3000, 3000, 999, 0, 'building', 0]
                for i, (owner, x, y) in enumerate(TowerAttestation.standard_locations)]
    return {'ok': True, 'schema': 'cr-minimal.v4', 'generation': 1, 'tick': 90,
            'finalized': False, 'result': None, 'crowns': [0, 0], 'entities': entities,
            'players': [{'owner': owner, 'elixir': 50000,
                         'hand': [[slot, 26000000+slot, slot+2, 777, 888] for slot in range(4)],
                         'cycle': [101, 102, 103, 104], 'abilities': [], 'evolution': []} for owner in (0, 1)],
            'plays': [], 'next': 1}


class ActorProjectionTests(unittest.TestCase):
    def test_enemy_private_and_internal_perturbations_do_not_change_actor(self):
        before = capture(); attested = TowerAttestation.from_reset(before)
        expected = project_actor(before, 0, attested)
        for seed in range(100):
            changed = deepcopy(before)
            changed.update(seed=seed, future=[{'tick': 800, 'card': seed}], next=seed)
            changed['players'][1].update(elixir=seed, hand=[[9, seed, 99, seed, seed]], cycle=[seed])
            changed['players'][0].update(cycle=[seed], abilities=[seed], evolution=[seed])
            for card in changed['players'][0]['hand']:
                card[3] = seed; card[4] = seed+1000
            changed['entities'].append([999, 1, 26000050, seed, seed, 100, 500, seed, seed, 'troop', seed])
            changed['entities'][0][7:9] = [seed, seed]
            changed['plays'] = [[seed, 10, 1, seed, 99]]
            self.assertEqual(project_actor(changed, 0, attested), expected)

    def test_own_hand_and_affordability_are_observable(self):
        state = capture(); before = project_actor(state, 0)
        state['players'][0]['elixir'] = 30000
        after = project_actor(state, 0)
        self.assertNotEqual(before, after)
        mask = affordability_mask(after)
        self.assertEqual(mask['values'], [True, True, True, False, False])
        self.assertFalse(mask['full_legality_verified'])

    def test_no_tower_attestation_no_oracle_entities(self):
        state = capture(); state['entities'].append([100, 1, 26000050, 10, 20, 30, 40, 1, 1, 'troop', 0])
        self.assertEqual(project_actor(state, 0)['towers'], [])

    def test_tower_reuse_and_reset_rejected(self):
        state = capture(); towers = TowerAttestation.from_reset(state)
        state['generation'] = 2
        with self.assertRaises(ValueError): project_actor(state, 0, towers)
        state['generation'] = 1; state['entities'][0][2] = 26000050
        with self.assertRaises(ValueError): project_actor(state, 0, towers)

    def test_absent_tower_is_unknown_hp_not_injected_zero(self):
        state = capture(); towers = TowerAttestation.from_reset(state)
        missing = state['entities'].pop(0)
        obs = project_actor(state, 0, towers)
        role = next(t.role for t in towers.identities if t.entity_id == missing[0])
        tower = next(t for t in obs['towers'] if t['side'] == 'own' and t['role'] == role)
        self.assertIsNone(tower['hp'])
        self.assertFalse(tower['present'])

    def test_malformed_or_nonstandard_reset_rejected(self):
        state = capture(); state['entities'][0][3] += 1
        with self.assertRaises(ValueError): TowerAttestation.from_reset(state)
        state = capture(); state['players'][0]['elixir'] = float('nan')
        with self.assertRaises(ValueError): project_actor(state, 0)

    def test_forged_tower_roster_cannot_bypass_reset_constructor(self):
        with self.assertRaises(ValueError):
            TowerAttestation(1, (TowerIdentity(999, 1, 'king', 100, 100),))
        state = capture(); state['ok'] = False
        with self.assertRaises(ValueError): TowerAttestation.from_reset(state)
        state = capture(); state['tick'] = 1000
        with self.assertRaises(ValueError): TowerAttestation.from_reset(state)

    def test_partial_hand_explicit_and_invalid_max_hp_rejected(self):
        state = capture(); towers = TowerAttestation.from_reset(state)
        state['players'][0]['hand'].pop()
        observation = project_actor(state, 0, towers)
        self.assertFalse(observation['hand_complete'])
        self.assertFalse(affordability_mask(observation)['values'][4])
        state['entities'][0][5:7] = [0, 0]
        with self.assertRaises(ValueError): project_actor(state, 0, towers)

    def test_boolean_identity_not_an_integer_alias(self):
        state = capture(); state['generation'] = True
        with self.assertRaises(ValueError): project_actor(state, 0)
        state = capture(); state['players'][0]['owner'] = False
        with self.assertRaises(ValueError): project_actor(state, 0)


if __name__ == '__main__':
    unittest.main()

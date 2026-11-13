"""Behavioral checks for the public-observation scripted opponent."""

import random
import unittest

import numpy as np

from arena_lab.baselines import scripted_action


class ArenaBaselineTests(unittest.TestCase):
    catalogue = [
        {"name": "Knight", "card_kind": "TROOP"},
        {"name": "Fireball", "card_kind": "SPELL"},
        {"name": "Cannon", "card_kind": "BUILDING"},
        {"name": "Giant", "card_kind": "TROOP"},
    ]

    def observation(self, elixir=5):
        obs = {"global_features": np.zeros(20, dtype=np.float32),
               "entities": np.zeros((12, 21), dtype=np.float32),
               "entity_mask": np.zeros(12, dtype=bool),
               "hand_ids": np.array([1, 2, 3, 4]),
               "legal_cards": np.array([True, True, False, False, False, False, False, False]),
               "legal_positions": np.ones((4, 576), dtype=bool)}
        obs["global_features"][4] = elixir / 10
        obs["global_features"][16:20] = np.array([3, 4, 3, 5]) / 10
        return obs

    def test_visible_invasion_changes_defensive_lane(self):
        obs = self.observation()
        obs["entity_mask"][0] = True
        obs["entities"][0, :5] = [.15, .3, 1, 0, 1]
        left = scripted_action(obs, catalogue=self.catalogue)
        obs["entities"][0, 0] = .85
        right = scripted_action(obs, catalogue=self.catalogue)
        self.assertEqual((left["card"], right["card"]), (1, 1))
        self.assertLess(left["position"] % 18, 9)
        self.assertGreater(right["position"] % 18, 9)

    def test_hidden_padding_cannot_trigger_defense_and_low_elixir_waits(self):
        obs = self.observation(elixir=4)
        obs["entities"][0, :5] = [.2, .2, 1, 0, 1]
        self.assertEqual(scripted_action(obs, catalogue=self.catalogue), {"card": 0, "position": 0})
        obs["entity_mask"][0] = True
        self.assertEqual(scripted_action(obs, catalogue=self.catalogue)["card"], 1)

    def test_proposed_actions_respect_sparse_legality_and_abilities(self):
        rng = np.random.default_rng(719)
        obs = self.observation(elixir=10)
        for _ in range(40):
            obs["legal_cards"][1:] = rng.integers(0, 2, size=7).astype(bool)
            obs["legal_positions"][:] = False
            for slot in range(4):
                obs["legal_positions"][slot, rng.integers(0, 576, size=3)] = True
            obs["entity_mask"][:2] = True
            obs["entities"][:2, :5] = [[.4, .2, 1, 0, 1], [.6, .3, 1, 0, 1]]
            action = scripted_action(obs, catalogue=self.catalogue, rng=random.Random(2))
            self.assertTrue(obs["legal_cards"][action["card"]])
            if 1 <= action["card"] <= 4:
                self.assertTrue(obs["legal_positions"][action["card"]-1, action["position"]])
            else:
                self.assertEqual(action["position"], 0)

    def test_empty_location_masks_do_not_create_illegal_deployments(self):
        obs = self.observation(elixir=10)
        obs["legal_positions"][:] = False
        self.assertEqual(scripted_action(obs, catalogue=self.catalogue)["card"], 0)


if __name__ == "__main__":
    unittest.main()

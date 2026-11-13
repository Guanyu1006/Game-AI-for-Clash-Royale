"""Real-time discounting, exact joint likelihood and actual PPO weight updates."""
import unittest
import numpy as np
try:
    import torch
except ImportError:
    torch = None


@unittest.skipIf(torch is None, "CPU Torch is optional")
class SelfPlayTests(unittest.TestCase):
    def test_gae_terminal_and_truncation_are_different(self):
        from arena_lab.selfplay import generalized_advantages
        _, terminal = generalized_advantages([0, 1], [.2, .4], [.5, .5],
            gamma_per_second=.81, lambda_per_second=1, terminal=True, bootstrap_value=5)
        np.testing.assert_allclose(terminal, [.9, 1], atol=1e-6)
        _, truncation = generalized_advantages([0, 1], [.2, .4], [.5, .5],
            gamma_per_second=.81, lambda_per_second=1, terminal=False, bootstrap_value=5)
        np.testing.assert_allclose(truncation, [4.95, 5.5], atol=1e-6)
        with self.assertRaises(ValueError):
            generalized_advantages([0], [0], [0], gamma_per_second=.9, lambda_per_second=.9, terminal=True)

    def test_time_resampling_preserves_discount(self):
        from arena_lab.selfplay import generalized_advantages
        _, short = generalized_advantages([0, 0], [0, 0], [.5, .5],
            gamma_per_second=.81, lambda_per_second=1, terminal=False, bootstrap_value=1)
        _, long = generalized_advantages([0], [0], [1.],
            gamma_per_second=.81, lambda_per_second=1, terminal=False, bootstrap_value=1)
        self.assertAlmostEqual(float(short[0]), float(long[0]), places=6)

    def test_joint_entropy_weights_positions_by_selected_slot_probability(self):
        from arena_lab.selfplay import action_statistics
        output = {"card_logits": torch.tensor([[[.5, .5, 0, 0, 0]]]).log(),
                  "position_logits": torch.zeros(1, 1, 4, 2)}
        logp, entropy = action_statistics(output, torch.tensor([[1]]), torch.tensor([[0]]))
        self.assertAlmostEqual(float(logp), float(np.log(.25)), places=6)
        self.assertAlmostEqual(float(entropy), float(1.5*np.log(2)), places=6)
        wait_logp, _ = action_statistics(output, torch.tensor([[0]]), torch.tensor([[0]]))
        self.assertAlmostEqual(float(wait_logp), float(np.log(.5)), places=6)

    def test_ppo_updates_from_chronological_public_trajectory(self):
        from arena_lab.model import EntityGRUPolicy, PolicySpec, OBSERVATION_KEYS, tensor_observation
        from arena_lab.selfplay import PPOConfig, action_statistics, update_ppo
        torch.set_num_threads(1)
        torch.manual_seed(2)
        spec = PolicySpec(global_dim=7, entity_dim=9, card_vocab_size=12,
                          grid_h=2, grid_w=2, hidden_dim=12, entity_hidden=6, card_embedding_dim=4)
        model = EntityGRUPolicy(spec)
        critic = torch.nn.Linear(12, 1)
        optimizer = torch.optim.Adam([*model.parameters(), *critic.parameters()], lr=.001)
        t = 5
        trajectory = {"global_features": np.ones((t, 7), np.float32),
            "entities": np.zeros((t, 2, 9), np.float32), "entity_mask": np.ones((t, 2), bool),
            "hand_ids": np.tile([1, 2, 3, 4], (t, 1)), "legal_cards": np.ones((t, 8), bool),
            "legal_positions": np.ones((t, 4, 4), bool), "cards": np.array([0, 1, 0, 2, 0]),
            "positions": np.zeros(t, np.int64), "advantage": np.array([-1, 2, -1, 2, -1], np.float32),
            "returns": np.linspace(0, 1, t, dtype=np.float32)}
        with torch.no_grad():
            output = model(tensor_observation({key: trajectory[key][None] for key in OBSERVATION_KEYS}))
            logp, _ = action_statistics(output, torch.tensor(trajectory["cards"])[None], torch.tensor(trajectory["positions"])[None])
        trajectory["old_logp"] = logp[0].numpy()
        before = {k: v.clone() for k, v in model.state_dict().items()}
        result = update_ppo(model, critic, optimizer, [trajectory], PPOConfig(sequence=2, epochs=2))
        self.assertTrue(any(not torch.equal(v, before[k]) for k, v in model.state_dict().items()))
        self.assertLess(result[0]["approximate_kl"], 1e-6)
        self.assertTrue(all(np.isfinite(list(row.values())).all() for row in result))


if __name__ == "__main__":
    unittest.main()

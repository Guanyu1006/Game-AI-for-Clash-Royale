"""Contract checks for the independent public-state imitation policy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
import tempfile
import unittest

import numpy as np

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from arena_lab.model import (
        EntityGRUPolicy, PolicyAgent, PolicySpec, decode_actions,
        load_policy, save_policy, tensor_observation,
    )


@unittest.skipIf(torch is None, "Optional CPU Torch is not installed")
class ArenaModelTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        torch.manual_seed(1729)
        self.spec = PolicySpec(global_dim=7, entity_dim=9, card_vocab_size=12,
                               grid_h=8, grid_w=6, hidden_dim=24,
                               entity_hidden=12, card_embedding_dim=6, n_card_actions=5)
        self.model = EntityGRUPolicy(self.spec).eval()
        self.rng = np.random.default_rng(1729)

    def frame(self):
        frame = {
            "global_features": self.rng.normal(size=7).astype(np.float32),
            "entities": self.rng.normal(size=(5, 9)).astype(np.float32),
            "entity_mask": np.array([True, True, True, False, False]),
            "hand_ids": np.array([1, 3, 5, 7], dtype=np.int64),
            "legal_cards": np.ones(5, dtype=bool),
            "legal_positions": np.ones((4, 48), dtype=bool),
        }
        frame["entities"][:, :2] = self.rng.uniform(size=(5, 2))
        return frame

    def sequence(self, frames):
        return tensor_observation({key: np.stack([frame[key] for frame in frames])[None]
                                   for key in frames[0]})

    def test_sequence_and_online_share_recurrence_and_decoder(self):
        frames = [self.frame() for _ in range(4)]
        with torch.no_grad():
            offline = self.model(self.sequence(frames))
            decoded = decode_actions(offline)
            hidden = None
            for index, frame in enumerate(frames):
                action, hidden = self.model.act(frame, hidden)
                self.assertEqual(action["card"], decoded["card"][0, index].item())
                self.assertEqual(action["position"], decoded["position"][0, index].item())
                self.assertAlmostEqual(action["log_probability"], decoded["log_probability"][0, index].item(), places=5)
            torch.testing.assert_close(hidden, offline["hidden"], rtol=1e-5, atol=1e-6)

    def test_episode_reset_removes_previous_battle_memory(self):
        frame = self.frame()
        agent = PolicyAgent(self.model)
        first_action = agent.act(frame)
        first_hidden = agent.hidden.clone()
        agent.act(frame)
        self.assertFalse(torch.allclose(first_hidden, agent.hidden))
        agent.reset()
        self.assertIsNone(agent.hidden)
        self.assertEqual(first_action, agent.act(frame))
        torch.testing.assert_close(agent.hidden, first_hidden)
        frames = [self.frame(), frame]
        with torch.no_grad():
            reset_output = self.model(self.sequence(frames), episode_starts=torch.tensor([[True, True]]))
        torch.testing.assert_close(reset_output["hidden"], first_hidden, atol=1e-6, rtol=1e-5)

    def test_visible_entities_affect_predictions_hidden_padding_does_not(self):
        frame = self.frame()
        with torch.no_grad():
            original = self.model(tensor_observation(frame, single=True))
            frame["entities"][3:] = np.nan
            padding_changed = self.model(tensor_observation(frame, single=True))
            torch.testing.assert_close(original["card_logits"], padding_changed["card_logits"], rtol=0, atol=0)
            frame["entities"][0, 2:] += 3
            changed = self.model(tensor_observation(frame, single=True))
        self.assertGreater((original["card_logits"] - changed["card_logits"]).abs().max().item(), 1e-5)
        self.assertTrue(torch.isfinite(changed["position_logits"]).all())

    def test_all_empty_entities_and_no_affordable_card_select_wait(self):
        frame = self.frame()
        frame["entity_mask"][:] = False
        frame["entities"][:] = np.nan
        frame["legal_cards"][1:] = False
        frame["legal_positions"][:] = False
        action, hidden = self.model.act(frame, deterministic=False, generator=torch.Generator().manual_seed(4))
        self.assertEqual((action["card"], action["position"]), (0, 0))
        self.assertEqual(action["log_probability"], 0.0)
        self.assertTrue(torch.isfinite(hidden).all())

    def test_legal_masks_are_enforced_in_sampling_and_missing_locations(self):
        frame = self.frame()
        frame["legal_cards"][:] = [True, False, True, True, False]
        frame["legal_positions"][:] = False
        frame["legal_positions"][1, [3, 11]] = True
        # Slot 3 claims affordability but has no valid deployment square.
        with torch.no_grad():
            self.model.wait_head.weight.zero_()
            self.model.wait_head.bias.fill_(-30)
            self.model.card_head[-1].bias.fill_(30)
        generator = torch.Generator().manual_seed(43)
        for _ in range(20):
            action, _ = self.model.act(frame, deterministic=False, generator=generator)
            self.assertEqual(action["card"], 2)
            self.assertIn(action["position"], [3, 11])

    def test_location_distribution_is_card_conditioned(self):
        frame = self.frame()
        with torch.no_grad():
            output = self.model(tensor_observation(frame, single=True))
        self.assertFalse(torch.allclose(output["position_logits"][..., 0, :], output["position_logits"][..., 1, :]))
        frame["hand_ids"][1] = frame["hand_ids"][0]
        with torch.no_grad():
            same_card = self.model(tensor_observation(frame, single=True))
        torch.testing.assert_close(same_card["position_logits"][..., 0, :], same_card["position_logits"][..., 1, :])

    def test_abilities_are_supported_without_deployment_position(self):
        model = EntityGRUPolicy(replace(self.spec, n_card_actions=8)).eval()
        frame = self.frame()
        frame["legal_cards"] = np.array([True, False, False, False, False, False, False, True])
        frame["legal_positions"][:] = False
        with torch.no_grad():
            model.wait_head.bias.fill_(-30)
            model.ability_head.weight.zero_()
            model.ability_head.bias.fill_(30)
        action, _ = model.act(frame)
        self.assertEqual((action["card"], action["position"]), (7, 0))
        self.assertEqual(action["position_probability"], 1.0)

    def test_spatial_branch_uses_position_and_entity_ablation_is_complete(self):
        frame = self.frame()
        with torch.no_grad():
            original = self.model(tensor_observation(frame, single=True))
            original_blind = self.model(tensor_observation(frame, single=True), ablate_entities=True)
            frame["entities"][0, :2] = [0.05, 0.95]
            moved = self.model(tensor_observation(frame, single=True))
            moved_blind = self.model(tensor_observation(frame, single=True), ablate_entities=True)
        self.assertFalse(torch.allclose(original["spatial_features"], moved["spatial_features"]))
        self.assertGreater((original["position_logits"] - moved["position_logits"]).abs().max().item(), 1e-6)
        torch.testing.assert_close(original_blind["card_logits"], moved_blind["card_logits"], rtol=0, atol=0)
        torch.testing.assert_close(original_blind["position_logits"], moved_blind["position_logits"], rtol=0, atol=0)

    def test_nonrecurrent_configuration_ignores_old_hidden_state(self):
        model = EntityGRUPolicy(replace(self.spec, use_recurrent=False)).eval()
        frame = self.frame()
        with torch.no_grad():
            fresh = model(tensor_observation(frame, single=True))
            stale = model(tensor_observation(frame, single=True), hidden=torch.ones_like(model.initial_state()))
        torch.testing.assert_close(fresh["card_logits"], stale["card_logits"], rtol=0, atol=0)
        self.assertEqual(tuple(stale["hidden"].shape), (1, 1, self.spec.hidden_dim))

    def test_strict_checkpoint_hash_spec_and_weight_roundtrip(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-model-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            path = Path(directory) / "policy.pt"
            report = save_policy(self.model, path, metadata={"data_role": "synthetic_unit_test"})
            loaded, metadata = load_policy(path, expected_spec=self.spec, expected_sha256=report["sha256"])
            self.assertEqual(metadata["data_role"], "synthetic_unit_test")
            frame = self.frame()
            self.assertEqual(self.model.act(frame)[0], loaded.act(frame)[0])
            with self.assertRaises(FileExistsError):
                save_policy(self.model, path)
            with self.assertRaisesRegex(ValueError, "SHA256"):
                load_policy(path, expected_sha256="0" * 64)
            with self.assertRaisesRegex(ValueError, "spec"):
                load_policy(path, expected_spec=replace(self.spec, entity_dim=10))
            payload = torch.load(path, weights_only=True)
            del payload["state_dict"]["wait_head.bias"]
            torch.save(payload, path)
            with self.assertRaises(RuntimeError):
                load_policy(path)

    def test_invalid_public_observations_fail_before_inference(self):
        frame = self.frame()
        frame["entities"][0, 3] = np.nan
        with self.assertRaisesRegex(ValueError, "Nonfinite visible"):
            self.model.act(frame)
        frame = self.frame()
        frame["hand_ids"][0] = self.spec.card_vocab_size
        with self.assertRaisesRegex(ValueError, "vocabulary"):
            self.model.act(frame)
        frame = self.frame()
        frame["entities"][0, 0] = 1.1
        with self.assertRaisesRegex(ValueError, "normalized"):
            self.model.act(frame)
        frame = self.frame()
        frame["legal_cards"][0] = False
        with self.assertRaisesRegex(ValueError, "WAIT"):
            self.model.act(frame)

    def test_cpu_training_updates_entity_and_position_branches(self):
        # Synthetic labels only verify differentiability, never expert quality.
        frames = [self.frame() for _ in range(8)]
        observation = self.sequence(frames)
        cards = torch.tensor([[1, 2, 3, 4, 1, 2, 3, 4]])
        positions = torch.tensor([[1, 5, 10, 20, 25, 30, 35, 40]])
        optimizer = torch.optim.Adam(self.model.parameters(), lr=0.01)
        losses = []
        before = self.model.entity_encoder[0].weight.detach().clone()
        self.model.train()
        for _ in range(18):
            output = self.model(observation)
            selected = output["position_logits"].gather(2, (cards - 1)[..., None, None].expand(1, 8, 1, 48)).squeeze(2)
            loss = torch.nn.functional.cross_entropy(output["card_logits"].flatten(0, 1), cards.flatten())
            loss += torch.nn.functional.cross_entropy(selected.flatten(0, 1), positions.flatten())
            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            losses.append(loss.item())
        self.assertLess(losses[-1], losses[0] * 0.7)
        self.assertFalse(torch.equal(before, self.model.entity_encoder[0].weight))


if __name__ == "__main__":
    unittest.main()

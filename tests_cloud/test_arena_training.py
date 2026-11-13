"""Synthetic contract fixtures; never mixed into the expert dataset."""
from dataclasses import asdict
import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

try:
    import torch
except ImportError:
    torch = None

if torch is not None:
    from arena_lab.model import EntityGRUPolicy, PolicySpec, tensor_observation
    from arena_lab.training import (BattleDataset, TrainConfig, losses, sha256,
                                    stream_batches, train)


@unittest.skipIf(torch is None, "CPU Torch optional")
class TrainingContracts(unittest.TestCase):
    def setUp(self):
        self.parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-training-tests"
        self.parent.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=self.parent)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.spec = PolicySpec(global_dim=3, entity_dim=5, card_vocab_size=8,
                               grid_h=4, grid_w=4, hidden_dim=12,
                               entity_hidden=8, card_embedding_dim=4)
        torch.set_num_threads(2)

    def make_data(self, index: int, steps: int = 7):
        rng = np.random.default_rng(index)
        entities = rng.random((steps, 3, 5)).astype(np.float32)
        cards = np.array([0, 1, 0, 5, 0, 2, 0], dtype=np.int64)[:steps]
        return {
            "global_features": np.full((steps, 3), index/10, dtype=np.float32),
            "entities": entities, "entity_mask": np.ones((steps, 3), dtype=bool),
            "hand_ids": np.tile(np.arange(1, 5), (steps, 1)),
            "legal_cards": np.ones((steps, self.spec.n_card_actions), dtype=bool),
            "legal_positions": np.ones((steps, 4, self.spec.positions), dtype=bool),
            "target_card": cards, "target_position": np.ones(steps, dtype=np.int64),
            "label_valid": np.ones(steps, dtype=bool),
            "position_valid": (cards > 0) & (cards <= 4),
            "ticks": np.arange(steps, dtype=np.int64)*10,
        }

    def index(self, *, bad_label=False, leaking=False):
        rows = []
        for i, split in enumerate(("train", "validation", "test"), 1):
            path = self.root / f"fixture-{i}.npz"
            data = self.make_data(i)
            if bad_label and i == 1:
                data["legal_cards"][1, 1] = False
            np.savez_compressed(path, **data)
            rows.append({"game_id": "same" if leaking else f"synthetic-{i}",
                         "path": path.name, "sha256": sha256(path), "split": split, "steps": 7})
        path = self.root / "index.json"
        path.write_text(json.dumps({"schema_version": 1, "policy_spec": asdict(self.spec),
                                    "provenance": {"role": "synthetic_unit_test"}, "games": rows}))
        return path

    def test_chronological_chunks_preserve_memory_boundaries_and_padding(self):
        data = BattleDataset(self.index())
        rows = [*data.splits["train"], *data.splits["validation"]]
        batches = list(stream_batches(data, rows, batch_size=1, sequence=4))
        self.assertEqual(len(batches), 4)
        self.assertEqual([bool(start[0]) for _, start in batches], [True, False, True, False])
        self.assertEqual(batches[0][0]["ticks"][0].tolist(), [0, 10, 20, 30])
        self.assertEqual(batches[1][0]["ticks"][0, :3].tolist(), [40, 50, 60])
        self.assertFalse(batches[1][0]["label_valid"][0, -1])
        self.assertTrue(batches[1][0]["legal_cards"][0, -1, 0])

    def test_same_battle_cannot_cross_splits(self):
        with self.assertRaisesRegex(ValueError, "multiple splits"):
            BattleDataset(self.index(leaking=True))

    def test_invalid_teacher_action_is_rejected_instead_of_silently_clamped(self):
        with self.assertRaisesRegex(ValueError, "forbidden card"):
            BattleDataset(self.index(bad_label=True))

    def test_changed_file_fails_hash_validation(self):
        index = self.index()
        path = self.root / "fixture-1.npz"
        path.write_bytes(path.read_bytes()+b"changed")
        with self.assertRaisesRegex(ValueError, "checksum mismatch"):
            BattleDataset(index)

    def test_trimming_keeps_invalid_history_before_last_supervised_label(self):
        index = self.index()
        manifest = json.loads(index.read_text())
        row = manifest["games"][0]
        path = self.root / row["path"]
        data = self.make_data(1)
        data["label_valid"][:] = [True, False, False, True, False, False, False]
        data["position_valid"][:] = False
        np.savez_compressed(path, **data)
        row["sha256"] = sha256(path)
        index.write_text(json.dumps(manifest))
        dataset = BattleDataset(index)
        batches = list(stream_batches(dataset, dataset.splits["train"], batch_size=1,
                                       sequence=2, trim_unsupervised_suffix=True))
        self.assertEqual(len(batches), 2)
        self.assertEqual(batches[0][0]["ticks"][0].tolist(), [0, 10])
        self.assertEqual(batches[1][0]["ticks"][0].tolist(), [20, 30])
        self.assertFalse(batches[1][1][0])

    def test_ability_has_no_location_loss_and_padding_cannot_create_nan(self):
        data = BattleDataset(self.index())
        batch, _ = next(stream_batches(data, data.splits["train"], batch_size=2, sequence=8))
        model = EntityGRUPolicy(self.spec)
        out = model(tensor_observation(batch))
        card, pos = losses(out, batch)
        self.assertTrue(torch.isfinite(card+pos))
        (card+pos).backward()
        self.assertGreater(float(model.ability_head.weight.grad.abs().sum()), 0)

    def test_training_checkpoint_and_frozen_test_report_are_reproducible(self):
        index = self.index()
        config = TrainConfig(epochs=1, batch_size=2, sequence=4, threads=2)
        first = train(index, self.root / "run1", config)
        second = train(index, self.root / "run2", config)
        self.assertEqual(first["checkpoint"]["metadata"]["training_steps"], 2)
        self.assertTrue(first["checkpoint"]["metadata"]["trained"])
        self.assertEqual(first["test"]["labels"], 7)
        self.assertEqual(first["test"]["position_labels"], 2)
        self.assertEqual(first["test"]["card_nll"], second["test"]["card_nll"])
        self.assertEqual(first["test"]["position_nll"], second["test"]["position_nll"])
        with self.assertRaises(FileExistsError):
            train(index, self.root / "run1", config)


if __name__ == "__main__":
    unittest.main()

"""Independent evaluator checks; fake battles never count as arena evidence."""

from __future__ import annotations

from dataclasses import asdict
import gzip
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
    from arena_lab.evaluation import (
        baseline_action, evaluate_policy, paired_cases, policy_spec_from_environment,
        validate_checkpoint_environment,
    )
    from arena_lab.model import EntityGRUPolicy, save_policy, tensor_observation


class _FakeArena:
    obs_spec = {"global_dim": 7, "entity_dim": 9, "num_card_types": 16,
                "grid_h": 8, "grid_w": 6, "n_card_actions": 8,
                "entity_x_index": 0, "entity_y_index": 1, "max_entities": 4}

    def __init__(self, outcome="natural", identity_version=1):
        self.mode = outcome
        self.identity_version = identity_version
        self.closed = False

    def identity(self):
        return {"backend": "synthetic_unit_test_only", "version": self.identity_version,
                "observation_version": "arena-public-v1", "tuple_roundtrip": (1, 2)}

    def observe(self, player):
        return {
            "global_features": np.zeros(7, dtype=np.float32),
            "entities": np.zeros((4, 9), dtype=np.float32),
            "entity_mask": np.ones(4, dtype=bool),
            "hand_ids": np.array([1, 2, 3, 4], dtype=np.int64),
            "legal_cards": np.ones(8, dtype=bool),
            "legal_positions": np.ones((4, 48), dtype=bool),
        }

    def reset(self, *, seed, decks=None):
        self.seed = seed
        self.tick = 0
        self.executed = 0
        return {side: self.observe(side) for side in (0, 1)}, self._info(0)

    def _info(self, accepted):
        return {"tick": self.tick, "elapsed_sim_seconds": self.tick * 0.1,
                "accepted": accepted, "refused": 0, "executed_total": self.executed,
                "winner": self.seed % 2 if self.tick >= 3 else -1,
                "state_hash": f"fake-state-{self.tick}",
                "private_internal_field": "must-not-appear-in-trace"}

    def step(self, actions):
        if self.mode == "fault":
            raise RuntimeError("intentional fake engine failure")
        self.tick += 1
        accepted = sum(action["card"] > 0 for action in actions.values())
        self.executed += accepted
        end = self.tick >= 3
        return {side: self.observe(side) for side in (0, 1)}, {0: 0, 1: 0}, \
            end and self.mode == "natural", end and self.mode == "truncate", self._info(accepted)

    def close(self):
        self.closed = True


@unittest.skipIf(torch is None, "Optional CPU Torch is not installed")
class ArenaEvaluationTests(unittest.TestCase):
    def setUp(self):
        torch.set_num_threads(2)
        torch.manual_seed(3)
        parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-evaluation-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temporary.cleanup)
        self.directory = Path(self.temporary.name)
        self.environment = _FakeArena()
        self.model = EntityGRUPolicy(policy_spec_from_environment(
            self.environment, hidden_dim=16, entity_hidden=8, card_embedding_dim=4, spatial_channels=4))
        # One real optimizer update verifies checkpoint training metadata is literal.
        optimizer = torch.optim.SGD(self.model.parameters(), lr=0.01)
        logits = self.model(tensor_observation(self.environment.observe(0), single=True))["card_logits"]
        loss = torch.nn.functional.cross_entropy(logits.reshape(1, 8), torch.tensor([1]))
        loss.backward()
        optimizer.step()
        self.metadata = {"trained": True, "training_steps": 1,
                         "training_data_role": "synthetic_unit_test_only",
                         "environment_identity": self.environment.identity()}
        self.checkpoint = self.directory / "synthetic-test-policy.pt"
        save_policy(self.model, self.checkpoint, metadata=self.metadata)
        self.cases = paired_cases([(range(8), range(8, 16))], [7], opponents=["wait"])

    def test_paired_cases_keep_candidate_deck_and_swap_sides(self):
        self.assertEqual(len(self.cases), 2)
        self.assertEqual(self.cases[0].decks[self.cases[0].candidate_side], tuple(range(8)))
        self.assertEqual(self.cases[1].decks[self.cases[1].candidate_side], tuple(range(8)))
        self.assertEqual(self.cases[0].seed, self.cases[1].seed)
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            paired_cases([(range(8), range(8, 16))], [1, 1])

    def test_paired_cases_also_swap_forms_levels_and_resources(self):
        matchup = {"decks": [list(range(8)), list(range(8, 16))],
                   "reset_options": {"forms": [[1] * 8, [0] * 8], "levels": [[16] * 8, [15] * 8],
                                     "tower_levels": [16, 15], "elixir_milli": [5000, 7000], "shuffle": 0}}
        cases = paired_cases([matchup], [1], opponents=["wait"])
        for case in cases:
            self.assertEqual(case.reset_options["forms"][case.candidate_side], [1] * 8)
            self.assertEqual(case.reset_options["levels"][case.candidate_side], [16] * 8)
            self.assertEqual(case.reset_options["tower_levels"][case.candidate_side], 16)
            self.assertEqual(case.reset_options["elixir_milli"][case.candidate_side], 5000)
            self.assertEqual(case.reset_options["shuffle"], 0)

    def test_closed_loop_reports_pairs_timings_and_public_only_traces(self):
        report = evaluate_policy(self.checkpoint, self.directory / "run", cases=self.cases,
                                 env_factory=lambda: self.environment,
                                 variants=("model", "model_without_entities", "random", "wait"), trace_every=1)
        self.assertTrue(self.environment.closed)
        self.assertEqual(report["total"]["natural_games"], 8)
        self.assertEqual(report["total"]["faulted_games"], 0)
        self.assertEqual(report["total"]["wins"], 4)
        self.assertEqual(report["total"]["losses"], 4)
        self.assertEqual(report["total"]["joint_steps"], 24)
        self.assertGreater(report["total"]["whole_run_real_time_factor"], 0)
        self.assertEqual(report["paired_comparisons"][0]["valid_pairs"], 2)
        self.assertEqual(report["paired_comparisons"][0]["mean_score_difference"], 0)
        for trace in (self.directory / "run" / "traces").glob("*.gz"):
            text = gzip.open(trace, "rt").read()
            self.assertNotIn("must-not-appear", text)
            self.assertIn("visible_entities_sparse", text)
            self.assertIn('"kind": "ending"', text)

    def test_faults_and_truncation_never_inflate_natural_game_denominator(self):
        for mode, metric in (("fault", "faulted_games"), ("truncate", "truncated_games")):
            with self.subTest(mode=mode):
                report = evaluate_policy(self.checkpoint, self.directory / mode, cases=self.cases,
                                         env_factory=lambda: _FakeArena(mode), variants=("model",))
                self.assertEqual(report["total"][metric], 2)
                self.assertEqual(report["total"]["natural_games"], 0)
                self.assertIsNone(report["total"]["win_rate_natural"])

    def test_evaluator_budget_is_truncation(self):
        report = evaluate_policy(self.checkpoint, self.directory / "budget", cases=self.cases,
                                 env_factory=_FakeArena, variants=("model",), max_decisions=1)
        self.assertEqual(report["total"]["truncated_games"], 2)
        rows = [json.loads(line) for line in (self.directory / "budget" / "episodes.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["termination_reason"] == "evaluation_decision_budget" for row in rows))

    def test_untrained_and_wrong_identity_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "trained"):
            validate_checkpoint_environment(self.model, {"trained": False}, self.environment)
        with self.assertRaisesRegex(ValueError, "environment_identity"):
            validate_checkpoint_environment(self.model, self.metadata, _FakeArena(identity_version=2))

    def test_trace_byte_limit_is_explicit(self):
        evaluate_policy(self.checkpoint, self.directory / "limited", cases=self.cases,
                        env_factory=_FakeArena, variants=("wait",), trace_every=1, trace_max_bytes=10)
        rows = [json.loads(line) for line in (self.directory / "limited" / "episodes.jsonl").read_text().splitlines()]
        self.assertTrue(all(row["trace"]["size_limited"] for row in rows))
        self.assertTrue(all(row["trace"]["uncompressed_bytes"] <= 10 for row in rows))

    def test_missing_scripted_baseline_is_explicit(self):
        import random
        with self.assertRaisesRegex(ValueError, "no declared scripted"):
            baseline_action("scripted", self.environment.observe(0), random.Random(1), environment=self.environment)

    def test_policy_benchmark_separates_warmup_and_measured_samples(self):
        from arena_lab.policy_benchmark import benchmark_policy
        report = benchmark_policy(self.checkpoint, self.directory / "benchmark.json",
                                  env_factory=_FakeArena, observation_steps=4, warmup_steps=2,
                                  online_passes=1, offline_batch=2, offline_sequence=2, offline_iterations=2)
        self.assertTrue(report["trained"])
        self.assertEqual(report["online_batch_one"]["calls"], 4)
        self.assertEqual(report["online_batch_one"]["warmup_calls"], 2)
        self.assertEqual(report["offline_sequences"]["decisions"], 8)
        self.assertEqual(report["offline_sequences"]["warmup_iterations"], 3)
        self.assertGreater(report["offline_sequences"]["input_tensor_bytes"], 0)
        self.assertEqual(report["hardware"]["device"], "cpu")


if __name__ == "__main__":
    unittest.main()

"""Failure denominators, paired identities, PFSP and promotion integrity."""
from pathlib import Path
import random
import tempfile
import unittest

from arena_lab.league import OpponentPool, PromotionRules, promotion_report


class LeagueTests(unittest.TestCase):
    def rows(self, outcome, seeds=30):
        return [{"case_id": f"{opponent}-{seed}-{side}", "seed": seed, "side": side,
                 "opponent": opponent, "decks": [["a"]*8, ["b"]*8], "reset_options": {},
                 "status": "natural_end", "outcome": outcome, "attempts": 40, "executed": 40, "refused": 0}
                for opponent in ("historical", "scripted") for seed in range(seeds) for side in (0, 1)]

    def test_improvement_must_recur_across_independent_seeds_and_opponents(self):
        report = promotion_report(self.rows("win"), self.rows("loss"))
        self.assertTrue(report["promoted"])
        self.assertEqual(report["by_opponent"]["historical"]["seed_clusters"], 30)
        self.assertEqual(report["by_opponent"]["historical"]["positive_clusters"], 30)
        equal = promotion_report(self.rows("win"), self.rows("win"))
        self.assertFalse(equal["promoted"])

    def test_mirrored_games_do_not_inflate_evidence_and_faults_block(self):
        candidate = self.rows("win", seeds=1)
        for i in range(1, 100):
            candidate.extend(dict(row, case_id=f"copy-{i}-{row['case_id']}") for row in self.rows("win", seeds=1))
        reference = [dict(row, outcome="loss") for row in candidate]
        report = promotion_report(candidate, reference)
        self.assertFalse(report["promoted"])
        self.assertEqual(report["by_opponent"]["historical"]["seed_clusters"], 1)
        candidate = self.rows("win")
        candidate[0].update(status="fault", outcome=None)
        report = promotion_report(candidate, self.rows("loss"))
        self.assertFalse(report["promoted"])
        self.assertIn("historical:complete", report["reasons"])

    def test_reject_mismatched_pairs_and_missing_cases(self):
        candidate, reference = self.rows("win"), self.rows("loss")
        reference[0]["seed"] = 9999
        with self.assertRaisesRegex(ValueError, "Mismatched"):
            promotion_report(candidate, reference)
        with self.assertRaisesRegex(ValueError, "same full"):
            promotion_report(candidate, self.rows("loss")[:-1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            promotion_report(candidate+candidate[:1], self.rows("loss"))

    def test_zero_actions_and_unknown_consumption_never_pass(self):
        candidate = self.rows("win")
        for row in candidate:
            row.update(attempts=0, executed=None)
        self.assertFalse(promotion_report(candidate, self.rows("loss"))["promoted"])
        for row in candidate:
            row.update(attempts=40, executed=0)
        self.assertFalse(promotion_report(candidate, self.rows("loss"))["promoted"])
        for row in candidate:
            row.update(executed=41)
        self.assertFalse(promotion_report(candidate, self.rows("loss"))["promoted"])
        for row in candidate:
            row.update(executed=39)  # One unresolved command per battle.
        self.assertFalse(promotion_report(candidate, self.rows("loss"))["promoted"])

    def test_pfsp_is_reproducible_and_keeps_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = OpponentPool(directory)
            weak, strong = pool.add_baseline("random"), pool.add_baseline("scripted")
            for i in range(20):
                pool.record_training(weak, weak, "win", status="natural_end", seed=i, side=i%2)
                pool.record_training(weak, strong, "loss", status="natural_end", seed=i, side=i%2)
            before = pool.probabilities(weak)
            self.assertGreater(before[strong], before[weak])
            self.assertGreaterEqual(min(before.values()), .125)
            pool.record_training(weak, strong, None, status="fault", seed=30, side=0)
            self.assertEqual(before, pool.probabilities(weak))
            a, b = random.Random(70), random.Random(70)
            loaded = OpponentPool(directory)
            self.assertEqual([pool.sample(weak, a) for _ in range(50)], [loaded.sample(weak, b) for _ in range(50)])
            with self.assertRaises(ValueError):
                pool.record_training(weak, strong, "win", status="fault", seed=0, side=0)

    def test_new_snapshot_inherits_bounded_prior_and_adapts_to_own_results(self):
        with tempfile.TemporaryDirectory() as directory:
            pool = OpponentPool(directory)
            weak, strong = pool.add_baseline("random"), pool.add_baseline("scripted")
            pool.state["members"]["new-policy"] = {"kind": "test-only", "parent_member": weak}
            for i in range(32):
                pool.record_training(weak, weak, "win", status="natural_end", seed=i, side=0)
                pool.record_training(weak, strong, "loss", status="natural_end", seed=i, side=0)
            inherited = pool.probabilities("new-policy")
            self.assertGreater(inherited[strong], inherited[weak])
            for i in range(8):
                pool.record_training("new-policy", weak, "loss", status="natural_end", seed=i, side=0)
                pool.record_training("new-policy", strong, "win", status="natural_end", seed=i, side=0)
            updated = pool.probabilities("new-policy")
            self.assertGreater(updated[weak], updated[strong])
            pool.state["members"][weak]["parent_member"] = "new-policy"
            with self.assertRaisesRegex(ValueError, "cyclic"):
                pool.probabilities("new-policy")


if __name__ == "__main__":
    unittest.main()

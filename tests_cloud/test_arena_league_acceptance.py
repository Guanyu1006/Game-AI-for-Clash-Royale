"""Frozen acceptance rejects mutually hash-consistent but wrong case evidence."""
import json
from pathlib import Path
import tempfile
import unittest

from arena_lab.league import digest, write_json
from arena_lab.league_acceptance import read_evidence, summarize_rows


class AcceptanceEvidenceTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-acceptance-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.cases = [{"case_id": "case-1", "seed": 1, "side": 0, "opponent": "baseline-random",
                       "decks": [["A"]*8, ["B"]*8], "reset_options": {}}]
        self.identity = {"test_fixture": True}
        self.members = {"candidate": {"sha256": "fixture"}}
        self.plan = {"candidate": "candidate", "cases": self.cases, "environment_identity": self.identity,
            "members": self.members, "max_decisions": 1500, "max_wall_seconds": 120,
            "partition": "promotion_frozen_not_training", "deterministic": True}
        self.rows = [dict(self.cases[0], status="natural_end", outcome="win", attempts=3, executed=3,
                          refused=0, decisions=100, final_tick=1000, wall_seconds=2)]
        self.arguments = dict(candidate="candidate", expected_cases=self.cases, environment_identity=self.identity,
                              members=self.members, max_decisions=1500, max_wall_seconds=120)

    def save(self):
        write_json(self.directory/"plan.json", self.plan)
        (self.directory/"episodes.jsonl").write_text("".join(json.dumps(row)+"\n" for row in self.rows))
        summary = {"plan_sha256": digest(self.directory/"plan.json"),
                   "episodes_sha256": digest(self.directory/"episodes.jsonl")}
        write_json(self.directory/"summary.json", summary)

    def test_frozen_budget_and_opponent_identity_cannot_change_with_new_hash(self):
        self.save()
        read_evidence(self.directory, **self.arguments)
        self.plan["max_decisions"] += 1
        self.save()
        with self.assertRaisesRegex(ValueError, "frozen acceptance"):
            read_evidence(self.directory, **self.arguments)
        self.plan["max_decisions"] -= 1
        self.plan["members"] = {"candidate": {"sha256": "wrong"}}
        self.save()
        with self.assertRaisesRegex(ValueError, "frozen acceptance"):
            read_evidence(self.directory, **self.arguments)

    def test_missing_or_wrong_cases_fail_and_failures_stay_in_summary(self):
        original = self.rows[0].copy()
        self.rows = []
        self.save()
        with self.assertRaisesRegex(ValueError, "Incomplete"):
            read_evidence(self.directory, **self.arguments)
        self.rows = [dict(original, seed=2)]
        self.save()
        with self.assertRaisesRegex(ValueError, "configuration"):
            read_evidence(self.directory, **self.arguments)
        rows = [original, dict(original, status="fault", outcome=None, attempts=1, executed=0,
                               refused=1, decisions=4, final_tick=40)]
        summary = summarize_rows(rows, {})["overall"]
        self.assertEqual((summary["games"], summary["win"], summary["faults"]), (2, 1, 1))
        self.assertEqual((summary["attempts"], summary["executed"], summary["refused"]), (4, 3, 1))


if __name__ == "__main__":
    unittest.main()

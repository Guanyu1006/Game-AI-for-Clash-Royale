"""Synthetic artifact-contract checks; these results are not game evidence."""

from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from arena_lab.compare_evaluations import compare_evaluations, load_completed_evaluation


def _bytes(value):
    return (json.dumps(value, sort_keys=True) + "\n").encode()


def _stats(rows):
    natural = [row for row in rows if row["status"] == "natural_end"]
    wins = sum(row["outcome"] == "win" for row in natural)
    losses = sum(row["outcome"] == "loss" for row in natural)
    draws = sum(row["outcome"] == "draw" for row in natural)
    return {"attempted_games": len(rows), "natural_games": len(natural), "wins": wins,
            "losses": losses, "draws": draws,
            "truncated_games": sum(row["status"] == "truncated" for row in rows),
            "faulted_games": sum(row["status"] == "fault" for row in rows),
            "win_rate_natural": wins / len(natural) if natural else None,
            "score_natural": (wins + draws / 2) / len(natural) if natural else None}


def _write(directory, plan, rows):
    directory.mkdir(exist_ok=True)
    plan_bytes = _bytes(plan)
    episode_bytes = b"".join(_bytes(row) for row in rows)
    summary = {
        "schema": "arena-evaluation-report-v1",
        "plan_sha256": hashlib.sha256(plan_bytes).hexdigest(),
        "episodes_sha256": hashlib.sha256(episode_bytes).hexdigest(),
        "scope": plan["score_scope"], "total": _stats(rows),
        "by_variant": {}, "by_opponent": {},
    }
    opponents = {case["opponent"] for case in plan["cases"]}
    for variant in plan["variants"]:
        selected = [row for row in rows if row["variant"] == variant]
        summary["by_variant"][variant] = _stats(selected)
        summary["by_opponent"][variant] = {
            opponent: _stats([row for row in selected if row["opponent"] == opponent])
            for opponent in opponents}
    (directory / "plan.json").write_bytes(plan_bytes)
    (directory / "episodes.jsonl").write_bytes(episode_bytes)
    (directory / "summary.json").write_bytes(_bytes(summary))


class ArenaComparisonTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-compare-tests"
        parent.mkdir(parents=True, exist_ok=True)
        temporary = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        self.directory = Path(temporary.name)
        self.old = self.directory / "reference"
        self.new = self.directory / "candidate"
        self.plan = {
            "schema": "arena-evaluation-plan-v1", "checkpoint": "old.pt", "checkpoint_sha256": "a" * 64,
            "checkpoint_metadata": {"environment_identity": {"backend": "synthetic-only", "version": 1}},
            "environment_identity": {"backend": "synthetic-only", "version": 1},
            "variants": ["model"], "deterministic": True,
            "max_decisions": 100, "max_wall_seconds": 10.0, "trace_every": 20, "trace_max_bytes": 1000,
            "score_scope": "synthetic unit-test data only",
            "cases": [{"case_id": f"case-{i}", "seed": 70 + i, "candidate_side": i % 2,
                       "opponent": "scripted" if i % 2 else "random",
                       "decks": [list(range(8)), list(range(8, 16))],
                       "reset_options": {"levels": [[16] * 8] * 2, "tower_levels": [16, 16], "shuffle": 1}}
                      for i in range(6)],
        }
        self.new_plan = deepcopy(self.plan)
        self.new_plan.update(checkpoint="new.pt", checkpoint_sha256="b" * 64)
        self.old_rows = self.rows(["win", "loss", "draw", "win", "fault", "truncated"])
        self.new_rows = self.rows(["loss", "win", "draw", "truncated", "win", "fault"])
        self.write()

    def rows(self, outcomes, variant="model", plan=None):
        rows = []
        for case, outcome in zip((plan or self.plan)["cases"], outcomes):
            row = {**deepcopy(case), "variant": variant,
                   "status": "natural_end" if outcome in {"win", "loss", "draw"} else outcome,
                   "winner": None, "outcome": None, "termination_reason": outcome}
            if row["status"] == "natural_end":
                row["outcome"] = outcome
                row["winner"] = {"win": case["candidate_side"], "loss": 1 - case["candidate_side"], "draw": 2}[outcome]
            rows.append(row)
        return rows

    def write(self):
        _write(self.old, self.plan, self.old_rows)
        _write(self.new, self.new_plan, self.new_rows)

    def test_pairing_preserves_failed_denominator_and_scores_only_two_natural_endings(self):
        result = compare_evaluations(self.old, self.new)
        self.assertEqual(result["planned_pairs"], 6)
        self.assertEqual(result["both_natural_pairs"], 3)
        self.assertEqual(result["unscored_pairs"], 3)
        self.assertEqual((result["positive_pairs"], result["negative_pairs"], result["tied_pairs"]), (1, 1, 1))
        self.assertEqual(result["mean_score_difference"], 0)
        self.assertEqual(result["old"]["natural_games"], 4)
        self.assertEqual(result["old"]["faulted_games"], 1)
        self.assertEqual(result["new"]["truncated_games"], 1)
        self.assertEqual(result["old_on_both_natural_pairs"]["wins"], 1)
        self.assertEqual(result["old_on_both_natural_pairs"]["win_rate_natural"], 1 / 3)
        self.assertEqual(len(result["cases"]), 6)
        self.assertTrue(all(row["score_difference_new_minus_old"] is None for row in result["cases"][3:]))
        self.assertEqual(result["old"]["files_sha256"]["summary.json"],
                         hashlib.sha256((self.old / "summary.json").read_bytes()).hexdigest())

    def test_different_extra_variants_are_allowed_but_their_completeness_is_checked(self):
        self.plan["variants"].append("random")
        self.old_rows.extend(self.rows(["draw"] * 6, "random"))
        self.write()
        self.assertEqual(compare_evaluations(self.old, self.new)["planned_pairs"], 6)
        self.old_rows.pop()
        self.write()
        with self.assertRaisesRegex(ValueError, "Incomplete evaluation"):
            compare_evaluations(self.old, self.new)

    def test_file_hash_and_external_summary_receipt_detect_changes(self):
        trusted = hashlib.sha256((self.old / "summary.json").read_bytes()).hexdigest()
        load_completed_evaluation(self.old, expected_summary_sha256=trusted)
        for filename in ("plan.json", "episodes.jsonl"):
            with self.subTest(filename=filename):
                self.write()
                with (self.old / filename).open("ab") as stream:
                    stream.write(b" ")
                with self.assertRaisesRegex(ValueError, "SHA256"):
                    compare_evaluations(self.old, self.new)
        self.write()
        with (self.old / "summary.json").open("ab") as stream:
            stream.write(b" ")
        with self.assertRaisesRegex(ValueError, "Summary SHA256"):
            load_completed_evaluation(self.old, expected_summary_sha256=trusted)

    def test_duplicate_or_missing_episode_rejected_even_with_updated_hashes_and_counts(self):
        for rows in (self.old_rows + [self.old_rows[0]], self.old_rows[:-1]):
            with self.subTest(length=len(rows)):
                _write(self.old, self.plan, rows)
                with self.assertRaisesRegex(ValueError, "Duplicate episode|Incomplete evaluation"):
                    compare_evaluations(self.old, self.new)

    def test_case_input_tampering_and_forged_summary_counts_are_rejected(self):
        self.old_rows[0]["reset_options"]["levels"][0][0] = 11
        self.write()
        with self.assertRaisesRegex(ValueError, "Episode inputs differ"):
            compare_evaluations(self.old, self.new)
        self.old_rows[0] = self.rows(["win"])[0]
        self.write()
        path = self.old / "summary.json"
        summary = json.loads(path.read_text())
        summary["by_variant"]["model"]["natural_games"] = 6
        path.write_bytes(_bytes(summary))
        with self.assertRaisesRegex(ValueError, "Summary mismatch"):
            compare_evaluations(self.old, self.new)

    def test_matching_case_ids_are_insufficient_when_plan_inputs_or_decoder_differ(self):
        mutations = [
            lambda p: p["cases"][0].update(seed=900),
            lambda p: p["cases"][0].update(candidate_side=1),
            lambda p: p["cases"][0].update(opponent="wait"),
            lambda p: p["cases"][0]["decks"][0].__setitem__(0, 999),
            lambda p: p["cases"][0]["reset_options"].update(shuffle=0),
            lambda p: p.update(deterministic=False),
            lambda p: p.update(max_decisions=101),
            lambda p: p.update(decoder_settings={"threshold": 0.2}),
        ]
        for mutation in mutations:
            with self.subTest(mutation=mutation):
                plan = deepcopy(self.new_plan)
                mutation(plan)
                # These candidate rows agree with their own plan, but inputs
                # differ from reference despite identical case IDs.
                rows = self.rows(["loss", "win", "draw", "truncated", "win", "fault"], plan=plan)
                _write(self.new, plan, rows)
                with self.assertRaisesRegex(ValueError, "plans differ"):
                    compare_evaluations(self.old, self.new)

    def test_environment_identity_and_checkpoint_decoder_changes_are_rejected(self):
        self.new_plan["environment_identity"]["version"] = 2
        self.new_plan["checkpoint_metadata"]["environment_identity"]["version"] = 2
        self.write()
        with self.assertRaisesRegex(ValueError, "plans differ"):
            compare_evaluations(self.old, self.new)
        self.new_plan = deepcopy(self.plan)
        self.new_plan["checkpoint_metadata"]["decoder_settings"] = {"threshold": 0.4}
        self.write()
        with self.assertRaisesRegex(ValueError, "Checkpoint decoder settings differ"):
            compare_evaluations(self.old, self.new)

    def test_invalid_outcome_or_duplicate_case_cannot_masquerade_as_complete(self):
        self.old_rows[0]["outcome"] = "loss"
        self.write()
        with self.assertRaisesRegex(ValueError, "Winner/outcome mismatch"):
            compare_evaluations(self.old, self.new)
        self.old_rows[0]["outcome"] = "win"
        self.plan["cases"].append(deepcopy(self.plan["cases"][0]))
        self.write()
        with self.assertRaisesRegex(ValueError, "duplicate case_id"):
            compare_evaluations(self.old, self.new)

    def test_no_both_natural_pairs_reports_null_rates_and_keeps_all_cases(self):
        self.old_rows = self.rows(["fault"] * 6)
        self.new_rows = self.rows(["truncated"] * 6)
        self.write()
        result = compare_evaluations(self.old, self.new)
        self.assertEqual(result["unscored_pairs"], 6)
        self.assertIsNone(result["mean_score_difference"])
        self.assertIsNone(result["old_on_both_natural_pairs"]["win_rate_natural"])
        self.assertEqual(result["status_pairs"], {"fault->truncated": 6})

    def test_identical_case_inputs_with_renamed_id_are_still_duplicate(self):
        duplicate = deepcopy(self.plan["cases"][0])
        duplicate["case_id"] = "duplicate-inputs-with-new-name"
        self.plan["cases"].append(duplicate)
        self.write()
        with self.assertRaisesRegex(ValueError, "Duplicate case inputs"):
            compare_evaluations(self.old, self.new)


if __name__ == "__main__":
    unittest.main()

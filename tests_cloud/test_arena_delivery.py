"""Delivery receipts cannot confuse a partial smoke run with promotion."""
from pathlib import Path
import json
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

try:
    import torch
except ImportError:
    torch = None

from arena_lab.delivery import ARTIFACTS, relabel_evaluation_scope, smoke_cases
from arena_lab.league import digest, write_json


class DeliveryScopeTests(unittest.TestCase):
    def test_relabel_preserves_raw_receipts_and_rebinds_hash(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            write_json(root/"plan.json", {"partition": "promotion_frozen_not_training", "cases": [1, 2]})
            (root/"episodes.jsonl").write_text('{"case": 1}\n{"case": 2}\n')
            write_json(root/"summary.json", {"plan_sha256": digest(root/"plan.json"),
                                             "episodes_sha256": digest(root/"episodes.jsonl"), "natural": 2})
            plan, summary = (root/"plan.json").read_bytes(), (root/"summary.json").read_bytes()
            result = relabel_evaluation_scope(root, partition="delivery_smoke_not_promotion", scope="Smoke only")
            self.assertEqual((root/"original-runner-plan.json").read_bytes(), plan)
            self.assertEqual((root/"original-runner-summary.json").read_bytes(), summary)
            self.assertEqual(result["plan_sha256"], digest(root/"plan.json"))
            self.assertNotEqual(json.loads((root/"plan.json").read_text())["partition"], "promotion_frozen_not_training")
            self.assertEqual(result, relabel_evaluation_scope(root, partition="delivery_smoke_not_promotion", scope="Smoke only"))
            with self.assertRaisesRegex(ValueError, "non-promotion"):
                relabel_evaluation_scope(root, partition="promotion_frozen_not_training", scope="Wrong")

    def test_modified_episodes_rejected_before_and_after_scope_correction(self):
        for already_corrected in (False, True):
            with self.subTest(already_corrected=already_corrected), tempfile.TemporaryDirectory() as root:
                root = Path(root)
                write_json(root/"plan.json", {"partition": "promotion_frozen_not_training", "cases": [1]})
                (root/"episodes.jsonl").write_text('{"outcome": "loss"}\n')
                write_json(root/"summary.json", {"plan_sha256": digest(root/"plan.json"),
                                                 "episodes_sha256": digest(root/"episodes.jsonl")})
                if already_corrected:
                    relabel_evaluation_scope(root, partition="delivery_smoke_not_promotion", scope="Smoke only")
                before = {p.name: p.read_bytes() for p in root.glob('*.json')}
                (root/"episodes.jsonl").write_text('{"outcome": "win"}\n')
                with self.assertRaisesRegex(ValueError, "episodes receipt"):
                    relabel_evaluation_scope(root, partition="delivery_smoke_not_promotion", scope="Smoke only")
                self.assertEqual(before, {p.name: p.read_bytes() for p in root.glob('*.json')})

    def test_smoke_cases_have_stable_reserved_seed_and_exchange_configurations(self):
        config = SimpleNamespace(decks=[["a"]*8, ["b"]*8], forms=[[0]*8, [1]*8],
                                 levels=[[11]*8, [12]*8], tower_levels=[11, 12],
                                 elixir_milli=[5000, 6000], shuffle=1)
        rows = smoke_cases("pixel-masked-v1", config)
        self.assertEqual([r["seed"] for r in rows], [730004, 730004])
        self.assertEqual(rows[1]["decks"], config.decks[::-1])
        for field in ("forms", "levels", "tower_levels", "elixir_milli"):
            self.assertEqual(rows[1]["reset_options"][field], getattr(config, field)[::-1])

    @unittest.skipIf(torch is None, "Optional CPU Torch unavailable")
    def test_missing_later_artifact_keeps_failure_and_pending_rows(self):
        from arena_lab.delivery import check
        with tempfile.TemporaryDirectory() as root:
            output = Path(root)/"check"
            with patch("arena_lab.delivery._check_one", side_effect=[
                    {"status": "passed", "passed": True, "executed": 10}, FileNotFoundError("Missing reviewed artifact")]):
                with self.assertRaises(FileNotFoundError):
                    check(output, ARTIFACTS[:3])
            report = json.loads((output/"report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertFalse(report["complete"])
            self.assertEqual(report["models"][ARTIFACTS[0]]["status"], "passed")
            self.assertEqual(report["models"][ARTIFACTS[1]]["status"], "failed")
            self.assertEqual(report["models"][ARTIFACTS[2]]["status"], "pending")
            self.assertEqual(report["models"][ARTIFACTS[1]]["error"], "FileNotFoundError: Missing reviewed artifact")

    @unittest.skipIf(torch is None, "Optional CPU Torch unavailable")
    def test_first_artifact_failure_is_saved_and_duplicate_selection_rejected(self):
        from arena_lab.delivery import check
        with tempfile.TemporaryDirectory() as root:
            with patch("arena_lab.delivery._check_one", side_effect=ValueError("Identity mismatch")):
                with self.assertRaises(ValueError):
                    check(Path(root)/"bad", ARTIFACTS[:1])
            report = json.loads((Path(root)/"bad/report.json").read_text())
            self.assertFalse(report["passed"])
            self.assertTrue(report["complete"])
            with self.assertRaisesRegex(ValueError, "unique"):
                check(Path(root)/"duplicate", [ARTIFACTS[0], ARTIFACTS[0]])


if __name__ == "__main__":
    unittest.main()

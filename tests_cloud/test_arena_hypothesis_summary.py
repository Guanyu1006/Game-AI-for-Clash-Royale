import copy
import json
from pathlib import Path
import tempfile
import unittest

from arena_lab.data_sources import sha256_file
from arena_lab.hypothesis_audit import VARIANTS
from arena_lab.hypothesis_summary import summarize_audit


class HypothesisSummaryContracts(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp/arena-hypothesis-summary-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.tmp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source_game = self.root / "train-game.json"
        self.source_game.write_text(json.dumps({"game_id": "g", "source_id": "raw", "split": "train",
            "end_tick": 200, "winner": 0, "players": [{"crowns": 1}, {"crowns": 0}],
            "events": [{"ability": False}, {"ability": True}, {"ability": True}]}))
        self.index = self.root / "source-index.json"
        # An unrelated test path deliberately does not exist. Descriptive train
        # aggregation must never load that game or any policy-test observations.
        self.index.write_text(json.dumps({"games": [
            {"game_id": "g", "path": str(self.source_game)},
            {"game_id": "held-out", "path": str(self.root / "must-not-read-test.json")}]}))
        self.audit = self.root / "audit"
        self.audit.mkdir()
        counts = {"source_events": 3, "source_ability_events": 2, "sampled_frames": 10,
                  "valid_wait_labels": 7, "valid_play_labels": 1, "valid_ability_labels": 1,
                  "invalid_labels": 1, "ability_accepted": 1,
                  "events_not_reached_before_native_terminal": 0}
        variants = [{"variant": v, "counts": copy.deepcopy(counts), "wall_seconds": 1,
                     "first_command_divergence": {"status": "ambiguous-public-buttons", "source_tick": 150},
                     "source_team_to_native_seat": {"0": 1, "1": 0},
                     "native_terminal": {"terminated": True, "truncated": False, "tick": 180,
                                         "winner_source_team": 0, "crowns_native_seat": [0, 1]},
                     "diagnostics": [{"ability": False, "status": "OK", "native_feedback": {"reason": "OK"}},
                                     {"ability": True, "status": "OK", "native_feedback": {"reason": "OK"}},
                                     {"ability": True, "status": "ambiguous-public-buttons"}]} for v in VARIANTS]
        self.report_path = self.audit / "game.json"
        self.report = {"game_id": "g", "split": "train", "variants": variants,
                       "normalized_source_sha256": sha256_file(self.source_game)}
        self.write_report()

    def write_report(self):
        self.report_path.write_text(json.dumps(self.report))
        (self.audit / "summary.json").write_text(json.dumps({
            "source_index_sha256": sha256_file(self.index), "historical_hypotheses_sha256": "facts",
            "eligible_games": 1, "requested_limit": 1, "failures": [], "split": "train",
            "games": [{"path": "game.json", "sha256": sha256_file(self.report_path)}]}))

    def test_preserves_ability_denominator_and_maps_crowns_before_comparing(self):
        result = summarize_audit(self.audit, self.index, self.root / "out.json")
        self.assertEqual(result["selected_game_ids"], ["g"])
        for variant in result["variants"].values():
            self.assertEqual(variant["counts"]["natural_crowns_match_source_games"], 1)
            self.assertEqual(variant["natural_terminal_tick_error"]["median"], -20)
            self.assertEqual(variant["valid_grid_fraction"], .9)
            self.assertEqual(variant["reached_unknown_ability_reasons"], {"ambiguous-public-buttons": 1})
            self.assertEqual(variant["natural_winner_agreement"]["known_source_winner_denominator"], 1)
            self.assertEqual(variant["natural_winner_agreement"]["unknown_source_winner_games"], 0)
        self.assertFalse(result["strong_imitation_delivery_gate_passed"])
        self.assertFalse(result["model_scoring_performed"])

    def test_missing_ability_outcome_fails_instead_of_shrinking_denominator(self):
        self.report["variants"][0]["diagnostics"].pop()
        # Keep all events marked reached to reproduce a missing diagnostic row.
        self.write_report()
        with self.assertRaisesRegex(ValueError, "Event outcome accounting"):
            summarize_audit(self.audit, self.index, self.root / "out.json")

    def test_modified_event_evidence_is_rejected_by_hash(self):
        self.report_path.write_text(self.report_path.read_text() + " ")
        with self.assertRaisesRegex(ValueError, "checksum"):
            summarize_audit(self.audit, self.index, self.root / "out.json")

    def test_unknown_source_winner_is_excluded_from_agreement_denominator(self):
        game = json.loads(self.source_game.read_text())
        game["winner"] = None
        self.source_game.write_text(json.dumps(game))
        self.report["normalized_source_sha256"] = sha256_file(self.source_game)
        self.write_report()
        result = summarize_audit(self.audit, self.index, self.root / "out.json")
        for variant in result["variants"].values():
            counts = variant["counts"]
            self.assertEqual(counts["natural_end_games"], 1)
            self.assertEqual(counts["source_winner_unknown_games"], 1)
            self.assertEqual(variant["natural_winner_agreement"]["matches"], 0)
            self.assertEqual(variant["natural_winner_agreement"]["known_source_winner_denominator"], 0)
            self.assertEqual(variant["natural_winner_agreement"]["unknown_source_winner_games"], 1)


if __name__ == "__main__":
    unittest.main()

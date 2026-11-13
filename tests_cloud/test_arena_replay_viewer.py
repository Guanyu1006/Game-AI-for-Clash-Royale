"""Offline viewer artifact and untrusted-string boundary checks."""

import gzip
import json
from pathlib import Path
import tempfile
import unittest

from arena_lab.replay_viewer import load_public_trace, render_replay, safe_script_json


class ArenaReplayViewerTests(unittest.TestCase):
    def setUp(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp" / "arena-viewer-tests"
        parent.mkdir(parents=True, exist_ok=True)
        self.directory = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        (self.root / "traces").mkdir()
        self.trace = self.root / "traces" / "model-0000.jsonl.gz"
        self.hostile = '</script><script>alert("untrusted name")</script>&'
        self.frame = {"kind": "actor_observation", "step": 0, "tick": 0, "player": 0,
                      "entity_dim": 12, "visible_entities_sparse": [{"indices": [0, 1, 2], "values": [.2, .3, .8]}],
                      "global_features": [.5], "hand_ids": [1, 1, 1, 1], "action": {"card": 0, "position": 0}}
        self.write_trace(self.frame)
        identity = {"backend": "synthetic_fixture", "observation_version": "fixture-only",
                    "entity_base_fields": ["x", "y", "hp_ratio"], "global_base_fields": ["own_elixir"],
                    "unit_types": [self.hostile], "cards": [self.hostile], "obs_spec": {"grid_w": 18, "grid_h": 32}}
        plan = {"environment_identity": identity, "checkpoint_metadata": {"trained": False}, "trace_every": 20}
        (self.root / "plan.json").write_text(json.dumps(plan))
        row = {"variant": "model", "case_id": self.hostile, "candidate_side": 0, "seed": 1,
               "status": "truncated", "outcome": None, "final_tick": 100, "simulated_seconds": 5,
               "trace": {"path": str(self.trace), "size_limited": False}}
        (self.root / "episodes.jsonl").write_text(json.dumps(row) + "\n")

    def write_trace(self, frame):
        with gzip.open(self.trace, "wt", encoding="utf-8") as stream:
            stream.write(json.dumps(frame) + "\n")

    def test_standalone_html_has_controls_and_escaped_source_names(self):
        output = self.root / "replay.html"
        report = render_replay(self.root, output)
        html = output.read_text(encoding="utf-8")
        self.assertEqual(report["frames"], 1)
        self.assertFalse(report["trained_checkpoint"])
        self.assertNotIn(self.hostile, html)
        self.assertIn("\\u003c/script\\u003e", html)
        self.assertIn('id="timeline"', html)
        self.assertIn('id="speed"', html)
        self.assertNotIn("innerHTML", html)
        self.assertNotIn('<script src=', html)
        with self.assertRaises(FileExistsError):
            render_replay(self.root, output)

    def test_sparse_entities_and_decompression_limits_are_validated(self):
        with self.assertRaisesRegex(ValueError, "budget"):
            load_public_trace(self.trace, maximum_bytes=1)
        self.frame["visible_entities_sparse"][0]["indices"] = [0, 1, 99]
        self.write_trace(self.frame)
        with self.assertRaisesRegex(ValueError, "outside"):
            load_public_trace(self.trace)

    def test_safe_json_roundtrips_and_cannot_close_script(self):
        encoded = safe_script_json({"name": self.hostile, "newline": "\u2028\u2029"})
        self.assertNotIn("<", encoded)
        self.assertEqual(json.loads(encoded)["name"], self.hostile)

    def test_foreign_unknown_hp_layout_is_not_rendered_as_measured_zero(self):
        path = self.root / "plan.json"
        plan = json.loads(path.read_text())
        plan["environment_identity"]["entity_extra_fields"] = ["hp_known", "row_known"]
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "unknown HP"):
            render_replay(self.root, self.root / "foreign.html")
        self.assertFalse((self.root / "foreign.html").exists())
        plan["environment_identity"].pop("entity_extra_fields")
        plan["environment_identity"]["visual_projection"] = {"known_masks": "explicit"}
        path.write_text(json.dumps(plan))
        with self.assertRaisesRegex(ValueError, "unknown HP"):
            render_replay(self.root, self.root / "masked.html")
        self.assertFalse((self.root / "masked.html").exists())


if __name__ == "__main__":
    unittest.main()

"""Alignment rejects shifted labels, forged confirmations and stale frame claims."""
import copy
import json
from pathlib import Path
import struct
import tempfile
import types
import unittest
from unittest.mock import patch

import numpy as np

from arena_lab.alignment import (audit_capture_pairs, audit_timing_arrays,
                                 contained_artifact, observation_sha256, replay_alignment, sha256_file)


class TimingAlignmentTests(unittest.TestCase):
    def arrays(self):
        return {"ticks": np.array([0, 10, 20]), "observation_tick": np.array([0, 10, 20]),
                "request_tick": np.array([-1, 10, -1]), "confirmed_tick": np.array([-1, 10, -1]),
                "elapsed_ticks": np.array([0, 10, 10]), "decision_ticks": np.array([10, 10, 10]),
                "target_card": np.array([0, 2, 0]), "label_valid": np.ones(3, bool)}

    def test_request_cannot_be_attached_to_earlier_window(self):
        arrays = self.arrays()
        arrays["request_tick"][1] = 19
        with self.assertRaisesRegex(ValueError, "immediately after"):
            audit_timing_arrays(arrays, 10)

    def test_unconfirmed_action_cannot_be_a_valid_imitation_target(self):
        arrays = self.arrays()
        arrays["confirmed_tick"][1] = -1
        with self.assertRaisesRegex(ValueError, "actual execution"):
            audit_timing_arrays(arrays, 10)
        arrays["label_valid"][1] = False
        self.assertEqual(audit_timing_arrays(arrays, 10)["confirmed"], 0)

    def test_real_delayed_confirmation_retains_request_time(self):
        arrays = self.arrays()
        arrays["confirmed_tick"][1] = 13
        self.assertEqual(audit_timing_arrays(arrays, 10)["max_confirmation_delay_ticks"], 3)

    def test_cadence_and_elapsed_time_are_not_inferred_from_row_number(self):
        arrays = self.arrays()
        arrays["elapsed_ticks"][2] = 20
        with self.assertRaisesRegex(ValueError, "Elapsed time"):
            audit_timing_arrays(arrays, 10)
        arrays = self.arrays()
        arrays["ticks"][2] = arrays["observation_tick"][2] = 30
        with self.assertRaisesRegex(ValueError, "control cadence"):
            audit_timing_arrays(arrays, 10)

    def test_hash_matches_persisted_precision_and_changes_with_public_state(self):
        obs = {"global_features": np.array([.4, .2], np.float32),
               "entities": np.array([[.3, .1]], np.float32), "entity_mask": np.array([True]),
               "hand_ids": np.array([1, 2, 3, 4]), "legal_cards": np.array([True, False]),
               "legal_positions": np.array([[True, False]])}
        saved = copy.deepcopy(obs)
        saved["entities"] = saved["entities"].astype(np.float16)
        saved["global_features"] = saved["global_features"].astype(np.float16)
        self.assertEqual(observation_sha256(obs), observation_sha256(saved))
        saved["entities"][0, 0] = .8
        self.assertNotEqual(observation_sha256(obs), observation_sha256(saved))


class CaptureAlignmentTests(unittest.TestCase):
    def test_array_length_and_extra_private_fields_fail_before_engine_construction(self):
        root = Path(__file__).resolve().parents[1] / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            (folder / "trace.gz").write_bytes(b"checksum-only-fixture")
            data = TimingAlignmentTests().arrays()
            data.update(target_position=np.zeros(3, np.int32), position_valid=np.array([False, True, False]))
            for key in ("global_features", "entities", "entity_mask", "hand_ids", "legal_cards", "legal_positions"):
                data[key] = np.zeros((3, 1))
            entries = []
            for seat in (0, 1):
                path = folder / f"seat-{seat}.npz"
                np.savez_compressed(path, **data)
                entries.append({"seat": seat, "path": path.name, "sha256": sha256_file(path), "steps": 2})
            manifest = {"schema": "arena-paired-episode-v1", "joint_decisions": 2,
                        "decision_ticks": 10, "actor_sequences": entries,
                        "trajectory": {"path": "trace.gz", "sha256": sha256_file(folder / "trace.gz")}}
            path = folder / "manifest.json"
            path.write_text(json.dumps(manifest))
            fake = types.ModuleType("arena_lab.environment")
            fake.ArenaConfig = fake.ArenaEnv = lambda *args, **kwargs: self.fail("Invalid archive reached engine")
            with patch.dict("sys.modules", {"arena_lab.environment": fake}):
                with self.assertRaisesRegex(ValueError, "length differs"):
                    replay_alignment(path)
                data["enemy_private_hand"] = np.zeros((3, 4))
                np.savez_compressed(folder / "seat-0.npz", **data)
                manifest["actor_sequences"][0]["sha256"] = sha256_file(folder / "seat-0.npz")
                path.write_text(json.dumps(manifest))
                with self.assertRaisesRegex(ValueError, "public observation/timing schema"):
                    replay_alignment(path)

    def test_manifest_cannot_escape_episode_via_parent_or_symlink(self):
        root = Path(__file__).resolve().parents[1] / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            (folder / "episode").mkdir()
            (folder / "outside.json").write_text("{}")
            (folder / "episode" / "outward.json").symlink_to(folder / "outside.json")
            for name in ("../outside.json", "outward.json", str(folder / "outside.json")):
                with self.assertRaisesRegex(ValueError, "Artifact path"):
                    contained_artifact(folder / "episode", name)

    def test_stable_clock_does_not_certify_fresh_rendered_pixels(self):
        root = Path(__file__).resolve().parents[1] / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            # PNG header sufficient for this integrity/timing auditor; detector
            # acceptance separately decodes and measures real image content.
            (folder / "frame.png").write_bytes(b"\x89PNG\r\n\x1a\n" + struct.pack(">I", 13) +
                                                b"IHDR" + struct.pack(">II", 1, 1))
            (folder / "state.json").write_text(json.dumps({"tick": 100}))
            row = {"frame_path": "frame.png", "frame_sha256": sha256_file(folder / "frame.png"),
                   "state_path": "state.json", "state_sha256": sha256_file(folder / "state.json"),
                   "state_tick_before": 100, "state_tick_after": 100,
                   "generation_before": 4, "generation_after": 4, "version_fingerprint": "fixed",
                   "capture_started_ns": 1, "capture_finished_ns": 2,
                   "rendered_state_tick": None, "render_latency_bound_ms": None}
            result = audit_capture_pairs([row], folder, "fixed")
            self.assertTrue(result["temporal_alignment_passed"])
            self.assertFalse(result["render_alignment_passed"])
            row["rendered_state_tick"] = 100
            row["render_latency_bound_ms"] = 75
            self.assertTrue(audit_capture_pairs([row], folder, "fixed")["render_alignment_passed"])
            row["generation_after"] = 5
            self.assertFalse(audit_capture_pairs([row], folder, "fixed")["temporal_alignment_passed"])

    def test_tampered_image_and_version_are_rejected(self):
        root = Path(__file__).resolve().parents[1] / ".tmp"
        root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            folder = Path(temporary)
            (folder / "frame.png").write_bytes(b"image")
            (folder / "state.json").write_text(json.dumps({"tick": 100}))
            row = {"frame_path": "frame.png", "frame_sha256": "wrong",
                   "state_path": "state.json", "state_sha256": sha256_file(folder / "state.json"),
                   "state_tick_before": 100, "state_tick_after": 101,
                   "generation_before": 4, "generation_after": 4, "version_fingerprint": "other",
                   "capture_started_ns": 2, "capture_finished_ns": 1}
            result = audit_capture_pairs([row], folder, "fixed")
            self.assertFalse(result["temporal_alignment_passed"])
            self.assertEqual(set(result["failures"][0]["reasons"]),
                             {"frame_hash", "unstable_state_tick", "version_mismatch", "wall_clock_order"})


if __name__ == "__main__":
    unittest.main()

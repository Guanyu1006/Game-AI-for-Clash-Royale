"""Pinned source integrity and interrupted acquisition recovery contracts."""
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from arena_lab.alpha_data import cached_file, source_name, validate_plan, verify_file
from arena_lab.paired_data import collect
from arena_lab.alignment import sha256_file
from arena_lab.alpha_completion import audit_review

ROOT = Path(__file__).resolve().parents[1]


def setUpModule():
    (ROOT/".tmp").mkdir(exist_ok=True)


class PinnedSourceTests(unittest.TestCase):
    def test_paths_and_basename_collisions_fail_before_download(self):
        for name in ("../outside.mp4", "/tmp/out.mp4", "a/../../bad.mp4", "x\\evil.mp4", "."):
            with self.assertRaises(ValueError):
                source_name({"path": name})
        plan = json.loads((ROOT/"arena_lab/manifests/alphaclash-ranked70-v1.json").read_text())
        validate_plan(plan)
        plan["files"].append(copy.deepcopy(plan["files"][0]))
        with self.assertRaisesRegex(ValueError, "collide"):
            validate_plan(plan)

    def test_same_visual_battle_cannot_cross_split(self):
        plan = json.loads((ROOT/"arena_lab/manifests/alphaclash-ranked70-v1.json").read_text())
        train = next(g for g in plan["games"] if g["split"] == "train")
        test = next(g for g in plan["games"] if g["split"] == "test")
        test["group_id"] = train["group_id"]
        with self.assertRaisesRegex(ValueError, "crosses splits"):
            validate_plan(plan)

    def test_same_size_tampering_detected_for_git_and_lfs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".tmp") as temporary:
            path = Path(temporary)/"sidecar.json"
            payload = b'{"a":1}'
            path.write_bytes(payload)
            sha = hashlib.sha256(payload).hexdigest()
            blob = hashlib.sha1(b"blob "+str(len(payload)).encode()+b"\0"+payload).hexdigest()
            records = [{"size": len(payload), "oid": blob},
                       {"size": len(payload), "lfs": {"oid": sha}}]
            for record in records:
                self.assertEqual(verify_file(path, record), sha)
            path.write_bytes(b'{"a":2}')
            for record in records:
                with self.assertRaisesRegex(ValueError, "mismatch"):
                    verify_file(path, record)

    def test_cache_combines_without_copy_and_rejects_conflicting_aliases(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".tmp") as temporary:
            roots = [Path(temporary)/"one", Path(temporary)/"two"]
            for root in roots:
                (root/"raw").mkdir(parents=True)
            (roots[1]/"raw/example.mp4").write_bytes(b"video")
            self.assertEqual(cached_file("example.mp4", roots), roots[1]/"raw/example.mp4")
            (roots[0]/"raw/example.mp4").write_bytes(b"other")
            with self.assertRaisesRegex(ValueError, "Conflicting"):
                cached_file("example.mp4", roots)


class InterruptedAcquisitionTests(unittest.TestCase):
    def test_complete_manifest_integrity_failure_is_not_silently_recollected(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".tmp") as temporary:
            root = Path(temporary)
            case = {"game_id": "complete", "split": "train", "environment_config": {"seed": 1}}
            plan = {"schema": "arena-paired-plan-v1", "cases": [case],
                    "teacher_source_sha256": sha256_file(ROOT/"arena_lab/baselines.py"),
                    "tick_hz": 20, "decision_ticks": 10, "wait_semantics": "explicit"}
            source = root/"frozen.json"; source.write_text(json.dumps(plan))
            out = root/"out"; (out/"complete").mkdir(parents=True)
            receipt = out/"complete/manifest.json"
            original = json.dumps(case); receipt.write_text(original)
            with patch("arena_lab.paired_data.collect_episode") as new, \
                 patch("arena_lab.paired_data.replay_alignment", side_effect=ValueError("Source hash mismatch")):
                with self.assertRaisesRegex(RuntimeError, "Acquisition incomplete"):
                    collect(source, out)
            new.assert_not_called()
            self.assertEqual(receipt.read_text(), original)
            self.assertFalse((out/"index.json").exists())
            self.assertFalse((out/"interrupted").exists())

    def test_partial_episode_preserved_and_completed_episode_replayed(self):
        for malformed in (False, True):
            with self.subTest(malformed=malformed), tempfile.TemporaryDirectory(dir=ROOT/".tmp") as temporary:
                root = Path(temporary)
                cases = [{"game_id": name, "split": "train", "environment_config": {"seed": seed}}
                         for name, seed in (("complete", 1), ("partial", 2))]
                plan = {"schema": "arena-paired-plan-v1", "cases": cases,
                        "teacher_source_sha256": sha256_file(ROOT/"arena_lab/baselines.py"),
                        "tick_hz": 20, "decision_ticks": 10, "wait_semantics": "explicit"}
                source = root/"frozen.json"; source.write_text(json.dumps(plan))
                out = root/"out"; out.mkdir()
                def manifest(case):
                    return dict(case, environment_identity={"obs_spec": {"global_dim": 2, "entity_dim": 2,
                        "max_entities": 2, "num_card_types": 2}, "observation_version": "test"},
                        terminal={"natural": True}, trajectory={"sha256": "test"}, actor_sequences=[],
                        counts={"joint_decisions": 1}, card_counts={})
                (out/"complete").mkdir(); (out/"complete/manifest.json").write_text(json.dumps(manifest(cases[0])))
                (out/"partial").mkdir(); (out/"partial/trace.gz").write_bytes(b"preserve interrupted raw bytes")
                if malformed:
                    (out/"partial/manifest.json").write_text('{"unfinished":')
                aligned = {"same_engine_replay_passed": True}
                def fresh(case, destination):
                    self.assertEqual(case, cases[1])
                    self.assertFalse(destination.exists())
                    destination.mkdir()
                    return manifest(case), aligned
                with patch("arena_lab.paired_data.collect_episode", side_effect=fresh) as new, \
                     patch("arena_lab.paired_data.replay_alignment", return_value=aligned) as old:
                    report = collect(source, out)
                self.assertTrue(report["state_alignment_passed"])
                self.assertEqual(new.call_count, 1); self.assertEqual(old.call_count, 1)
                self.assertEqual(report["session_work"]["new_games"], 1)
                self.assertEqual(report["session_work"]["reused_games"], 1)
                self.assertEqual(report["session_work"]["new_joint_decisions"], 1)
                self.assertEqual(report["session_work"]["reused_joint_decisions"], 1)
                self.assertFalse(report["throughput_including_compression_and_independent_replay"]["full_run_elapsed_known"])
                preserved = list((out/"interrupted").glob("partial-*/trace.gz"))
                self.assertEqual(len(preserved), 1)
                self.assertEqual(preserved[0].read_bytes(), b"preserve interrupted raw bytes")


class VisualCoverageReviewTests(unittest.TestCase):
    def test_unknown_or_changed_endpoint_cannot_pass_visual_review(self):
        with tempfile.TemporaryDirectory(dir=ROOT/".tmp") as temporary:
            root = Path(temporary)
            plan = root/"plan.json"
            plan.write_text(json.dumps({"games": [{"stem": "game", "split": "test"}]}))
            opening, terminal = root/"opening.png", root/"terminal.png"
            opening.write_bytes(b"reviewed opening image"); terminal.write_bytes(b"reviewed terminal image")
            row = {"stem": "game", "split": "test",
                "opening": {"path": str(opening), "sha256": sha256_file(opening), "visible_evidence": "opening_player_presentation"},
                "terminal": {"path": str(terminal), "sha256": sha256_file(terminal), "visible_evidence": "unknown"}}
            data = {"schema": "arena-alpha-completion-annotations-v1", "plan_path": str(plan),
                    "plan_sha256": sha256_file(plan), "games": [row]}
            annotations = root/"annotations.json"; annotations.write_text(json.dumps(data))
            self.assertEqual(audit_review(annotations)["opening_and_terminal_visual_coverage_games"], 0)
            row["terminal"]["visible_evidence"] = "red_blue_result_banners"
            annotations.write_text(json.dumps(data))
            self.assertEqual(audit_review(annotations)["opening_and_terminal_visual_coverage_games"], 1)
            terminal.write_bytes(b"changed terminal image")
            with self.assertRaisesRegex(ValueError, "image checksum"):
                audit_review(annotations)
            data["games"] = []
            annotations.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "omits or adds"):
                audit_review(annotations)


if __name__ == "__main__":
    unittest.main()

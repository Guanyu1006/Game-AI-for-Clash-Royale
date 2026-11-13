import json
from pathlib import Path
import tempfile
import unittest

from arena_lab.experiment import ROOT, freeze_unseen_test_index


class ExpandedHoldoutContracts(unittest.TestCase):
    def setUp(self):
        root = ROOT / ".tmp" / "arena-experiment-tests"
        root.mkdir(parents=True, exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(dir=root)
        self.root = Path(self.temporary.name)
        self.addCleanup(self.temporary.cleanup)
        (self.root / "sequence.npz").write_bytes(b"not loaded by this index-only operation")
        self.rows = [dict(game_id=g, source_game_id=g, source_sha256=g, split=s, path="sequence.npz")
                     for g, s in [("train", "train"), ("val", "validation"), ("old", "test"), ("new", "test")]]
        self.previous = self.root / "previous.json"
        self.previous.write_text(json.dumps({"schema_version": 1, "games": [self.rows[2]]}))

    def run_freeze(self):
        index = self.root / "index.json"
        index.write_text(json.dumps({"schema_version": 1, "games": self.rows}))
        destination = self.root / "subset" / "index.json"
        freeze_unseen_test_index(index, self.previous, destination)
        return json.loads(destination.read_text())

    def test_retains_training_and_resolves_paths_while_excluding_all_old_views(self):
        self.rows.append(dict(self.rows[2]))
        result = self.run_freeze()
        self.assertEqual([r["game_id"] for r in result["games"]], ["train", "val", "new"])
        self.assertTrue(all(Path(r["path"]).is_file() for r in result["games"]))
        self.assertEqual(result["provenance"]["experiment_test_selection"]["withheld_teacher_sequences"], 2)

    def test_rejects_old_test_moved_into_training(self):
        self.rows[2]["split"] = "train"
        with self.assertRaisesRegex(ValueError, "moved into training"):
            self.run_freeze()

    def test_rejects_identity_drift(self):
        self.rows[2]["source_sha256"] = "different"
        with self.assertRaisesRegex(ValueError, "disagrees"):
            self.run_freeze()

    def test_rejects_missing_previous_test(self):
        self.rows.pop(2)
        with self.assertRaisesRegex(ValueError, "all earlier test"):
            self.run_freeze()

    def test_rejects_renamed_old_source_in_training_by_id_or_raw_hash(self):
        for preserve in ("source_game_id", "source_sha256"):
            with self.subTest(preserve=preserve):
                renamed = dict(self.rows[2], game_id="renamed", split="train")
                for key in ("source_game_id", "source_sha256"):
                    if key != preserve:
                        renamed[key] = "changed"
                self.rows.append(renamed)
                try:
                    with self.assertRaisesRegex(ValueError, "renamed"):
                        self.run_freeze()
                finally:
                    self.rows.pop()

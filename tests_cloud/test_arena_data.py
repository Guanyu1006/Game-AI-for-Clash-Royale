"""Data contract regressions independent of the optional simulator/torch wheels."""
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from urllib.parse import urlencode

import numpy as np

from arena_lab.data_sources import parse_cochon, iter_raw_tar, sha256_file, whole_game_split
from arena_lab.replay_data import infer_cycle_openings, validate_arrays


def raw_replay(reverse=False):
    tags = ["AAA", "BBB"]
    if reverse:
        tags.reverse()
    html = []
    for team, tag in enumerate(tags):
        color = "blue" if team == 0 else "red"
        html.extend(f'<img data-card="{tag.lower()}{i}" data-s="{color}">' for i in range(8))
        tick = 100 if tag == "AAA" else 120
        html.append(f'<img data-card="{tag.lower()}0" data-s="{color}" data-t="{tick}" data-ability="0">')
        html.append(f'<div class="{color} marker" data-s="{"t" if team == 0 else "o"}" '
                    f'data-c="{tag.lower()}0" data-t="{tick}" data-x="{17500 if reverse else 500}" '
                    f'data-y="{31500 if reverse else 500}" data-i="{int(reverse)}"></div>')
    html.append('<div class="marker end_time" style="left:100px;">0:10</div>')
    crowns = [1 if tag == "AAA" else 0 for tag in tags]
    query = urlencode(dict(tag="other-source-id" if reverse else "source-id", team_tags=tags[0],
                           opponent_tags=tags[1], team_crowns=crowns[0], opponent_crowns=crowns[1]))
    return {"payload": {"success": True, "html": "".join(html)}, "request_url": "https://royaleapi.com/data/replay?"+query}


class SourceContractTests(unittest.TestCase):
    def test_opposite_views_share_dedup_identity_and_split(self):
        a, b = parse_cochon(raw_replay()), parse_cochon(raw_replay(True))
        self.assertNotEqual(a["source_id"], b["source_id"])
        self.assertEqual(a["game_id"], b["game_id"])
        self.assertEqual(whole_game_split(a["game_id"]), whole_game_split(b["game_id"]))

    def test_unsuffixed_form_and_original_hidden_fields_stay_unknown(self):
        game = parse_cochon(raw_replay())
        self.assertIsNone(game["events"][0]["form"])
        self.assertIsNone(game["source_quality"]["initial_hand"])
        self.assertEqual(game["end_tick"], 200)

    def test_equal_crowns_without_an_explicit_result_stay_unknown(self):
        raw = raw_replay()
        raw["request_url"] = raw["request_url"].replace("team_crowns=1", "team_crowns=0")
        self.assertIsNone(parse_cochon(raw)["winner"])

    def test_missing_event_marker_is_rejected_not_dropped(self):
        raw = raw_replay()
        raw["payload"]["html"] = raw["payload"]["html"].replace('data-c="aaa0"', 'data-c="missing"')
        with self.assertRaisesRegex(ValueError, "Timeline/map mismatch"):
            parse_cochon(raw)

    def test_untrusted_replay_tag_cannot_escape_output_directory(self):
        raw = raw_replay()
        raw["request_url"] = raw["request_url"].replace("tag=source-id", "tag=..%2Fescape")
        with self.assertRaisesRegex(ValueError, "Unsafe or empty source replay identifier"):
            parse_cochon(raw)

    def test_archive_member_reader_never_extracts_paths(self):
        temporary_root = Path(__file__).resolve().parents[1] / ".tmp"
        temporary_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
            path = Path(directory) / "source.tar.gz"
            with tarfile.open(path, "w:gz") as archive:
                for name in ("data/raw/good.json", "data/raw/../../escape.json", "elsewhere.json"):
                    payload = b"{}"
                    member = tarfile.TarInfo(name); member.size = len(payload)
                    archive.addfile(member, io.BytesIO(payload))
            self.assertEqual(list(iter_raw_tar(path)), [("data/raw/good.json", b"{}")])


class ReconstructionContractTests(unittest.TestCase):
    def test_opening_is_one_global_consistent_queue_not_per_play_insertion(self):
        source = [0, 1, 2, 3, 4, 5, 6, 7] * 2
        result = infer_cycle_openings(list(range(8)), source)
        self.assertGreater(result["consistent_opening_classes"], 0)
        self.assertFalse(result["unique_original_hand"])
        opening = result["selected_hypothesis"]
        hand, queue = opening[:4], opening[4:]
        for card in source:
            self.assertIn(card, hand)
            hand[hand.index(card)] = queue.pop(0); queue.append(card)
        self.assertEqual(source, [0, 1, 2, 3, 4, 5, 6, 7] * 2)

    def test_impossible_cycle_is_reported(self):
        result = infer_cycle_openings(list(range(8)), [0, 0])
        self.assertEqual(result["consistent_opening_classes"], 0)
        self.assertEqual(result["best_consistent_prefix"], 1)

    def arrays(self):
        return {"global_features": np.zeros((2, 3), np.float16),
                "entities": np.zeros((2, 128, 4), np.float16), "entity_mask": np.zeros((2, 128), bool),
                "hand_ids": np.ones((2, 4), np.int16), "legal_cards": np.ones((2, 8), bool),
                "legal_positions": np.ones((2, 4, 576), bool), "target_card": np.array([0, 1]),
                "target_position": np.array([0, 1]), "label_valid": np.ones(2, bool),
                "position_valid": np.array([False, True]), "ticks": np.array([0, 10])}

    def test_legal_masks_are_not_relaxed_for_a_source_target(self):
        arrays = self.arrays()
        validate_arrays(arrays, dict(global_dim=3, entity_dim=4))
        arrays["legal_positions"][1, 0, 1] = False
        with self.assertRaisesRegex(ValueError, "position label violates"):
            validate_arrays(arrays, dict(global_dim=3, entity_dim=4))

    def test_committed_expert_fact_table_matches_fetch_lock(self):
        root = Path(__file__).resolve().parents[1] / "arena_lab" / "manifests"
        lock = json.loads((root / "cochon-historical-top100-v1.json").read_text())
        facts = root / lock["expert_fact_table"]["path"]
        self.assertEqual(sha256_file(facts), lock["expert_fact_table"]["sha256"])
        rows = json.loads(facts.read_text())
        self.assertEqual(sorted(row["rank"] for row in rows), list(range(1, 101)))
        self.assertTrue(all(row["tag"] and row["evidence_url"].startswith("https://royaleapi.com/") for row in rows))

    def test_full_snapshot_preserves_original_evidence_and_uses_complete_archive_lock(self):
        root = Path(__file__).resolve().parents[1] / "arena_lab" / "manifests"
        prefix = json.loads((root / "cochon-historical-top100-v1.json").read_text())
        full = json.loads((root / "cochon-historical-top100-full-v2.json").read_text())
        self.assertEqual(prefix["revision"], full["revision"])
        self.assertEqual(prefix["expert_fact_table"], full["expert_fact_table"])
        self.assertEqual(prefix["archive"]["full_sha256"], full["archive"]["full_sha256"])
        self.assertNotIn("prefix", full)
        self.assertEqual(full["archive"]["full_bytes"], 534361487)


if __name__ == "__main__":
    unittest.main()

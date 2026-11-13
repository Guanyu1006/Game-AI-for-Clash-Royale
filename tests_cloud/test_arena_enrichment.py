import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import tempfile
import unittest

import numpy as np

from arena_lab.enrichment import historical_deck_hypotheses
from arena_lab.hypothesis_audit import unique_ability_hypothesis


class HistoricalDeckTests(unittest.TestCase):
    def test_exact_account_deck_evidence_is_separate_from_other_account_archetype(self):
        parent = Path(__file__).resolve().parents[1] / ".tmp/arena-enrichment-tests"
        parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as d:
            root = Path(d)
            cards = ["knight", "archers", "giant", "zap", "arrows", "fireball", "skeletons", "goblins"]
            game = {"game_id": "g", "source_id": "raw", "source_sha256": "abc", "split": "train",
                    "captured_at": "2026-08-04T00:00:00Z", "teacher_seats": [0],
                    "players": [{"tag": "A", "deck": cards}, {"tag": "B", "deck": cards}]}
            gp = root / "game.json"; gp.write_text(json.dumps(game))
            sp = root / "source.json"; sp.write_text(json.dumps({"games": [{"path": str(gp)}]}))
            lp = root / "links.json"
            lp.write_text(json.dumps({"source_url": "https://example.com/blog", "season_end": "2026-08-03", "rows": [
                {"rank": 1, "name": "A", "tag": "A", "deck_url": "https://example.com/deck",
                 "cards": ["knight-hero", "archers-ev1"] + cards[2:]}]}))
            before = gp.read_bytes()
            report = historical_deck_hypotheses(sp, lp, root / "out.json")
            self.assertEqual(report["counts"]["exact_account_and_deck_seats"], 1)
            self.assertEqual(report["counts"]["archetype_only_seats"], 1)
            p0, p1 = report["games"][0]["players"]
            self.assertEqual(p0["exact_account_and_deck_candidates"][0]["forms_in_source_deck_order"][:2], [2, 1])
            self.assertFalse(p1["exact_account_and_deck_candidates"])
            self.assertTrue(p1["source_original_forms_remain_unknown"])
            self.assertFalse(report["original_replay_forms_verified"])
            self.assertEqual(before, gp.read_bytes())
            with self.assertRaises(FileExistsError):
                historical_deck_hypotheses(sp, lp, root / "out.json")

    def test_tracked_facts_are_complete_hash_checked_and_do_not_claim_match_proof(self):
        path = Path(__file__).parents[1] / "arena_lab/manifests/leaderboard-decks-202607.json"
        facts = json.loads(path.read_text())
        self.assertEqual(sorted(r["rank"] for r in facts["rows"]), list(range(1, 101)))
        self.assertEqual(len({r["tag"] for r in facts["rows"]}), 100)
        self.assertEqual(facts["representation"], "public blog hyperlinks, not per-game loadout evidence")
        self.assertFalse(facts["original_match_proof"])
        digest = hashlib.sha256(json.dumps(facts["rows"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        self.assertEqual(facts["rows_sha256"], digest)
        for row in facts["rows"]:
            self.assertEqual(row["deck_url"].rsplit("/", 1)[1].split(","), row["cards"])
            self.assertEqual(len(row["cards"]), 8)


class AbilityHypothesisTests(unittest.TestCase):
    def _env(self):
        own = SimpleNamespace(abilities=[[1, 0, 2, 0, 0], [1, 0, 1, 69, 0]])
        env = SimpleNamespace(state=SimpleNamespace(players=[own, own], entities=[
            SimpleNamespace(team=0, card_id=0, hp=100, status_flags=16, uid=7)]))
        env.observe = lambda seat: {"legal_cards": np.ones(8, dtype=bool)}
        return env

    def test_undeployed_or_enemy_champion_does_not_create_false_ambiguity(self):
        env = self._env()
        env.state.entities.append(SimpleNamespace(team=1, card_id=69, hp=100, status_flags=0, uid=8))
        result = unique_ability_hypothesis(env, 0, {0: 2, 69: 0}, [
            {"card_id": 0, "name": "Knight", "champion": False},
            {"card_id": 69, "name": "ArcherQueen", "champion": True}])
        self.assertEqual(result["selected"]["native_card_id"], 0)
        self.assertEqual(result["selected"]["living_own_entity_uids"], [7])
        self.assertFalse(result["original_source_ability_identity_verified"])

    def test_two_deployed_buttons_stay_ambiguous_and_mask_refusal_stays_unknown(self):
        env = self._env()
        env.state.entities.append(SimpleNamespace(team=0, card_id=69, hp=100, status_flags=0, uid=8))
        catalogue = [{"card_id": 0, "name": "Knight", "champion": False},
                     {"card_id": 69, "name": "ArcherQueen", "champion": True}]
        result = unique_ability_hypothesis(env, 0, {0: 2, 69: 0}, catalogue)
        self.assertEqual(result["status"], "ambiguous-public-buttons")
        self.assertIsNone(result["selected"])
        env.observe = lambda seat: {"legal_cards": np.array([True] + [False] * 7)}
        self.assertEqual(unique_ability_hypothesis(env, 0, {0: 2, 69: 0}, catalogue)["status"], "no-eligible-public-button")

    def test_declared_form_and_live_hero_flag_are_both_required(self):
        env = self._env()
        cat = [{"card_id": 0, "name": "Knight", "champion": False}]
        self.assertIsNone(unique_ability_hypothesis(env, 0, {0: 0}, cat)["selected"])
        env.state.entities[0].status_flags = 0
        self.assertIsNone(unique_ability_hypothesis(env, 0, {0: 2}, cat)["selected"])


if __name__ == "__main__":
    unittest.main()

"""Join historical deck-link facts to replay accounts without overwriting unknowns."""

from __future__ import annotations

import argparse
from collections import Counter
from datetime import datetime
import hashlib
import json
from pathlib import Path
from typing import Any

from .data_sources import base_card, sha256_file


def historical_deck_hypotheses(
    source_index: str | Path, deck_links: str | Path, destination: str | Path,
    account_evidence: str | Path | None = None,
) -> dict[str, Any]:
    """Exact account + all-eight-base-card matches are candidates, never proof.

    Same-archetype evidence from other accounts is retained in a separate weaker
    field. Nothing here marks unknown original match forms or abilities as known.
    """
    source_index, deck_links, destination = map(Path, (source_index, deck_links, destination))
    if destination.exists():
        raise FileExistsError("Preserve prior historical-deck hypothesis artifacts")
    source = json.loads(source_index.read_text(encoding="utf-8"))
    links = json.loads(deck_links.read_text(encoding="utf-8"))
    if "rows_sha256" in links:
        digest = hashlib.sha256(json.dumps(links["rows"], sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if digest != links["rows_sha256"]:
            raise ValueError("Historical hyperlink fact rows fail their recorded SHA-256")
    if account_evidence is not None:
        account_evidence = Path(account_evidence)
        accounts = json.loads(account_evidence.read_text(encoding="utf-8"))
    else:
        accounts = links["rows"]
    by_rank = {row["rank"]: row for row in accounts}
    table = []
    for row in links["rows"]:
        account = by_rank.get(row["rank"])
        if account is None:
            raise ValueError("Deck rank lacks independently recorded account identity")
        forms = dict(base_card(card) for card in row["cards"])
        if len(row["cards"]) != 8 or len(forms) != 8:
            raise ValueError("Historical hyperlink must identify eight distinct base cards")
        table.append({"rank": row["rank"], "name": account["name"], "tag": account["tag"],
                      "deck_url": row["deck_url"], "source_blog_url": links["source_url"],
                      "snapshot_date": links["season_end"], "card_forms": forms,
                      "account_evidence_url": account.get("evidence_url", account.get("account_evidence_url", links["source_url"])),
                      "confidence": "historical-account-deck-snapshot; not original replay proof"})
    games = []
    counts: Counter = Counter()
    for source_row in source["games"]:
        game = json.loads(Path(source_row["path"]).read_text(encoding="utf-8"))
        players = []
        exact_sides = 0
        for seat, player in enumerate(game["players"]):
            base_deck = [base_card(card)[0] for card in player["deck"]]
            base_set = set(base_deck)
            if len(base_set) != 8:
                raise ValueError("Replay deck does not contain eight unique base cards")
            matching_decks = [row for row in table if set(row["card_forms"]) == base_set]
            exact = [row for row in matching_decks if row["tag"].lstrip("#") == player["tag"].lstrip("#")]
            archetype = [row for row in matching_decks if row not in exact]
            # Multiple accounts using the same forms do not create extra hypotheses.
            unique_alternatives: dict[str, dict] = {}
            for row in archetype:
                key = json.dumps(row["card_forms"], sort_keys=True)
                if key not in unique_alternatives:
                    unique_alternatives[key] = {"card_forms": row["card_forms"], "supporting_deck_urls": [], "supporting_tags": []}
                hypothesis = unique_alternatives[key]
                if row["deck_url"] not in hypothesis["supporting_deck_urls"]:
                    hypothesis["supporting_deck_urls"].append(row["deck_url"])
                hypothesis["supporting_tags"].append(row["tag"])
            exact_sides += bool(exact)
            counts["player_seats"] += 1
            counts["exact_account_and_deck_seats"] += bool(exact)
            counts["teacher_seats"] += seat in game["teacher_seats"]
            counts["exact_teacher_seats"] += bool(exact) and seat in game["teacher_seats"]
            counts["archetype_only_seats"] += not exact and bool(archetype)
            players.append({"team": seat, "tag": player["tag"], "source_base_deck": base_deck,
                            "source_original_forms_remain_unknown": True,
                            "exact_account_and_deck_candidates": [dict(row, forms_in_source_deck_order=[row["card_forms"][card] for card in base_deck]) for row in exact],
                            "other_account_same_archetype_candidates": list(unique_alternatives.values()),
                            "archetype_candidates_are_weaker_and_not_auto_applied": True})
        counts["games"] += 1
        counts["games_with_at_least_one_exact_side"] += exact_sides >= 1
        counts["games_with_both_sides_exact"] += exact_sides == 2
        capture_delta = None
        if game.get("captured_at"):
            try:
                captured = datetime.fromisoformat(game["captured_at"].replace("Z", "+00:00")).date()
                snapshot = datetime.fromisoformat(links["season_end"]).date()
                capture_delta = (captured - snapshot).days
            except ValueError:
                pass
        games.append({"game_id": game["game_id"], "source_game_id": game["source_id"],
                      "source_sha256": game["source_sha256"], "split": game["split"],
                      "captured_at": game.get("captured_at"), "capture_minus_snapshot_days": capture_delta,
                      "capture_time_is_not_proven_battle_time": True, "players": players})
    report = {
        "schema": "historical-deck-hypotheses-v1", "source_index_sha256": sha256_file(source_index),
        "account_evidence_sha256": sha256_file(account_evidence) if account_evidence else sha256_file(deck_links),
        "deck_link_facts_sha256": sha256_file(deck_links),
        "source_blog_url": links["source_url"], "snapshot_date": links["season_end"],
        "counts": dict(counts), "historical_decks": table, "games": games,
        "original_replay_forms_verified": False, "original_ability_identity_verified": False,
        "source_data_modified": False,
        "interpretation": "Historical deck links suggest a separately versioned replay hypothesis only. Exact account and eight-card match does not prove original match forms, evolution charge, levels, initial hand, or ability identity.",
    }
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("source_index", "destination"):
        parser.add_argument("--" + key.replace("_", "-"), required=True)
    parser.add_argument("--deck-links", default=str(Path(__file__).parent / "manifests/leaderboard-decks-202607.json"))
    parser.add_argument("--account-evidence", default=None,
                        help="Optional independent account table; default uses account facts embedded in the manifest")
    args = vars(parser.parse_args())
    report = historical_deck_hypotheses(**args)
    print(json.dumps({"counts": report["counts"], "destination": args["destination"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

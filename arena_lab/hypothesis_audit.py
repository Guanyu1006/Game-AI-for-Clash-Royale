"""Compare explicit historical-deck hypotheses; never certify official states.

This audit does not export imitation labels or alter the normalized source. The
default five-game train-only comparison is a diagnostic, not model selection.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import time
from typing import Any

from .data_sources import base_card, sha256_file
from .replay_data import card_mapping, infer_cycle_openings, resolve_card


VARIANTS = ("base_unknown_abilities", "historical_unknown_abilities", "historical_unique_ability")


def unique_ability_hypothesis(env, seat: int, forms_by_id: dict[int, int], catalogue: list[dict]) -> dict:
    """Use only own public button, own living entity and declared deck evidence.

    Uniqueness is necessary but does not prove the unidentified source button.
    Multiple living copies of one qualifying card still correspond to one button.
    """
    from .environment import STATUS_HERO
    own = env.state.players[seat]
    legal = env.observe(seat)["legal_cards"]
    cards = {c["card_id"]: c for c in catalogue}
    candidates, rejected = [], []
    for button, row in enumerate(own.abilities[:3]):
        cid = int(row[3]) if len(row) > 3 else -1
        record = {"button": button, "action_card": button + 5, "native_card_id": cid,
                  "native_card_name": cards.get(cid, {}).get("name"),
                  "available": bool(row[0]), "spent": bool(row[1]),
                  "mask_legal": bool(legal[button + 5]), "historical_form": forms_by_id.get(cid),
                  "native_champion": bool(cards.get(cid, {}).get("champion", False))}
        # All own units are visible to this seat. Enemy state is never consulted.
        live = [e for e in env.state.entities if e.team == seat and e.card_id == cid and e.hp > 0]
        hero_live = [e for e in live if e.status_flags & STATUS_HERO]
        record["living_own_entity_uids"] = [int(e.uid) for e in live]
        record["living_own_hero_entity_uids"] = [int(e.uid) for e in hero_live]
        declared = cid in forms_by_id and (record["native_champion"] or forms_by_id[cid] == 2)
        matching_entity = bool(live) if record["native_champion"] else bool(hero_live)
        if declared and matching_entity and record["available"] and not record["spent"] and record["mask_legal"]:
            candidates.append(record)
        else:
            rejected.append(record)
    return {"status": "inferred-unique-public-button" if len(candidates) == 1 else
                      "ambiguous-public-buttons" if candidates else "no-eligible-public-button",
            "selected": candidates[0] if len(candidates) == 1 else None,
            "candidates": candidates, "rejected_buttons": rejected,
            "original_source_ability_identity_verified": False}


def _prepared_game(game: dict, hypothesis: dict, mapping: dict) -> dict:
    if game["game_id"] != hypothesis["game_id"] or game["source_sha256"] != hypothesis["source_sha256"]:
        raise ValueError("Source game and enrichment fingerprint differ")
    flags = {str(e["data_i"]) for e in game["events"] if e.get("data_i") is not None}
    if len(flags) != 1 or not flags <= {"0", "1"}:
        raise ValueError("Unknown source orientation")
    side_xor = 1 - int(next(iter(flags)))
    decks, forms, audits, candidates = [None, None], [None, None], [], []
    for team, player in enumerate(game["players"]):
        evidence = hypothesis["players"][team]
        if evidence["tag"].lstrip("#") != player["tag"].lstrip("#"):
            raise ValueError("Historical account identity mismatch")
        exact = evidence["exact_account_and_deck_candidates"]
        unique = {json.dumps(r["card_forms"], sort_keys=True) for r in exact}
        if len(unique) != 1:
            raise ValueError("Each account must have one unambiguous historical eight-card form hypothesis")
        candidate = exact[0]
        if candidate["tag"].lstrip("#") != player["tag"].lstrip("#"):
            raise ValueError("Candidate does not belong to the source account")
        if any(form not in (0, 1, 2) for form in candidate["card_forms"].values()):
            raise ValueError("Historical card form lies outside the supported encoding")
        if set(candidate["card_forms"]) != {base_card(c)[0] for c in player["deck"]}:
            raise ValueError("Historical eight-card set differs from source deck")
        native_forms = {resolve_card(slug, mapping): form for slug, form in candidate["card_forms"].items()}
        cards = [resolve_card(slug, mapping) for slug in player["deck"]]
        plays = [resolve_card(e["card"], mapping) for e in game["events"] if e["team"] == team and not e["ability"]]
        audit = infer_cycle_openings(cards, plays)
        seat = team ^ side_xor
        decks[seat] = audit["selected_hypothesis"]
        forms[seat] = native_forms
        audits.append(audit)
        candidates.append(candidate)
    return {"decks": decks, "forms_by_id": forms, "opening_constraints": audits,
            "historical_candidates": candidates, "side_xor": side_xor}


def replay_hypothesis(game: dict, prepared: dict, *, variant: str, seed: int = 0,
                      decision_ticks: int = 10) -> dict[str, Any]:
    from .environment import ArenaConfig, ArenaEnv
    if variant not in VARIANTS:
        raise ValueError("Unknown audit variant")
    started = time.perf_counter()
    env = ArenaEnv(ArenaConfig(decision_ticks=decision_ticks, max_entities=128))
    try:
        catalogue = env.catalogue()
        mapping = card_mapping(catalogue)
        historical = variant != "base_unknown_abilities"
        infer_abilities = variant == "historical_unique_ability"
        forms_by_id = prepared["forms_by_id"]
        forms = [[forms_by_id[s][c] if historical else 0 for c in prepared["decks"][s]] for s in (0, 1)]
        env.reset(seed=seed, decks=prepared["decks"], forms=forms, shuffle=0,
                  levels=[[16] * 8] * 2, tower_levels=[16, 16])
        identity = env.identity()
        effective_elixir = [p.elixir_milli for p in env.state.players]
        counts: Counter = Counter(source_events=len(game["events"]), source_ability_events=sum(e["ability"] for e in game["events"]))
        diagnostics, cursor, first_divergence, grid_pending = [], 0, None, []
        events, end_tick, side_xor = game["events"], game["end_tick"], prepared["side_xor"]
        while env.state.tick <= end_tick and not env.terminated and not env.truncated:
            tick = env.state.tick
            if tick % decision_ticks == 0:
                grid_pending = []
                future = []
                j = cursor
                while j < len(events) and events[j]["tick"] < tick + decision_ticks:
                    future.append(events[j]); j += 1
                for team in game["teacher_seats"]:
                    seat = team ^ side_xor
                    own_events = [e for e in future if e["team"] == team]
                    kind = "ability" if own_events and own_events[0]["ability"] else "play" if own_events else "wait"
                    reason = None
                    obs = env.observe(seat)
                    if first_divergence is not None:
                        reason = "after-first-command-divergence"
                    elif len(own_events) > 1:
                        reason = "multiple-source-actions-in-window"
                    elif own_events:
                        event = own_events[0]
                        if event["ability"]:
                            evidence = unique_ability_hypothesis(env, seat, forms_by_id[seat], catalogue) if infer_abilities else None
                            if evidence is None or evidence["selected"] is None:
                                reason = "unknown-or-ambiguous-source-ability"
                        else:
                            cid = resolve_card(event["card"], mapping)
                            hand = list(env.state.players[seat].hand)
                            if cid not in hand:
                                reason = "card-absent-from-hypothesis-hand"
                            else:
                                slot = hand.index(cid)
                                x, y = int(event["x"]), int(event["y"])
                                if seat == 1:
                                    x, y = 18000 - x, 32000 - y
                                position = min(31, max(0, y // 1000)) * 18 + min(17, max(0, x // 1000))
                                if not obs["legal_cards"][slot + 1] or not obs["legal_positions"][slot, position]:
                                    reason = "public-mask-rejects-grid-label"
                    counts["sampled_frames"] += 1
                    if reason:
                        counts["invalid_labels"] += 1
                        counts["label_reason:" + reason] += 1
                    else:
                        counts["valid_" + kind + "_labels"] += 1
                        grid_pending.append(kind)
            pending, commands = [], []
            while cursor < len(events) and events[cursor]["tick"] == tick:
                event = events[cursor]; cursor += 1
                seat = event["team"] ^ side_xor
                row = {"source_index": event["source_index"], "source_tick": tick, "source_team": event["team"],
                       "native_seat": seat, "source_card": event["card"], "source_form": event.get("form"),
                       "ability": bool(event["ability"]), "state_hash_before": str(env.engine.state_hash())}
                command = None
                if event["ability"]:
                    if infer_abilities:
                        row["ability_hypothesis"] = unique_ability_hypothesis(env, seat, forms_by_id[seat], catalogue)
                        selected = row["ability_hypothesis"]["selected"]
                        if selected:
                            command = {"team": seat, "hand_slot": selected["button"] + 4, "x": 0, "y": 0}
                            counts["ability_unique_hypotheses"] += 1
                        else:
                            row["status"] = row["ability_hypothesis"]["status"]
                    else:
                        row["status"] = "source-ability-identity-unknown-not-submitted"
                else:
                    cid = resolve_card(event["card"], mapping)
                    assumed_form = forms_by_id[seat][cid] if historical else 0
                    if event.get("form") is not None and event["form"] != assumed_form:
                        row["status"] = "explicit-source-form-conflicts-with-hypothesis"
                    elif cid not in env.state.players[seat].hand:
                        row["status"] = "source-card-absent-from-hypothesis-hand"
                    else:
                        command = {"team": seat, "card_id": cid, "x": int(event["x"]) * 18, "y": int(event["y"]) * 18}
                if command is None:
                    diagnostics.append(row)
                    counts["not_submitted:" + row["status"]] += 1
                    if first_divergence is None:
                        first_divergence = dict(row)
                else:
                    pending.append(row); commands.append(command)
            next_event = events[cursor]["tick"] if cursor < len(events) else end_tick + 1
            next_grid = (tick // decision_ticks + 1) * decision_ticks
            delta = max(1, min(next_event, next_grid, end_tick + 1) - tick)
            *_, info = env.advance_commands(commands, ticks=delta)
            if len(pending) != len(info["deploy_results"]):
                raise RuntimeError("Native feedback lost submitted command correspondence")
            for row, feedback in zip(pending, info["deploy_results"]):
                row["status"] = feedback["reason"]
                row["native_feedback"] = feedback
                prefix = "ability" if row["ability"] else "play"
                counts[prefix + ("_accepted" if row["status"] == "OK" else "_refused")] += 1
                if row["status"] != "OK" and first_divergence is None:
                    first_divergence = dict(row)
                diagnostics.append(row)
            if first_divergence and first_divergence["source_tick"] // decision_ticks == tick // decision_ticks:
                for kind in grid_pending:
                    counts["valid_" + kind + "_labels"] -= 1
                    counts["invalid_labels"] += 1
                    counts["label_reason:window-containing-first-command-divergence"] += 1
                grid_pending = []
        counts["events_not_reached_before_native_terminal"] = len(events) - cursor
        native_winner = env.state.winner
        return {"variant": variant, "counts": dict(counts), "first_command_divergence": first_divergence,
                "diagnostics": diagnostics, "environment_identity": identity,
                "reset": {"seed": seed, "shuffle": 0, "decks": prepared["decks"], "forms": forms,
                          "levels": [[16] * 8] * 2, "tower_levels": [16, 16], "effective_elixir_milli": effective_elixir},
                "source_team_to_native_seat": {str(t): t ^ side_xor for t in (0, 1)},
                "native_terminal": {"tick": env.state.tick, "terminated": env.terminated, "truncated": env.truncated,
                                    "winner_native_seat": native_winner,
                                    "winner_source_team": native_winner ^ side_xor if native_winner in (0, 1) else native_winner,
                                    "crowns_native_seat": [p.crowns for p in env.state.players]},
                "source_terminal": {"tick": end_tick, "winner": game["winner"]},
                "wall_seconds": time.perf_counter() - started,
                "strong_imitation_delivery_gate_passed": False,
                "interpretation": "Counterfactual simulator hypothesis. Historical forms, ordinary-cycle opening, levels, seed and inferred ability are unverified for the original match. Native acceptance and terminal agreement cannot certify original state."}
    finally:
        env.close()


def audit_hypotheses(source_index: str | Path, hypotheses: str | Path, output: str | Path,
                     *, limit: int = 5, split: str = "train") -> dict:
    from .environment import ArenaEnv
    if limit < 1 or split not in ("train", "val", "test"):
        raise ValueError("Choose positive limit and one explicit whole-game split")
    source_index, hypotheses, output = map(Path, (source_index, hypotheses, output))
    if output.exists():
        raise FileExistsError("Preserve previous hypothesis audits")
    source = json.loads(source_index.read_text())
    enriched = json.loads(hypotheses.read_text())
    if enriched["source_index_sha256"] != sha256_file(source_index):
        raise ValueError("Enrichment was made from a different source index")
    by_id = {r["game_id"]: r for r in source["games"]}
    eligible = sorted([g for g in enriched["games"] if g["split"] == split and
                       all(p["exact_account_and_deck_candidates"] for p in g["players"])], key=lambda g: g["game_id"])
    output.mkdir(parents=True)
    env = ArenaEnv()
    try:
        mapping = card_mapping(env.catalogue())
    finally:
        env.close()
    games, failures = [], []
    aggregate = {v: Counter() for v in VARIANTS}
    for h in eligible[:limit]:
        row = by_id[h["game_id"]]
        game = json.loads(Path(row["path"]).read_text())
        if game["split"] != split:
            raise ValueError("Source split changed")
        try:
            prepared = _prepared_game(game, h, mapping)
            variants = [replay_hypothesis(game, prepared, variant=v) for v in VARIANTS]
            report = {"game_id": game["game_id"], "source_id": game["source_id"], "source_sha256": game["source_sha256"],
                      "normalized_source_sha256": sha256_file(Path(row["path"])), "split": split,
                      "opening_constraints": prepared["opening_constraints"],
                      "historical_candidates": prepared["historical_candidates"], "variants": variants,
                      "original_replay_fields_unchanged": True, "original_forms_verified": False}
            filename = game["game_id"] + ".json"
            (output / filename).write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n")
            for v in variants:
                aggregate[v["variant"]].update(v["counts"])
            short = {"game_id": game["game_id"], "source_id": game["source_id"], "path": filename,
                     "sha256": sha256_file(output / filename), "counts": {v["variant"]: v["counts"] for v in variants},
                     "first_divergence_ticks": {v["variant"]: (v["first_command_divergence"] or {}).get("source_tick") for v in variants}}
            games.append(short)
            print(json.dumps(short, ensure_ascii=False), flush=True)
        except (ValueError, RuntimeError, KeyError) as exc:
            failures.append({"game_id": game["game_id"], "reason": str(exc)})
    result = {"schema": "historical-forms-ability-audit-v1", "source_index_sha256": sha256_file(source_index),
              "historical_hypotheses_sha256": sha256_file(hypotheses), "split": split, "eligible_games": len(eligible),
              "requested_limit": limit, "selection": "first sorted game_id; no score-based selection",
              "variants": list(VARIANTS), "games": games, "failures": failures,
              "aggregate_counts": {v: dict(c) for v, c in aggregate.items()},
              "strong_imitation_delivery_gate_passed": False, "training_labels_exported": False,
              "source_data_modified": False, "official_state_equivalence_verified": False}
    (output / "summary.json").write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("source-index", "hypotheses", "output"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--limit", type=int, default=5)
    parser.add_argument("--split", choices=("train", "val", "test"), default="train")
    args = vars(parser.parse_args())
    result = audit_hypotheses(**args)
    print(json.dumps({"games": len(result["games"]), "failures": result["failures"],
                      "aggregate_counts": result["aggregate_counts"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

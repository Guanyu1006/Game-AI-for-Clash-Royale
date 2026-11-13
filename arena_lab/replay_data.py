"""Audit public commands, constrain unknown openings, and export simulator hypotheses.

An exported observation is an explicitly counterfactual simulator state: it is
never labelled as an observation recorded in the original official match.
"""
from __future__ import annotations

from collections import Counter
import itertools
import json
from pathlib import Path
import re
from typing import Any, Mapping, Sequence

import numpy as np

from .data_sources import base_card, sha256_file, safe_source_id

# Explicit localization aliases for the pinned simulator's internal card names.
# Automatic normalized spelling is used only when it identifies exactly one card.
CARD_ALIASES = {
    "archers": "Archer", "guards": "SkeletonWarriors", "ice-spirit": "IceSpirits",
    "fire-spirit": "FireSpirits", "sparky": "ZapMachine", "lumberjack": "RageBarbarian",
    "ice-golem": "IceGolemite", "dart-goblin": "BlowdartGoblin",
    "elite-barbarians": "AngryBarbarians", "executioner": "AxeMan", "bandit": "Assassin",
    "night-witch": "DarkWitch", "royal-ghost": "Ghost", "zappies": "MiniSparkys",
    "cannon-cart": "MovingCannon", "skeleton-barrel": "SkeletonBalloon",
    "flying-machine": "DartBarrell", "magic-archer": "EliteArcher",
    "mother-witch": "WitchMother", "furnace": "FirespiritHut", "the-log": "Log",
    "barbarian-barrel": "BarbLog", "giant-snowball": "Snowball",
    # The internal SPELL wrapper actually summons HealSpirit (native probe saved).
    "heal-spirit": "Heal", "spirit-empress": "MergeMaiden", "void": "DarkMagic",
    "rune-giant": "GiantBuffer",
}


def card_mapping(catalogue: Sequence[dict]) -> dict[str, int]:
    clean = lambda name: re.sub("[^a-z0-9]", "", name.lower())
    by_name = {c["name"]: c["card_id"] for c in catalogue}
    normalized: dict[str, list[int]] = {}
    for c in catalogue:
        normalized.setdefault(clean(c["name"]), []).append(c["card_id"])
    result = {key: values[0] for key, values in normalized.items() if len(values) == 1}
    result.update({slug: by_name[name] for slug, name in CARD_ALIASES.items() if name in by_name})
    return result


def resolve_card(slug: str, mapping: Mapping[str, int]) -> int:
    base, _ = base_card(slug)
    key = base if base in mapping else re.sub("[^a-z0-9]", "", base.lower())
    if key not in mapping:
        raise ValueError(f"Unsupported source card: {slug}")
    return mapping[key]


def infer_cycle_openings(deck: Sequence[int], plays: Sequence[int]) -> dict:
    """Enumerate all 1,680 initial-set/queue classes under an ordinary 8-card cycle.

    Initial four slot permutations are equivalent for availability and fixed to
    sorted order. This is an offline constraint analysis, not proof of the source
    initial hand, RNG, or champion-alive three-card-cycle dynamics.
    """
    if len(deck) != 8 or len(set(deck)) != 8:
        raise ValueError("Eight distinct cards required")
    if any(card not in deck for card in plays):
        raise ValueError("A source play is absent from its declared deck")
    candidates = []
    best_prefix, best = -1, None
    for initial in itertools.combinations(sorted(deck), 4):
        remaining = sorted(set(deck) - set(initial))
        for queue in itertools.permutations(remaining):
            hand, cycle = list(initial), list(queue)
            matched = 0
            for card in plays:
                if card not in hand:
                    break
                slot = hand.index(card)
                hand[slot] = cycle.pop(0)
                cycle.append(card)
                matched += 1
            opening = list(initial) + list(queue)
            if matched > best_prefix:
                best_prefix, best = matched, opening
            if matched == len(plays):
                candidates.append(opening)
    return {"model": "ordinary-eight-card-cycle; no live-champion cycle model",
            "hypotheses_tested": 1680, "consistent_opening_classes": len(candidates),
            "initial_slot_permutations_per_class": 24,
            "unique_original_hand": False, "original_seed_known": False,
            "selected_hypothesis": candidates[0] if candidates else best,
            "selection_rule": "lexicographically first fully consistent class, else first maximum-prefix diagnostic",
            "source_plays": len(plays), "best_consistent_prefix": best_prefix,
            "all_commands_availability_consistent": bool(candidates),
            "used_future_source_actions_for_offline_constraints": True}


def validate_arrays(arrays: Mapping[str, np.ndarray], spec: Mapping[str, Any]) -> None:
    required = {"global_features", "entities", "entity_mask", "hand_ids", "legal_cards",
                "legal_positions", "target_card", "target_position", "label_valid", "position_valid", "ticks"}
    if set(arrays) != required:
        raise ValueError("NPZ fields differ from the versioned observation/label contract")
    n = len(arrays["ticks"])
    if not n or any(value.dtype.hasobject or len(value) != n for value in arrays.values()):
        raise ValueError("Empty, object-valued or mismatched sequence arrays")
    g, e = spec["global_dim"], spec["entity_dim"]
    expected = {"global_features": (n, g), "entities": (n, 128, e), "entity_mask": (n, 128),
                "hand_ids": (n, 4), "legal_cards": (n, 8), "legal_positions": (n, 4, 576)}
    if any(arrays[key].shape != shape for key, shape in expected.items()):
        raise ValueError("Observation shape mismatch")
    for key in ("global_features", "entities"):
        if not np.isfinite(arrays[key]).all():
            raise ValueError("Nonfinite observation")
    card = arrays["target_card"].astype(np.int64)
    valid = arrays["label_valid"].astype(bool)
    pvalid = arrays["position_valid"].astype(bool)
    position = arrays["target_position"].astype(np.int64)
    if (card < 0).any() or (card >= 8).any() or (np.diff(arrays["ticks"]) <= 0).any():
        raise ValueError("Invalid targets or nonmonotone ticks")
    if not arrays["legal_cards"][np.flatnonzero(valid), card[valid]].all():
        raise ValueError("Valid card label violates legal mask")
    if (pvalid & (~valid | (card < 1) | (card > 4))).any():
        raise ValueError("Only valid hand-card labels have a position target")
    ix = np.flatnonzero(pvalid)
    if ((position[ix] < 0) | (position[ix] >= 576)).any():
        raise ValueError("Invalid position target")
    if not arrays["legal_positions"][ix, card[ix] - 1, position[ix]].all():
        raise ValueError("Valid position label violates legal mask")


def write_game_npz(destination: Path, arrays: Mapping[str, np.ndarray], metadata: dict,
                   spec: dict) -> dict:
    validate_arrays(arrays, spec)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        raise FileExistsError(f"Refuse to replace an existing derived game: {destination}")
    np.savez_compressed(destination, **arrays)
    meta_path = destination.with_suffix(".json")
    meta_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2) + "\n")
    return {"game_id": metadata["game_id"], "source_game_id": metadata["source_game_id"],
            "source_sha256": metadata["source_sha256"], "source_id": metadata["source_game_id"],
            "split": metadata["split"], "path": destination.name, "sha256": sha256_file(destination),
            "metadata_path": meta_path.name, "steps": len(arrays["ticks"]),
            "teacher_seat": metadata["teacher_seat"], "quality": metadata["quality"]}


def _stack(frames: list[dict], labels: list[dict]) -> dict[str, np.ndarray]:
    arrays = {key: np.stack([frame[key] for frame in frames]) for key in frames[0]}
    for key in ("global_features", "entities"):
        arrays[key] = arrays[key].astype(np.float16)
    for key in ("hand_ids",):
        arrays[key] = arrays[key].astype(np.int16)
    for key in ("target_card", "target_position", "ticks"):
        arrays[key] = np.asarray([row[key] for row in labels], dtype=np.int32)
    for key in ("label_valid", "position_valid"):
        arrays[key] = np.asarray([row[key] for row in labels], dtype=np.bool_)
    return arrays


def reconstruct_game(game: dict, destination: Path, *, decision_ticks: int = 10,
                     seed: int = 0, initial_elixir_milli: int | None = None,
                     batch_ticks: bool = True) -> tuple[list[dict], dict, dict]:
    """Replay both command streams with one globally inferred opening per seat.

    Labels are sampled on a fixed decision grid before any action in that window.
    No timing search, elixir top-up, hand insertion or refusal retry is performed.
    After the first unmapped/refused/unknown-ability command, remaining state
    labels are invalidated, while the entire source denominator is retained.
    """
    from .environment import ArenaConfig, ArenaEnv

    safe_source_id(game["source_id"])
    env = ArenaEnv(ArenaConfig(decision_ticks=decision_ticks, max_entities=128))
    identity = env.identity()
    mapping = card_mapping(env.catalogue())
    spec = {k: v for k, v in env.obs_spec.items() if k not in ("num_card_types", "max_entities")}
    spec["card_vocab_size"] = env.obs_spec["num_card_types"]
    spec["observation_version"] = identity["observation_version"]
    flags = {str(e["data_i"]) for e in game["events"] if e["data_i"] is not None}
    if len(flags) != 1 or not flags <= {"0", "1"}:
        raise ValueError("Missing or inconsistent source orientation data-i")
    side_xor = 1 - int(next(iter(flags)))
    decks, opening_audits = [None, None], [None, None]
    for team in (0, 1):
        cards = [resolve_card(slug, mapping) for slug in game["players"][team]["deck"]]
        plays = [resolve_card(e["card"], mapping) for e in game["events"]
                 if e["team"] == team and not e["ability"]]
        audit = infer_cycle_openings(cards, plays)
        seat = team ^ side_xor
        decks[seat], opening_audits[team] = audit["selected_hypothesis"], audit
    initial_elixir = None if initial_elixir_milli is None else [initial_elixir_milli] * 2
    env.reset(seed=seed, decks=decks, shuffle=0, forms=[[0]*8]*2,
              levels=[[16]*8]*2, tower_levels=[16, 16], elixir_milli=initial_elixir)
    actual_opening_elixir = [p.elixir_milli for p in env.state.players]
    teacher_teams = game["teacher_seats"]
    frames = {team: [] for team in teacher_teams}
    labels = {team: [] for team in teacher_teams}
    counts: Counter = Counter(source_events=len(game["events"]),
                              source_play_events=sum(not e["ability"] for e in game["events"]),
                              source_ability_events=sum(e["ability"] for e in game["events"]))
    diagnostics, first_divergence, cursor = [], None, 0
    events = game["events"]
    max_tick = game["end_tick"]
    quantization, spatial_errors = [], []
    while env.state.tick <= max_tick and not env.terminated and not env.truncated:
        tick = env.state.tick
        if tick % decision_ticks == 0:
            future = []
            j = cursor
            while j < len(events) and events[j]["tick"] < tick + decision_ticks:
                future.append(events[j]); j += 1
            for team in teacher_teams:
                seat = team ^ side_xor
                obs = env.observe(seat)
                own = [e for e in future if e["team"] == team]
                target, position, reason = 0, 0, None
                valid = first_divergence is None
                pvalid = False
                if len(own) > 1:
                    counts["same_side_multi_action_windows"] += 1
                    valid, reason = False, "same-side-multiple-source-actions-in-window"
                elif own:
                    event = own[0]
                    quantization.append(event["tick"] - tick)
                    if event["ability"]:
                        valid, reason = False, "source-ability-identity-unavailable"
                    else:
                        cid = resolve_card(event["card"], mapping)
                        hand = list(env.state.players[seat].hand)
                        if cid not in hand:
                            valid, reason = False, "source-card-not-in-hypothesis-hand-at-grid-observation"
                        else:
                            target = hand.index(cid) + 1
                            x, y = int(event["x"]), int(event["y"])
                            if seat == 1:
                                x, y = 18000 - x, 32000 - y
                            gx, gy = min(17, max(0, x // 1000)), min(31, max(0, y // 1000))
                            position = gy * 18 + gx
                            spatial_errors.append(((gx + .5) * 1000 - x, (gy + .5) * 1000 - y))
                            if not obs["legal_cards"][target]:
                                valid, reason = False, "card-mask-rejects-source-target-at-grid-observation"
                            elif not obs["legal_positions"][target - 1, position]:
                                valid, reason = False, "position-mask-rejects-quantized-source-target"
                            pvalid = valid
                if first_divergence is not None:
                    valid, pvalid, reason = False, False, "state-after-first-command-divergence"
                counts["sampled_frames"] += 1
                counts["sampled_play_targets" if own and not own[0]["ability"] else
                       "sampled_ability_targets" if own else "sampled_wait_targets"] += 1
                counts["valid_play_labels" if valid and target else "valid_wait_labels" if valid else "invalid_labels"] += 1
                if reason:
                    counts["label_reason:" + reason] += 1
                frames[team].append(obs)
                labels[team].append({"target_card": target, "target_position": position,
                                     "label_valid": valid, "position_valid": pvalid, "ticks": tick})
        at_tick = []
        while cursor < len(events) and events[cursor]["tick"] == tick:
            at_tick.append(events[cursor]); cursor += 1
        commands = []
        pending = []
        for event in at_tick:
            row = {"source_index": event["source_index"], "source_tick": tick,
                   "source_team": event["team"], "native_seat": event["team"] ^ side_xor,
                   "card": event["card"], "source_form": event.get("form"),
                   "state_hash_before": str(env.engine.state_hash())}
            if event.get("form") in (1, 2):
                row["status"] = "explicit-source-form-not-reproduced-by-base-hypothesis"
                counts["explicit_form_not_reproduced"] += 1
            elif event["ability"]:
                row["status"] = "unknown-source-ability-not-submitted"
                counts["unknown_ability_events"] += 1
            else:
                cid = resolve_card(event["card"], mapping)
                if cid not in env.state.players[row["native_seat"]].hand:
                    row["status"] = "source-card-absent-from-hypothesis-hand"
                    counts["play_not_in_hand"] += 1
                else:
                    commands.append({"team": row["native_seat"], "card_id": cid,
                                     "x": int(event["x"]) * 18, "y": int(event["y"]) * 18})
                    pending.append(row)
                    continue
            diagnostics.append(row)
            if first_divergence is None:
                first_divergence = dict(row)
        next_event = events[cursor]["tick"] if cursor < len(events) else max_tick + 1
        next_grid = (tick // decision_ticks + 1) * decision_ticks
        delta = max(1, min(next_event, next_grid, max_tick + 1) - tick) if batch_ticks else 1
        _, _, _, _, info = env.advance_commands(commands, ticks=delta)
        for row, result in zip(pending, info["deploy_results"]):
            row["status"] = result["reason"]
            row["native_feedback"] = result
            counts["play_accepted" if result["reason"] == "OK" else "play_native_refused"] += 1
            if result["reason"] != "OK" and first_divergence is None:
                first_divergence = dict(row)
            diagnostics.append(row)
        if first_divergence is not None and first_divergence["source_tick"] // decision_ticks == tick // decision_ticks:
            # This frame was observed before a failure later in its label window.
            # Invalidate it retroactively rather than retaining a target whose
            # corresponding source command could not be faithfully replayed.
            for team in teacher_teams:
                label = labels[team][-1]
                if label["label_valid"]:
                    counts["valid_play_labels" if label["target_card"] else "valid_wait_labels"] -= 1
                    counts["invalid_labels"] += 1
                    counts["label_reason:state-window-containing-first-command-divergence"] += 1
                    label["label_valid"] = label["position_valid"] = False
    counts["events_not_reached_before_native_terminal"] = len(events) - cursor
    native_terminal = {"tick": env.state.tick, "terminated": env.terminated,
                       "winner": env.state.winner, "crowns": [p.crowns for p in env.state.players]}
    report = {"game_id": game["game_id"], "source_game_id": game["source_id"],
              "source_sha256": game["source_sha256"], "split": game["split"],
              "counts": dict(counts), "opening_constraints": opening_audits,
              "first_command_divergence": first_divergence, "events": diagnostics,
              "native_terminal": native_terminal, "source_terminal": {"tick": max_tick, "winner": game["winner"]},
              "source_team_to_native_seat": {str(t): t ^ side_xor for t in (0, 1)},
              "card_alias_evidence": "arena_lab/manifests/card-alias-evidence-v1.json",
              "card_version_warnings": ([{"card": "void", "native_name": "DarkMagic", "native_cost": 5,
                  "official_cost_change_date": "2026-08-04", "source_capture": game.get("captured_at"),
                  "source_exact_battle_date_unknown": True,
                  "warning": "Official change from3 to5 occurred within the raw capture period; no per-game version equivalence asserted"}]
                  if any(base_card(e["card"])[0] == "void" for e in events) else []),
              "timing": {"decision_ticks": decision_ticks, "source_hz": 20, "native_tick_ms": env.state.tick_ms,
                         "offset_ticks": 0, "retry_or_expanded_tolerance": False,
                         "labels": "first own action in [grid_tick,grid_tick+decision_ticks), observed before its execution",
                         "label_advance_ticks_histogram": dict(Counter(quantization))},
              "positions": {"native_commands": "exact source millitile x/y multiplied by18; native seats follow data-i",
                            "labels": "own-frame floor cell; centre quantization not silently applied to replay",
                            "max_axis_grid_centre_error_millitiles": max((max(abs(x),abs(y)) for x,y in spatial_errors), default=0)},
              "quality": {"expert_identity": "historical-July2026-global-top100-exact-account-tag",
                          "observations": "counterfactual pinned simulator, not original game state",
                          "original_initial_hand_known": False, "original_rng_known": False,
                          "opening_hindsight_conditioned_on_full_source_actions": True,
                          "original_forms_known": False, "original_levels_known": False,
                          "original_game_build_known": False, "assumed_forms": "all base",
                          "assumed_levels": 16, "official_state_reconstruction_verified": False,
                          "assumed_opening_elixir_milli": actual_opening_elixir,
                          "opening_elixir_basis": "pinned simulator calibrated default, not verified original source" if initial_elixir is None else "explicit sensitivity hypothesis, not verified original source",
                          "label_valid_semantics": "legal under this hypothesis before first unmapped/refused source command; not official-state certification",
                          "strong_imitation_delivery_gate_passed": False}}
    rows = []
    for team in teacher_teams:
        if not frames[team]:
            continue
        metadata = {**report, "teacher_seat": team,
                    "teacher_evidence": [e for e in game["expert_evidence"] if e["team"] == team]}
        name = game["source_id"] + f"-teacher{team}.npz"
        rows.append(write_game_npz(destination / name, _stack(frames[team], labels[team]), metadata, spec))
    env.close()
    return rows, report, {"policy_spec": spec, "observation_version": identity["observation_version"],
                          "environment_identity": identity}


def build_dataset(source_index: str | Path, destination: str | Path, *, limit: int | None = None,
                  split: str | None = None, initial_elixir_milli: int | None = None,
                  batch_ticks: bool = True) -> dict:
    source_index, destination = Path(source_index), Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    source = json.loads(source_index.read_text())
    candidates = [r for r in source["games"] if split is None or r["split"] == split]
    if limit is not None:
        candidates = candidates[:limit]
    rows, reports, failures = [], [], []
    interface = None
    for row in candidates:
        try:
            game = json.loads(Path(row["path"]).read_text())
            exported, report, interface = reconstruct_game(game, destination,
                                                           initial_elixir_milli=initial_elixir_milli,
                                                           batch_ticks=batch_ticks)
            rows.extend(exported); reports.append(report)
            print(json.dumps({"source_id": game["source_id"], "counts": report["counts"]}), flush=True)
        except (ValueError, KeyError, RuntimeError) as exc:
            failures.append({"game_id": row["game_id"], "source_game_id": row["source_id"],
                             "reason": str(exc), "source_events": row["events"]})
            print(json.dumps(failures[-1]), flush=True)
    index = {"schema_version": 1, **(interface or {}), "games": rows,
             "source_index_path": str(source_index), "source_index_sha256": sha256_file(source_index),
             "quality": "hypothetical simulator reconstruction; strong imitation gate not passed"}
    index["provenance"] = {"matching_version": "fixed-grid-strict-first-divergence-v1",
                           "source": "Cochon123/clash-royale-replays", "source_revision": source.get("source_revision"),
                           "reconstruction_reset_options": {"decks": "per-game globally constrained opening, see sequence metadata",
                               "seed": 0, "shuffle": 0, "forms": "all base hypothesis; source forms remain unknown",
                               "levels": [[16]*8]*2, "tower_levels": [16, 16],
                               "elixir_milli": None if initial_elixir_milli is None else [initial_elixir_milli]*2,
                               "effective_opening_elixir_milli": [6000, 6000] if initial_elixir_milli is None else [initial_elixir_milli]*2,
                               "offset_ticks": 0, "decision_ticks": 10},
                           "environment_identity_config_note": "identity.config records constructor defaults; explicit per-game reset overrides above define actual reconstruction"}
    index["reconstruction_reset_options"] = dict(index["provenance"]["reconstruction_reset_options"])
    audit = {"source_denominator": source["counts"], "normalized_source_games": len(source["games"]),
             "requested_games": len(candidates), "exported_source_games": len(reports),
             "exported_teacher_sequences": len(rows), "rejected_games": failures,
             "aggregate_counts": dict(sum((Counter(r["counts"]) for r in reports), Counter())),
             "reports": reports, "strong_imitation_delivery_gate_passed": False}
    (destination / "index.json").write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n")
    (destination / "validation-audit.json").write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n")
    return audit


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-index", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--split", choices=("train", "validation", "test"))
    parser.add_argument("--initial-elixir-milli", type=int, help="Explicit sensitivity override; default uses pinned backend default")
    parser.add_argument("--step-every-tick", action="store_true", help="Diagnostic reference implementation; batching preserves all event/grid boundaries")
    args = parser.parse_args()
    audit = build_dataset(args.source_index, args.destination, limit=args.limit, split=args.split,
                          initial_elixir_milli=args.initial_elixir_milli,
                          batch_ticks=not args.step_every_tick)
    print(json.dumps({k: v for k, v in audit.items() if k != "reports"}, indent=2))


if __name__ == "__main__":
    main()

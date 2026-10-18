"""Lossless, provenance-aware parsing of public IL_Replay source actions.

This module does not import an engine, infer a hidden initial deal, apply a deploy
delay, create actor observations, or call an action engine-confirmed.
"""
from __future__ import annotations
import hashlib
import json
import math
from pathlib import Path
import re

CONVERTER_VERSION = "il-replay-source-v1"
EPISODE_SCHEMA = "royale-phase2-source-episode.v1"
JOURNAL_SCHEMA = "royale-phase2-source-action.v1"
SOURCE_HZ = 20
SIDES = {"team": 0, "opponent": 1}


def canonical_json(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def value_hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def converter_hash():
    return hashlib.sha256(Path(__file__).read_bytes()).hexdigest()


def _reject_nonfinite(value):
    raise ValueError(f"Nonfinite JSON number {value}")


def decode_payload(row):
    payload = json.loads(row["payload_json"], parse_constant=_reject_nonfinite)
    if not isinstance(payload, dict):
        raise ValueError("payload_json must decode to an object")
    return payload


def _coordinate(event, space, axis):
    return ((event.get("coordinates") or {}).get(space) or {}).get(axis)


def _fingerprint(payload, near=False, swapped=False):
    battle = payload.get("battle", {})
    decks = []
    for side in ("team", "opponent"):
        players = battle.get(side, {}).get("players", [])
        decks.append([{"deck": sorted((str(c.get("card_key")), c.get("level")) for c in p.get("deck", [])),
                       "tower": p.get("tower_card")} for p in players])
    if swapped:
        decks.reverse()
    events = []
    for event in payload.get("events", []):
        owner = SIDES.get(event.get("side"), -1)
        x, y = (_coordinate(event, "native_world_units", axis) for axis in ("x", "y"))
        if swapped:
            owner = 1 - owner if owner in (0, 1) else owner
            x = 18000 - x if x is not None else None
            y = 32000 - y if y is not None else None
        tick = event.get("replay_tick_20hz")
        if near:
            tick = tick // 2 if isinstance(tick, int) else tick
            x = round(x / 500) if isinstance(x, (int, float)) else x
            y = round(y / 500) if isinstance(y, (int, float)) else y
        events.append([owner, event.get("kind"), event.get("card_key"), tick, x, y,
                       event.get("form_at_play"), sorted(event.get("ability_source_candidates") or [])])
    return canonical_json({"decks": decks, "events_in_source_order": events})


def content_fingerprints(payload):
    """Seat-swapped exact/quantized signatures for keeping derived copies together.

    Near matching is deliberately documented and bounded: equal event signatures
    at 0.1s bins / half-tile positions. It is not a universal replay-identity test.
    """
    exact = min(_fingerprint(payload), _fingerprint(payload, swapped=True))
    near = min(_fingerprint(payload, near=True), _fingerprint(payload, near=True, swapped=True))
    return {"content_sha256": value_hash({"battle": payload.get("battle"), "events": payload.get("events")}),
            "seat_invariant_action_sha256": hashlib.sha256(exact.encode()).hexdigest(),
            "near_group_sha256": hashlib.sha256(near.encode()).hexdigest()}


def parse_payload(row, source=None):
    """Return source-only configuration and journal, retaining every event.

    Structural problems are listed rather than repaired; parse_valid then fails.
    Raw source times, coordinates and same-tick order are never silently changed.
    """
    source = dict(source or {})
    payload = decode_payload(row)
    tag = row.get("replay_tag")
    if not isinstance(tag, str) or not re.fullmatch(r"[0-9a-f]{32}", tag):
        raise ValueError("Invalid anonymized replay_tag")
    issues = []
    if payload.get("source", {}).get("replay_tag") != tag:
        issues.append("row_and_payload_replay_tag_mismatch")
    if row.get("schema_version") != "royaleapi-replay-parquet.v1":
        issues.append("unrecognized_parquet_schema")
    if payload.get("schema_version") != "royaleapi-battle-actions.v1":
        issues.append("unrecognized_payload_schema")
    battle = payload.get("battle")
    events = payload.get("events")
    if not isinstance(battle, dict) or not isinstance(events, list):
        raise ValueError("Missing battle object or events list")
    if row.get("event_count") != len(events):
        issues.append("event_count_mismatch")
    if battle.get("result") != row.get("result"):
        issues.append("row_and_payload_result_mismatch")
    players = []
    all_card_keys = set()
    for side, owner in SIDES.items():
        side_players = battle.get(side, {}).get("players", [])
        if len(side_players) != 1:
            issues.append(f"owner_{owner}_requires_explicit_non_1v1_support")
        for index, player in enumerate(side_players):
            deck = player.get("deck", [])
            if len(deck) != 8:
                issues.append(f"owner_{owner}_deck_size_{len(deck)}")
            all_card_keys.update(c.get("card_key") for c in deck if isinstance(c.get("card_key"), str))
            players.append({"owner": owner, "owner_semantics": "source-side enumeration team=0/opponent=1; not native owner", "native_owner": None,
                            "source_side": side, "source_player_index": index,
                            "deck": deck, "deck_order_semantics": "source display order; not initial hand or cycle",
                            "tower_card": player.get("tower_card"), "king_tower_level": None,
                            "initial_hand": None, "initial_cycle": None, "starting_elixir": None})
    journal = []
    previous_tick = -1
    same_tick_pairs = 0
    ambiguous_abilities = 0
    seen_source_indices = set()
    seen_event_hashes = set()
    for index, event in enumerate(events):
        if not isinstance(event, dict):
            raise ValueError(f"Event {index} is not an object")
        tick, seconds = event.get("replay_tick_20hz"), event.get("time_seconds")
        owner, kind = SIDES.get(event.get("side")), event.get("kind")
        event_issues = []
        source_index = event.get("source_index")
        event_hash = value_hash(event)
        if source_index in seen_source_indices:
            event_issues.append("duplicate_source_index")
        seen_source_indices.add(source_index)
        if event_hash in seen_event_hashes:
            event_issues.append("duplicate_exact_event")
        seen_event_hashes.add(event_hash)
        if not isinstance(tick, int) or isinstance(tick, bool) or tick < 0:
            event_issues.append("invalid_source_tick")
        else:
            if tick < previous_tick:
                event_issues.append("nonmonotonic_source_tick")
            same_tick_pairs += int(tick == previous_tick)
            previous_tick = tick
        if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds < 0:
            event_issues.append("invalid_source_seconds")
        elif isinstance(tick, int) and abs(seconds * SOURCE_HZ - tick) > 1e-6:
            event_issues.append("source_tick_seconds_disagree")
        if owner is None:
            event_issues.append("unknown_source_side")
        if kind not in ("play_card", "activate_ability"):
            event_issues.append("unsupported_source_kind")
        native = (event.get("coordinates") or {}).get("native_world_units")
        normalized = None
        if kind == "play_card":
            if not isinstance(event.get("card_key"), str):
                event_issues.append("missing_card_key")
            if not isinstance(native, dict) or not all(isinstance(native.get(k), (int, float)) and math.isfinite(native[k]) for k in ("x", "y")):
                event_issues.append("missing_or_nonfinite_coordinates")
            else:
                if not (0 <= native["x"] <= 18000 and 0 <= native["y"] <= 32000):
                    event_issues.append("source_coordinate_outside_18x32")
                normalized = {"x": native["x"] / 18000, "y": native["y"] / 32000}
        if kind == "activate_ability" and (event.get("ability_source_authoritative") is not True):
            ambiguous_abilities += 1
        issues.extend(f"event_{index}:{issue}" for issue in event_issues)
        journal.append({"schema_version": JOURNAL_SCHEMA, "source_episode_id": tag,
                        "source_event_index": index, "source_index": event.get("source_index"),
                        "request_id": f"{tag}:{index}", "owner": owner,
                        "owner_semantics": "source-side enumeration team=0/opponent=1; not native owner", "native_owner": None,
                        "source_side": event.get("side"),
                        "kind": kind, "card_key": event.get("card_key"), "card_play_number": event.get("card_play_number"),
                        "deck_card_key_candidates": event.get("deck_card_key_candidates", []),
                        "form_at_play": event.get("form_at_play"),
                        "ability_source_candidates": event.get("ability_source_candidates", []),
                        "ability_source_authoritative": event.get("ability_source_authoritative"),
                        "source_authoritative": False,
                        "evidence": "public_collected_timeline; not confirmed by this engine",
                        "source_tick_20hz": tick, "source_seconds": seconds,
                        "target_engine_tick": None, "actual_execution_tick": None,
                        "time_semantics": "source timeline; click/queue/deploy/entity-appearance distinction not established",
                        "source_coordinates": event.get("coordinates"), "source_native_world_units": native,
                        "normalized_source_native": normalized,
                        "coordinate_semantics": "dataset-provided 18x32 native frame; no extra seat flip or engine calibration applied",
                        "engine_card_id": None, "hand_slot": None, "execution_status": "not_run",
                        "source_event_sha256": event_hash, "raw_event": event, "issues": event_issues})
    terminal = {"result_from_team_view": battle.get("result"),
                "crowns": {side: battle.get(side, {}).get("crowns") for side in SIDES},
                "final_tower_hitpoints": {side: [p.get("final_tower_hitpoints") for p in battle.get(side, {}).get("players", [])] for side in SIDES},
                "source_duration": payload.get("replay", {}).get("duration"),
                "duration_semantics": "source duration fields retained; exact native terminal tick not established",
                "terminal_engine_tick": None}
    provenance = {**source, "dataset": "VanguardX101/IL_Replay", "anonymized_source_id": tag,
                  "original_source_id": None, "source_site": payload.get("source", {}).get("site"),
                  "payload_sha256": hashlib.sha256(row["payload_json"].encode("utf-8")).hexdigest(),
                  "converter_version": CONVERTER_VERSION, "converter_sha256": converter_hash(),
                  "source_authoritative": False}
    return {"schema_version": EPISODE_SCHEMA, "episode_id": tag, "artifact_kind": "source_reconstruction_inputs_not_training_trajectory",
            "provenance": provenance, "fingerprints": content_fingerprints(payload),
            "match_config": {"scope": "reconstruction_only; both decks are not actor features", "players": players,
                             "game_mode": battle.get("game_mode"), "battle_type": battle.get("battle_type"),
                             "arena": None, "random_seed": None, "initial_tick": None, "engine_card_id_mapping": None,
                             "game_build": "unknown", "balance_date": "unknown", "source_battle_date": None,
                             "required_card_keys": sorted(all_card_keys)},
            "reference_terminal": terminal, "action_journal": journal,
            "quality": {"parse_valid": "pass" if not issues else "fail", "runtime_replayable": "not_run",
                        "deterministic": "not_run", "outcome_consistent": "not_run", "trajectory_validated": "not_available"},
            "diagnostics": {"issues": issues, "source_event_count": len(events), "same_tick_adjacent_pairs": same_tick_pairs,
                            "ability_source_unconfirmed_count": ambiguous_abilities,
                            "runtime_not_run_reason": "No native replay execution evidenced by source conversion",
                            "missing_initialization": ["initial_hand", "initial_cycle", "random_seed", "starting_elixir", "king_tower_level", "arena", "native_build", "balance_date"],
                            "actor_observation_available": False, "training_decision_count": 0}}

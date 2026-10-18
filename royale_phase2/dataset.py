"""Source replay audit, leakage grouping and immutable formal/dev selection."""
from __future__ import annotations
from collections import Counter, defaultdict
import hashlib
import json
import time
from pathlib import Path

from .replays import canonical_json, converter_hash, decode_payload, parse_payload, value_hash

DATASET_REVISION = "059d43a02138a34b1b3009cc2acc7630fb99a638"
DATASET_REPOSITORY = "VanguardX101/IL_Replay"
ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "third_party/phase2/dataset_metadata/IL_Replay"
RAW = ROOT / "data/phase2/raw/IL_Replay" / DATASET_REVISION
OUT = ROOT / "outputs/phase2/data"
CONVERTED = ROOT / "data/phase2/converted/source-v1"


def file_hash(path):
    hasher = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def write_json(path, value):
    path = Path(path).resolve()
    path.relative_to(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    # On Windows a concurrent read-only progress reader can briefly prevent
    # replacement. Retry only publication of our complete temporary file;
    # never retry a native action or expose partially written JSON.
    for attempt in range(30):
        try:
            temporary.replace(path)
            break
        except PermissionError as error:
            if getattr(error, 'winerror', None) not in (5, 32, 33) or attempt == 29:
                raise
            time.sleep(min(0.01 * (attempt + 1), 0.2))


def load_source_shard(relative_path="replays/part-000051.parquet"):
    import pyarrow.parquet as pq
    manifest = json.loads((META / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["files"] if item["path"] == relative_path)
    path = RAW / relative_path
    if file_hash(path) != record["sha256"]:
        raise ValueError("Raw Parquet does not match pinned published SHA256")
    parquet = pq.ParquetFile(path)
    if parquet.metadata.num_rows != record["rows"]:
        raise ValueError("Raw Parquet row count differs from pinned manifest")
    source = {"dataset_revision": DATASET_REVISION, "raw_file": path.relative_to(ROOT).as_posix(),
              "raw_file_sha256": record["sha256"], "source_manifest_sha256": file_hash(META / "manifest.json"),
              "dataset_license": "not_declared_in_pinned_card_or_file_tree"}
    rows = [row for batch in parquet.iter_batches(batch_size=128) for row in batch.to_pylist()]
    return rows, source, str(parquet.schema_arrow)


def group_episodes(episodes):
    """Union same anonymous IDs, exact action copies and quantized near copies."""
    parents = list(range(len(episodes)))
    def find(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index
    seen = {}
    for i, episode in enumerate(episodes):
        keys = [("source_id", episode["episode_id"])]
        keys.extend((key, episode["fingerprints"][key]) for key in ("content_sha256", "seat_invariant_action_sha256", "near_group_sha256"))
        for key in keys:
            if key in seen:
                parents[find(i)] = find(seen[key])
            else:
                seen[key] = i
    groups = defaultdict(list)
    for i, episode in enumerate(episodes):
        groups[find(i)].append(episode)
    result = {}
    for members in groups.values():
        group_id = min(member["episode_id"] for member in members)
        for member in members:
            result[member["episode_id"]] = group_id
    return result, [sorted(member["episode_id"] for member in members) for members in groups.values() if len(members) > 1]


def audit_rows(rows, source, arrow_schema):
    episodes = []
    parse_exceptions = []
    counts = {key: Counter() for key in ("row_schema", "payload_schema", "collection_schema", "battle_type", "game_mode", "result", "deck_level", "tower_level", "deck_card_keys", "event_kind", "event_form_at_play", "ability_authoritative", "coordinate_inversion", "payload_top_level_keys", "event_keys", "player_keys", "parse_quality", "issues")}
    for row_index, row in enumerate(rows):
        try:
            payload = decode_payload(row)
            episode = parse_payload(row, {**source, "raw_row_index": row_index})
        except Exception as error:
            parse_exceptions.append({"raw_row_index": row_index, "episode_id": row.get("replay_tag"), "error": repr(error)})
            continue
        episodes.append(episode)
        counts["row_schema"][row.get("schema_version")] += 1
        counts["payload_schema"][payload.get("schema_version")] += 1
        counts["collection_schema"][payload.get("collection_schema_version")] += 1
        for key in ("battle_type", "game_mode", "result"):
            counts[key][payload["battle"].get(key)] += 1
        counts["payload_top_level_keys"].update(payload.keys())
        for player in episode["match_config"]["players"]:
            raw_player = payload["battle"][player["source_side"]]["players"][player["source_player_index"]]
            counts["player_keys"].update(raw_player.keys())
            for card in player["deck"]:
                counts["deck_level"][str(card.get("level"))] += 1
                counts["deck_card_keys"][card.get("card_key")] += 1
            counts["tower_level"][str((player.get("tower_card") or {}).get("level"))] += 1
        for event in payload["events"]:
            counts["event_keys"].update(event.keys())
            counts["event_kind"][event.get("kind")] += 1
            counts["event_form_at_play"][str(event.get("form_at_play"))] += 1
            if event.get("kind") == "activate_ability":
                counts["ability_authoritative"][str(event.get("ability_source_authoritative"))] += 1
            counts["coordinate_inversion"][str((event.get("coordinates") or {}).get("inversion_applied"))] += 1
        counts["parse_quality"][episode["quality"]["parse_valid"]] += 1
        counts["issues"].update(episode["diagnostics"]["issues"])
    source_ids = [row.get("replay_tag") for row in rows]
    duplicate_ids = {key: value for key, value in Counter(source_ids).items() if value > 1}
    groups, duplicate_groups = group_episodes(episodes)
    observed_cards = sorted(counts["deck_card_keys"])
    report = {"scope": "full downloaded shard, not whole 252238-match dataset", "dataset_revision": DATASET_REVISION,
              "source": source, "arrow_schema": arrow_schema, "raw_rows": len(rows), "unique_source_ids": len(set(source_ids)),
              "parse_exceptions": parse_exceptions, "duplicate_source_ids": duplicate_ids,
              "parsed_episodes": len(episodes), "counts": {key: dict(value) for key, value in counts.items()},
              "leakage_groups": len(set(groups.values())), "duplicate_or_near_duplicate_groups": duplicate_groups,
              "near_duplicate_rule": "same ordered actions after optional seat swap and 180-degree frame rotation; ticks floor-binned by 2 (0.1s), positions nearest 500 units (half tile); conservative grouping heuristic, not exhaustive identity",
              "observed_hero_keys": [key for key in observed_cards if "-hero" in key],
              "observed_evolution_keys": [key for key in observed_cards if "-ev" in key],
              "source_build": "unknown", "source_balance_date": "unknown",
              "date_limitation": "publisher removed battle dates/collection timestamps; card availability is not a build or balance-date fingerprint",
              "source_license": "not_declared; FirstLight source license does not license this dataset",
              "training_decision_samples_created": 0, "engine_replay_executed": False}
    return episodes, groups, report


def compare_action_table(episodes, relative_path="actions/part-000051.parquet"):
    import pyarrow.parquet as pq
    manifest = json.loads((META / "manifest.json").read_text(encoding="utf-8"))
    record = next(item for item in manifest["files"] if item["path"] == relative_path)
    path = RAW / relative_path
    if file_hash(path) != record["sha256"]:
        raise ValueError("Actions Parquet published SHA256 mismatch")
    expected = {episode["episode_id"]: episode["action_journal"] for episode in episodes}
    seen = set()
    errors = []
    mismatch_count = 0
    rows = 0
    parquet = pq.ParquetFile(path)
    for batch in parquet.iter_batches(batch_size=256):
        for row in batch.to_pylist():
            rows += 1
            key = row["replay_tag"], row["event_index"]
            problem = []
            if key in seen:
                problem.append("duplicate_action_primary_key")
            seen.add(key)
            journal = expected.get(row["replay_tag"], [])
            if not 0 <= row["event_index"] < len(journal):
                problem.append("orphan_action_or_out_of_range_index")
            else:
                action = journal[row["event_index"]]
                if value_hash(json.loads(row["event_json"])) != action["source_event_sha256"]:
                    problem.append("event_json_differs_from_replay_payload")
                flat = {"source_index": action["source_index"], "kind": action["kind"], "side": action["source_side"],
                        "card_key": action["card_key"], "card_play_number": action["card_play_number"],
                        "replay_tick_20hz": action["source_tick_20hz"], "time_seconds": action["source_seconds"],
                        "form_at_play": action["form_at_play"], "ability_source_authoritative": action["ability_source_authoritative"],
                        "deck_card_key_candidates": action["deck_card_key_candidates"], "ability_source_candidates": action["ability_source_candidates"]}
                coordinates = action["source_coordinates"] or {}
                for prefix, space, axes in (("native", "native_world_units", ("x", "y")), ("grid", "grid_cell_floor", ("x", "y")), ("arena", "arena_label_from_blue", ("column", "row"))):
                    for axis in axes:
                        flat[f"{prefix}_{axis}"] = (coordinates.get(space) or {}).get(axis)
                problem.extend("flat_column_mismatch:" + key for key, value in flat.items() if row.get(key) != value)
            if problem:
                mismatch_count += 1
                if len(errors) < 30:
                    errors.append({"key": key, "issues": problem})
    expected_keys = {(tag, i) for tag, actions in expected.items() for i in range(len(actions))}
    missing = expected_keys - seen
    report = {"raw_file": path.relative_to(ROOT).as_posix(), "raw_file_sha256": record["sha256"], "arrow_schema": str(parquet.schema_arrow),
              "rows": rows, "expected_payload_events": len(expected_keys), "unique_action_primary_keys": len(seen),
              "mismatched_rows": mismatch_count, "missing_actions": len(missing), "mismatch_examples": errors,
              "status": "pass" if rows == record["rows"] and not mismatch_count and not missing else "fail"}
    return report


def select_frozen(episodes, groups, formal_count=100, development_count=20):
    if len({episode["episode_id"] for episode in episodes}) != len(episodes):
        raise ValueError("Duplicate source replay IDs must be resolved before freezing")
    grouped = defaultdict(list)
    for episode in episodes:
        grouped[groups[episode["episode_id"]]].append(episode)
    ranked = sorted(grouped, key=lambda group: hashlib.sha256((DATASET_REVISION + "|formal-freeze-v1|" + group).encode()).hexdigest())
    if len(ranked) < formal_count + development_count:
        raise ValueError("Not enough distinct whole-replay leakage groups")
    selected = []
    for index, group in enumerate(ranked[:formal_count + development_count]):
        episode = min(grouped[group], key=lambda item: item["episode_id"])
        selected.append(("development" if index < development_count else "formal", group, episode))
    return selected


def _action_table(choices):
    import pyarrow as pa
    schema = pa.schema([
        ("schema_version", pa.string()), ("episode_id", pa.string()), ("split", pa.string()), ("leakage_group", pa.string()),
        ("source_event_index", pa.int32()), ("source_index", pa.int32()), ("owner", pa.int8()), ("kind", pa.string()),
        ("card_key", pa.string()), ("source_tick_20hz", pa.int32()), ("source_seconds", pa.float64()),
        ("target_engine_tick", pa.int32()), ("actual_execution_tick", pa.int32()),
        ("raw_x", pa.float64()), ("raw_y", pa.float64()), ("native_x", pa.float64()), ("native_y", pa.float64()),
        ("normalized_x", pa.float64()), ("normalized_y", pa.float64()), ("form_at_play", pa.string()),
        ("ability_source_authoritative", pa.bool_()), ("source_authoritative", pa.bool_()),
        ("source_event_sha256", pa.string()), ("payload_sha256", pa.string()), ("raw_file_sha256", pa.string()),
        ("raw_row_index", pa.int32()), ("dataset_revision", pa.string()), ("converter_sha256", pa.string()),
        ("execution_status", pa.string()), ("event_json", pa.large_string()),
        ("actor_observation_json", pa.large_string()), ("legal_mask_json", pa.large_string()), ("reward", pa.float64()),
        ("training_decision_valid", pa.bool_())], metadata={b"artifact_kind": b"source_actions_not_engine_training_decisions", b"schema_version": b"royale-phase2-source-actions-arrow.v1"})
    rows = []
    for split, group, episode in choices:
        p = episode["provenance"]
        for action in episode["action_journal"]:
            coordinate = action["source_coordinates"] or {}
            raw = coordinate.get("raw_royaleapi_units") or {}
            native = action["source_native_world_units"] or {}
            normalized = action["normalized_source_native"] or {}
            rows.append({"schema_version": action["schema_version"], "episode_id": episode["episode_id"], "split": split,
                         "leakage_group": group, "source_event_index": action["source_event_index"], "source_index": action["source_index"],
                         "owner": action["owner"], "kind": action["kind"], "card_key": action["card_key"],
                         "source_tick_20hz": action["source_tick_20hz"], "source_seconds": action["source_seconds"],
                         "target_engine_tick": None, "actual_execution_tick": None, "raw_x": raw.get("x"), "raw_y": raw.get("y"),
                         "native_x": native.get("x"), "native_y": native.get("y"), "normalized_x": normalized.get("x"), "normalized_y": normalized.get("y"),
                         "form_at_play": action["form_at_play"], "ability_source_authoritative": action["ability_source_authoritative"], "source_authoritative": False,
                         "source_event_sha256": action["source_event_sha256"], "payload_sha256": p["payload_sha256"],
                         "raw_file_sha256": p["raw_file_sha256"], "raw_row_index": p["raw_row_index"], "dataset_revision": DATASET_REVISION,
                         "converter_sha256": p["converter_sha256"], "execution_status": "not_run", "event_json": canonical_json(action["raw_event"]),
                         "actor_observation_json": None, "legal_mask_json": None, "reward": None, "training_decision_valid": False})
    return pa.Table.from_pylist(rows, schema=schema), rows


def export_frozen(choices, rows, source, grouping_report):
    import pyarrow as pa
    import pyarrow.parquet as pq
    CONVERTED.mkdir(parents=True, exist_ok=True)
    entries = []
    for split, group, episode in choices:
        path = CONVERTED / "episodes" / (episode["episode_id"] + ".json")
        entries.append({"source_episode_id": episode["episode_id"], "split": split, "leakage_group": group,
                        "raw_row_index": episode["provenance"]["raw_row_index"], "payload_sha256": episode["provenance"]["payload_sha256"],
                        "content_sha256": episode["fingerprints"]["content_sha256"], "episode": path.relative_to(ROOT).as_posix(),
                        "parse_valid": episode["quality"]["parse_valid"], "source_events": len(episode["action_journal"])})
    manifest = {"schema_version": "royale-phase2-frozen-source-manifest.v1", "dataset_revision": DATASET_REVISION, "source": source,
                "selection_rule": "smallest published replay shard; SHA256(revision|formal-freeze-v1|leakage_group) ascending; first 20 development then 100 formal; one lexicographic source ID per group; no runtime/outcome/parse-success filtering",
                "selection_scope_limitation": "one 852-row shard, not representative sampling of all 252238 games; balance date unknown",
                "near_duplicate_rule": grouping_report["near_duplicate_rule"], "converter_sha256": converter_hash(),
                "counts": dict(Counter(item["split"] for item in entries)), "entries": entries,
                "unselected_source_ids": sorted(set(row["replay_tag"] for row in rows) - {item["source_episode_id"] for item in entries}),
                "native_reconstruction_status": "not_run", "training_decision_samples": 0}
    lock_path = ROOT / "data/phase2/frozen_manifest.json"
    if lock_path.is_file() and canonical_json(json.loads(lock_path.read_text(encoding="utf-8"))) != canonical_json(manifest):
        raise ValueError("Frozen source selection or converter changed; preserve old manifest and declare a new dataset version")
    write_json(lock_path, manifest)
    episode_rows = []
    for (split, group, episode), entry in zip(choices, entries):
        episode = {**episode, "split": split, "leakage_group": group}
        write_json(ROOT / entry["episode"], episode)
        episode_rows.append({"episode_id": episode["episode_id"], "split": split, "leakage_group": group,
                             "episode_json": canonical_json(episode)})
    episode_table = pa.Table.from_pylist(episode_rows, schema=pa.schema([("episode_id", pa.string()), ("split", pa.string()), ("leakage_group", pa.string()), ("episode_json", pa.large_string())]))
    action_table, expected_action_rows = _action_table(choices)
    paths = {"episodes": CONVERTED / "episodes.parquet", "source_actions": CONVERTED / "source_actions.parquet"}
    for key, table in (("episodes", episode_table), ("source_actions", action_table)):
        temporary = paths[key].with_suffix(".parquet.partial")
        pq.write_table(table, temporary, compression="zstd", version="2.6")
        temporary.replace(paths[key])
    # Reopen both actual Parquet outputs; compare every row and null value.
    actual_episode_rows = pq.read_table(paths["episodes"]).to_pylist()
    actual_action_rows = pq.read_table(paths["source_actions"]).to_pylist()
    if actual_episode_rows != episode_rows or actual_action_rows != expected_action_rows:
        raise ValueError("Parquet write/readback differs")
    sample = choices[0][2]
    sample_payload = rows[sample["provenance"]["raw_row_index"]]["payload_json"]
    sample_raw = CONVERTED / "sample_raw_payload.json"
    sample_raw.write_text(sample_payload, encoding="utf-8")
    sample_jsonl = CONVERTED / "source_actions.sample.jsonl"
    sample_jsonl.write_text("".join(canonical_json(row) + "\n" for row in expected_action_rows[:20]), encoding="utf-8")
    artifacts = {key: {"path": path.relative_to(ROOT).as_posix(), "bytes": path.stat().st_size, "sha256": file_hash(path)} for key, path in paths.items()}
    result = {"status": "passed_source_conversion_and_readback_only", "frozen_manifest": lock_path.relative_to(ROOT).as_posix(),
              "frozen_manifest_sha256": file_hash(lock_path), "counts": manifest["counts"], "source_action_rows": len(expected_action_rows),
              "training_decision_samples": 0, "actor_observation_rows": 0, "engine_execution_confirmations": 0,
              "readback_episode_rows": len(actual_episode_rows), "readback_action_rows": len(actual_action_rows),
              "all_actor_observation_mask_reward_fields_null": all(row["actor_observation_json"] is None and row["legal_mask_json"] is None and row["reward"] is None for row in actual_action_rows),
              "split_source_id_intersection": 0, "split_leakage_group_intersection": 0,
              "sample_episode": entries[0]["episode"], "sample_raw_payload": sample_raw.relative_to(ROOT).as_posix(),
              "sample_jsonl": sample_jsonl.relative_to(ROOT).as_posix(), "artifacts": artifacts}
    dev_ids = {entry["source_episode_id"] for entry in entries if entry["split"] == "development"}
    formal_ids = {entry["source_episode_id"] for entry in entries if entry["split"] == "formal"}
    dev_groups = {entry["leakage_group"] for entry in entries if entry["split"] == "development"}
    formal_groups = {entry["leakage_group"] for entry in entries if entry["split"] == "formal"}
    if dev_ids & formal_ids or dev_groups & formal_groups:
        raise ValueError("Split leakage")
    write_json(OUT / "conversion_readback.json", result)
    return result


def source_level_fidelity(source_episode, hypothesis):
    """Audit known card/tower levels against FirstLight's uniform-level input.

    A pass means only that known source levels aren't changed by this rule; it
    doesn't fill unknown King Tower level, build, initial hand or RNG seed.
    """
    tags = hypothesis["upstream_replay"]["episode_config"]["tags"]
    candidate_level = tags["level_cap"]
    candidate_minimum = tags["minimum_card_level"]
    differences = []
    if candidate_minimum != candidate_level:
        raise ValueError("This audit applies to a uniform-level conversion only")
    for player in source_episode["match_config"]["players"]:
        for card in player["deck"]:
            level = card.get("level")
            if level is not None and level != candidate_level:
                differences.append({"source_side": player["source_side"], "kind": "deck_card", "card_key": card.get("card_key"),
                                    "source_level": level, "hypothesis_level": candidate_level})
        tower = player.get("tower_card") or {}
        if tower.get("level") is not None and tower["level"] != candidate_level:
            differences.append({"source_side": player["source_side"], "kind": "tower_troop", "card_key": tower.get("card_key"),
                                "source_level": tower["level"], "hypothesis_level": candidate_level})
    return {"known_source_level_fidelity": "fail" if differences else "pass", "differences": differences,
            "uniform_hypothesis_level": candidate_level, "strict_runtime_compatible": "unknown",
            "strict_runtime_ready": False, "unknown_source_fields": ["native_build", "balance_date", "king_tower_level", "initial_hand", "initial_cycle", "random_seed"],
            "guard": "reject known source-level changes before main replay; matching uniform levels alone does not validate the runtime"}


def require_lossless_source_levels(source_episode, hypothesis):
    report = source_level_fidelity(source_episode, hypothesis)
    if report["known_source_level_fidelity"] != "pass":
        raise ValueError("Lossy source-level normalization rejected: " + source_episode["episode_id"] + "; " + canonical_json(report["differences"]))
    return report

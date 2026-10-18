"""Meaningful source-conversion boundary and frozen-data readback checks."""
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import OUT, group_episodes, load_source_shard, require_lossless_source_levels, select_frozen, write_json
from royale_phase2.replays import canonical_json, content_fingerprints, converter_hash, parse_payload


def main():
    rows, source, _ = load_source_shard()
    original = rows[0]
    payload = json.loads(original["payload_json"])
    checks = []
    def check(name, condition):
        if not condition:
            raise AssertionError(name)
        checks.append(name)
    def row_with(value, tag=None):
        row = dict(original)
        if tag:
            value["source"]["replay_tag"] = tag
            row["replay_tag"] = tag
        row["payload_json"] = json.dumps(value, ensure_ascii=False)
        row["event_count"] = len(value["events"])
        row["result"] = value["battle"]["result"]
        return row
    episode = parse_payload(original, source)
    check("source_event_count_preserved", len(episode["action_journal"]) == len(payload["events"]))
    check("source_event_values_and_order_preserved", [a["raw_event"] for a in episode["action_journal"]] == payload["events"])
    check("no_initial_hand_or_seed_fabricated", episode["match_config"]["random_seed"] is None and all(p["initial_hand"] is None for p in episode["match_config"]["players"]))
    check("no_native_execution_or_actor_samples_fabricated", all(a["target_engine_tick"] is None and a["execution_status"] == "not_run" for a in episode["action_journal"]) and episode["diagnostics"]["training_decision_count"] == 0)
    check("ability_false_is_preserved_as_upstream_field", all(a["ability_source_authoritative"] == a["raw_event"]["ability_source_authoritative"] for a in episode["action_journal"]))
    for value in (float("nan"), float("inf"), float("-inf")):
        changed = deepcopy(payload)
        changed["events"][0]["coordinates"]["native_world_units"]["x"] = value
        try:
            parse_payload(row_with(changed), source)
        except ValueError:
            check("reject_nonfinite_" + str(value), True)
        else:
            raise AssertionError("accepted nonfinite")
    changed = deepcopy(payload)
    changed["events"][1]["replay_tick_20hz"] = changed["events"][0]["replay_tick_20hz"]
    changed["events"][1]["time_seconds"] = changed["events"][0]["time_seconds"]
    tied = parse_payload(row_with(changed), source)
    check("same_tick_source_order_not_sorted_by_player", [a["source_index"] for a in tied["action_journal"]] == [e["source_index"] for e in changed["events"]])
    changed = deepcopy(payload)
    changed["events"][0]["replay_tick_20hz"] += 1
    bad = parse_payload(row_with(changed), source)
    check("tick_seconds_disagreement_fails_parse", bad["quality"]["parse_valid"] == "fail")
    changed = deepcopy(payload)
    changed["events"][1], changed["events"][0] = changed["events"][0], changed["events"][1]
    check("nonmonotonic_source_time_fails_without_reordering", parse_payload(row_with(changed), source)["quality"]["parse_valid"] == "fail")
    changed = deepcopy(payload)
    changed["events"][0]["coordinates"]["native_world_units"]["x"] = 19000
    bad = parse_payload(row_with(changed), source)
    check("out_of_bounds_not_clipped_to_make_valid", bad["quality"]["parse_valid"] == "fail" and bad["action_journal"][0]["source_native_world_units"]["x"] == 19000)
    changed = deepcopy(payload)
    changed["events"].insert(1, deepcopy(changed["events"][0]))
    bad = parse_payload(row_with(changed), source)
    check("duplicate_event_retained_and_flagged", len(bad["action_journal"]) == len(changed["events"]) and bad["quality"]["parse_valid"] == "fail")
    changed = deepcopy(payload)
    changed["battle"]["team"], changed["battle"]["opponent"] = changed["battle"]["opponent"], changed["battle"]["team"]
    changed["battle"]["result"] = {"victory": "defeat", "defeat": "victory", "draw": "draw"}[changed["battle"]["result"]]
    for event in changed["events"]:
        event["side"] = {"team": "opponent", "opponent": "team"}[event["side"]]
        native = (event.get("coordinates") or {}).get("native_world_units")
        if native:
            native["x"], native["y"] = 18000 - native["x"], 32000 - native["y"]
    mirrored = parse_payload(row_with(changed, "f" * 32), source)
    check("seat_swapped_copy_same_leakage_signature", mirrored["fingerprints"]["seat_invariant_action_sha256"] == episode["fingerprints"]["seat_invariant_action_sha256"])
    groups, _ = group_episodes([episode, mirrored])
    check("seat_swapped_copy_not_separate_split_group", len(set(groups.values())) == 1)
    changed = deepcopy(payload)
    changed["events"][0]["coordinates"]["native_world_units"]["x"] += 100
    check("small_position_change_near_grouped", content_fingerprints(changed)["near_group_sha256"] == episode["fingerprints"]["near_group_sha256"])
    try:
        select_frozen([episode, episode], {episode["episode_id"]: episode["episode_id"]}, 1, 1)
    except ValueError:
        check("duplicate_source_id_freeze_rejected", True)
    else:
        raise AssertionError("duplicate source ID accepted")
    frozen_path = ROOT / "data/phase2/frozen_manifest.json"
    if frozen_path.is_file():
        from royale_phase2.dataset import file_hash
        import pyarrow.parquet as pq
        frozen = json.loads(frozen_path.read_text(encoding="utf-8"))
        check("actual_frozen_converter_fingerprint_matches", frozen["converter_sha256"] == converter_hash())
        entries = frozen["entries"]
        dev = {x["leakage_group"] for x in entries if x["split"] == "development"}
        formal = {x["leakage_group"] for x in entries if x["split"] == "formal"}
        check("actual_formal_100_development_20_distinct", len(formal) == 100 and len(dev) == 20 and not dev & formal)
        for entry in entries:
            exported = json.loads((ROOT / entry["episode"]).read_text(encoding="utf-8"))
            source_row = rows[entry["raw_row_index"]]
            check_name = "actual_all_120_episode_lineage_reopened"
            if exported["episode_id"] != source_row["replay_tag"] or exported["provenance"]["payload_sha256"] != entry["payload_sha256"]:
                raise AssertionError(check_name)
            expected = parse_payload(source_row, {**source, "raw_row_index": entry["raw_row_index"]})
            expected.update(split=entry["split"], leakage_group=entry["leakage_group"])
            if canonical_json(exported) != canonical_json(expected):
                raise AssertionError("Actual converted episode differs from immutable source parse: " + entry["source_episode_id"])
        check(check_name, True)
        converted = ROOT / "data/phase2/converted/source-v1/source_actions.parquet"
        actions = pq.read_table(converted).to_pylist()
        check("actual_source_action_rows_nonempty", len(actions) > 0)
        check("actual_source_actions_not_training_samples", all(not x["training_decision_valid"] and x["actor_observation_json"] is None and x["legal_mask_json"] is None and x["actual_execution_tick"] is None for x in actions))
        report = json.loads((OUT / "conversion_readback.json").read_text(encoding="utf-8"))
        check("actual_export_file_hashes_match_readback_report", all(file_hash(ROOT / artifact["path"]) == artifact["sha256"] for artifact in report["artifacts"].values()))
        compatibility_path = OUT / "configuration_compatibility.json"
        if compatibility_path.is_file():
            compatibility = json.loads(compatibility_path.read_text(encoding="utf-8"))
            by_id = {entry["source_episode_id"]: entry for entry in entries}
            rejected = 0
            admitted_levels = 0
            for result in compatibility["episodes"]:
                entry = by_id[result["source_episode_id"]]
                source_episode = json.loads((ROOT / entry["episode"]).read_text(encoding="utf-8"))
                hypothesis = json.loads((ROOT / result["hypothesis"]).read_text(encoding="utf-8"))
                try:
                    guard_report = require_lossless_source_levels(source_episode, hypothesis)
                except ValueError:
                    if result["known_source_level_fidelity"] != "fail":
                        raise
                    rejected += 1
                else:
                    if result["known_source_level_fidelity"] != "pass" or guard_report["strict_runtime_ready"]:
                        raise AssertionError("Level guard silently admitted incompatible or unverified runtime")
                    admitted_levels += 1
            check("actual_lossy_level_hypotheses_rejected", rejected == compatibility["counts"]["fail"] and rejected > 0)
            check("actual_preserved_level_hypotheses_still_runtime_unknown", admitted_levels == compatibility["counts"]["pass"] and compatibility["strict_runtime_ready"] == 0)
    report = {"status": "passed", "checks_run": len(checks), "checks": checks, "scope": "source conversion and split safety only; not engine fidelity"}
    write_json(OUT / "source_converter_tests.json", report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

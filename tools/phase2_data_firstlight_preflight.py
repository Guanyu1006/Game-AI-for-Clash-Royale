"""Run the pinned public FirstLight converter, without loading native runtime.

Outputs are explicit reconstruction hypotheses, not executed battle trajectories.
The formal/dev source selection must already be frozen before this tool runs.
"""
from __future__ import annotations
from collections import Counter
import argparse
import json
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import OUT, file_hash, load_source_shard, write_json

FIRSTLIGHT = ROOT / "third_party/phase2/firstlight"
FIRSTLIGHT_COMMIT = "71944e3993b594b3cc89b38768b5e817dfbdbe0e"
DESTINATION = ROOT / "data/phase2/converted/firstlight-hypothesis-v1"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--frozen-manifest', default='data/phase2/frozen_manifest.json')
    parser.add_argument('--destination', default='data/phase2/converted/firstlight-hypothesis-v1')
    parser.add_argument('--output', default='outputs/phase2/data/firstlight_preflight.json')
    args = parser.parse_args()
    lock = (ROOT / args.frozen_manifest).resolve(); lock.relative_to(ROOT)
    destination_root = (ROOT / args.destination).resolve(); destination_root.relative_to(ROOT)
    report_path = (ROOT / args.output).resolve(); report_path.relative_to(ROOT)
    frozen = json.loads(lock.read_text(encoding="utf-8"))
    acquisition = json.loads((FIRSTLIGHT / "ACQUISITION.json").read_text(encoding="utf-8"))
    if acquisition["commit"] != FIRSTLIGHT_COMMIT:
        raise ValueError("FirstLight commit differs from audited converter")
    expected_files = {item["path"]: item["sha256"] for item in acquisition["files"]}
    converter_file = FIRSTLIGHT / "native_runner/royaleapi_replay.py"
    if file_hash(converter_file) != expected_files["native_runner/royaleapi_replay.py"]:
        raise ValueError("FirstLight converter modified relative to acquired source")
    # Process-local configuration only; never import torch, probe transport,
    # device control or call any native reset/step.
    os.environ["CR_WORKSPACE_ROOT"] = str(FIRSTLIGHT)
    os.environ["CR_REPLAY_DATASET_ROOT"] = str(ROOT / "data/phase2/raw/IL_Replay" / frozen["dataset_revision"])
    os.environ["CR_COMPETITIVE_DATA_ROOT"] = str(FIRSTLIGHT / "native_runner/data/competitive")
    sys.path.insert(0, str(FIRSTLIGHT))
    from native_runner.royaleapi_replay import prepare_collected_replay
    from native_runner.training.replay_archive import TrainingReplayV1
    source_rows, source, _ = load_source_shard()
    results = []
    command_kinds = Counter()
    modified_levels = []
    total_commands = 0
    for entry in frozen["entries"]:
        row = source_rows[entry["raw_row_index"]]
        if row["replay_tag"] != entry["source_episode_id"]:
            raise ValueError("Frozen raw row identity differs")
        payload = json.loads(row["payload_json"])
        start = time.perf_counter()
        result = {"source_episode_id": row["replay_tag"], "split": entry["split"], "raw_row_index": entry["raw_row_index"],
                  "payload_sha256": entry["payload_sha256"], "runtime_replayable": "not_run", "source_event_count": len(payload["events"])}
        try:
            prepared = prepare_collected_replay(payload)
            converted = prepared.replay.to_dict()
            # Validate the actual upstream serialization contract by reloading it.
            restored = TrainingReplayV1.from_mapping(converted)
            if restored.to_dict() != converted:
                raise ValueError("Upstream replay serialization roundtrip differs")
            commands = []
            padding_waits = 0
            for operation in prepared.replay.operations:
                for action in operation.actions:
                    if action.kind.value == "wait":
                        padding_waits += 1
                        continue
                    item = action.to_dict()
                    item.update(target_engine_tick=operation.start_native_tick + max(1, action.execute_offset_ticks or 1),
                                source_event_index=action.metadata["source_event_index"],
                                source_side=payload["events"][action.metadata["source_event_index"]]["side"],
                                execution_status="not_run", source_authoritative=False)
                    commands.append(item)
            source_indices = [item["source_event_index"] for item in commands]
            if source_indices != list(range(len(payload["events"]))):
                raise ValueError("Upstream command conversion dropped/duplicated/reordered source events")
            if prepared.event_count != len(commands):
                raise ValueError("Prepared event count differs")
            tags = prepared.replay.episode_config.tags
            selected_level = tags["level_cap"]
            source_levels = sorted({card["level"] for side in ("team", "opponent") for player in payload["battle"][side]["players"] for card in player["deck"]})
            level_changed = any(level != selected_level for level in source_levels)
            if level_changed:
                modified_levels.append(row["replay_tag"])
            hypothesis = {"artifact_kind": "upstream_source_conversion_hypothesis_not_executed",
                          "source_episode_id": row["replay_tag"], "split": entry["split"],
                          "provenance": {**source, "raw_row_index": entry["raw_row_index"], "payload_sha256": entry["payload_sha256"],
                                         "firstlight_commit": FIRSTLIGHT_COMMIT, "converter_sha256": file_hash(converter_file),
                                         "frozen_manifest_sha256": file_hash(lock)},
                          "source_build": "unknown", "source_balance_date": "unknown",
                          "assumptions": {"initial_hand": "one replay-tag-selected deal compatible with full public action sequence; not recovered private truth",
                                          "native_rng_seed": "fixed upstream NATIVE_DEAL_SEED; not collected source RNG seed",
                                          "deal_selection_uses": "public card sequence, not source winner/crowns/tower hp; full-sequence posterior hypothesis is not independent state truth",
                                          "source_coordinate_transform": "FirstLight true-side data_i orientation; see episode_config.tags",
                                          "timing_transform": "FirstLight command boundary -> observable state +1 tick; this runtime-specific rule is not applied to source-v1 files",
                                          "levels": "all cards and king tower assigned source level mode, capped 1..16; mixed source levels are modified",
                                          "source_levels": source_levels, "selected_uniform_level": selected_level,
                                          "source_levels_modified": level_changed,
                                          "ability_resolution": "deck candidates/native-ready-instance inference; source ability_source_authoritative is preserved in source-v1",
                                          "gap_waits": "engine-control padding only, not human WAIT decisions"},
                          "opening_cards_hypothesis": prepared.opening_cards, "cycle_hypothesis": prepared.queue_cards,
                          "policy_deal_invariance_bindings": [item.binding() for item in prepared.policy_deal_invariance],
                          "warnings": prepared.warnings, "commands": commands,
                          "upstream_replay": converted,
                          "quality": {"parse_valid": "pass", "runtime_replayable": "not_run", "deterministic": "not_run", "outcome_consistent": "not_run", "trajectory_validated": "not_available"},
                          "native_compatibility_confirmed": False, "training_decision_samples": 0}
            destination = destination_root / (row["replay_tag"] + ".json")
            write_json(destination, hypothesis)
            total_commands += len(commands)
            command_kinds.update(item["kind"] for item in commands)
            result.update(conversion_status="pass", hypothesis=destination.relative_to(ROOT).as_posix(), hypothesis_sha256=file_hash(destination),
                          commands=len(commands), gap_padding_waits=padding_waits,
                          source_levels=source_levels, selected_uniform_level=selected_level, source_levels_modified=level_changed,
                          opening_cards_hypothesis=prepared.opening_cards, cycle_hypothesis=prepared.queue_cards,
                          source_team_native_owner=tags["source_team_owner"], source_data_i=tags["royaleapi_data_i"],
                          command_to_observable_tick_offset=tags["native_observable_tick_offset"])
        except Exception as error:
            result.update(conversion_status="fail", error=repr(error))
        result["conversion_seconds"] = time.perf_counter() - start
        results.append(result)
    # Pin the actual imported converter dependency files and public compiled
    # tables, not just the headline repository commit.
    imported = {}
    for name, module in tuple(sys.modules.items()):
        path = getattr(module, "__file__", None)
        if name.startswith("native_runner") and path:
            path = Path(path).resolve()
            if path.is_file() and path.is_relative_to(FIRSTLIGHT):
                imported[path.relative_to(FIRSTLIGHT).as_posix()] = file_hash(path)
    tables = {path.relative_to(FIRSTLIGHT).as_posix(): file_hash(path) for path in (FIRSTLIGHT / "native_runner/data/competitive").iterdir() if path.is_file()}
    altered = [path for path, sha in {**imported, **tables}.items() if path in expected_files and expected_files[path] != sha]
    if altered:
        raise ValueError("Imported converter dependencies changed: " + repr(altered))
    report = {"scope": "public FirstLight source-only conversion; zero native engine calls",
              "firstlight_commit": FIRSTLIGHT_COMMIT, "firstlight_converter_sha256": file_hash(converter_file),
              "dataset_revision": frozen["dataset_revision"], "frozen_manifest_sha256": file_hash(lock),
              "denominator": len(results), "conversion_counts": dict(Counter(item["conversion_status"] for item in results)),
              "formal_conversion_counts": dict(Counter(item["conversion_status"] for item in results if item["split"] == "formal")),
              "development_conversion_counts": dict(Counter(item["conversion_status"] for item in results if item["split"] == "development")),
              "converted_source_commands": total_commands, "command_kinds": dict(command_kinds),
              "mixed_source_level_hypotheses": modified_levels, "source_build": "unknown", "source_balance_date": "unknown",
              "native_replays_executed": 0, "training_decision_samples": 0, "runtime_replayable": "not_run",
              "imported_source_sha256": imported, "competitive_table_sha256": tables, "episodes": results}
    write_json(report_path, report)
    print(json.dumps({key: value for key, value in report.items() if key not in ("episodes", "imported_source_sha256", "competitive_table_sha256")}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

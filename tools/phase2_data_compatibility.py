"""Keep strict configuration compatibility separate from source parse success."""
from collections import Counter
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import OUT, file_hash, source_level_fidelity, write_json


def main():
    frozen = json.loads((ROOT / "data/phase2/frozen_manifest.json").read_text(encoding="utf-8"))
    preflight = json.loads((OUT / "firstlight_preflight.json").read_text(encoding="utf-8"))
    converted = {item["source_episode_id"]: item for item in preflight["episodes"]}
    firstlight = ROOT / "third_party/phase2/firstlight"
    source_path = firstlight / "native_runner/match_factory.py"
    source_text = source_path.read_text(encoding="utf-8")
    # The inspected constructor has level_cap/minimum_card_level plus shared
    # king_tower_level; sp entries emit d/el only. No supported per-card level
    # API was found, so do not invent an unverified native field such as sp.l.
    required_fragments = ('level_cap: int = 0', 'minimum_card_level: int = 0', 'king_tower_level: int | None = None',
                          'deck_object["sp"] = [', '{"d": card_id, "el": form_availability[index]}')
    if not all(fragment in source_text for fragment in required_fragments):
        raise ValueError("MatchConfig source changed; repeat per-card-level capability audit")
    records = []
    distributions = {key: {name: Counter() for name in ("source_result", "source_game_mode", "has_hero_key", "has_evolution_key", "has_ability_event", "source_data_i")}
                     for key in ("all_frozen", "known_levels_preserved", "known_levels_changed")}
    for entry in frozen["entries"]:
        source_episode = json.loads((ROOT / entry["episode"]).read_text(encoding="utf-8"))
        conversion = converted[entry["source_episode_id"]]
        result = {"source_episode_id": entry["source_episode_id"], "split": entry["split"],
                  "parse_valid": entry["parse_valid"], "runtime_replayable": "not_run", "strict_runtime_ready": False}
        if conversion["conversion_status"] != "pass":
            result.update(known_source_level_fidelity="unknown", blocker="FirstLight conversion failed")
        else:
            hypothesis_path = ROOT / conversion["hypothesis"]
            if file_hash(hypothesis_path) != conversion["hypothesis_sha256"]:
                raise ValueError("Preflight hypothesis changed after audit")
            hypothesis = json.loads(hypothesis_path.read_text(encoding="utf-8"))
            result.update(source_level_fidelity(source_episode, hypothesis))
            result["hypothesis"] = conversion["hypothesis"]
            if result["known_source_level_fidelity"] == "fail":
                requirement_path = ROOT / "data/phase2/requirements/preserve_source_levels" / (entry["source_episode_id"] + ".json")
                requirements = {"artifact_kind": "source_level_requirements_not_native_configuration", "source_episode_id": entry["source_episode_id"],
                                "split": entry["split"], "source_episode": entry["episode"], "source_episode_sha256": file_hash(ROOT / entry["episode"]),
                                "source_players": source_episode["match_config"]["players"], "differences": result["differences"],
                                "native_configuration": None, "status": "blocked_missing_verified_per_card_level_serializer",
                                "reason": "Pinned MatchConfig emits only card d/el plus global level cap/minimum; unverified native level fields are not fabricated",
                                "required_evidence": "matching native decoder field semantics including rarity-relative vs displayed level, per-owner towers, then runtime card HP/level verification"}
                write_json(requirement_path, requirements)
                result.update(strict_blocker="known source card/tower levels modified by upstream uniform-level hypothesis; rejected for main strict replay",
                              preserve_level_requirements=requirement_path.relative_to(ROOT).as_posix(), preserve_level_candidate_native_config="not_generated_no_verified_serializer")
        records.append(result)
        distribution_keys = ["all_frozen", "known_levels_changed" if result["known_source_level_fidelity"] == "fail" else "known_levels_preserved"]
        source_keys = source_episode["match_config"]["required_card_keys"]
        values = {"source_result": source_episode["reference_terminal"]["result_from_team_view"],
                  "source_game_mode": source_episode["match_config"]["game_mode"],
                  "has_hero_key": str(any("-hero" in key for key in source_keys)),
                  "has_evolution_key": str(any("-ev" in key for key in source_keys)),
                  "has_ability_event": str(any(action["kind"] == "activate_ability" for action in source_episode["action_journal"])),
                  "source_data_i": str(conversion.get("source_data_i"))}
        for bucket in distribution_keys:
            for key, value in values.items():
                distributions[bucket][key][value] += 1
    report = {"schema_version": "royale-phase2-source-configuration-compatibility.v1", "denominator": len(records),
              "counts": dict(Counter(item["known_source_level_fidelity"] for item in records)),
              "formal_counts": dict(Counter(item["known_source_level_fidelity"] for item in records if item["split"] == "formal")),
              "development_counts": dict(Counter(item["known_source_level_fidelity"] for item in records if item["split"] == "development")),
              "strict_runtime_ready": 0, "runtime_replays_executed": 0,
              "source_level_guard": "royale_phase2.dataset.require_lossless_source_levels(source_episode, hypothesis)",
              "per_card_level_support": "not exposed by pinned MatchConfig; native decoder semantics unverified",
              "distribution_by_known_level_fidelity": {bucket: {key: dict(count) for key, count in distribution.items()} for bucket, distribution in distributions.items()},
              "source_evidence": {"firstlight_commit": preflight["firstlight_commit"], "path": source_path.relative_to(ROOT).as_posix(),
                                  "sha256": file_hash(source_path), "lines": "MatchConfig fields 93-112; to_replay_dict deck sp 188-191; globals 200-201; King Tower 219-226"},
              "limitation": "known-level pass is not version compatibility; source build/balance/king level/seed/initial hand remain unknown",
              "episodes": records}
    write_json(OUT / "configuration_compatibility.json", report)
    print(json.dumps({key: value for key, value in report.items() if key != "episodes"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

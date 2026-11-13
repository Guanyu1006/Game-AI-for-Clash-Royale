"""Describe fixed replay-hypothesis audits without model scores or data selection."""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import statistics

from .data_sources import sha256_file
from .hypothesis_audit import VARIANTS


def summarize_audit(audit: str | Path, source_index: str | Path, output: str | Path) -> dict:
    audit, source_index, output = map(Path, (audit, source_index, output))
    if output.exists():
        raise FileExistsError("Keep previous descriptive audit summaries")
    summary = json.loads((audit / "summary.json").read_text())
    if summary["source_index_sha256"] != sha256_file(source_index):
        raise ValueError("Audit source index changed")
    source = json.loads(source_index.read_text())
    source_rows = {r["game_id"]: r for r in source["games"]}
    totals = {v: Counter() for v in VARIANTS}
    native_rejections = {v: Counter() for v in VARIANTS}
    ability_unknown = {v: Counter() for v in VARIANTS}
    first_reasons = {v: Counter() for v in VARIANTS}
    terminal_tick_errors = {v: [] for v in VARIANTS}
    selected_ids, pairwise, runtime = [], [], 0.0
    for entry in summary["games"]:
        report_path = (audit / entry["path"]).resolve()
        if not report_path.is_relative_to(audit.resolve()) or sha256_file(report_path) != entry["sha256"]:
            raise ValueError("Per-game audit file checksum or location changed")
        report = json.loads(report_path.read_text())
        if report["split"] != summary["split"]:
            raise ValueError("Audit contains a different split")
        # Only load source games that were selected in the frozen audit, never
        # observations or policy outputs from other splits.
        source_path = Path(source_rows[report["game_id"]]["path"])
        if sha256_file(source_path) != report["normalized_source_sha256"]:
            raise ValueError("Normalized source game changed after audit")
        game = json.loads(source_path.read_text())
        if game["split"] != summary["split"]:
            raise ValueError("Normalized source split differs from audit")
        selected_ids.append(game["game_id"])
        pair = {"game_id": game["game_id"], "source_id": game["source_id"], "variants": {}}
        for variant in report["variants"]:
            name = variant["variant"]
            count, terminal = totals[name], variant["native_terminal"]
            recorded = variant["counts"]
            if recorded["source_events"] != len(game["events"]) or (
                    recorded["source_ability_events"] != sum(e["ability"] for e in game["events"])):
                raise ValueError("Original source event denominator changed")
            if len(variant["diagnostics"]) + recorded["events_not_reached_before_native_terminal"] != len(game["events"]):
                raise ValueError("Event outcome accounting lost a reached source command")
            count.update(variant["counts"])
            count["games"] += 1
            source_winner_known = type(game.get("winner")) is int and game["winner"] in (0, 1, 2)
            count["source_winner_known_games"] += source_winner_known
            count["source_winner_unknown_games"] += not source_winner_known
            runtime += variant["wall_seconds"]
            divergence = variant["first_command_divergence"]
            count["no_first_command_divergence_games"] += divergence is None
            count["all_source_events_reached_games"] += variant["counts"]["events_not_reached_before_native_terminal"] == 0
            count["all_source_events_reached_without_command_divergence_games"] += (
                divergence is None and variant["counts"]["events_not_reached_before_native_terminal"] == 0)
            if divergence:
                first_reasons[name][divergence["status"]] += 1
            ability_rows = [r for r in variant["diagnostics"] if r["ability"]]
            count["ability_events_reached"] += len(ability_rows)
            count["ability_events_not_reached"] += sum(e["ability"] for e in game["events"]) - len(ability_rows)
            for row in variant["diagnostics"]:
                if row.get("native_feedback") and row["status"] != "OK":
                    native_rejections[name][row["status"]] += 1
                if row["ability"] and not row.get("native_feedback"):
                    ability_unknown[name][row["status"]] += 1
            count["natural_end_games"] += bool(terminal["terminated"])
            count["source_boundary_without_natural_end_games"] += not terminal["terminated"] and not terminal["truncated"]
            count["truncated_games"] += bool(terminal["truncated"])
            if terminal["terminated"]:
                count["natural_source_winner_known_games"] += source_winner_known
                count["natural_source_winner_unknown_games"] += not source_winner_known
                count["natural_winner_matches_source_games"] += source_winner_known and terminal["winner_source_team"] == game["winner"]
                source_crowns = [p["crowns"] for p in game["players"]]
                native_crowns = [terminal["crowns_native_seat"][variant["source_team_to_native_seat"][str(t)]] for t in (0, 1)]
                count["natural_crowns_match_source_games"] += native_crowns == source_crowns
                error = terminal["tick"] - game["end_tick"]
                terminal_tick_errors[name].append(error)
                count["natural_terminal_tick_exact_matches"] += error == 0
            pair["variants"][name] = {"valid_play_windows": variant["counts"].get("valid_play_labels", 0),
                                      "valid_ability_windows": variant["counts"].get("valid_ability_labels", 0),
                                      "first_command_divergence_tick": divergence["source_tick"] if divergence else None,
                                      "unreached_source_events": variant["counts"]["events_not_reached_before_native_terminal"]}
        pairwise.append(pair)
    variants = {}
    for name, count in totals.items():
        valid = sum(count["valid_" + k + "_labels"] for k in ("wait", "play", "ability"))
        errors = terminal_tick_errors[name]
        if valid + count["invalid_labels"] != count["sampled_frames"]:
            raise ValueError("Valid/invalid grid-window accounting differs from saved frame denominator")
        if count["ability_accepted"] + count["ability_refused"] + sum(ability_unknown[name].values()) + count["ability_events_not_reached"] != count["source_ability_events"]:
            raise ValueError("Ability outcome accounting does not cover the original source denominator")
        variants[name] = {"counts": dict(count), "valid_grid_windows": valid,
                          "valid_grid_fraction": valid / count["sampled_frames"] if count["sampled_frames"] else None,
                          "natural_winner_agreement": {
                              "matches": count["natural_winner_matches_source_games"],
                              "known_source_winner_denominator": count["natural_source_winner_known_games"],
                              "unknown_source_winner_games": count["natural_source_winner_unknown_games"],
                              "definition": "Winner agreement only among native natural endings with a known source winner; unknown is never an agreement"},
                          "reached_unknown_ability_reasons": dict(ability_unknown[name]),
                          "native_refusal_reasons": dict(native_rejections[name]),
                          "first_command_divergence_reasons": dict(first_reasons[name]),
                          "natural_terminal_tick_error": {"definition": "native minus source end tick, among native natural endings only",
                              "n": len(errors), "minimum": min(errors) if errors else None,
                              "median": statistics.median(errors) if errors else None,
                              "maximum": max(errors) if errors else None,
                              "median_absolute": statistics.median(abs(e) for e in errors) if errors else None}}
    result = {"schema": "historical-hypothesis-descriptive-comparison-v1", "split": summary["split"],
              "audit_summary_sha256": sha256_file(audit / "summary.json"),
              "source_index_sha256": summary["source_index_sha256"],
              "historical_hypotheses_sha256": summary["historical_hypotheses_sha256"],
              "eligible_games": summary["eligible_games"], "requested_limit": summary["requested_limit"],
              "completed_games": len(selected_ids), "failed_games": summary["failures"],
              "selected_game_ids": selected_ids, "variants": variants, "paired_game_results": pairwise,
              "sum_variant_replay_wall_seconds": runtime, "model_scoring_performed": False,
              "training_labels_exported": False, "source_data_modified": False,
              "strong_imitation_delivery_gate_passed": False,
              "limitations": ["Historical account/deck snapshots do not prove original match forms or ability identity.",
                  "Every opening uses future source actions as offline constraints; original RNG, hand, build and levels remain unknown.",
                  "Uniquely legal public ability mapping remains a hypothesis, even when native execution accepts it.",
                  "Variants can terminate at different times: compare paired games and retain their differing sampled/reached denominators.",
                  "No original entity-state trace exists to certify the reconstructed observations; winner/crown agreement is insufficient.",
                  "These are command-reconstruction diagnostics, not policy wins or an estimate of live-game strength."]}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("audit", "source-index", "output"):
        parser.add_argument("--" + name, required=True)
    args = vars(parser.parse_args())
    result = summarize_audit(**args)
    print(json.dumps({"completed_games": result["completed_games"], "failed_games": result["failed_games"],
                      "variants": result["variants"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()

"""Verify portable league evidence without Torch, weights, or game execution."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path


ROOT = Path(__file__).resolve().parent


def read(name):
    return json.loads((ROOT / name).read_text())


def digest(name):
    return hashlib.sha256((ROOT / name).read_bytes()).hexdigest()


def rows(name):
    return [json.loads(line) for line in (ROOT / name).read_text().splitlines()]


def counts(games):
    states = Counter(row["status"] for row in games)
    outcomes = Counter(row["outcome"] for row in games)
    result = {"games": len(games), "natural": states["natural_end"],
              "faults": states["fault"], "truncated": states["truncated"]}
    result.update({key: outcomes[key] for key in ("win", "draw", "loss")})
    result.update({key: sum(row[key] for row in games)
                   for key in ("attempts", "executed", "refused", "decisions")})
    result["game_wall_seconds"] = sum(row["wall_seconds"] for row in games)
    result["decisions_per_second"] = result["decisions"] / max(result["game_wall_seconds"], 1e-9)
    result["simulated_seconds"] = sum(row.get("final_tick", 0) / 20 for row in games)
    result["simulation_realtime_multiple"] = result["simulated_seconds"] / max(result["game_wall_seconds"], 1e-9)
    return result


def check_promotion(candidate, incumbent, report, rules):
    reference = {row["case_id"]: row for row in incumbent}
    scores = {"win": 1.0, "draw": 0.5, "loss": 0.0}
    failed = []
    for opponent, recorded in report["by_opponent"].items():
        group = [row for row in candidate if row["opponent"] == opponent]
        valid = [row for row in group if row["status"] == reference[row["case_id"]]["status"] == "natural_end"]
        clusters = {}
        for row in valid:
            clusters.setdefault(row["seed"], []).append(scores[row["outcome"]] - scores[reference[row["case_id"]]["outcome"]])
        deltas = [sum(values) / len(values) for values in clusters.values()]
        positive, negative = sum(value > 0 for value in deltas), sum(value < 0 for value in deltas)
        discordant = positive + negative
        p = sum(math.comb(discordant, k) for k in range(positive, discordant + 1)) / 2 ** discordant if discordant else 1.0
        score = sum(scores[row["outcome"]] for row in valid) / len(valid) if valid else 0.0
        gain = sum(deltas) / len(deltas) if deltas else 0.0
        expected = {"attempted_games": len(group), "valid_pairs": len(valid), "seed_clusters": len(clusters),
                    "score": score, "mean_cluster_gain": gain, "positive_clusters": positive,
                    "negative_clusters": negative, "one_sided_sign_p": p}
        assert all(recorded[key] == value for key, value in expected.items()), opponent
        consumption_valid = all(type(row[key]) is int for row in group for key in ("attempts", "executed", "refused"))
        consumption_valid &= all(row["attempts"] >= 0 and 0 <= row["refused"] <= row["attempts"]
                                 and row["executed"] == row["attempts"] - row["refused"] for row in group)
        attempts, executed, refused = (sum(row[key] for row in group) for key in ("attempts", "executed", "refused"))
        checks = {"complete": len(valid) == len(group), "enough_games": len(group) >= rules["minimum_games_per_opponent"],
                  "enough_seed_clusters": len(clusters) >= rules["minimum_clusters_per_opponent"],
                  "both_seats": {row["side"] for row in group} == {0, 1},
                  "paired_seats_per_seed": all({row["side"] for row in group if row["seed"] == seed} == {0, 1}
                                               for seed in {row["seed"] for row in group}),
                  "score_floor": score >= rules["minimum_score"], "gain_floor": gain >= rules["minimum_mean_gain"],
                  "significant_gain": p <= rules["familywise_alpha"] / len(report["by_opponent"]),
                  "consumption": consumption_valid and attempts > 0 and executed > 0
                                 and refused / max(1, attempts) <= rules["maximum_refused_fraction"]}
        assert checks == recorded["checks"], opponent
        failed.extend(opponent + ":" + key for key, passed in checks.items() if not passed)
    assert sorted(failed) == sorted(report["reasons"])
    assert report["promoted"] == (not failed)


def main():
    package = read("package.json")
    for name, expected in package["files"].items():
        assert digest(name) == expected["sha256"], name
        assert (ROOT / name).stat().st_size == expected["bytes"], name
    plan, report = read("execution-plan.json"), read("report.json")
    assert digest("execution-plan.json") == report["execution_plan_sha256"]
    assert digest("pool.json") == report["pool_sha256"]
    assert digest("promotion-template.json") == plan["template"]["sha256"]
    assert digest("promotion-cases.json") == plan["cases_source"]["sha256"]
    for original, expected in plan["unchanged_training_artifacts"].items():
        if original.endswith("/pool/pool.json"):
            assert digest("selfplay/pool/pool.json") == expected
        elif original.endswith("/report.json"):
            assert digest("selfplay/report.json") == expected
    for name, expected in plan["implementation_sha256"].items():
        assert digest("frozen_source/" + name + ".txt") == expected
    evidence = {}
    expected_cases = {case["case_id"]: case for case in plan["cases"]}
    for arm, checkpoint in plan["checkpoints"].items():
        arm_plan, summary = read(arm + "/plan.json"), read(arm + "/summary.json")
        assert digest(arm + "/plan.json") == summary["plan_sha256"]
        assert digest(arm + "/episodes.jsonl") == summary["episodes_sha256"]
        assert arm_plan["candidate"] == checkpoint["member"]
        for key in ("cases", "environment_identity", "deterministic", "max_decisions", "max_wall_seconds"):
            assert arm_plan[key] == plan[key], (arm, key)
        assert arm_plan["members"] == read("pool.json")["members"]
        evidence[arm] = games = rows(arm + "/episodes.jsonl")
        assert len(games) == len(expected_cases) == len({row["case_id"] for row in games})
        for row in games:
            assert all(row.get(key) == value for key, value in expected_cases[row["case_id"]].items())
        assert counts(games) == report["arms"][arm]["overall"]
        for opponent, metrics in report["arms"][arm]["by_opponent"].items():
            assert counts([row for row in games if row["opponent"] == opponent]) == metrics
    for arm, key in (("ppo", "ppo_promotion"), ("joint", "joint_vs_foundation_diagnostic")):
        check_promotion(evidence[arm], evidence["foundation"], report[key], plan["rules"])
    assert not report["joint_vs_foundation_diagnostic"]["decision_applied_to_pool"]
    expected_champion = plan["checkpoints"]["ppo" if report["ppo_promotion"]["promoted"] else "foundation"]["member"]
    assert report["champion"] == read("pool.json")["champion"] == expected_champion
    interruption = read("interruption-audit.json")
    for name, sha in interruption["partial_files_sha256"].items():
        assert digest("interrupted-ppo/" + name) == sha
    recovered = {row["case_id"]: row for row in evidence["ppo"]}
    partial = rows("interrupted-ppo/episodes.jsonl")
    for row in partial:
        assert {key: value for key, value in row.items() if key != "wall_seconds"} == {
            key: value for key, value in recovered[row["case_id"]].items() if key != "wall_seconds"}
    assert len(partial) == interruption["partial_completed_games"]
    recovery = read("recovery-comparison.json")
    assert recovery["matched_cases"] == len(partial) and not recovery["mismatches"]
    assert recovery["formal_receipt_sha256"] == digest("ppo/episodes.jsonl")
    assert recovery["archived_receipt_sha256"] == digest("interrupted-ppo/episodes.jsonl")
    print(json.dumps({"verified_files": len(package["files"]), "formal_games": sum(map(len, evidence.values())),
                      "preserved_repeat_games": len(partial), "repeat_receipts_equal_excluding_wall_time": True,
                      "ppo_promoted": report["ppo_promotion"]["promoted"],
                      "scope": "Portable evidence hashes and receipt/statistical consistency; simulator games are not replayed"}))


if __name__ == "__main__":
    main()

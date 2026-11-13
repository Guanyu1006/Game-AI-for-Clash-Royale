"""Freeze and run independent foundation / joint / PPO promotion evaluation.

Prepare does not construct a simulator or play games. Run keeps the training
league untouched and resumes only fully written candidate evidence. Interrupted
partial match directories are preserved and require an explicit recovery audit.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import time

from .league import OpponentPool, PromotionRules, canonical, digest, promotion_report, run_matches, write_json

ROOT = Path(__file__).resolve().parents[1]
IMPLEMENTATIONS = ("league_acceptance.py", "league.py", "model.py", "timing.py",
                   "environment.py", "evaluation.py", "baselines.py")


def prepare(output, *, template, cases, foundation, joint, ppo, training_run, threads=1):
    from .model import load_policy
    import torch
    if threads < 1:
        raise ValueError("Positive CPU thread count required")
    torch.set_num_threads(threads)
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Acceptance output must stay in the repository")
    output.mkdir(parents=True, exist_ok=False)
    template, cases = Path(template).resolve(), Path(cases).resolve()
    source_plan = json.loads(template.read_text())
    resolved_cases = json.loads(cases.read_text())
    rules = PromotionRules(**source_plan["rules"])
    pool = OpponentPool(output / "pool")
    for name in ("random", "scripted"):
        pool.add_baseline(name)
    checkpoints, identities = {}, []
    for label, checkpoint in (("foundation", foundation), ("joint", joint), ("ppo", ppo)):
        checkpoint = Path(checkpoint).resolve()
        _, metadata = load_policy(checkpoint)
        identity = metadata["environment_identity"]
        identities.append(identity)
        identifier = pool.add_checkpoint(checkpoint, label=label,
            foundation=label == "foundation",
            parent_member=checkpoints["foundation"]["member"] if label == "ppo" else None)
        checkpoints[label] = {"path": str(checkpoint), "sha256": digest(checkpoint), "member": identifier}
    if any(canonical(identity) != canonical(identities[0]) for identity in identities[1:]):
        raise ValueError("All candidates must share the exact frozen environment identity")
    expected = json.loads(json.dumps(source_plan["cases"]))
    for case in expected:
        if case["opponent"] == "imitation-foundation":
            case["opponent"] = checkpoints["foundation"]["member"]
    if canonical(expected) != canonical(resolved_cases):
        raise ValueError("Resolved cases differ from the predeclared template")
    if len({case["case_id"] for case in expected}) != len(expected):
        raise ValueError("Duplicate frozen case identity")
    training_run = Path(training_run).resolve()
    training_artifacts = {str(training_run / name): digest(training_run / name)
                          for name in ("pool/pool.json", "resume.pt", "report.json")}
    plan = {"schema": "arena-three-arm-acceptance-v1", "checkpoints": checkpoints,
        "template": {"path": str(template), "sha256": digest(template)},
        "cases_source": {"path": str(cases), "sha256": digest(cases)}, "cases": expected,
        "rules": source_plan["rules"], "environment_identity": identities[0],
        "threads": threads, "deterministic": True, "max_decisions": 1500, "max_wall_seconds": 120,
        "implementation_sha256": {name: digest(Path(__file__).with_name(name)) for name in IMPLEMENTATIONS},
        "unchanged_training_artifacts": training_artifacts,
        "promotion_decision": "PPO vs predeclared timing foundation only; joint is a frozen diagnostic comparison, never a replacement foundation"}
    # Validate rule construction before saving the plan; no decisions use results.
    assert rules.minimum_games_per_opponent == source_plan["rules"]["minimum_games_per_opponent"]
    write_json(output / "execution-plan.json", plan)
    return {"status": "prepared_no_games_run", "games_per_arm": len(expected),
            "checkpoints": checkpoints, "execution_plan_sha256": digest(output / "execution-plan.json")}


def read_evidence(directory, *, candidate, expected_cases, environment_identity, members,
                  max_decisions, max_wall_seconds):
    directory = Path(directory)
    summary = json.loads((directory / "summary.json").read_text())
    plan = json.loads((directory / "plan.json").read_text())
    if (digest(directory / "plan.json") != summary["plan_sha256"]
            or digest(directory / "episodes.jsonl") != summary["episodes_sha256"]):
        raise ValueError("Match evidence hash drift")
    if (plan["candidate"] != candidate or canonical(plan["cases"]) != canonical(expected_cases)
            or canonical(plan["environment_identity"]) != canonical(environment_identity)
            or canonical(plan["members"]) != canonical(members)
            or plan["max_decisions"] != max_decisions or plan["max_wall_seconds"] != max_wall_seconds
            or plan["partition"] != "promotion_frozen_not_training" or not plan["deterministic"]):
        raise ValueError("Match evidence differs from the frozen acceptance plan")
    rows = [json.loads(line) for line in (directory / "episodes.jsonl").read_text().splitlines()]
    expected = {case["case_id"]: case for case in expected_cases}
    if len(rows) != len(expected) or {row["case_id"] for row in rows} != expected.keys():
        raise ValueError("Incomplete or repeated frozen match cases")
    for row in rows:
        if any(canonical(row.get(key)) != canonical(value) for key, value in expected[row["case_id"]].items()):
            raise ValueError("Match row configuration differs from the frozen case")
    return rows, summary


def summarize_rows(rows, summary):
    def count(selected):
        result = {"games": len(selected), "natural": sum(row["status"] == "natural_end" for row in selected),
                  "faults": sum(row["status"] == "fault" for row in selected),
                  "truncated": sum(row["status"] == "truncated" for row in selected)}
        result.update({outcome: sum(row["outcome"] == outcome for row in selected)
                       for outcome in ("win", "draw", "loss")})
        result.update({key: sum(row[key] for row in selected)
                       for key in ("attempts", "executed", "refused", "decisions")})
        result["game_wall_seconds"] = sum(row["wall_seconds"] for row in selected)
        result["decisions_per_second"] = result["decisions"] / max(result["game_wall_seconds"], 1e-9)
        result["simulated_seconds"] = sum(row.get("final_tick", 0) / 20 for row in selected)
        result["simulation_realtime_multiple"] = result["simulated_seconds"] / max(result["game_wall_seconds"], 1e-9)
        return result
    return {"overall": count(rows), "by_opponent": {opponent: count([row for row in rows if row["opponent"] == opponent])
             for opponent in sorted({row["opponent"] for row in rows})}, "raw_summary": summary}


def run(output):
    from .environment import ArenaConfig, ArenaEnv
    from .model import load_policy
    import torch
    output = Path(output).resolve()
    if (output / "report.json").exists():
        raise FileExistsError("Acceptance already completed; do not replay frozen outcomes")
    plan = json.loads((output / "execution-plan.json").read_text())
    for entry in (plan["template"], plan["cases_source"], *plan["checkpoints"].values()):
        if digest(entry["path"]) != entry["sha256"]:
            raise ValueError("Frozen source/checkpoint changed")
    for name, sha in plan["implementation_sha256"].items():
        if digest(Path(__file__).with_name(name)) != sha:
            raise ValueError("Acceptance implementation changed: " + name)
    for path, sha in plan["unchanged_training_artifacts"].items():
        if digest(path) != sha:
            raise ValueError("Training league artifacts changed: " + path)
    torch.set_num_threads(plan["threads"])
    pool = OpponentPool(output / "pool")
    started, evidence, summaries = time.perf_counter(), {}, {}
    for label, checkpoint in plan["checkpoints"].items():
        directory = output / label
        if directory.exists() and not (directory / "summary.json").exists():
            raise FileExistsError("Preserved partial match attempt needs explicit recovery audit: " + str(directory))
        if not directory.exists():
            _, metadata = load_policy(checkpoint["path"], expected_sha256=checkpoint["sha256"])
            env = ArenaEnv(ArenaConfig(**metadata["environment_identity"]["config"]))
            try:
                run_matches(pool, checkpoint["member"], plan["cases"], env, directory,
                    deterministic=plan["deterministic"], max_decisions=plan["max_decisions"],
                    max_wall_seconds=plan["max_wall_seconds"])
            finally:
                env.close()
        rows, summary = read_evidence(directory, candidate=checkpoint["member"], expected_cases=plan["cases"],
            environment_identity=plan["environment_identity"], members=pool.state["members"],
            max_decisions=plan["max_decisions"], max_wall_seconds=plan["max_wall_seconds"])
        evidence[label] = rows
        summaries[label] = summarize_rows(rows, summary)
        print(canonical({"completed_arm": label, **summaries[label]["overall"]}), flush=True)
    rules = PromotionRules(**plan["rules"])
    joint_comparison = promotion_report(evidence["joint"], evidence["foundation"], rules=rules)
    joint_comparison["decision_applied_to_pool"] = False
    joint_comparison["interpretation"] = "Diagnostic only; foundation choice was frozen before this evaluation"
    promotion = pool.promote(plan["checkpoints"]["ppo"]["member"], output / "ppo", output / "foundation", rules=rules)
    for path, sha in plan["unchanged_training_artifacts"].items():
        if digest(path) != sha:
            raise ValueError("Independent evaluation modified training league artifacts")
    report = {"schema": "arena-three-arm-acceptance-report-v1", "arms": summaries,
        "ppo_promotion": promotion, "joint_vs_foundation_diagnostic": joint_comparison,
        "champion": pool.state["champion"], "unchanged_training_artifacts": plan["unchanged_training_artifacts"],
        "execution_plan_sha256": digest(output / "execution-plan.json"),
        "pool_sha256": digest(pool.path), "wall_seconds_this_session": time.perf_counter()-started,
        "attempted_games": sum(summary["overall"]["games"] for summary in summaries.values()),
        "scope": "Frozen simulator games; no official ladder or physical-device strength claim"}
    write_json(output / "report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare", "run"))
    parser.add_argument("--output", required=True)
    for key in ("template", "cases", "foundation", "joint", "ppo", "training-run"):
        parser.add_argument("--"+key)
    parser.add_argument("--threads", type=int, default=1)
    args = vars(parser.parse_args())
    action, output = args.pop("action"), args.pop("output")
    if action == "prepare":
        if any(args[key] is None for key in args if key != "threads"):
            parser.error("prepare requires all frozen paths")
        result = prepare(output, **args)
    else:
        result = run(output)
    print(canonical(result))


if __name__ == "__main__":
    main()

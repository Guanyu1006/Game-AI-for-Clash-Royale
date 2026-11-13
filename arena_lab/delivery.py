"""Verify delivered weight hashes and run both seats in complete simulator games.

This is a reproducibility smoke check, never a promotion or strength estimate.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ARTIFACTS = ("timing-aligned-v1", "imitation-aligned-joint-v1",
             "selfplay-aligned-v1", "pixel-masked-v1")


def relabel_evaluation_scope(directory, *, partition, scope):
    """Label a non-promotion caller's results without changing the frozen runner.

    The runner currently writes a promotion partition for every caller. Preserve
    its original receipts, then bind the corrected plan to the final summary.
    This deliberately makes these outputs ineligible for OpponentPool.promote.
    """
    from .league import digest, write_json
    if partition not in {"delivery_smoke_not_promotion", "independent_visual_evaluation_not_promotion"}:
        raise ValueError("An explicit non-promotion partition is required")
    directory = Path(directory)
    plan_path, summary_path = directory / "plan.json", directory / "summary.json"
    plan, summary = json.loads(plan_path.read_text()), json.loads(summary_path.read_text())
    if summary["plan_sha256"] != digest(plan_path):
        raise ValueError("Cannot relabel evaluation with mismatched plan receipt")
    if summary["episodes_sha256"] != digest(directory / "episodes.jsonl"):
        raise ValueError("Cannot relabel evaluation with mismatched episodes receipt")
    if plan["partition"] == partition:
        return summary
    if plan["partition"] != "promotion_frozen_not_training":
        raise ValueError("Unexpected original evaluation partition")
    originals = {}
    for source in (plan_path, summary_path):
        destination = directory / ("original-runner-" + source.name)
        with destination.open("xb") as stream:
            stream.write(source.read_bytes())
        originals[source.stem + "_sha256"] = digest(destination)
    plan.update(partition=partition, scope=scope, original_runner_receipts=originals)
    write_json(plan_path, plan)
    summary.update(partition=partition, scope=scope, plan_sha256=digest(plan_path),
                   original_runner_receipts=originals)
    write_json(summary_path, summary)
    return summary


def smoke_cases(name, config):
    """Stable smoke seeds independent of CLI selection/order; swap full setups."""
    seed = 730001 + ARTIFACTS.index(name)
    cases = []
    for seat in (0, 1):
        options = {"shuffle": config.shuffle}
        for key in ("forms", "levels", "tower_levels", "elixir_milli"):
            value = getattr(config, key)
            if value is not None:
                options[key] = value if seat == 0 else value[::-1]
        cases.append({"case_id": f"smoke-{name}-{seat}", "seed": seed,
                      "side": seat, "opponent": "baseline-scripted",
                      "decks": config.decks if seat == 0 else config.decks[::-1],
                      "reset_options": options})
    return cases


def _check_one(output, name):
    import torch
    from .environment import ArenaConfig, ArenaEnv
    from .evaluation import validate_checkpoint_environment
    from .league import OpponentPool, digest, run_matches
    from .model import load_policy
    from .pixel_policy import MaskedArenaEnv

    artifacts = Path(__file__).parent / "artifacts"
    manifest = json.loads((artifacts / (name + ".json")).read_text())
    path = artifacts / (name + ".pt")
    if path.stat().st_size != manifest["bytes"] or digest(path) != manifest["sha256"]:
        raise ValueError("Delivered checkpoint differs from reviewed manifest: " + name)
    model, metadata = load_policy(path, expected_sha256=manifest["sha256"])
    if not metadata.get("trained") or metadata.get("training_steps", 0) <= 0:
        raise ValueError("Delivered checkpoint has no trained updates: " + name)
    if not all(torch.isfinite(t).all().item() for t in model.state_dict().values()):
        raise ValueError("Non-finite checkpoint: " + name)
    config = ArenaConfig(**metadata["environment_identity"]["config"])
    masked = metadata["environment_identity"]["observation_version"] == "arena-masked-pixel-v3"
    env = MaskedArenaEnv(config) if masked else ArenaEnv(config)
    cases = smoke_cases(name, config)
    try:
        validate_checkpoint_environment(model, metadata, env)
        pool = OpponentPool(output / name / "pool")
        pool.add_baseline("scripted")
        candidate = pool.add_checkpoint(path)
        rows, _ = run_matches(pool, candidate, cases, env, output / name / "games")
        summary = relabel_evaluation_scope(output / name / "games", partition="delivery_smoke_not_promotion",
                                           scope="Hash/reload/complete-game smoke only; not strength or device acceptance")
    finally:
        env.close()
    counts = {key: sum(row[key] for row in rows) for key in ("attempts", "executed", "refused")}
    passed = (summary["natural"] == len(cases) and counts["executed"] > 0
              and counts["attempts"] == counts["executed"]+counts["refused"])
    return dict(summary, **counts, checkpoint_sha256=manifest["sha256"],
                observation_version=metadata["environment_identity"]["observation_version"],
                passed=passed, status="passed" if passed else "failed")


def check(output, names=ARTIFACTS):
    import torch
    from .league import write_json

    names = tuple(names)
    if not names or len(set(names)) != len(names) or set(names)-set(ARTIFACTS):
        raise ValueError("Select nonempty unique delivered model names")
    torch.set_num_threads(1)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    results = {name: {"status": "pending", "passed": False} for name in names}

    def receipt():
        complete = all(r["status"] in {"passed", "failed"} for r in results.values())
        write_json(output / "report.json", {"passed": complete and all(r["passed"] for r in results.values()),
                   "complete": complete, "requested_models": names, "models": results,
                   "partition": "delivery_smoke_not_promotion",
                   "scope": "Hash/reload/complete-game smoke only; not strength or device acceptance"})

    receipt()
    for name in names:
        results[name] = {"status": "running", "passed": False}
        receipt()
        try:
            results[name] = _check_one(output, name)
        except Exception as error:
            results[name] = {"status": "failed", "passed": False,
                             "error": type(error).__name__ + ": " + str(error)}
            receipt()
            raise
        receipt()
        if not results[name]["passed"]:
            raise RuntimeError("Delivered checkpoint failed complete-game smoke: " + name)
    return results


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="A new output directory")
    parser.add_argument("--models", nargs="+", choices=ARTIFACTS, default=list(ARTIFACTS))
    args = parser.parse_args()
    print(json.dumps(check(args.output, args.models), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

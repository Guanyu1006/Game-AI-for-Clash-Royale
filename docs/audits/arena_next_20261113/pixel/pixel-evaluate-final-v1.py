"""Run from repository root after the predeclared pixel training report exists.

This records the frozen checkpoint and case identities before the first game.
It never selects/calibrates a model and never writes to the training run.
"""
from pathlib import Path
import json
import sys
import time

sys.path.insert(0, str(Path.cwd()))
import torch
from arena_lab.delivery import relabel_evaluation_scope
from arena_lab.environment import ArenaConfig
from arena_lab.evaluation import validate_checkpoint_environment
from arena_lab.league import OpponentPool, canonical, digest, run_matches, write_json
from arena_lab.league_acceptance import summarize_rows
from arena_lab.model import load_policy
from arena_lab.pixel_policy import MaskedArenaEnv

ROOT = Path("outputs/arena-next-20261113")
run = ROOT / "pixel-timing-v1"
output = ROOT / "pixel-independent40-v1"
report = json.loads((run / "report.json").read_text())
freeze_path = ROOT / "pixel-evaluation-freeze-v1.json"
freeze = json.loads(freeze_path.read_text())
training_plan = ROOT / "pixel-experiment-plan-v3.json"
plan = json.loads(training_plan.read_text())
assert digest(training_plan) == freeze["training_plan_sha256"]
assert digest(freeze["cases"]) == freeze["cases_sha256"]
assert digest(plan["index"]) == plan["index_sha256"]
for name, sha in plan["implementation_sha256"].items():
    assert digest(name) == sha, name
before_test = json.loads((run / "frozen-before-test.json").read_text())
checkpoint = Path("arena_lab/artifacts/pixel-masked-v1.pt")
assert digest(checkpoint) == digest(run / "best.pt") == before_test["checkpoint_sha256"] == report["checkpoint"]["sha256"]
torch.set_num_threads(1)
model, metadata = load_policy(checkpoint, expected_sha256=before_test["checkpoint_sha256"])
env = MaskedArenaEnv(ArenaConfig(**metadata["environment_identity"]["config"]))
try:
    validate_checkpoint_environment(model, metadata, env)
    output.mkdir(parents=True, exist_ok=False)
    cases = json.loads(Path(freeze["cases"]).read_text())
    assert len(cases) == 40 and len({case["case_id"] for case in cases}) == 40
    pool = OpponentPool(output / "pool")
    for name in ("random", "scripted"):
        pool.add_baseline(name)
    candidate = pool.add_checkpoint(checkpoint, label="frozen-masked-transfer-candidate")
    implementation = {name: digest(Path("arena_lab") / name) for name in
        ("league.py", "model.py", "timing.py", "pixel_policy.py", "environment.py", "evaluation.py", "baselines.py", "delivery.py")}
    execution = {"schema": "arena-pixel-independent-evaluation-v1", "candidate": candidate,
        "checkpoint": {"path": str(checkpoint), "sha256": digest(checkpoint)},
        "training_plan": {"path": str(training_plan), "sha256": digest(training_plan)},
        "freeze": {"path": str(freeze_path), "sha256": digest(freeze_path)},
        "cases_source": {"path": freeze["cases"], "sha256": digest(freeze["cases"])},
        "cases": cases, "environment_identity": env.identity(), "implementation_sha256": implementation,
        "deterministic": True, "torch_threads": 1, "max_decisions": 1500, "max_wall_seconds": 120,
        "partition": "independent_visual_evaluation_not_promotion", "calibration_changed": False,
        "rule_reference_information": "scripted opponent uses ordinary public numeric observation; learned actor gets only masked projection",
        "scope": "Frozen simulator masked projection transfer; no real detector, human expert, official-client or device acceptance"}
    write_json(output / "execution-plan.json", execution)
    started = time.perf_counter()
    rows, _ = run_matches(pool, candidate, cases, env, output / "games", deterministic=True,
                          max_decisions=1500, max_wall_seconds=120)
    summary = relabel_evaluation_scope(output / "games", partition="independent_visual_evaluation_not_promotion",
        scope="Frozen masked simulator transfer evaluation; not promotion or real-device acceptance")
    assert all(digest(Path("arena_lab") / name) == sha for name, sha in implementation.items())
    for row, case in zip(rows, cases):
        assert all(canonical(row.get(key)) == canonical(value) for key, value in case.items())
    result = {"schema": "arena-pixel-independent-evaluation-report-v1",
        **summarize_rows(rows, summary), "checkpoint_sha256": digest(checkpoint),
        "execution_plan_sha256": digest(output / "execution-plan.json"),
        "pool_sha256": digest(pool.path), "wall_seconds": time.perf_counter()-started,
        "promotion_claim": False, "real_pixel_inputs": False, "real_device_verified": False,
        "same_configuration_all_natural": len(rows) == 40 and all(r["status"] == "natural_end" for r in rows),
        "consumption_accounted": all(r["attempts"] == r["executed"] + r["refused"] for r in rows),
        "source_frozen_before_evaluation": True, "calibration_changed": False,
        "scope": execution["scope"]}
    write_json(output / "report.json", result)
    print(canonical({"output": str(output), **result["overall"],
                     "same_configuration_all_natural": result["same_configuration_all_natural"],
                     "consumption_accounted": result["consumption_accounted"]}), flush=True)
finally:
    env.close()

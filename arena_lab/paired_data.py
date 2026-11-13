"""Collect reproducible same-version state/action pairs from live simulator games.

These are rule-teacher demonstrations, not human-expert or official-client data.
Full private state is retained only in the audit trajectory. Training NPZ uses
the existing public actor boundary and never future command-window labels.
"""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
import gzip
import io
import json
from pathlib import Path
import re
import time

import numpy as np

from .alignment import (OBSERVATION_KEYS, audit_timing_arrays, object_sha256,
                        observation_sha256, replay_alignment, sha256_file)

DECKS = (
    ("HogRider", "Musketeer", "Knight", "Cannon", "Fireball", "Log", "Skeletons", "IceSpirits"),
    ("Xbow", "Archer", "Knight", "Tesla", "Fireball", "Log", "Skeletons", "ElectroSpirit"),
    ("Knight", "Archer", "Goblins", "Giant", "Musketeer", "Fireball", "Arrows", "Skeletons"),
)


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
    temporary.replace(path)


def freeze_plan(destination: Path, *, train: int = 60, validation: int = 10, test: int = 10,
                seed_start: int = 2026111300, decision_ticks: int = 10) -> dict:
    """Freeze cases before any collection/model fitting. Both seats share a split."""
    from .environment import ArenaConfig

    if min(train, validation, test) < 1 or seed_start < 0 or decision_ticks < 1:
        raise ValueError("Positive split counts, seed and control interval required")
    cases, number = [], 0
    for split, count in (("train", train), ("validation", validation), ("test", test)):
        for local in range(count):
            case_seed = seed_start + number
            decks = [list(DECKS[local % 3]), list(DECKS[(local // 3 + local + 1) % 3])]
            if local % 2:
                decks.reverse()
            config = ArenaConfig(decks=decks, seed=case_seed, decision_ticks=decision_ticks,
                                 forms=[[0] * 8 for _ in range(2)],
                                 levels=[[11] * 8 for _ in range(2)], tower_levels=[11, 11],
                                 elixir_milli=[6000, 6000], command_delay_ticks=0, max_ticks=None)
            cases.append({"game_id": f"paired-{case_seed}", "split": split,
                          "environment_config": asdict(config)})
            number += 1
    plan = {"schema": "arena-paired-plan-v1", "source_kind": "rule_teacher_simulator",
            "teacher": "arena_lab.baselines.scripted_action",
            "teacher_source_sha256": sha256_file(Path(__file__).with_name("baselines.py")),
            "official_client_equivalence_verified": False, "human_expert_demonstration": False,
            "split_unit": "unique engine seed / complete battle / both perspectives",
            "test_policy": "Freeze before fitting; test cannot select models or calibrations.",
            "tick_hz": 20, "decision_ticks": decision_ticks,
            "wait_semantics": "teacher explicitly returned WAIT at observed tick",
            "cases": cases}
    if destination.exists():
        previous = json.loads(destination.read_text())
        if previous != plan:
            raise ValueError("Existing frozen plan differs; choose a new destination")
        return previous
    write_json(destination, plan)
    return plan


def collect_episode(case: dict, destination: Path) -> tuple[dict, dict]:
    from .environment import ArenaConfig, ArenaEnv
    import msgspec

    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", case["game_id"]):
        raise ValueError("Unsafe battle identifier")
    destination.mkdir(parents=True, exist_ok=False)
    config = ArenaConfig(**case["environment_config"])
    if config.command_delay_ticks or config.max_ticks is not None:
        raise ValueError("This acquisition contract requires synchronous commands and natural terminal")
    env = ArenaEnv(config)
    frames = {seat: [] for seat in (0, 1)}
    labels = {seat: [] for seat in (0, 1)}
    counts: Counter = Counter()
    by_card: Counter = Counter()
    started = time.monotonic()
    try:
        obs, info = env.reset()
        identity, initial = env.identity(), info["episode_setup"]
        trajectory_path = destination / "trajectory.jsonl.gz"
        with trajectory_path.open("wb") as raw:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                with io.TextIOWrapper(compressed) as stream:
                    index = 0
                    while not env.terminated and not env.truncated:
                        tick = info["tick"]
                        actions = {seat: env.scripted_action(obs[seat], player=seat) for seat in (0, 1)}
                        raw_state = msgspec.to_builtins(env.state)
                        row = {"index": index, "observation_tick": tick,
                               "state_hash_before": info["state_hash"],
                               "raw_state": raw_state, "raw_state_sha256": object_sha256(raw_state),
                               "actor_sha256": {str(s): observation_sha256(obs[s]) for s in (0, 1)},
                               "actions": actions, "decision_ticks": config.decision_ticks}
                        for seat in (0, 1):
                            frames[seat].append(obs[seat])
                        next_obs, _, _, _, info = env.joint_step(actions)
                        row.update(next_tick=info["tick"], state_hash_after=info["state_hash"],
                                   feedback=info["deploy_results"], commands_run=info["commands_run"])
                        stream.write(json.dumps(row, separators=(",", ":"), allow_nan=False) + "\n")
                        feedback_by_seat = {r["team"]: r for r in info["deploy_results"]}
                        for seat in (0, 1):
                            action, feedback = actions[seat], feedback_by_seat.get(seat)
                            confirmed = feedback is not None and feedback["status"] == 0
                            valid = action["card"] == 0 or confirmed
                            if confirmed:
                                by_card[env.cards[feedback["card_id"]].name] += 1
                            labels[seat].append({"ticks": tick, "observation_tick": tick,
                                "request_tick": tick if action["card"] else -1,
                                "confirmed_tick": feedback["tick"] if confirmed else -1,
                                "elapsed_ticks": config.decision_ticks if index else 0,
                                "decision_ticks": config.decision_ticks,
                                "target_card": action["card"], "target_position": action["position"],
                                "label_valid": valid,
                                "position_valid": valid and 1 <= action["card"] <= 4})
                            counts["play_labels" if action["card"] else "wait_labels"] += int(valid)
                            counts["invalid_labels"] += int(not valid)
                        counts["accepted"] += info["accepted"]
                        counts["refused"] += info["refused"]
                        obs, index = next_obs, index + 1
                    counts["joint_decisions"] = index
        source_sha = sha256_file(trajectory_path)
        actor_sequences = []
        for seat in (0, 1):
            arrays = {key: np.stack([frame[key] for frame in frames[seat]]) for key in OBSERVATION_KEYS}
            for key in ("global_features", "entities"):
                arrays[key] = arrays[key].astype(np.float16)
            arrays["hand_ids"] = arrays["hand_ids"].astype(np.int16)
            for key in labels[seat][0]:
                dtype = np.bool_ if key in ("label_valid", "position_valid") else np.int32
                arrays[key] = np.asarray([label[key] for label in labels[seat]], dtype=dtype)
            audit_timing_arrays(arrays, config.decision_ticks)
            artifact = destination / f"seat-{seat}.npz"
            np.savez_compressed(artifact, **arrays)
            actor_sequences.append({"seat": seat, "path": artifact.name,
                                    "sha256": sha256_file(artifact), "steps": len(labels[seat])})
        manifest = {"schema": "arena-paired-episode-v1", "game_id": case["game_id"],
            "split": case["split"], "source_kind": "rule_teacher_simulator",
            "human_expert_demonstration": False, "official_client_equivalence_verified": False,
            "environment_config": asdict(config), "environment_identity": identity,
            "environment_identity_sha256": object_sha256(identity), "initial_setup": initial,
            "rng": {"engine_seed": config.seed, "algorithm_identity": identity["binary_sha256"],
                    "internal_state_serialization_available": False,
                    "reproduction": "fresh reset with pinned binary and exact setup"},
            "teacher_source_sha256": sha256_file(Path(__file__).with_name("baselines.py")),
            "tick_hz": 20, "decision_ticks": config.decision_ticks,
            "trajectory": {"path": trajectory_path.name, "sha256": source_sha,
                           "contains_private_oracle": True, "allowed_use": "alignment audit only"},
            "actor_sequences": actor_sequences, "joint_decisions": counts["joint_decisions"],
            "terminal": {"natural": info["terminated"], "truncated": info["truncated"],
                         "tick": info["tick"], "winner": info["winner"], "crowns": info["crowns"],
                         "tower_hp": info["tower_hp"]},
            "counts": dict(counts), "card_counts": dict(by_card),
            "collection_wall_seconds": time.monotonic() - started}
        write_json(destination / "manifest.json", manifest)
        alignment = replay_alignment(destination / "manifest.json")
        write_json(destination / "alignment.json", alignment)
        return manifest, alignment
    finally:
        env.close()


def collect(plan_path: Path, output: Path) -> dict:
    plan = json.loads(plan_path.read_text())
    if plan.get("schema") != "arena-paired-plan-v1":
        raise ValueError("Unknown acquisition plan")
    if plan["teacher_source_sha256"] != sha256_file(Path(__file__).with_name("baselines.py")):
        raise ValueError("Teacher changed after acquisition plan freeze")
    cases = plan["cases"]
    if any(not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", case["game_id"]) for case in cases):
        raise ValueError("Unsafe battle identifier in acquisition plan")
    seeds = [case["environment_config"]["seed"] for case in cases]
    if len(set(seeds)) != len(seeds) or len({c["game_id"] for c in cases}) != len(cases):
        raise ValueError("Duplicate seed/battle in acquisition plan")
    output.mkdir(parents=True, exist_ok=True)
    receipt = output / "plan.json"
    if receipt.exists() and object_sha256(json.loads(receipt.read_text())) != object_sha256(plan):
        raise ValueError("Output belongs to a different frozen acquisition")
    if not receipt.exists():
        write_json(receipt, plan)
    games, summaries, failures = [], [], []
    started = time.monotonic()
    identity = None
    counts, card_counts = Counter(), Counter()
    session_work = {"new_games": 0, "reused_games": 0, "new_joint_decisions": 0,
                    "reused_joint_decisions": 0, "new_collection_wall_seconds": 0.,
                    "reused_audit_wall_seconds": 0., "interrupted_episode_directories_preserved": 0}
    for case in cases:
        destination = output / case["game_id"]
        case_started = time.monotonic()
        collection_attempt = False
        try:
            incomplete = destination.exists() and not (destination / "manifest.json").exists()
            if destination.exists() and not incomplete:
                try:
                    json.loads((destination / "manifest.json").read_text())
                except json.JSONDecodeError:
                    # Compatibility with pre-atomic manifests interrupted by
                    # process loss. A complete but inconsistent receipt still
                    # fails the normal integrity checks below.
                    incomplete = True
            if incomplete:
                # Collection was interrupted before its atomic completion
                # boundary. Preserve raw partial files, then retry the exact
                # frozen case; never treat a partial directory as a full game.
                interrupted = output / "interrupted"
                interrupted.mkdir(exist_ok=True)
                destination.rename(interrupted / f"{case['game_id']}-{time.time_ns()}")
                session_work["interrupted_episode_directories_preserved"] += 1
            if destination.exists():
                manifest = json.loads((destination / "manifest.json").read_text())
                if (manifest["environment_config"] != case["environment_config"] or
                        manifest["split"] != case["split"]):
                    raise ValueError("Existing episode differs from frozen case")
                # Resume never trusts an old boolean receipt over fresh checks.
                alignment = replay_alignment(destination / "manifest.json")
            else:
                collection_attempt = True
                manifest, alignment = collect_episode(case, destination)
            if not alignment["same_engine_replay_passed"] or not manifest["terminal"]["natural"]:
                raise ValueError("State alignment or natural-terminal gate failed")
            identity = manifest["environment_identity"]
            source_sha = manifest["trajectory"]["sha256"]
            for sequence in manifest["actor_sequences"]:
                games.append({"game_id": case["game_id"], "source_game_id": case["game_id"],
                    "source_id": case["game_id"], "source_sha256": source_sha, "split": case["split"],
                    "path": f"{case['game_id']}/{sequence['path']}", "sha256": sequence["sha256"],
                    "steps": sequence["steps"], "teacher_seat": sequence["seat"],
                    "quality": "same-version live rule-teacher trajectory; deterministic replay verified",
                    "paired_manifest": f"{case['game_id']}/manifest.json"})
            counts.update(manifest["counts"])
            card_counts.update(manifest["card_counts"])
            work_kind = "new" if collection_attempt else "reused"
            session_work[work_kind + "_games"] += 1
            session_work[work_kind + "_joint_decisions"] += manifest["counts"]["joint_decisions"]
            summaries.append({"game_id": case["game_id"], "split": case["split"],
                              "terminal": manifest["terminal"], "counts": manifest["counts"],
                              "alignment": alignment})
        except Exception as exc:
            failure = {"game_id": case["game_id"], "split": case["split"],
                       "error": f"{type(exc).__name__}: {exc}"}
            failures.append(failure)
            write_json(output / f"{case['game_id']}-failure.json", failure)
        finally:
            time_key = "new_collection_wall_seconds" if collection_attempt else "reused_audit_wall_seconds"
            session_work[time_key] += time.monotonic() - case_started
        progress = {"planned_games": len(cases), "completed_games": len(summaries),
                    "failed_games": failures, "counts": dict(counts),
                    "wall_seconds": time.monotonic() - started}
        write_json(output / "progress.json", progress)
        print(json.dumps(progress), flush=True)
    report = dict(progress, schema="arena-paired-collection-v1", source_kind="rule_teacher_simulator",
                  human_expert_demonstration=False, official_client_equivalence_verified=False,
                  plan_sha256=sha256_file(receipt), episodes=summaries, card_counts=dict(card_counts))
    report["state_alignment_passed"] = len(summaries) == len(cases) and not failures
    report["session_work"] = session_work
    fresh_wall = session_work["new_collection_wall_seconds"]
    report["throughput_including_compression_and_independent_replay"] = {
        "scope": "new collection attempts in this session only; includes their independent replay and failures; reused trajectories excluded",
        "joint_decisions_per_second": session_work["new_joint_decisions"] / fresh_wall if fresh_wall else None,
        "complete_games_per_hour": session_work["new_games"] / fresh_wall * 3600 if fresh_wall else None,
        "full_run_elapsed_known": session_work["reused_games"] == 0 and not session_work["interrupted_episode_directories_preserved"],
    }
    write_json(output / "collection-report.json", report)
    if not report["state_alignment_passed"]:
        raise RuntimeError("Acquisition incomplete; failure receipts retained; no training index published")
    spec = {k: v for k, v in identity["obs_spec"].items() if k not in ("max_entities", "num_card_types")}
    spec.update(card_vocab_size=identity["obs_spec"]["num_card_types"],
                observation_version=identity["observation_version"])
    index = {"schema_version": 1, "policy_spec": spec, "games": games,
             "observation_version": identity["observation_version"],
             "environment_identity": identity, "tick_hz": plan["tick_hz"],
             "decision_ticks": plan["decision_ticks"], "data_origin": "rule_teacher_simulator",
             "strong_imitation_delivery_gate_passed": False,
             "same_version_state_alignment_passed": True,
             "wait_semantics": plan["wait_semantics"], "source_plan_sha256": sha256_file(receipt)}
    write_json(output / "index.json", index)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--train", type=int, default=60)
    parser.add_argument("--validation", type=int, default=10)
    parser.add_argument("--test", type=int, default=10)
    parser.add_argument("--seed-start", type=int, default=2026111300)
    parser.add_argument("--decision-ticks", type=int, default=10)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    plan_path = args.output / "plan.json"
    freeze_plan(plan_path, train=args.train, validation=args.validation, test=args.test,
                seed_start=args.seed_start, decision_ticks=args.decision_ticks)
    result = collect(plan_path, args.output)
    print(json.dumps({key: value for key, value in result.items() if key != "episodes"}))


if __name__ == "__main__":
    main()

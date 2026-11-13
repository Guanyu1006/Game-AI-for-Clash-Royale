"""Independent paired closed-loop evaluation with explicit failure denominators."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import torch

from .model import EntityGRUPolicy, PolicyAgent, PolicySpec, load_policy


@dataclass(frozen=True)
class EvaluationCase:
    case_id: str
    seed: int
    decks: tuple[tuple[Any, ...], tuple[Any, ...]]
    candidate_side: int
    opponent: str = "random"
    reset_options: Mapping[str, Any] | None = None

    def __post_init__(self) -> None:
        if self.candidate_side not in {0, 1}:
            raise ValueError("candidate_side must be 0 or 1")
        if len(self.decks) != 2 or any(len(deck) != 8 for deck in self.decks):
            raise ValueError("Evaluation requires two explicit eight-card decks")
        if self.opponent not in {"wait", "random", "scripted"}:
            raise ValueError("Unsupported evaluation opponent")
        allowed = {"forms", "levels", "tower_levels", "shuffle", "elixir_milli"}
        if self.reset_options is not None and set(self.reset_options) - allowed:
            raise ValueError("Unsupported evaluation reset options")


def paired_cases(
    matchups: Sequence[tuple[Sequence[Any], Sequence[Any]] | Mapping[str, Any]], seeds: Sequence[int],
    *, opponents: Sequence[str] = ("random", "wait"),
) -> list[EvaluationCase]:
    """The candidate keeps its deck while sides are exchanged for every seed."""
    cases = []
    for matchup_index, matchup in enumerate(matchups):
        if isinstance(matchup, Mapping):
            candidate_deck, opponent_deck = matchup["decks"]
            reset_options = dict(matchup.get("reset_options", {}))
        else:
            candidate_deck, opponent_deck = matchup
            reset_options = {}
        for seed in seeds:
            for opponent in opponents:
                for side in (0, 1):
                    decks = (tuple(candidate_deck), tuple(opponent_deck))
                    options = dict(reset_options)
                    if side == 1:
                        decks = decks[::-1]
                        for key in ("forms", "levels", "tower_levels", "elixir_milli"):
                            if options.get(key) is not None:
                                if len(options[key]) != 2:
                                    raise ValueError(f"{key} must describe both seats")
                                options[key] = list(options[key])[::-1]
                    cases.append(EvaluationCase(
                        f"matchup-{matchup_index}-seed-{seed}-{opponent}-side-{side}",
                        int(seed), decks, side, opponent, options or None,
                    ))
    if len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Duplicate evaluation case IDs; seeds and opponents must be unique")
    return cases


def policy_spec_from_environment(environment: Any, **overrides: Any) -> PolicySpec:
    raw = environment.obs_spec() if callable(environment.obs_spec) else environment.obs_spec
    raw = dict(raw)
    if "card_vocab_size" not in raw and "num_card_types" in raw:
        raw["card_vocab_size"] = raw["num_card_types"]
    raw.setdefault("observation_version", environment.identity()["observation_version"])
    values = {key: value for key, value in raw.items() if key in PolicySpec.__dataclass_fields__}
    values.update(overrides)
    return PolicySpec(**values)


def validate_checkpoint_environment(
    model: EntityGRUPolicy, metadata: Mapping[str, Any], environment: Any,
) -> dict[str, Any]:
    """Reject untrained weights and any environment/observation identity drift."""
    if metadata.get("trained") is not True or type(metadata.get("training_steps")) is not int or metadata["training_steps"] <= 0:
        raise ValueError("Evaluation requires trained=True and positive training_steps in checkpoint metadata")
    # Checkpoint metadata has passed through JSON, where tuples become lists.
    identity = json.loads(_json(environment.identity()))
    recorded = metadata.get("environment_identity")
    if not isinstance(recorded, dict) or recorded != identity:
        raise ValueError("Checkpoint environment_identity does not match evaluation environment")
    env_spec = policy_spec_from_environment(environment)
    for key in ("global_dim", "entity_dim", "card_vocab_size", "grid_h", "grid_w",
                "n_card_actions", "entity_x_index", "entity_y_index", "observation_version"):
        if getattr(model.spec, key) != getattr(env_spec, key):
            raise ValueError(f"Checkpoint observation spec mismatch: {key}")
    return identity


def baseline_action(
    name: str, observation: Mapping[str, Any], rng: random.Random,
    *, environment: Any = None, player: int = 0,
) -> dict[str, int]:
    if name == "wait":
        return {"card": 0, "position": 0}
    if name == "scripted":
        implementation = getattr(environment, "scripted_action", None)
        if not callable(implementation):
            raise ValueError("This environment has no declared scripted_action baseline")
        action = implementation(observation, player=player, rng=rng)
        return {"card": int(action["card"]), "position": int(action.get("position", 0))}
    if name != "random":
        raise ValueError(f"Unknown baseline: {name}")
    legal = np.asarray(observation["legal_cards"], dtype=bool).copy()
    positions = np.asarray(observation["legal_positions"], dtype=bool)
    legal[1:5] &= positions.any(axis=-1)
    choices = np.flatnonzero(legal).tolist()
    if not choices:
        raise ValueError("Observation has no legal action, including WAIT")
    card = int(rng.choice(choices))
    position = int(rng.choice(np.flatnonzero(positions[card - 1]).tolist())) if 1 <= card <= 4 else 0
    return {"card": card, "position": position}


def _json_value(value: Any) -> Any:
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"Unsupported report value {type(value).__name__}")


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, default=_json_value)


class _PublicTrace:
    """Size-bounded, lossless sparse visible-entity frames; no raw engine state."""

    def __init__(self, path: Path, maximum_bytes: int):
        self.path = path
        self.stream = gzip.open(path, "wt", encoding="utf-8")
        self.maximum_bytes = maximum_bytes
        self.raw_bytes = 0
        self.frames = 0
        self.limited = False

    def write(self, frame: Mapping[str, Any]) -> None:
        encoded = _json(frame) + "\n"
        size = len(encoded.encode("utf-8"))
        if self.raw_bytes + size > self.maximum_bytes:
            self.limited = True
            return
        self.stream.write(encoded)
        self.raw_bytes += size
        self.frames += 1

    def observation(self, obs: Mapping[str, Any], *, step: int, tick: int,
                    side: int, action: Mapping[str, Any]) -> None:
        visible = np.asarray(obs["entities"])[np.asarray(obs["entity_mask"], dtype=bool)]
        sparse = [{"indices": np.flatnonzero(row).tolist(), "values": row[row != 0].tolist()}
                  for row in visible]
        self.write({
            "kind": "actor_observation", "step": step, "tick": tick, "player": side,
            "global_features": obs["global_features"], "hand_ids": obs["hand_ids"],
            "entity_dim": int(np.asarray(obs["entities"]).shape[-1]),
            "visible_entities_sparse": sparse, "action": dict(action),
        })

    def close(self) -> dict[str, Any]:
        self.stream.close()
        return {"path": str(self.path), "frames": self.frames, "uncompressed_bytes": self.raw_bytes,
                "compressed_bytes": self.path.stat().st_size, "size_limited": self.limited,
                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest()}


def _latencies(values: Sequence[float]) -> dict[str, float | int]:
    if not values:
        return {"count": 0, "mean_ms": 0.0, "p50_ms": 0.0, "p95_ms": 0.0, "maximum_ms": 0.0}
    milliseconds = np.asarray(values) * 1000
    return {"count": len(values), "mean_ms": float(milliseconds.mean()),
            "p50_ms": float(np.percentile(milliseconds, 50)),
            "p95_ms": float(np.percentile(milliseconds, 95)), "maximum_ms": float(milliseconds.max())}


def _wilson(wins: int, games: int) -> list[float] | None:
    if games == 0:
        return None
    z = 1.959963984540054
    p = wins / games
    denominator = 1 + z * z / games
    center = (p + z * z / (2 * games)) / denominator
    half = z * ((p * (1 - p) / games + z * z / (4 * games * games)) ** 0.5) / denominator
    return [max(0.0, center - half), min(1.0, center + half)]


def summarize_episodes(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    natural = [row for row in rows if row["status"] == "natural_end"]
    wins = sum(row["outcome"] == "win" for row in natural)
    losses = sum(row["outcome"] == "loss" for row in natural)
    draws = sum(row["outcome"] == "draw" for row in natural)
    wall = sum(float(row["wall_seconds"]) for row in rows)
    simulated = sum(float(row["simulated_seconds"]) for row in rows)
    decisions = sum(int(row["joint_steps"]) for row in rows)
    denominator = max(wall, 1e-12)
    return {
        "attempted_games": len(rows), "natural_games": len(natural),
        "truncated_games": sum(row["status"] == "truncated" for row in rows),
        "faulted_games": sum(row["status"] == "fault" for row in rows),
        "wins": wins, "losses": losses, "draws": draws,
        "win_rate_natural": wins / len(natural) if natural else None,
        "win_rate_wilson95": _wilson(wins, len(natural)),
        "score_natural": (wins + draws / 2) / len(natural) if natural else None,
        "wall_seconds": wall, "simulated_seconds": simulated,
        "reset_seconds": sum(float(row["reset_seconds"]) for row in rows),
        "model_seconds": sum(float(row["model_seconds"]) for row in rows),
        "policy_seconds": sum(float(row["policy_seconds"]) for row in rows),
        "environment_seconds": sum(float(row["environment_seconds"]) for row in rows),
        "accepted": sum(int(row["accepted"]) for row in rows),
        "refused": sum(int(row["refused"]) for row in rows),
        "executed": sum(int(row["executed"]) for row in rows) if all(row["executed"] is not None for row in rows) else None,
        "executed_known_subtotal": sum(int(row["executed"]) for row in rows if row["executed"] is not None),
        "executed_unknown_games": sum(row["executed"] is None for row in rows),
        "candidate_accepted": sum(int(row["candidate_accepted"]) for row in rows),
        "candidate_refused": sum(int(row["candidate_refused"]) for row in rows),
        "candidate_executed": sum(int(row["candidate_executed"]) for row in rows) if all(row["candidate_executed"] is not None for row in rows) else None,
        "candidate_deploy_attempts": sum(int(row["candidate_deploy_attempts"]) for row in rows),
        "candidate_ability_attempts": sum(int(row["candidate_ability_attempts"]) for row in rows),
        "joint_steps": decisions, "player_decisions": decisions * 2,
        "joint_steps_per_second": decisions / denominator,
        "player_decisions_per_second": 2 * decisions / denominator,
        "real_time_factor": simulated / denominator,
        "natural_games_per_hour": len(natural) * 3600 / denominator,
        "attempted_games_per_hour": len(rows) * 3600 / denominator,
    }


def evaluate_policy(
    checkpoint: str | Path, output_dir: str | Path, *, cases: Sequence[EvaluationCase],
    env_factory: Callable[[], Any],
    variants: Sequence[str] = ("model", "model_without_entities", "random", "wait"),
    deterministic: bool = True, max_decisions: int = 4000, max_wall_seconds: float = 180.0,
    trace_every: int = 20, trace_max_bytes: int = 1_000_000,
) -> dict[str, Any]:
    """Run frozen cases; output includes every fault/truncation and paired score.

    Environment factory must produce the same fixed identity used in training.
    ``cases`` are an independently declared matchup suite, not an adaptive search.
    This function never selects or updates model weights based on its results.
    """
    if not cases or len({case.case_id for case in cases}) != len(cases):
        raise ValueError("Evaluation needs nonempty, uniquely identified cases")
    allowed = {"model", "model_without_entities", "random", "wait", "scripted"}
    if not variants or len(set(variants)) != len(variants) or set(variants) - allowed:
        raise ValueError("Invalid or duplicate policy variants")
    if max_decisions <= 0 or max_wall_seconds <= 0 or trace_every <= 0 or trace_max_bytes <= 0:
        raise ValueError("Evaluation budgets must be positive")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    (output_dir / "traces").mkdir()
    torch.set_num_threads(2)
    started = time.perf_counter()
    model, metadata = load_policy(checkpoint)
    construction_start = time.perf_counter()
    environment = env_factory()
    construction_seconds = time.perf_counter() - construction_start
    rows: list[dict[str, Any]] = []
    try:
        identity = validate_checkpoint_environment(model, metadata, environment)
        plan = {
            "schema": "arena-evaluation-plan-v1", "checkpoint": str(checkpoint),
            "checkpoint_sha256": hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
            "checkpoint_metadata": metadata, "environment_identity": identity,
            "cases": [asdict(case) for case in cases], "variants": list(variants),
            "deterministic": deterministic, "max_decisions": max_decisions,
            "max_wall_seconds": max_wall_seconds, "trace_every": trace_every,
            "trace_max_bytes": trace_max_bytes,
            "score_scope": "simulator-only independent matchup suite; not official ladder evidence",
        }
        (output_dir / "plan.json").write_text(_json(plan) + "\n", encoding="utf-8")
        with (output_dir / "episodes.jsonl").open("w", encoding="utf-8") as episodes_file:
            for variant in variants:
                for case_index, case in enumerate(cases):
                    start = time.perf_counter()
                    trace = _PublicTrace(output_dir / "traces" / f"{variant}-{case_index:04d}.jsonl.gz", trace_max_bytes)
                    row: dict[str, Any] = {
                        "variant": variant, **asdict(case), "status": "fault", "outcome": None,
                        "winner": None, "joint_steps": 0, "simulated_seconds": 0.0,
                        "reset_seconds": 0.0, "policy_seconds": 0.0, "model_seconds": 0.0,
                        "environment_seconds": 0.0, "accepted": 0, "refused": 0, "executed": None,
                        "candidate_accepted": 0, "candidate_refused": 0,
                        "candidate_executed": 0 if identity.get("backend") in {"royalesim", "crforge"} else None,
                        "candidate_waits": 0, "candidate_deploy_attempts": 0, "candidate_ability_attempts": 0,
                    }
                    latencies = []
                    info: Mapping[str, Any] = {}
                    try:
                        reset_start = time.perf_counter()
                        try:
                            observations, info = environment.reset(seed=case.seed, decks=case.decks,
                                                                    **dict(case.reset_options or {}))
                        finally:
                            row["reset_seconds"] = time.perf_counter() - reset_start
                        initial_sim = float(info.get("elapsed_sim_seconds", 0))
                        agent = PolicyAgent(model, deterministic=deterministic, seed=case.seed,
                                            ablate_entities=variant == "model_without_entities")
                        agent.reset()
                        candidate_rng = random.Random(case.seed * 1009 + case.candidate_side)
                        opponent_rng = random.Random(case.seed * 1009 + 1 - case.candidate_side)
                        for decision in range(max_decisions):
                            if time.perf_counter() - start >= max_wall_seconds:
                                row.update(status="truncated", termination_reason="evaluation_wall_budget")
                                break
                            candidate_obs = observations[case.candidate_side]
                            policy_start = time.perf_counter()
                            try:
                                if variant in {"model", "model_without_entities"}:
                                    try:
                                        candidate_action = agent.act(candidate_obs)
                                    finally:
                                        elapsed_model = time.perf_counter() - policy_start
                                        row["model_seconds"] += elapsed_model
                                        latencies.append(elapsed_model)
                                else:
                                    candidate_action = baseline_action(variant, candidate_obs, candidate_rng,
                                                                       environment=environment, player=case.candidate_side)
                                opponent_side = 1 - case.candidate_side
                                opponent_action = baseline_action(case.opponent, observations[opponent_side], opponent_rng,
                                                                  environment=environment, player=opponent_side)
                            finally:
                                row["policy_seconds"] += time.perf_counter() - policy_start
                            card = int(candidate_action["card"])
                            row["candidate_waits"] += int(card == 0)
                            row["candidate_deploy_attempts"] += int(1 <= card <= 4)
                            row["candidate_ability_attempts"] += int(card >= 5)
                            if decision % trace_every == 0:
                                trace.observation(candidate_obs, step=decision, tick=int(info.get("tick", 0)),
                                                  side=case.candidate_side, action=candidate_action)
                            actions = {case.candidate_side: {key: int(candidate_action[key]) for key in ("card", "position")},
                                       opponent_side: opponent_action}
                            env_start = time.perf_counter()
                            try:
                                observations, rewards, terminated, truncated, info = environment.step(actions)
                            finally:
                                row["environment_seconds"] += time.perf_counter() - env_start
                            row["joint_steps"] += 1
                            row["accepted"] += int(info.get("accepted", 0))
                            row["refused"] += int(info.get("refused", 0))
                            executed_total = info.get("executed_total")
                            row["executed"] = int(executed_total) if executed_total is not None else None
                            candidate_results = [result for result in info.get("deploy_results", [])
                                                 if result.get("team") == case.candidate_side]
                            candidate_ok = sum(result.get("status") == 0 for result in candidate_results)
                            row["candidate_accepted"] += candidate_ok
                            row["candidate_refused"] += len(candidate_results) - candidate_ok
                            if identity.get("backend") in {"royalesim", "crforge"}:
                                if identity.get("backend") == "royalesim" and (identity.get("config", {}).get("command_delay_ticks") or 0) == 0:
                                    row["candidate_executed"] += candidate_ok
                                else:
                                    row["candidate_executed"] += sum(len(command) == 5 and command[1] == case.candidate_side
                                                                       and command[-1] == 0 for command in info.get("commands_run", []))
                            row["simulated_seconds"] = max(0.0, float(info.get("elapsed_sim_seconds", initial_sim)) - initial_sim)
                            if terminated:
                                winner = int(info["winner"])
                                if winner not in {0, 1, 2}:
                                    raise ValueError("Natural ending has invalid/missing winner")
                                outcome = "draw" if winner == 2 else "win" if winner == case.candidate_side else "loss"
                                row.update(status="natural_end", termination_reason="engine_game_over", winner=winner, outcome=outcome)
                                break
                            if truncated:
                                row.update(status="truncated", termination_reason="environment_budget")
                                break
                        else:
                            row.update(status="truncated", termination_reason="evaluation_decision_budget")
                        row["final_tick"] = int(info.get("tick", 0))
                        row["final_state_hash"] = info.get("state_hash")
                        row["final_crowns"] = info.get("crowns")
                        row["final_tower_hp"] = info.get("tower_hp")
                        trace.write({"kind": "ending", "status": row["status"], "winner": row["winner"],
                                     "tick": row.get("final_tick"), "state_hash": row.get("final_state_hash")})
                    except Exception as exc:
                        row.update(status="fault", winner=None, outcome=None, termination_reason="exception",
                                   error_type=type(exc).__name__, error=str(exc))
                    finally:
                        row["trace"] = trace.close()
                        row["model_latency"] = _latencies(latencies)
                        row["wall_seconds"] = time.perf_counter() - start
                    rows.append(row)
                    episodes_file.write(_json(row) + "\n")
                    episodes_file.flush()
        by_variant = {variant: summarize_episodes([row for row in rows if row["variant"] == variant]) for variant in variants}
        by_opponent = {
            variant: {opponent: summarize_episodes([row for row in rows if row["variant"] == variant and row["opponent"] == opponent])
                      for opponent in sorted({case.opponent for case in cases})}
            for variant in variants
        }
        paired = []
        full = {row["case_id"]: row for row in rows if row["variant"] == "model"}
        score = {"win": 1.0, "draw": 0.5, "loss": 0.0}
        for variant in variants:
            if variant == "model":
                continue
            comparison = {row["case_id"]: row for row in rows if row["variant"] == variant}
            differences = [score[full[key]["outcome"]] - score[comparison[key]["outcome"]]
                           for key in full.keys() & comparison.keys()
                           if full[key]["status"] == comparison[key]["status"] == "natural_end"]
            paired.append({"reference": "model", "comparison": variant, "valid_pairs": len(differences),
                           "mean_score_difference": float(np.mean(differences)) if differences else None,
                           "positive_pairs": sum(value > 0 for value in differences),
                           "negative_pairs": sum(value < 0 for value in differences),
                           "tied_pairs": sum(value == 0 for value in differences)})
        total_wall = time.perf_counter() - started
        total = summarize_episodes(rows)
        total["whole_run_wall_seconds"] = total_wall
        total["environment_construction_seconds"] = construction_seconds
        total["whole_run_real_time_factor"] = total["simulated_seconds"] / max(total_wall, 1e-12)
        total["whole_run_natural_games_per_hour"] = total["natural_games"] * 3600 / max(total_wall, 1e-12)
        report = {"schema": "arena-evaluation-report-v1", "total": total, "by_variant": by_variant,
                  "by_opponent": by_opponent, "paired_comparisons": paired,
                  "plan_sha256": hashlib.sha256((output_dir / "plan.json").read_bytes()).hexdigest(),
                  "episodes_sha256": hashlib.sha256((output_dir / "episodes.jsonl").read_bytes()).hexdigest(),
                  "scope": plan["score_scope"], "output_dir": str(output_dir)}
        (output_dir / "summary.json").write_text(_json(report) + "\n", encoding="utf-8")
        return report
    finally:
        close = getattr(environment, "close", None)
        if callable(close):
            close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--cases", required=True, help="JSON list of frozen EvaluationCase objects")
    parser.add_argument("--output", required=True)
    parser.add_argument("--variants", nargs="+", default=["model", "model_without_entities", "random", "wait"])
    parser.add_argument("--sample", action="store_true", help="Seeded stochastic decoding instead of argmax")
    parser.add_argument("--max-decisions", type=int, default=4000)
    parser.add_argument("--max-wall-seconds", type=float, default=180)
    parser.add_argument("--trace-every", type=int, default=20)
    parser.add_argument("--trace-max-bytes", type=int, default=1_000_000)
    args = parser.parse_args()
    cases = [EvaluationCase(**row) for row in json.loads(Path(args.cases).read_text(encoding="utf-8"))]
    _, metadata = load_policy(args.checkpoint)
    from .environment import ArenaConfig, ArenaEnv
    settings = metadata.get("environment_identity", {}).get("config")
    if not isinstance(settings, dict):
        raise ValueError("Checkpoint is missing the exact environment construction config")
    report = evaluate_policy(
        args.checkpoint, args.output, cases=cases,
        env_factory=lambda: ArenaEnv(ArenaConfig(**settings)), variants=args.variants,
        deterministic=not args.sample, max_decisions=args.max_decisions,
        max_wall_seconds=args.max_wall_seconds, trace_every=args.trace_every,
        trace_max_bytes=args.trace_max_bytes,
    )
    print(_json(report))


if __name__ == "__main__":
    main()

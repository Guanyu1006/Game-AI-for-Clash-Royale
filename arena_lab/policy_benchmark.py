"""Measure trained-policy inference on recorded real simulator observations.

This separates observation collection, warmup, online batch-one recurrent calls,
and offline batched sequence inference. Closed-loop episode throughput belongs to
``arena_lab.evaluation`` and must not be inferred from cached-observation speed.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import platform
import random
import time
from typing import Any, Callable

import numpy as np
import torch

from .evaluation import baseline_action, validate_checkpoint_environment
from .model import OBSERVATION_KEYS, decode_actions, load_policy, tensor_observation


def _summary(durations: list[float]) -> dict[str, float | int]:
    values = np.asarray(durations, dtype=np.float64)
    return {"calls": len(values), "total_seconds": float(values.sum()),
            "mean_ms": float(values.mean() * 1000),
            "p50_ms": float(np.percentile(values, 50) * 1000),
            "p95_ms": float(np.percentile(values, 95) * 1000),
            "maximum_ms": float(values.max() * 1000)}


def benchmark_policy(
    checkpoint: str | Path, output: str | Path, *, env_factory: Callable[[], Any],
    observation_steps: int = 256, warmup_steps: int = 32, online_passes: int = 3,
    offline_batch: int = 32, offline_sequence: int = 16, offline_iterations: int = 20,
    seed: int = 1729, threads: int = 2,
) -> dict[str, Any]:
    """Use CPU only; validation rejects untrained or incompatible checkpoints."""
    sizes = (observation_steps, warmup_steps, online_passes, offline_batch, offline_sequence, offline_iterations)
    if min(sizes) < 1 or threads not in {1, 2}:
        raise ValueError("Benchmark sizes must be positive; CPU threads must be one or two")
    output = Path(output)
    if output.exists():
        raise FileExistsError("Choose a new benchmark output path")
    torch.set_num_threads(threads)
    model, metadata = load_policy(checkpoint, device="cpu")
    environment = env_factory()
    started = time.perf_counter()
    try:
        identity = validate_checkpoint_environment(model, metadata, environment)
        rng = random.Random(seed)
        observations, info = environment.reset(seed=seed)
        frames, episode_starts = [], []
        natural_ends = truncations = 0
        new_episode = True
        collection_start = time.perf_counter()
        for _ in range(observation_steps):
            frames.append({key: np.array(observations[0][key], copy=True) for key in OBSERVATION_KEYS})
            episode_starts.append(new_episode)
            new_episode = False
            actions = {side: baseline_action("random", observations[side], rng) for side in (0, 1)}
            observations, rewards, terminated, truncated, info = environment.step(actions)
            if terminated or truncated:
                natural_ends += int(terminated)
                truncations += int(truncated)
                observations, info = environment.reset(seed=seed + natural_ends + truncations)
                new_episode = True
        collection_seconds = time.perf_counter() - collection_start
        hidden = None
        warmup_start = time.perf_counter()
        for index in range(warmup_steps):
            position = index % len(frames)
            if episode_starts[position]:
                hidden = None
            _, hidden = model.act(frames[position], hidden)
        online_warmup_seconds = time.perf_counter() - warmup_start
        durations = []
        loop_start = time.perf_counter()
        for _ in range(online_passes):
            hidden = None
            for frame, reset in zip(frames, episode_starts):
                if reset:
                    hidden = None
                call_start = time.perf_counter()
                _, hidden = model.act(frame, hidden)
                durations.append(time.perf_counter() - call_start)
        online_wall = time.perf_counter() - loop_start
        online = _summary(durations)
        online.update({"batch": 1, "sequence_per_call": 1, "passes": online_passes,
                       "wall_seconds": online_wall, "decisions_per_second": len(durations) / online_wall,
                       "includes": ["array-to-tensor conversion", "public observation checks", "recurrent model", "legal-mask decoding"],
                       "excludes": ["environment stepping", "I/O", "actual screenshot perception"],
                       "warmup_calls": warmup_steps, "warmup_seconds": online_warmup_seconds})
        # Preserve frame order within each window, explicitly reset at wrap/game boundaries.
        indices = [[(batch * offline_sequence + tick) % len(frames) for tick in range(offline_sequence)]
                   for batch in range(offline_batch)]
        arrays = {key: np.stack([np.stack([frames[index][key] for index in row]) for row in indices])
                  for key in OBSERVATION_KEYS}
        starts = torch.tensor([[tick == 0 or episode_starts[index] for tick, index in enumerate(row)] for row in indices])
        preparation_start = time.perf_counter()
        tensors = tensor_observation(arrays)
        tensor_preparation_seconds = time.perf_counter() - preparation_start
        tensor_bytes = sum(value.numel() * value.element_size() for value in tensors.values())
        offline_durations = []
        with torch.inference_mode():
            warmup_start = time.perf_counter()
            for _ in range(3):
                decode_actions(model(tensors, episode_starts=starts))
            offline_warmup_seconds = time.perf_counter() - warmup_start
            offline_start = time.perf_counter()
            for _ in range(offline_iterations):
                call_start = time.perf_counter()
                decoded = decode_actions(model(tensors, episode_starts=starts))
                # Materialize a value so the result is exercised even on alternative devices.
                int(decoded["card"][0, 0])
                offline_durations.append(time.perf_counter() - call_start)
            offline_wall = time.perf_counter() - offline_start
        offline = _summary(offline_durations)
        offline.update({"batch": offline_batch, "sequence": offline_sequence, "iterations": offline_iterations,
                        "decisions": offline_batch * offline_sequence * offline_iterations,
                        "wall_seconds": offline_wall,
                        "decisions_per_second": offline_batch * offline_sequence * offline_iterations / offline_wall,
                        "warmup_iterations": 3, "warmup_seconds": offline_warmup_seconds,
                        "tensor_preparation_seconds": tensor_preparation_seconds,
                        "input_tensor_bytes": tensor_bytes,
                        "includes": ["public observation checks", "recurrent sequence model", "legal-mask decoding"],
                        "excludes": ["array stacking", "tensor preparation", "environment stepping", "backpropagation", "optimizer"]})
        report = {
            "schema": "arena-policy-benchmark-v1", "trained": True,
            "checkpoint": str(checkpoint), "checkpoint_sha256": hashlib.sha256(Path(checkpoint).read_bytes()).hexdigest(),
            "training_steps": metadata["training_steps"], "parameters": sum(value.numel() for value in model.parameters()),
            "policy_spec": asdict(model.spec), "environment_identity": identity,
            "hardware": {"device": "cpu", "threads": threads, "machine": platform.machine(),
                         "processor": platform.processor(), "torch": str(torch.__version__), "numpy": np.__version__},
            "observation_collection": {"source": "actual pinned simulator driven by declared legal-random actions; not expert demonstrations",
                                       "frames": len(frames), "seed": seed, "wall_seconds": collection_seconds,
                                       "natural_games": natural_ends, "truncated_games": truncations,
                                       "visible_entities_min": min(int(frame["entity_mask"].sum()) for frame in frames),
                                       "visible_entities_max": max(int(frame["entity_mask"].sum()) for frame in frames)},
            "online_batch_one": online, "offline_sequences": offline,
            "total_wall_seconds": time.perf_counter() - started,
            "interpretation": "Inference throughput on cached real-simulator public observations. Not training throughput, complete-game throughput, live-device latency, or policy strength.",
        }
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
        return report
    finally:
        environment.close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    for name, default in (("observation_steps", 256), ("warmup_steps", 32), ("online_passes", 3),
                          ("offline_batch", 32), ("offline_sequence", 16), ("offline_iterations", 20),
                          ("seed", 1729), ("threads", 2)):
        parser.add_argument("--" + name.replace("_", "-"), type=int, default=default)
    args = vars(parser.parse_args())
    checkpoint, output = args.pop("checkpoint"), args.pop("output")
    _, metadata = load_policy(checkpoint)
    from .environment import ArenaConfig, ArenaEnv
    settings = metadata.get("environment_identity", {}).get("config")
    if not isinstance(settings, dict):
        raise ValueError("Trained checkpoint must include exact environment config")
    report = benchmark_policy(checkpoint, output, env_factory=lambda: ArenaEnv(ArenaConfig(**settings)), **args)
    print(json.dumps({key: report[key] for key in ("checkpoint_sha256", "parameters", "hardware", "online_batch_one", "offline_sequences")}, ensure_ascii=False))


if __name__ == "__main__":
    main()

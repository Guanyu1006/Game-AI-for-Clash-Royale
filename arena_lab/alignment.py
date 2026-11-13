"""Independent state, command-time and screenshot pairing audits.

Simulator repeatability and Android screenshot timing are separate claims. A
stable native clock alone does not prove that rendered pixels are current.
"""
from __future__ import annotations

import argparse
import gzip
import hashlib
import json
from pathlib import Path
import struct
from typing import Any, Mapping

import numpy as np

OBSERVATION_KEYS = ("global_features", "entities", "entity_mask", "hand_ids",
                    "legal_cards", "legal_positions")
TIMING_KEYS = ("observation_tick", "request_tick", "confirmed_tick", "elapsed_ticks", "decision_ticks")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def object_sha256(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def contained_artifact(base: Path, relative: str) -> Path:
    """Manifest artifacts cannot traverse paths or follow outward symlinks."""
    if not isinstance(relative, str) or not relative or Path(relative).is_absolute():
        raise ValueError("Artifact path must be relative to its episode")
    path = (base / relative).resolve()
    if not path.is_relative_to(base.resolve()) or not path.is_file():
        raise ValueError("Artifact path escapes episode or does not exist")
    return path


def observation_sha256(observation: Mapping[str, np.ndarray]) -> str:
    """Hash exactly the numeric actor representation written to training NPZ."""
    digest = hashlib.sha256()
    for key in OBSERVATION_KEYS:
        array = np.asarray(observation[key])
        dtype = np.float16 if key in ("global_features", "entities") else (
            np.int16 if key == "hand_ids" else np.bool_)
        array = np.ascontiguousarray(array, dtype=dtype)
        digest.update(key.encode() + str(array.shape).encode() + array.dtype.str.encode())
        digest.update(array.tobytes())
    return digest.hexdigest()


def audit_timing_arrays(arrays: Mapping[str, np.ndarray], decision_ticks: int) -> dict:
    """Reject labels moved to an earlier observation or invented confirmations."""
    missing = set(TIMING_KEYS) - set(arrays)
    if missing:
        raise ValueError(f"Missing timing fields: {sorted(missing)}")
    tick = arrays["ticks"]
    n = len(tick)
    if not n or decision_ticks < 1:
        raise ValueError("Empty trajectory or invalid decision interval")
    for key in TIMING_KEYS:
        value = arrays[key]
        if value.shape != (n,) or value.dtype.kind not in "iu":
            raise ValueError(f"Timing array must contain aligned integer ticks: {key}")
    if not np.array_equal(tick, arrays["observation_tick"]):
        raise ValueError("Observation is not at the supervised decision tick")
    if not np.all(np.diff(tick) == decision_ticks):
        raise ValueError("Inconsistent fixed control cadence")
    elapsed = np.concatenate(([0], np.diff(tick)))
    if not np.array_equal(elapsed, arrays["elapsed_ticks"]):
        raise ValueError("Elapsed time differs from observation chronology")
    if not np.all(arrays["decision_ticks"] == decision_ticks):
        raise ValueError("Next control window differs from declared cadence")
    play = arrays["target_card"] > 0
    if not np.array_equal(arrays["request_tick"], np.where(play, tick, -1)):
        raise ValueError("Action request is not immediately after its own observation")
    confirmed = arrays["confirmed_tick"]
    if np.any((~play) & (confirmed != -1)) or np.any((confirmed >= 0) & (confirmed < tick)):
        raise ValueError("Impossible action confirmation")
    valid = arrays["label_valid"].astype(bool)
    if np.any(play & valid & (confirmed < 0)):
        raise ValueError("Valid play labels require actual execution confirmation")
    return {"rows": n, "requests": int(play.sum()),
            "confirmed": int((confirmed >= 0).sum()), "decision_ticks": decision_ticks,
            "max_confirmation_delay_ticks": int(np.max(confirmed[confirmed >= 0] - tick[confirmed >= 0]))
            if (confirmed >= 0).any() else None, "passed": True}


def replay_alignment(manifest_path: str | Path, *, verify_arrays: bool = True) -> dict:
    """Reset a fresh engine and compare every saved pre/post state and actor hash."""
    from .environment import ArenaConfig, ArenaEnv

    manifest_path = Path(manifest_path)
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("schema") != "arena-paired-episode-v1":
        raise ValueError("Unsupported paired episode schema")
    base = manifest_path.parent
    trace_path = contained_artifact(base, manifest["trajectory"]["path"])
    if sha256_file(trace_path) != manifest["trajectory"]["sha256"]:
        raise ValueError("Trajectory checksum mismatch")
    for artifact in manifest["actor_sequences"]:
        if sha256_file(contained_artifact(base, artifact["path"])) != artifact["sha256"]:
            raise ValueError("Actor sequence checksum mismatch")
    if sorted(item["seat"] for item in manifest["actor_sequences"]) != [0, 1]:
        raise ValueError("Exactly two distinct actor perspectives required")
    arrays = {}
    if verify_arrays:
        for entry in manifest["actor_sequences"]:
            with np.load(contained_artifact(base, entry["path"]), allow_pickle=False) as archive:
                arrays[entry["seat"]] = {key: archive[key] for key in archive.files}
            data = arrays[entry["seat"]]
            required = set(OBSERVATION_KEYS + TIMING_KEYS + (
                "ticks", "target_card", "target_position", "label_valid", "position_valid"))
            if set(data) != required:
                raise ValueError("Actor archive differs from public observation/timing schema")
            if (entry["steps"] != manifest["joint_decisions"] or
                    any(a.dtype.hasobject or a.ndim < 1 or len(a) != entry["steps"] for a in data.values())):
                raise ValueError("Actor archive length differs from whole battle")
            audit_timing_arrays(data, manifest["decision_ticks"])
    env = ArenaEnv(ArenaConfig(**manifest["environment_config"]))
    try:
        current_identity = env.identity()
        if object_sha256(current_identity) != manifest["environment_identity_sha256"]:
            raise ValueError("Engine/configuration identity mismatch")
        observations, info = env.reset()
        if info["episode_setup"] != manifest["initial_setup"]:
            raise ValueError("Initial hand/seed/setup mismatch")
        matched = steps = confirmations = 0
        first_mismatch = None
        with gzip.open(trace_path, "rt") as stream:
            for index, line in enumerate(stream):
                row = json.loads(line)
                failures = []
                if row["index"] != index or row["observation_tick"] != info["tick"]:
                    failures.append("chronology")
                if row["state_hash_before"] != info["state_hash"]:
                    failures.append("state_hash_before")
                # The raw state is an audit-only target, never fed to the policy.
                import msgspec
                if object_sha256(msgspec.to_builtins(env.state)) != row["raw_state_sha256"]:
                    failures.append("raw_state")
                if object_sha256(row["raw_state"]) != row["raw_state_sha256"]:
                    failures.append("raw_state_payload")
                for seat in (0, 1):
                    observed_hash = observation_sha256(observations[seat])
                    if observed_hash != row["actor_sha256"][str(seat)]:
                        failures.append(f"actor_{seat}")
                    if verify_arrays:
                        saved = {key: arrays[seat][key][index] for key in OBSERVATION_KEYS}
                        if observation_sha256(saved) != observed_hash:
                            failures.append(f"npz_actor_{seat}")
                        action = row["actions"][str(seat)]
                        if (int(arrays[seat]["target_card"][index]) != action["card"] or
                                int(arrays[seat]["target_position"][index]) != action["position"]):
                            failures.append(f"npz_action_{seat}")
                actions = {int(seat): action for seat, action in row["actions"].items()}
                observations, _, _, _, info = env.joint_step(actions, ticks=row["decision_ticks"])
                if row["state_hash_after"] != info["state_hash"]:
                    failures.append("state_hash_after")
                if row["feedback"] != info["deploy_results"]:
                    failures.append("execution_feedback")
                if row["next_tick"] != info["tick"]:
                    failures.append("next_tick")
                confirmations += info["accepted"]
                if verify_arrays:
                    by_seat = {entry["team"]: entry for entry in info["deploy_results"]}
                    for seat in (0, 1):
                        feedback = by_seat.get(seat)
                        tick = feedback["tick"] if feedback and feedback["status"] == 0 else -1
                        if arrays[seat]["confirmed_tick"][index] != tick:
                            failures.append(f"npz_confirm_{seat}")
                        card = actions[seat]["card"]
                        valid = card == 0 or tick >= 0
                        if (bool(arrays[seat]["label_valid"][index]) != valid or
                                bool(arrays[seat]["position_valid"][index]) != (valid and 1 <= card <= 4)):
                            failures.append(f"npz_supervision_validity_{seat}")
                steps += 1
                if not failures:
                    matched += 1
                elif first_mismatch is None:
                    first_mismatch = {"index": index, "fields": failures}
        expected_steps = manifest["joint_decisions"]
        if steps != expected_steps:
            raise ValueError("Trace length differs from manifest")
        terminal_matches = (info["tick"] == manifest["terminal"]["tick"] and
                            info["winner"] == manifest["terminal"]["winner"] and
                            info["terminated"] == manifest["terminal"]["natural"])
        return {"schema": "arena-state-alignment-v1", "game_id": manifest["game_id"],
                "manifest_sha256": sha256_file(manifest_path), "decisions_checked": steps,
                "decisions_matched": matched, "confirmed_actions": confirmations,
                "first_mismatch": first_mismatch, "terminal_matches": terminal_matches,
                "same_engine_replay_passed": matched == steps and terminal_matches,
                "official_client_equivalence_verified": False,
                "human_expert_demonstration": False,
                "scope": "same-version self-collected simulator trajectories"}
    finally:
        env.close()


def audit_capture_pairs(records: list[dict], root: str | Path, expected_version: str) -> dict:
    """Verify independently saved state/PNG identities and clock brackets.

    Rendering freshness is a distinct gate requiring an externally measured
    rendered_state_tick and a bounded display latency, not a same-clock guess.
    """
    root = Path(root).resolve()
    failures, temporal, rendered = [], 0, 0
    for index, row in enumerate(records):
        reasons = []
        contents = {}
        for kind in ("frame", "state"):
            path = (root / row[f"{kind}_path"]).resolve()
            if not path.is_relative_to(root) or not path.is_file():
                reasons.append(f"{kind}_path")
            elif sha256_file(path) != row[f"{kind}_sha256"]:
                reasons.append(f"{kind}_hash")
            else:
                contents[kind] = path
        before, after = row["state_tick_before"], row["state_tick_after"]
        if not isinstance(before, int) or before < 0 or before != after:
            reasons.append("unstable_state_tick")
        if row["generation_before"] != row["generation_after"]:
            reasons.append("generation_changed")
        if row["version_fingerprint"] != expected_version:
            reasons.append("version_mismatch")
        if row["capture_started_ns"] > row["capture_finished_ns"]:
            reasons.append("wall_clock_order")
        if "frame" in contents:
            with contents["frame"].open("rb") as stream:
                header = stream.read(24)
            if (len(header) != 24 or header[:8] != b"\x89PNG\r\n\x1a\n" or
                    header[12:16] != b"IHDR" or min(struct.unpack(">II", header[16:24])) < 1):
                reasons.append("invalid_png_header")
        if "state" in contents:
            try:
                state = json.loads(contents["state"].read_text())
                state = state.get("raw_capture", state)
                if state.get("tick") != before:
                    reasons.append("state_payload_tick")
                if state.get("generation", row["generation_before"]) != row["generation_before"]:
                    reasons.append("state_payload_generation")
            except (ValueError, AttributeError):
                reasons.append("invalid_state_json")
        if not reasons:
            temporal += 1
            if (row.get("rendered_state_tick") == before and
                    isinstance(row.get("render_latency_bound_ms"), (int, float)) and
                    0 <= row["render_latency_bound_ms"] < float("inf")):
                rendered += 1
        else:
            failures.append({"index": index, "reasons": reasons})
    return {"schema": "arena-capture-alignment-v1", "frames": len(records),
            "temporal_pairs_verified": temporal, "rendered_pairs_verified": rendered,
            "failures": failures, "temporal_alignment_passed": bool(records) and temporal == len(records),
            "render_alignment_passed": bool(records) and rendered == len(records),
            "note": "Stable native clock does not establish pixel freshness or detector accuracy."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = replay_alignment(args.manifest)
    if args.output.exists():
        raise FileExistsError(args.output)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result))


if __name__ == "__main__":
    main()

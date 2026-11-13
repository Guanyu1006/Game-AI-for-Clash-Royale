"""Paired offline comparison, with uncertainty resampled by original battle."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path

import numpy as np


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _metrics(record: dict[str, np.ndarray], indices: np.ndarray) -> dict[str, float | None]:
    y = record["event"][indices].astype(bool)
    pred = record["prediction"][indices] > 0
    pv = record["position_valid"][indices].astype(bool)
    probability = np.clip(record["probability"][indices].astype(np.float64), 1e-8, 1-1e-8)
    divide = lambda a, b: float(a/b) if b else None
    return {"event_precision": divide((y & pred).sum(), pred.sum()),
            "event_recall": divide((y & pred).sum(), y.sum()),
            "event_nll": float(-(y*np.log(probability)+(~y)*np.log1p(-probability)).mean()),
            "conditional_card_accuracy": divide((record["true_card_choice"][indices][y] == record["card"][indices][y]).sum(), y.sum()),
            "conditional_location_accuracy": divide((record["true_card_position"][indices][pv] == record["position"][indices][pv]).sum(), pv.sum()),
            "joint_action_accuracy": divide(((record["prediction"][indices][pv] == record["card"][indices][pv]) &
                                               (record["pred_position"][indices][pv] == record["position"][indices][pv])).sum(), pv.sum())}


def compare_timing(plan_path: str | Path, candidate: str | Path, reference: str | Path,
                   output: str | Path) -> dict:
    plan_path, candidate, reference, output = map(Path, (plan_path, candidate, reference, output))
    if output.exists():
        raise FileExistsError("Preserve previous comparisons; choose a new output")
    plan = json.loads(plan_path.read_text())
    reports = [json.loads((path / "report.json").read_text()) for path in (candidate, reference)]
    records = []
    for path, report, expected in zip((candidate, reference), reports, (plan["timing"], plan["joint_baseline"])):
        metadata = report["checkpoint"]["metadata"]
        if metadata["dataset_index_sha256"] != plan["dataset_index_sha256"]:
            raise ValueError("Dataset differs from frozen comparison plan")
        if metadata["train_config"] != expected or report["development_splits"] != plan["splits"]:
            raise ValueError("Training recipe or whole-battle split drift")
        checkpoint = Path(report["checkpoint"]["path"])
        if _sha(checkpoint) != report["checkpoint"]["sha256"]:
            raise ValueError("Checkpoint hash drift")
        frozen = json.loads((path / "frozen-before-test.json").read_text())
        if (frozen["checkpoint_sha256"] != report["checkpoint"]["sha256"]
                or frozen["dataset_index_sha256"] != plan["dataset_index_sha256"]
                or frozen["test_game_ids"] != plan["splits"]["test"]
                or frozen["tolerance_ticks"] != expected["tolerance_ticks"]):
            raise ValueError("Final checkpoint or holdout differs from the pre-test freeze")
        with np.load(path / "test-predictions.npz", allow_pickle=False) as data:
            records.append({name: data[name] for name in data.files})
    for key in ("card", "position", "event", "eligible", "position_valid", "ticks", "trajectory"):
        if not np.array_equal(records[0][key], records[1][key]):
            raise ValueError(f"Prediction records do not refer to identical held-out observations: {key}")
    index_path = Path(reports[0]["checkpoint"]["metadata"]["dataset_index"])
    if _sha(index_path) != plan["dataset_index_sha256"]:
        raise ValueError("Source index changed after freeze")
    index = json.loads(index_path.read_text())
    positions = int(index["policy_spec"]["grid_w"]) * int(index["policy_spec"]["grid_h"])
    test_rows = [row for row in index["games"] if row["split"] == "test"]
    # Recover true card identity from the frozen source hand, not from a model
    # prediction. This also binds saved label/tick arrays to their original NPZ.
    card_ids = []
    source_records = {key: [] for key in ("card", "position", "event", "eligible",
                                         "position_valid", "ticks", "trajectory")}
    for trajectory, row in enumerate(test_rows):
        source = (index_path.parent / row["path"]).resolve()
        if _sha(source) != row["sha256"]:
            raise ValueError("Original held-out source file hash drift")
        with np.load(source, allow_pickle=False) as archive:
            valid = archive["label_valid"].astype(bool)
            card = archive["target_card"][valid]
            hand = archive["hand_ids"][valid]
            ids = hand[np.arange(len(card)), (card-1).clip(0,3)]
            card_ids.append(np.where((card >= 1) & (card <= 4), ids, -1))
            legal = archive["legal_cards"][valid].astype(bool)
            legal[:, 1:5] &= archive["legal_positions"][valid].any(-1)
            fields = {"card": card, "position": archive["target_position"][valid].clip(0, positions-1),
                      "event": card > 0, "eligible": legal[:, 1:].any(-1),
                      "position_valid": archive["position_valid"][valid].astype(bool) & (card > 0) & (card <= 4),
                      "ticks": archive["ticks"][valid],
                      "trajectory": np.full(len(card), trajectory, dtype=np.int64)}
            for key, values in fields.items():
                source_records[key].append(values)
    card_ids = np.concatenate(card_ids)
    for key, pieces in source_records.items():
        if not np.array_equal(records[0][key], np.concatenate(pieces)):
            raise ValueError(f"Prediction labels/times/identity differ from frozen original source: {key}")
    trajectory_games = np.asarray([row["game_id"] for row in test_rows])
    trajectories = records[0]["trajectory"]
    if ((trajectories < 0) | (trajectories >= len(test_rows))).any():
        raise ValueError("Prediction trajectory identity outside test manifest")
    sample_games = trajectory_games[trajectories]
    games = np.unique(sample_games)
    if set(games) != set(plan["splits"]["test"]):
        raise ValueError("Test games differ from frozen holdout")
    blocks = [np.flatnonzero(sample_games == game) for game in games]
    all_indices = np.arange(len(trajectories))
    point = [_metrics(record, all_indices) for record in records]
    bootstrap = {key: [] for key in point[0]}
    rng = np.random.default_rng(plan["uncertainty"]["seed"])
    repetitions = int(plan["uncertainty"]["bootstrap_repetitions"])
    for _ in range(repetitions):
        sampled = np.concatenate([blocks[int(i)] for i in rng.integers(0, len(blocks), len(blocks))])
        a, b = (_metrics(record, sampled) for record in records)
        for key in bootstrap:
            if a[key] is not None and b[key] is not None:
                bootstrap[key].append(a[key]-b[key])
    comparisons = {}
    for key, samples in bootstrap.items():
        comparisons[key] = {"candidate": point[0][key], "reference": point[1][key],
                            "difference": point[0][key]-point[1][key] if point[0][key] is not None and point[1][key] is not None else None,
                            "paired_battle_bootstrap_95_percent_interval": np.quantile(samples, [.025,.975]).tolist() if samples else None,
                            "defined_bootstrap_repetitions": len(samples), "lower_is_better": key.endswith("nll")}
    vocabulary = index.get("environment_identity", {}).get("cards", [])
    per_card = []
    for card_id in sorted(set(card_ids[card_ids > 0])):
        selected = np.flatnonzero(card_ids == card_id)
        entry = {"card_id": int(card_id), "card": vocabulary[card_id-1] if card_id <= len(vocabulary) else f"card-index-{card_id}",
                 "test_events": len(selected), "limited_test_support": len(selected) < 20}
        for name, record in zip(("candidate", "reference"), records):
            metrics = _metrics(record, selected)
            entry[name] = {key: metrics[key] for key in ("event_recall", "conditional_card_accuracy", "conditional_location_accuracy", "joint_action_accuracy")}
        per_card.append(entry)
    result = {"schema": "arena-causal-timing-comparison-v1", "plan_sha256": _sha(plan_path),
              "dataset_index_sha256": plan["dataset_index_sha256"], "battles": len(games),
              "teacher_perspectives": len(test_rows), "labels": len(trajectories),
              "uncertainty": dict(plan["uncertainty"], method="paired percentile bootstrap, both seats sampled together"),
              "candidate_checkpoint_sha256": reports[0]["checkpoint"]["sha256"],
              "reference_checkpoint_sha256": reports[1]["checkpoint"]["sha256"],
              "predictions_sha256": {"candidate": _sha(candidate/"test-predictions.npz"), "reference": _sha(reference/"test-predictions.npz")},
              "comparisons": comparisons,
              "per_true_deployment_card": per_card,
              "scope": "Held-out rule-teacher imitation on same-version states; not a human-expert or live-game strength claim."}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n")
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    for key in ("plan", "candidate", "reference", "output"):
        parser.add_argument("--"+key, required=True)
    args = parser.parse_args()
    print(json.dumps(compare_timing(args.plan,args.candidate,args.reference,args.output),ensure_ascii=False,indent=2))


if __name__ == "__main__":
    main()

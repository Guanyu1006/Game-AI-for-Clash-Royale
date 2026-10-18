"""Reproduce the NumPy BC optimization from sanitized numeric arrays, offline.

This does not reconstruct battles or replace the full quality-gated exporter.
It uses the project's actual forward/loss/gradient implementation.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
os.environ.setdefault("OPENBLAS_NUM_THREADS", "1")
os.environ.setdefault("OMP_NUM_THREADS", "1")


def tensor_digest(parameters):
    h = hashlib.sha256()
    for key in sorted(parameters):
        value = parameters[key]
        h.update(key.encode())
        h.update(str(value.shape).encode())
        h.update(str(value.dtype).encode())
        h.update(value.tobytes())
    return h.hexdigest()


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--epochs", type=int, default=20)
    p.add_argument("--output", default="friend_runs/bc_reproduction")
    a = p.parse_args()
    if not 1 <= a.epochs <= 1000:
        p.error("epochs must be 1..1000")
    output = (ROOT / a.output).resolve()
    output.relative_to(ROOT)
    if output.exists():
        p.error("Use a new output directory; existing results are never replaced")
    from VERIFY_PACKAGE import verify
    verify()
    import numpy as np
    from royale_phase2.learning import init_parameters, loss_and_gradients, forward
    meta = json.loads((ROOT / "models/friend_bc/checkpoint.json").read_text(encoding="utf-8"))
    cfg = meta["training_configuration"]
    with np.load(ROOT / "data/friend_numeric/training_arrays.npz", allow_pickle=False) as archive:
        arrays = {split: tuple(archive[split + "_" + name] for name in ("x", "target", "xy", "masks"))
                  for split in ("train", "validation", "test")}
    parameters = init_parameters(arrays["train"][0].shape[1], meta["architecture"]["hidden"], cfg["seed"])
    weights = np.asarray(cfg["class_weights"], dtype=np.float32)
    rng = np.random.default_rng(cfg["seed"])
    moment = {k: np.zeros_like(v) for k, v in parameters.items()}
    variance = {k: np.zeros_like(v) for k, v in parameters.items()}
    x, target, xy, masks = arrays["train"]
    curve = []
    step = 0
    best_loss = float("inf")
    best_epoch = None
    best = None
    for epoch in range(1, a.epochs + 1):
        order = rng.permutation(len(x))
        total = 0.
        for start in range(0, len(order), cfg["batch_size"]):
            selected = order[start:start + cfg["batch_size"]]
            loss, gradients = loss_and_gradients(parameters, x[selected], target[selected],
                                                xy[selected], masks[selected], class_weights=weights)
            total += loss * len(selected)
            step += 1
            norm = sum(float(np.square(v).sum()) for v in gradients.values()) ** .5
            scale = min(1., 5 / max(norm, 1e-12))
            for key in parameters:
                gradient = gradients[key] * scale
                moment[key] = .9 * moment[key] + .1 * gradient
                variance[key] = .999 * variance[key] + .001 * gradient * gradient
                parameters[key] -= cfg["learning_rate"] * (moment[key] / (1 - .9 ** step)) / (
                    np.sqrt(variance[key] / (1 - .999 ** step)) + 1e-8)
        validation_loss, _ = loss_and_gradients(parameters, *arrays["validation"], class_weights=weights)
        curve.append({"epoch": epoch, "train_loss": total / len(x), "validation_loss": validation_loss})
        if validation_loss < best_loss:
            best_loss = validation_loss
            best_epoch = epoch
            best = {k: v.copy() for k, v in parameters.items()}
        print(json.dumps(curve[-1]), flush=True)
    with np.load(ROOT / "models/friend_bc/weights.npz", allow_pickle=False) as archive:
        original = {k: archive[k] for k in archive.files}
    metrics = {}
    for split, (sx, sy, sxy, sm) in arrays.items():
        _, probability, _ = forward(best, sx, sm)
        predicted = probability.argmax(axis=1)
        play = sy != 0
        metrics[split] = {"samples": len(sy), "play_labels": int(play.sum()),
                          "wait_labels": int((~play).sum()),
                          "unbiased_slot_accuracy": float((predicted == sy).mean()),
                          "unbiased_play_recall": float(((predicted != 0) & play).sum() / max(1, play.sum()))}
    report = {"schema": "friend-numeric-bc-reproduction.v1", "epochs_run": a.epochs,
              "selected_epoch": best_epoch, "validation_only_selection": True,
              "parameters_sha256": tensor_digest(best),
              "original_parameters_sha256": tensor_digest(original),
              "matches_saved_parameters": all(np.array_equal(best[k], original[k]) for k in best),
              "max_abs_parameter_difference": max(float(np.abs(best[k] - original[k]).max()) for k in best),
              "numpy": np.__version__, "python": sys.version,
              "metrics": metrics, "curve": curve,
              "scope": "Numeric BC reproduction from saved public actor features/recorded masks. "
                       "No game assets, raw replay, native reconstruction, policy-strength "
                       "or full legality revalidation is claimed. New outputs are not "
                       "a metadata-complete deployable native checkpoint."}
    output.mkdir(parents=True)
    np.savez(output / "weights.npz", **best)
    (output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({k: v for k, v in report.items() if k != "curve"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

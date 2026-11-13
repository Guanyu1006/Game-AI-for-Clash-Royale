"""Audited whole-battle data, chronological TBPTT and held-out evaluation.

No dataset download, engine construction or test-set selection occurs here.
NPZ files contain numeric arrays only and are always opened without pickle.
"""
from __future__ import annotations

import argparse
from collections import OrderedDict
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import random
import time
from typing import Any

import numpy as np
import torch
from torch.nn import functional as F

from .model import (EntityGRUPolicy, OBSERVATION_KEYS, PolicySpec, decode_actions,
                    load_policy, save_policy, tensor_observation)

ROOT = Path(__file__).resolve().parents[1]
LABEL_KEYS = ("target_card", "target_position", "label_valid", "position_valid", "ticks")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


class BattleDataset:
    """Validate checksums, split boundaries and every target before training."""

    def __init__(self, index: str | Path, cache_games: int = 4):
        self.path = Path(index).resolve()
        self.index_sha256 = sha256(self.path)
        self.index = json.loads(self.path.read_text())
        if self.index.get("schema_version") != 1:
            raise ValueError("Unsupported dataset index schema")
        self.spec = PolicySpec(**self.index["policy_spec"])
        if self.index.get("observation_version", self.spec.observation_version) != self.spec.observation_version:
            raise ValueError("Dataset observation version mismatch")
        self.games = self.index["games"]
        if not self.games:
            raise ValueError("Empty dataset")
        self.cache_games = max(1, cache_games)
        self.cache: OrderedDict[str, dict[str, np.ndarray]] = OrderedDict()
        self.splits: dict[str, list[dict]] = {key: [] for key in ("train", "validation", "test")}
        battles, sources, paths, hashes = {}, {}, set(), {}
        for row in self.games:
            split = row["split"]
            if split not in self.splits:
                raise ValueError(f"Unknown split: {split}")
            game_id = row["game_id"]
            if game_id in battles and battles[game_id] != split:
                raise ValueError("Same battle appears in multiple splits")
            battles[game_id] = split
            for key in ("source_game_id", "source_sha256"):
                source = row.get(key)
                if source:
                    identity = (key, source)
                    if identity in sources and sources[identity] != split:
                        raise ValueError("Same original source appears in multiple splits")
                    sources[identity] = split
            path = (self.path.parent / row["path"]).resolve()
            if not path.is_relative_to(ROOT):
                raise ValueError("Dataset artifacts must stay inside the repository")
            if path in paths:
                raise ValueError("Repeated dataset file")
            paths.add(path)
            row = dict(row, resolved_path=path)
            digest = sha256(path)
            if digest != row["sha256"]:
                raise ValueError(f"Dataset checksum mismatch: {path}")
            if digest in hashes and hashes[digest] != split:
                raise ValueError("Duplicate observations cross split boundary")
            hashes[digest] = split
            self.splits[split].append(row)
        self.audit: dict[str, Any] = {}
        self.entity_capacity = None
        for split, rows in self.splits.items():
            counts = {"files": len(rows), "battles": len({r["game_id"] for r in rows}),
                      "steps": 0, "valid_labels": 0, "play_labels": 0, "ability_labels": 0, "position_labels": 0,
                      "visible_entity_rows": 0}
            for row in rows:
                data = self.load(row)
                self._validate(data, row)
                valid = data["label_valid"].astype(bool)
                counts["steps"] += len(valid)
                counts["valid_labels"] += int(valid.sum())
                counts["play_labels"] += int((valid & (data["target_card"] > 0)).sum())
                counts["ability_labels"] += int((valid & (data["target_card"] > 4)).sum())
                counts["position_labels"] += int((valid & data["position_valid"].astype(bool)).sum())
                counts["visible_entity_rows"] += int(data["entity_mask"].any(-1).sum())
            self.audit[split] = counts

    def load(self, row: dict) -> dict[str, np.ndarray]:
        key = str(row["resolved_path"])
        if key in self.cache:
            self.cache.move_to_end(key)
            return self.cache[key]
        with np.load(key, allow_pickle=False) as archive:
            missing = set(OBSERVATION_KEYS + LABEL_KEYS) - set(archive.files)
            if missing:
                raise ValueError(f"Missing NPZ fields: {sorted(missing)}")
            data = {name: archive[name] for name in OBSERVATION_KEYS + LABEL_KEYS}
        self.cache[key] = data
        while len(self.cache) > self.cache_games:
            self.cache.popitem(last=False)
        return data

    def _validate(self, data: dict, row: dict) -> None:
        t = len(data["target_card"])
        if t <= 0 or t != row["steps"]:
            raise ValueError("Dataset step count mismatch")
        if any(len(value) != t or value.dtype.kind == "O" for value in data.values()):
            raise ValueError("Dataset arrays must be aligned numeric arrays")
        if not np.all(np.diff(data["ticks"]) > 0):
            raise ValueError("Ticks must strictly increase within a player trajectory")
        for key in LABEL_KEYS:
            if data[key].shape != (t,):
                raise ValueError(f"{key} must be one dimensional")
        if data["global_features"].shape != (t, self.spec.global_dim):
            raise ValueError("Global feature schema mismatch")
        if data["entities"].ndim != 3 or data["entities"].shape[-1] != self.spec.entity_dim:
            raise ValueError("Entity feature schema mismatch")
        if self.entity_capacity is None:
            self.entity_capacity = data["entities"].shape[1]
        if data["entities"].shape[1] != self.entity_capacity:
            raise ValueError("Entity capacity must be identical across player trajectories")
        if data["entity_mask"].shape != data["entities"].shape[:-1]:
            raise ValueError("Entity mask shape mismatch")
        for key, shape in {"hand_ids": (t, 4), "legal_cards": (t, self.spec.n_card_actions),
                           "legal_positions": (t, 4, self.spec.positions)}.items():
            if data[key].shape != shape:
                raise ValueError(f"{key} shape mismatch")
        for key in ("entity_mask", "legal_cards", "legal_positions", "label_valid", "position_valid"):
            if not np.all((data[key] == 0) | (data[key] == 1)):
                raise ValueError(f"Nonboolean mask: {key}")
        if not np.isfinite(data["global_features"]).all() or not np.isfinite(data["entities"][data["entity_mask"].astype(bool)]).all():
            raise ValueError("Nonfinite observation")
        xy = data["entities"][..., [self.spec.entity_x_index, self.spec.entity_y_index]]
        xy = xy[data["entity_mask"].astype(bool)]
        if ((xy < 0) | (xy > 1)).any():
            raise ValueError("Entity coordinates outside public normalized grid")
        hand = data["hand_ids"]
        if hand.dtype.kind not in "iu" or ((hand < 0) | (hand >= self.spec.card_vocab_size)).any():
            raise ValueError("Invalid card vocabulary ID")
        valid = data["label_valid"].astype(bool)
        card, position = data["target_card"], data["target_position"]
        if card.dtype.kind not in "iu" or position.dtype.kind not in "iu":
            raise ValueError("Targets must be integer arrays")
        if ((card[valid] < 0) | (card[valid] >= self.spec.n_card_actions)).any():
            raise ValueError("Invalid target hand slot")
        if not data["legal_cards"][:, 0].all():
            raise ValueError("WAIT must always be legal")
        rows = np.flatnonzero(valid)
        if not data["legal_cards"][rows, card[rows]].all():
            raise ValueError("Supervision targets a forbidden card")
        plays = np.flatnonzero(valid & (card > 0) & (card <= 4))
        if not data["legal_positions"][plays, card[plays] - 1].any(-1).all():
            raise ValueError("Play target has no legal positions")
        position_valid = data["position_valid"].astype(bool)
        if (position_valid & (~valid | (card == 0) | (card > 4))).any():
            raise ValueError("Position supervision requires a valid play")
        rows = np.flatnonzero(position_valid)
        if ((position[rows] < 0) | (position[rows] >= self.spec.positions)).any():
            raise ValueError("Position target outside grid")
        if not data["legal_positions"][rows, card[rows] - 1, position[rows]].all():
            raise ValueError("Supervision targets a forbidden location")


def stream_batches(dataset: BattleDataset, rows: list[dict], *, batch_size: int, sequence: int,
                   trim_unsupervised_suffix: bool = False):
    """Each batch lane follows complete games; no chunks cross game boundaries."""
    pending = iter(rows)
    active = [None] * batch_size
    positions = [0] * batch_size
    while True:
        starts = np.zeros(batch_size, dtype=bool)
        for lane in range(batch_size):
            if active[lane] is None:
                while active[lane] is None:
                    row = next(pending, None)
                    if row is None:
                        break
                    data = dataset.load(row)
                    if trim_unsupervised_suffix:
                        valid = np.flatnonzero(data["label_valid"])
                        if not len(valid):
                            continue
                        # Keep every preceding observation, including gaps, so
                        # temporal context is unchanged for every scored label.
                        data = {key: value[:valid[-1]+1] for key, value in data.items()}
                    active[lane] = data
                    positions[lane] = 0
                    starts[lane] = True
        first = next((item for item in active if item is not None), None)
        if first is None:
            return
        # Padding has legal WAIT and no loss. Its memory is discarded at reset.
        arrays = {name: np.zeros((batch_size, sequence, *value.shape[1:]), dtype=value.dtype)
                  for name, value in first.items()}
        arrays["legal_cards"][:, :, 0] = True
        for lane, data in enumerate(active):
            if data is None:
                continue
            start = positions[lane]
            end = min(len(data["ticks"]), start + sequence)
            for name in arrays:
                arrays[name][lane, :end-start] = data[name][start:end]
            positions[lane] = end
            if end == len(data["ticks"]):
                active[lane] = None
        yield arrays, starts


def losses(output: dict, batch: dict, play_weight: float = 1.0):
    valid = torch.as_tensor(batch["label_valid"], dtype=torch.bool)
    card = torch.as_tensor(batch["target_card"], dtype=torch.long)
    position = torch.as_tensor(batch["target_position"], dtype=torch.long)
    positions = torch.as_tensor(batch["position_valid"], dtype=torch.bool) & valid & (card > 0) & (card <= 4)
    if valid.any():
        ce = F.cross_entropy(output["card_logits"][valid], card[valid], reduction="none")
        weight = torch.where(card[valid] > 0, play_weight, 1.0)
        card_loss = (ce * weight).sum() / weight.sum()
    else:
        card_loss = output["hidden"].sum() * 0
    if positions.any():
        logits = output["position_logits"][positions]
        slots = card[positions] - 1
        pos_loss = F.cross_entropy(logits[torch.arange(len(slots)), slots], position[positions])
    else:
        pos_loss = output["hidden"].sum() * 0
    return card_loss, pos_loss


@torch.inference_mode()
def evaluate_offline(model: EntityGRUPolicy, dataset: BattleDataset, split: str,
                     sequence: int = 64, ablate_entities: bool = False) -> dict:
    model.eval()
    count = play_count = predicted_play = true_positive = slot_correct = pos_count = pos_correct = joint_correct = 0
    nll_sum = pos_nll_sum = distance_sum = 0.0
    confusion = np.zeros((model.spec.n_card_actions, model.spec.n_card_actions), dtype=np.int64)
    started = time.perf_counter()
    hidden = None
    for batch, starts in stream_batches(dataset, dataset.splits[split], batch_size=1, sequence=sequence,
                                       trim_unsupervised_suffix=True):
        if hidden is not None:
            hidden = hidden * torch.as_tensor(~starts)[None, :, None]
        out = model(tensor_observation(batch), hidden, ablate_entities=ablate_entities)
        hidden = out["hidden"]
        decoded = decode_actions(out)
        valid = torch.as_tensor(batch["label_valid"], dtype=torch.bool)
        card = torch.as_tensor(batch["target_card"], dtype=torch.long)
        pos = torch.as_tensor(batch["target_position"], dtype=torch.long)
        prediction = decoded["card"]
        real_play, pred_play = (card > 0) & valid, (prediction > 0) & valid
        n = int(valid.sum())
        count += n
        play_count += int(real_play.sum())
        predicted_play += int(pred_play.sum())
        true_positive += int((real_play & pred_play).sum())
        slot_correct += int(((prediction == card) & real_play).sum())
        if n:
            nll_sum += float(F.cross_entropy(out["card_logits"][valid], card[valid], reduction="sum"))
            np.add.at(confusion, (card[valid].numpy(), prediction[valid].numpy()), 1)
        pv = torch.as_tensor(batch["position_valid"], dtype=torch.bool) & real_play & (card <= 4)
        npos = int(pv.sum())
        if npos:
            logits = out["position_logits"][pv][torch.arange(npos), card[pv]-1]
            chosen = logits.argmax(-1)
            pos_count += npos
            pos_correct += int((chosen == pos[pv]).sum())
            pos_nll_sum += float(F.cross_entropy(logits, pos[pv], reduction="sum"))
            width = model.spec.grid_w
            delta = torch.stack(((chosen % width - pos[pv] % width).float(),
                                 (chosen // width - pos[pv] // width).float()), -1)
            distance_sum += float(delta.norm(dim=-1).sum())
            joint_correct += int(((prediction[pv] == card[pv]) & (decoded["position"][pv] == pos[pv])).sum())
    ratio = lambda a, b: a / b if b else None
    return {"split": split, "ablate_entities": ablate_entities, "labels": count,
            "play_labels": play_count, "predicted_plays": predicted_play,
            "position_labels": pos_count, "card_nll": ratio(nll_sum, count),
            "position_nll": ratio(pos_nll_sum, pos_count),
            "card_accuracy": ratio(int(confusion.trace()), count),
            "all_wait_accuracy": ratio(count-play_count, count),
            "play_precision": ratio(true_positive, predicted_play), "play_recall": ratio(true_positive, play_count),
            "play_slot_accuracy": ratio(slot_correct, play_count),
            "position_accuracy_given_true_card": ratio(pos_correct, pos_count),
            "position_mean_tile_distance_given_true_card": ratio(distance_sum, pos_count),
            "joint_play_exact_accuracy": ratio(joint_correct, pos_count),
            "metric_definition": "play/action = any non-WAIT including ability; location metrics use deployments 1..4 only",
            "confusion_true_rows_pred_columns": confusion.tolist(),
            "wall_seconds": time.perf_counter()-started}


@dataclass(frozen=True)
class TrainConfig:
    epochs: int = 4
    batch_size: int = 4
    sequence: int = 32
    learning_rate: float = 0.0003
    weight_decay: float = 0.0001
    position_loss_weight: float = 1.0
    play_weight_cap: float = 8.0
    seed: int = 1729
    threads: int = 2


def train(index: str | Path, output: str | Path, config: TrainConfig) -> dict:
    if min(config.epochs, config.batch_size, config.sequence, config.threads) < 1:
        raise ValueError("Epochs/batch/sequence/threads must be positive")
    output = Path(output).resolve()
    if not output.is_relative_to(ROOT):
        raise ValueError("Training outputs must stay in repository")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError("Use a new run directory to preserve earlier experiments")
    output.mkdir(parents=True, exist_ok=True)
    torch.set_num_threads(config.threads)
    torch.use_deterministic_algorithms(True)
    random.seed(config.seed)
    np.random.seed(config.seed)
    torch.manual_seed(config.seed)
    data = BattleDataset(index, cache_games=config.batch_size)
    for split in ("train", "validation", "test"):
        if data.audit[split]["play_labels"] == 0 or data.audit[split]["position_labels"] == 0:
            raise ValueError(f"{split} needs whole-battle play and position supervision")
    write_json(output / "data-audit.json", data.audit)
    train_counts = data.audit["train"]
    play_weight = min(config.play_weight_cap, max(1.0,
        (train_counts["valid_labels"]-train_counts["play_labels"]) / train_counts["play_labels"]))
    model = EntityGRUPolicy(data.spec)
    optimizer = torch.optim.AdamW(model.parameters(), lr=config.learning_rate, weight_decay=config.weight_decay)
    metadata = {"train_config": asdict(config), "dataset_index": str(data.path.relative_to(ROOT)),
                "dataset_index_sha256": data.index_sha256, "dataset_provenance": data.index.get("provenance", {}),
                "environment": data.index.get("environment", {}), "play_weight": play_weight,
                "environment_identity": data.index.get("environment_identity", {}),
                "data_manifest_sha256": data.index_sha256,
                "training_data_quality": data.index.get("quality", "unspecified"),
                "source_index_sha256": data.index.get("source_index_sha256"),
                "reconstruction_reset_options": data.index.get("reconstruction_reset_options", {}),
                "strong_imitation_delivery_gate_passed": bool(data.games) and all(
                    row.get("quality", {}).get("strong_imitation_delivery_gate_passed") is True for row in data.games),
                "model_selection": "validation card_nll + position_nll; test evaluated once after selection",
                "recurrent_training": "chronological full-battle TBPTT; hidden detached every sequence; reset only at game start",
                "unsupervised_suffix": "skip only frames after the final valid label; retain all earlier context and invalid gaps; original full trajectories preserved",
                "software": {"torch": torch.__version__, "numpy": np.__version__}}
    write_json(output / "config.json", dict(metadata, policy_spec=asdict(data.spec)))
    history, best_score, best_epoch = [], float("inf"), None
    started = time.perf_counter()
    for epoch in range(config.epochs):
        epoch_started = time.perf_counter()
        rows = list(data.splits["train"])
        random.Random(config.seed+epoch).shuffle(rows)
        hidden = None
        model.train()
        batches = samples = optimizer_steps = 0
        norm = torch.tensor(0.)
        card_sum = position_sum = 0.0
        for batch, starts in stream_batches(data, rows, batch_size=config.batch_size, sequence=config.sequence,
                                           trim_unsupervised_suffix=True):
            if hidden is not None:
                hidden = hidden.detach() * torch.as_tensor(~starts)[None, :, None]
            out = model(tensor_observation(batch), hidden)
            hidden = out["hidden"]
            card_loss, position_loss = losses(out, batch, play_weight)
            loss = card_loss + config.position_loss_weight * position_loss
            if not torch.isfinite(loss):
                raise FloatingPointError("Nonfinite training loss; preserve run for diagnosis")
            if batch["label_valid"].any():
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                norm = torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0, error_if_nonfinite=True)
                optimizer.step()
                optimizer_steps += 1
            batches += 1
            samples += int(batch["label_valid"].sum())
            card_sum += float(card_loss.detach())
            position_sum += float(position_loss.detach())
        training_seconds = time.perf_counter() - epoch_started
        validation = evaluate_offline(model, data, "validation", config.sequence)
        score = validation["card_nll"] + config.position_loss_weight * validation["position_nll"]
        entry = {"epoch": epoch+1, "batches": batches, "optimizer_steps": optimizer_steps, "valid_labels": samples,
                 "mean_batch_weighted_card_loss": card_sum/max(batches,1),
                 "mean_batch_position_loss": position_sum/max(batches,1),
                 "training_seconds": training_seconds, "valid_labels_per_training_second": samples/training_seconds,
                 "last_gradient_norm": float(norm), "validation": validation, "selection_score": score}
        history.append(entry)
        with (output / "history.jsonl").open("a") as stream:
            stream.write(json.dumps(entry, ensure_ascii=False, allow_nan=False)+"\n")
        if score < best_score:
            best_score, best_epoch = score, epoch+1
            checkpoint = save_policy(model, output / "best.pt", metadata=dict(metadata, epoch=best_epoch,
                trained=True, training_steps=sum(item["optimizer_steps"] for item in history)), overwrite=True)
            write_json(output / "checkpoint.json", checkpoint)
        print(json.dumps(entry, ensure_ascii=False), flush=True)
    model, _ = load_policy(output / "best.pt", expected_spec=data.spec, expected_sha256=checkpoint["sha256"])
    # Freeze checkpoint before test. Ablation is diagnostic, never a new selection.
    test = evaluate_offline(model, data, "test", config.sequence)
    blind = evaluate_offline(model, data, "test", config.sequence, ablate_entities=True)
    report = {"schema_version": 1, "checkpoint": checkpoint, "best_epoch": best_epoch,
              "test": test, "test_same_weights_without_entities": blind,
              "data_audit": data.audit, "history": history,
              "total_wall_seconds": time.perf_counter()-started,
              "interpretation": "Offline reconstruction imitation; neither official battlefield ground truth nor evidence of live-game strength."}
    write_json(output / "report.json", report)
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--output", required=True)
    for name, field in TrainConfig.__dataclass_fields__.items():
        parser.add_argument("--"+name.replace("_", "-"), type=type(field.default), default=field.default)
    args = vars(parser.parse_args())
    index, output = args.pop("index"), args.pop("output")
    train(index, output, TrainConfig(**args))


if __name__ == "__main__":
    main()

"""Derive explicitly missing-data visual inputs from aligned simulator trajectories."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np

from .model import OBSERVATION_KEYS
from .pixel_policy import PixelProjection, VERSION
from .training import sha256, write_json


def project_dataset(index, output):
    source = Path(index).resolve()
    index_data = json.loads(source.read_text())
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    from .environment import ArenaConfig, ArenaEnv
    env = ArenaEnv(ArenaConfig(**index_data["environment_identity"]["config"]))
    projection = PixelProjection(index_data["environment_identity"], env.catalogue())
    env.close()
    result = deepcopy(index_data)
    result["policy_spec"].update(global_dim=projection.gdim*2, entity_dim=projection.edim*2,
                                 observation_version=VERSION)
    result["environment_identity"] = projection.identity()
    result["source_same_version_state_alignment_passed"] = result.get("same_version_state_alignment_passed", False)
    result["same_version_state_alignment_passed"] = False
    result["real_pixel_state_alignment_passed"] = False
    result.update(observation_version=VERSION, source_index_sha256=sha256(source),
                  data_origin="same-version rule-teacher simulator projected to masked visual schema; not real pixels")
    counts = {}
    for row in result["games"]:
        path = source.parent/row["path"]
        if sha256(path) != row["sha256"]:
            raise ValueError("Source trajectory checksum drift")
        with np.load(path, allow_pickle=False) as archive:
            arrays = {k: archive[k] for k in archive.files}
        frames = [projection.simulator({key: arrays[key][i] for key in OBSERVATION_KEYS}) for i in range(len(arrays["ticks"]))]
        for key in OBSERVATION_KEYS:
            arrays[key] = np.stack([frame[key] for frame in frames])
        previous = arrays["label_valid"].copy()
        for i, card in enumerate(arrays["target_card"]):
            legal = arrays["legal_cards"][i, card]
            if 1 <= card <= 4:
                legal &= arrays["legal_positions"][i, card-1, arrays["target_position"][i]]
            arrays["label_valid"][i] &= legal
            arrays["position_valid"][i] &= legal
        artifact = output/(row["game_id"]+f"-seat{row['teacher_seat']}.npz")
        np.savez_compressed(artifact, **arrays)
        row.update(path=artifact.name, sha256=sha256(artifact), source_npz_sha256=sha256(path),
                   quality="Projected known-mask schema, missing quantities explicit, same-battle labels retained only when proposal is observable")
        if "paired_manifest" in row:
            original_manifest = (source.parent/row.pop("paired_manifest")).resolve()
            row.update(source_paired_manifest=os.path.relpath(original_manifest, output),
                       source_paired_manifest_sha256=sha256(original_manifest),
                       state_alignment_scope="Source simulator trajectory; visual projection and detector outputs are not independently aligned")
        count = counts.setdefault(row["split"], {"frames": 0, "valid": 0, "plays": 0, "withheld_unobservable_labels": 0})
        count["frames"] += len(previous)
        count["valid"] += int(arrays["label_valid"].sum())
        count["plays"] += int(((arrays["target_card"] > 0) & arrays["label_valid"]).sum())
        count["withheld_unobservable_labels"] += int((previous & ~arrays["label_valid"]).sum())
    write_json(output/"index.json", result)
    write_json(output/"projection-report.json", {"counts": counts, "source_index_sha256": sha256(source),
        "index_sha256": sha256(output/"index.json"), "pixel_detector_error_simulation": False,
        "official_client_equivalence_verified": False, "real_pixel_training": False})
    return counts


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(project_dataset(args.index, args.output)))


if __name__ == "__main__":
    main()

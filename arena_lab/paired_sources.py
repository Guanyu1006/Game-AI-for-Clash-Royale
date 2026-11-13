"""Fetch and inspect pinned real-pixel examples without inventing missing state.

The TV example is an arena-one visual sample, not a high-level demonstration.
The two historical OYASSU files contain short visually inferred fragments, not
verified complete games. None satisfies the same-version full-config gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import hashlib
import importlib.metadata
import io
import json
from pathlib import Path
import time
import urllib.request

from .alignment import sha256_file

TV_REVISION = "f3a750881e008e0daf237afd45736231b51a955a"
WTY_REVISION = "ce86e10bedcf97762c3d207633f035cd12be8936"
SOURCES = (
    {"name": "tv-arena01-01600528.parquet", "bytes": 488889400,
     "sha256": "4cae91038343aa2eb3dce6af611b0041c934f5124d63b11bff9b101a726c192d",
     "url": f"https://huggingface.co/datasets/chrisrca/clash-royale-tv-replays/resolve/{TV_REVISION}/arena_01/01600528-75d9-429a-bb4b-7c5f062600a5/frames.parquet",
     "revision": TV_REVISION, "license": "MIT dataset card; game artwork rights separately owned"},
    {"name": "OYASSU_20230630_episodes_5.npy.xz", "bytes": 198000,
     "sha256": "6c7e1cc7b2aeb2b8429d9111204dc7d72cf0f9ca90bf0e09eab5554cc3c43e9f",
     "url": f"https://raw.githubusercontent.com/wty-yy/Clash-Royale-Replay-Dataset/{WTY_REVISION}/fast_hog_2.6/OYASSU_20230630_episodes_5.npy.xz",
     "revision": WTY_REVISION, "license": "unspecified; no redistribution grant found"},
    {"name": "OYASSU_20231105_episodes_9.npy.xz", "bytes": 614388,
     "sha256": "88ad968e5b99fe040b299718a7ed5ca59e80ff60fbd31c85d21ca25649c1f712",
     "url": f"https://raw.githubusercontent.com/wty-yy/Clash-Royale-Replay-Dataset/{WTY_REVISION}/fast_hog_2.6/OYASSU_20231105_episodes_9.npy.xz",
     "revision": WTY_REVISION, "license": "unspecified; no redistribution grant found"},
)


def fetch_pinned(source: dict, folder: Path) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / source["name"]
    started = time.monotonic()
    reused = path.exists()
    if not reused:
        partial = path.with_suffix(path.suffix + ".partial")
        with urllib.request.urlopen(source["url"], timeout=120) as response, partial.open("xb") as out:
            downloaded = 0
            while block := response.read(1024 * 1024):
                downloaded += len(block)
                if downloaded > source["bytes"]:
                    raise ValueError("Source exceeded its fixed size")
                out.write(block)
        if partial.stat().st_size != source["bytes"] or sha256_file(partial) != source["sha256"]:
            raise ValueError("Source content differs from pinned size/hash; partial retained")
        partial.rename(path)
    if path.stat().st_size != source["bytes"] or sha256_file(path) != source["sha256"]:
        raise ValueError("Existing source differs from pin; original retained")
    return dict(source, local_path=str(path), reused=reused, wall_seconds=time.monotonic() - started)


def inspect_tv(path: Path, output: Path) -> dict:
    import numpy as np
    from PIL import Image
    import pyarrow.parquet as pq

    parquet = pq.ParquetFile(path)
    if parquet.schema_arrow.names != ["frame_id", "image", "hash"]:
        raise ValueError("Unexpected raw frame schema")
    samples = output / "tv-frame-samples"
    samples.mkdir(exist_ok=True)
    dimensions, seen, selected, frame_ids = Counter(), set(), [], []
    duplicates = bgr_matches = total = 0
    for batch in parquet.iter_batches(batch_size=16):
        for row in batch.to_pylist():
            payload = row["image"]["bytes"]
            image = Image.open(io.BytesIO(payload)); image.load()
            dimensions[f"{image.size}-{image.mode}"] += 1
            rgb = np.asarray(image.convert("RGB"))
            pixel_sha = hashlib.sha256(rgb.tobytes()).hexdigest()
            bgr_matches += hashlib.md5(rgb[:, :, ::-1].tobytes()).hexdigest() == row["hash"]
            duplicates += pixel_sha in seen
            seen.add(pixel_sha)
            frame_ids.append(row["frame_id"])
            if total in {0, 40, 100, 200, 300, 400, 500, 588}:
                destination = samples / f"row-{total:04d}-frame-{row['frame_id']:04d}.png"
                digest = hashlib.sha256(payload).hexdigest()
                if destination.exists() and sha256_file(destination) != digest:
                    raise ValueError("Existing sample differs from pinned raw frame")
                if not destination.exists():
                    destination.write_bytes(payload)
                selected.append({"row": total, "frame_id": row["frame_id"],
                                 "path": str(destination), "sha256": digest})
            total += 1
    return {"schema": str(parquet.schema_arrow), "rows": parquet.metadata.num_rows,
            "actual_image_decodes": total, "dimensions": dict(dimensions),
            "reported_md5_matches_bgr": bgr_matches, "duplicate_pixel_hashes": duplicates,
            "source_sha256": sha256_file(path), "selected": selected, "frame_ids": frame_ids,
            "source_version_known": False, "exact_frame_timestamps_known": False,
            "full_match_coverage_verified": False, "full_initial_config_known": False,
            "expert_identity_verified": False, "strong_state_alignment_accepted": False,
            "purpose": "actual low-arena original pixels for vision audit/pretraining only",
            "collector_commit": "08aafd87cb3707b779804918cc65f4ae430d37ab",
            "upstream_sampling": "hash-deduplicated captures; first and last 40 changed frames removed; no precise capture timestamp"}


def inspect_wty(path: Path, output: Path) -> dict:
    from royale_phase3.sources import interpret_object_npy_xz, wty_records, validate_record

    data, parser_audit = interpret_object_npy_xz(path)
    records = list(wty_records(data, {"path": str(path), "sha256": sha256_file(path)}, path.stem))
    errors = [{"row": i, "errors": validate_record(row)} for i, row in enumerate(records) if validate_record(row)]
    destination = output / (path.name + ".records.jsonl")
    payload = "".join(json.dumps(row, separators=(",", ":")) + "\n" for row in records)
    if destination.exists() and destination.read_text() != payload:
        raise ValueError("Existing conversion differs; choose a new output")
    if not destination.exists():
        destination.write_text(payload)
    return {"path": str(path), "source_sha256": sha256_file(path), "rows": len(records),
            "play_labels": sum(row["action"]["kind"] == "play_visual" for row in records),
            "unit_rows": sum(len(row["observation"]["unit_infos_visual"]) for row in records),
            "ocr_first_seconds": records[0]["time"]["seconds"],
            "ocr_last_seconds": records[-1]["time"]["seconds"],
            "audit": parser_audit, "errors": errors, "strong_state_alignment_accepted": False,
            "reason": "Short historical visually inferred fragments; exact build, full config, authoritative entities and natural termination unavailable."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    args.output.mkdir(parents=True, exist_ok=True)
    receipts = [fetch_pinned(source, args.output / "raw") for source in SOURCES]
    tv = inspect_tv(args.output / "raw" / SOURCES[0]["name"], args.output)
    wty = [inspect_wty(args.output / "raw" / source["name"], args.output) for source in SOURCES[1:]]
    report = {"schema": "arena-external-pairing-audit-v1", "receipts": receipts,
              "dependencies": {name: importlib.metadata.version(name) for name in ("Pillow", "pyarrow", "numpy")},
              "tv": tv, "wty": wty, "strictly_qualified_human_full_games": 0,
              "cross_source_join_performed": False}
    destination = args.output / "external-pairing-audit.json"
    if destination.exists():
        raise FileExistsError("Audit receipt already exists; preserve previous audit")
    destination.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({"image_frames_decoded": tv["actual_image_decodes"],
                      "visual_expert_fragment_rows": sum(row["rows"] for row in wty),
                      "strictly_qualified_human_full_games": 0}))


if __name__ == "__main__":
    main()

"""Acquire frozen high-league real videos and retain unknown dynamics fields.

The upstream sidecar contains OCR/classifier labels, not native configuration.
MP4 PTS are retained verbatim; the corpus README's divide-by-four instruction
is contradicted by the observed screen clock and is not applied.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from concurrent.futures import ThreadPoolExecutor
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import subprocess
import time
import urllib.request

from .alignment import sha256_file

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PLAN = Path(__file__).with_name("manifests") / "alphaclash-ultimate80-v1.json"


def source_name(record: dict) -> str:
    path = PurePosixPath(record["path"])
    if (path.is_absolute() or ".." in path.parts or "\\" in str(path) or
            not re.fullmatch(r"[A-Za-z0-9_.-]+", path.name) or path.name in {".", ".."}):
        raise ValueError("Invalid source filename")
    return path.name


def cached_file(name: str, roots: list[Path]) -> Path:
    matches = [root / "raw" / name for root in roots if (root / "raw" / name).is_file()]
    if not matches:
        raise FileNotFoundError(f"No cached source: {name}")
    if len(matches) > 1 and len({sha256_file(path) for path in matches}) != 1:
        raise ValueError(f"Conflicting cached source: {name}")
    return matches[0]


def validate_plan(plan: dict) -> None:
    if plan.get("schema") != "arena-alphaclash-paired-video-plan-v1":
        raise ValueError("Unknown frozen source plan")
    if not re.fullmatch(r"[a-f0-9]{40}", plan["revision"]):
        raise ValueError("Source revision must be a fixed commit")
    names = [source_name(row) for row in plan["files"]]
    if len(names) != len(set(names)):
        raise ValueError("Source basenames collide")
    stems = [game["stem"] for game in plan["games"]]
    if len(stems) != len(set(stems)) or any(not re.fullmatch(r"[A-Za-z0-9_-]+", stem) for stem in stems):
        raise ValueError("Invalid or duplicate game stem")
    expected = {stem + suffix for stem in stems for suffix in (".json", ".mp4", "_deck.png")}
    if set(names) != expected:
        raise ValueError("Each game requires exactly its video, sidecar and deck image")
    group_splits = {}
    for game in plan["games"]:
        if game["split"] not in {"train", "validation", "test"}:
            raise ValueError("Unknown source split")
        if game["group_id"] in group_splits and group_splits[game["group_id"]] != game["split"]:
            raise ValueError("Same visual game group crosses splits")
        group_splits[game["group_id"]] = game["split"]


def verify_file(path: Path, record: dict) -> str:
    if path.stat().st_size != record["size"]:
        raise ValueError("Source byte length mismatch")
    digest = sha256_file(path)
    if "lfs" in record:
        if digest != record["lfs"]["oid"]:
            raise ValueError("Source LFS SHA256 mismatch")
    else:
        payload = path.read_bytes()
        blob = hashlib.sha1(b"blob " + str(len(payload)).encode() + b"\0" + payload).hexdigest()
        if blob != record["oid"]:
            raise ValueError("Source Git blob SHA1 mismatch")
    return digest


def acquire_file(record: dict, raw: Path, revision: str) -> dict:
    name = source_name(record)
    destination = raw / name
    reused = destination.exists()
    started = time.monotonic()
    if not reused:
        partial = raw / (name + ".partial")
        if partial.exists():
            # Preserve interrupted bytes, but never trust an incomplete source
            # or silently append to a response with uncertain Range support.
            partial.rename(raw / (name + f".interrupted-{time.time_ns()}"))
        url = f"https://huggingface.co/datasets/josefbednar/alphaclash-replays/resolve/{revision}/{record['path']}"
        with urllib.request.urlopen(url, timeout=120) as response, partial.open("xb") as output:
            received = 0
            while block := response.read(1024 * 1024):
                received += len(block)
                if received > record["size"]:
                    raise ValueError("Download exceeded pinned source size")
                output.write(block)
        verify_file(partial, record)
        partial.rename(destination)
    digest = verify_file(destination, record)
    return {"path": str(destination), "source_path": record["path"], "sha256": digest,
            "bytes": record["size"], "reused": reused, "wall_seconds": time.monotonic() - started}


def video_probe(path: Path, executable: Path) -> dict:
    result = subprocess.run([str(executable), "-v", "error", "-count_packets",
        "-show_entries", "format=duration,size:stream=codec_name,width,height,r_frame_rate,avg_frame_rate,nb_frames,nb_read_packets,time_base,start_time,duration",
        "-of", "json", str(path)], check=True, capture_output=True, text=True, timeout=120)
    return json.loads(result.stdout)


def inspect_corpus(plan: dict, output: Path, *, ffprobe: Path, ffmpeg: Path, decode_samples: bool = True,
                   cached_corpora: list[Path] | None = None) -> dict:
    validate_plan(plan)
    roots = cached_corpora or [output]
    rows, duplicates, group_split = [], defaultdict(list), {}
    by_path = {Path(row["path"]).name: row for row in plan["files"]}
    for game in plan["games"]:
        stem = game["stem"]
        sidecar_path = cached_file(stem + ".json", roots)
        verify_file(sidecar_path, by_path[sidecar_path.name])
        sidecar = json.loads(sidecar_path.read_text())
        fingerprint = hashlib.sha256(sidecar["fingerprint"].encode()).hexdigest()
        if fingerprint != game["group_id"]:
            raise ValueError("Visual grouping fingerprint differs from frozen plan")
        if fingerprint in group_split and group_split[fingerprint] != game["split"]:
            raise ValueError("Same visual game group crosses splits")
        group_split[fingerprint] = game["split"]
        video = cached_file(stem + ".mp4", roots)
        deck_image = cached_file(stem + "_deck.png", roots)
        verify_file(deck_image, by_path[deck_image.name])
        digest = verify_file(video, by_path[video.name])
        duplicates[digest].append({"stem": stem, "split": game["split"]})
        probe = video_probe(video, ffprobe)
        stream = probe["streams"][0]
        duration = float(probe["format"]["duration"])
        start, end = sidecar.get("battle_start_s"), sidecar.get("battle_end_s")
        boundaries = (isinstance(start, (int, float)) and isinstance(end, (int, float)) and
                      0 <= start < end <= duration + .2)
        images = []
        if decode_samples and boundaries:
            sample_dir = output / "frame-samples" / stem
            sample_dir.mkdir(parents=True, exist_ok=True)
            for label, seconds in (("start", start), ("middle", (start + end) / 2),
                                   ("end", min(end, duration - .1))):
                target = sample_dir / (label + ".png")
                if not target.exists():
                    subprocess.run([str(ffmpeg), "-v", "error", "-threads", "1", "-ss", str(seconds),
                        "-i", str(video), "-frames:v", "1", "-threads", "1", str(target)],
                        check=True, capture_output=True, timeout=120)
                images.append({"kind": label, "seek_video_seconds": seconds,
                               "path": str(target), "sha256": sha256_file(target)})
        card_rows = [row for seat in ("player0", "player1") for row in sidecar["decks"][seat]]
        row = {"stem": stem, "split": game["split"], "group_id": game["group_id"],
               "arena": sidecar["arena"], "video_sha256": digest,
               "sidecar_sha256": sha256_file(sidecar_path),
               "deck_image_sha256": sha256_file(deck_image),
               "source_paths": {"video": str(video), "sidecar": str(sidecar_path), "deck_image": str(deck_image)},
               "video_probe": probe, "boundary_fields_inside_video": boundaries,
               "source_truncated_flag": sidecar.get("truncated"),
               "deck_cards": len(card_rows), "unknown_card_levels": sum(row.get("level") is None for row in card_rows),
               "minimum_card_recognition_confidence": min(row["conf"] for row in card_rows),
               "samples": images, "original_clock_fields": {key: sidecar.get(key) for key in
                   ("capture_fps", "speed_multiplier", "speed_change_at_s", "battle_start_s", "battle_end_s", "duration_s")},
               "exact_game_ticks_known": False, "game_build_known": False,
               "native_initial_state_known": False, "forms_verified": False,
               "expert_rank_evidence": "ranked_ultimate_champion source pool; per-player rank OCR/manual evidence separate",
               "action_labels_available": False,
               "quality": "recorded human high-league pixels plus visually inferred deck metadata"}
        rows.append(row)
        (output / "inspection-progress.json").write_text(json.dumps({"games_inspected": len(rows),
            "games_planned": len(plan["games"]), "latest": stem}, indent=2) + "\n")
    cross_split = [items for items in duplicates.values() if len({item["split"] for item in items}) > 1]
    if cross_split:
        raise ValueError("Duplicate source video crosses frozen split boundary")
    return {"schema": "arena-alphaclash-paired-video-audit-v1", "revision": plan["revision"],
            "games": rows, "group_counts": dict(Counter(group_split.values())),
            "verified_containers": len(rows), "original_recording_frames": sum(int(row["video_probe"]["streams"][0]["nb_frames"]) for row in rows),
            "boundary_fields_inside_video": sum(row["boundary_fields_inside_video"] for row in rows),
            "decoded_sample_frames": sum(len(row["samples"]) for row in rows),
            "exact_duplicate_video_groups": [items for items in duplicates.values() if len(items) > 1],
            "same_version_full_config_human_games": 0, "games_with_native_action_labels": 0,
            "game_seconds_conversion_applied": False,
            "reason": "Use video PTS; README divide-by-four contradicts screen clocks; exact ticks/config/actions remain unknown."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", type=Path, default=DEFAULT_PLAN)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=3)
    parser.add_argument("--ffprobe", type=Path, default=ROOT / ".cache/native-tools/bin/ffprobe")
    parser.add_argument("--ffmpeg", type=Path, default=ROOT / ".cache/native-tools/bin/ffmpeg")
    parser.add_argument("--skip-sample-decode", action="store_true")
    parser.add_argument("--cached-corpus", type=Path, action="append", default=[],
                        help="Audit pinned files already in CORPUS/raw; repeat to combine archives without copying or downloading")
    args = parser.parse_args(argv)
    if not 1 <= args.workers <= 4:
        raise ValueError("Download concurrency must be in 1..4")
    plan = json.loads(args.plan.read_text())
    validate_plan(plan)
    raw = args.output / "raw"; raw.mkdir(parents=True, exist_ok=True)
    if (args.output / "video-audit.json").exists():
        raise FileExistsError("Completed audit exists; preserve it")
    if args.cached_corpus:
        for row in plan["files"]:
            verify_file(cached_file(source_name(row), args.cached_corpus), row)
        result = inspect_corpus(plan, args.output, ffprobe=args.ffprobe, ffmpeg=args.ffmpeg,
                                decode_samples=not args.skip_sample_decode, cached_corpora=args.cached_corpus)
        result["plan_sha256"] = sha256_file(args.plan)
        (args.output / "video-audit.json").write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({key: value for key, value in result.items() if key != "games"}))
        return
    missing = sum(row["size"] for row in plan["files"] if not (raw / source_name(row)).exists())
    if shutil.disk_usage(args.output).free < missing + 1500 * 1024 * 1024:
        raise OSError("Insufficient space: preserve 1.5 GiB for native runtime and derived samples")
    receipts = []
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        jobs = [pool.submit(acquire_file, row, raw, plan["revision"]) for row in plan["files"]]
        for job in jobs:
            receipts.append(job.result())
            (args.output / "download-progress.json").write_text(json.dumps({"files": len(receipts),
                "total_files": len(jobs), "verified_bytes": sum(row["bytes"] for row in receipts)}, indent=2) + "\n")
    (args.output / "download-receipts.json").write_text(json.dumps(receipts, indent=2) + "\n")
    result = inspect_corpus(plan, args.output, ffprobe=args.ffprobe, ffmpeg=args.ffmpeg,
                            decode_samples=not args.skip_sample_decode)
    result["plan_sha256"] = sha256_file(args.plan)
    (args.output / "video-audit.json").write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps({key: value for key, value in result.items() if key != "games"}))


if __name__ == "__main__":
    main()

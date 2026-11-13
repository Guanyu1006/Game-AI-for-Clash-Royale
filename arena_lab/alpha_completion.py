"""Decode original replay endpoints for explicit visual whole-game review.

No filename, crown metadata or duration can certify a whole recorded game.
The generated contact sheets require separate visual annotations of both the
opening presentation and a terminal result presentation. A terminal banner
does not by itself identify winner, crown count, engine build or exact ticks.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
import subprocess

from .alignment import sha256_file


def audit_review(annotations: Path) -> dict:
    """Validate image-bound visual annotations; unknown labels cannot pass."""
    reviewed = json.loads(annotations.read_text())
    if reviewed.get("schema") != "arena-alpha-completion-annotations-v1":
        raise ValueError("Unsupported completion annotation schema")
    plan_path = Path(reviewed["plan_path"])
    if sha256_file(plan_path) != reviewed["plan_sha256"]:
        raise ValueError("Frozen video plan checksum mismatch")
    plan = json.loads(plan_path.read_text())
    planned = {g["stem"]: g for g in plan["games"]}
    if {g["stem"] for g in reviewed["games"]} != planned.keys():
        raise ValueError("Visual review omits or adds a frozen replay")
    seen, rows = set(), []
    for row in reviewed["games"]:
        if row["stem"] in seen:
            raise ValueError("Repeated replay in visual coverage review")
        seen.add(row["stem"])
        if row["split"] != planned[row["stem"]]["split"]:
            raise ValueError("Visual review changed a frozen split")
        for kind in ("opening", "terminal"):
            frame = row[kind]
            if sha256_file(Path(frame["path"])) != frame["sha256"]:
                raise ValueError("Reviewed endpoint image checksum mismatch")
        opening = row["opening"]["visible_evidence"] == "opening_player_presentation"
        terminal = row["terminal"]["visible_evidence"] in {
            "red_blue_result_banners", "decisive_crown_award_during_overtime"}
        rows.append({"stem": row["stem"], "split": row["split"],
                     "opening_and_terminal_visual_coverage": opening and terminal,
                     "terminal_evidence": row["terminal"]["visible_evidence"]})
    return {"schema": "arena-alpha-completion-audit-v1", "annotations_sha256": sha256_file(annotations),
            "games": rows, "reviewed_games": len(rows),
            "opening_and_terminal_visual_coverage_games": sum(r["opening_and_terminal_visual_coverage"] for r in rows),
            "terminal_evidence_counts": dict(Counter(r["terminal_evidence"] for r in rows)),
            "coverage_games_per_split": dict(Counter(r["split"] for r in rows if r["opening_and_terminal_visual_coverage"])),
            "review_method": "Explicit visual inspection of decoded endpoints; hash-validated annotations, not an OCR accuracy test",
            "every_interior_frame_reviewed": False, "exact_game_tick_alignment_verified": False,
            "winner_and_final_crown_labels_verified": False,
            "same_version_full_configuration_verified": False}


def reproduce_review(annotations: Path, corpus: Path, output: Path, ffmpeg: Path) -> dict:
    """Recreate annotated endpoints from a fresh download, without old outputs."""
    from .alpha_data import source_name, validate_plan, verify_file

    reviewed = json.loads(annotations.read_text())
    plan_path = Path(reviewed["plan_path"])
    if sha256_file(plan_path) != reviewed["plan_sha256"]:
        raise ValueError("Frozen video plan checksum mismatch")
    plan = json.loads(plan_path.read_text()); validate_plan(plan)
    records = {source_name(record): record for record in plan["files"]}
    output.mkdir(parents=True, exist_ok=True)
    completed = output/"reproduced-annotations.json"
    if completed.exists():
        raise FileExistsError("Completed visual review exists; preserve it")
    for i, row in enumerate(reviewed["games"]):
        video = corpus/"raw"/(row["stem"]+".mp4")
        if verify_file(video, records[video.name]) != row["video_sha256"]:
            raise ValueError("Reviewed video differs from frozen source")
        for kind in ("opening", "terminal"):
            endpoint = row[kind]
            target = output/(row["stem"]+"-"+kind+".png")
            if not target.exists():
                subprocess.run([str(ffmpeg), "-v", "error", "-threads", "1", "-ss", str(endpoint["requested_seek_seconds"]),
                                "-i", str(video), "-frames:v", "1", "-threads", "1", str(target)],
                               check=True, capture_output=True, timeout=120)
            if sha256_file(target) != endpoint["sha256"]:
                raise ValueError("Reproduced endpoint differs from reviewed bytes; verify FFmpeg version and review a new artifact")
            endpoint["path"] = str(target)
        (output/"progress.json").write_text(json.dumps({"reproduced_games": i+1, "planned": len(reviewed["games"])})+"\n")
    reviewed["original_annotations_sha256"] = sha256_file(annotations)
    completed.write_text(json.dumps(reviewed, ensure_ascii=False, indent=2)+"\n")
    report = audit_review(completed)
    (output/"completion-audit.json").write_text(json.dumps(report, indent=2)+"\n")
    return report


def prepare(inputs: Path, output: Path, ffmpeg: Path, *, opening_lead_seconds: float = 0.) -> dict:
    from PIL import Image, ImageDraw

    output.mkdir(parents=True, exist_ok=True)
    source = json.loads(inputs.read_text())
    rows = []
    for i, row in enumerate(source):
        root = Path(row["root"])
        video = root / "raw" / (row["stem"] + ".mp4")
        start = root / "frame-samples" / row["stem"] / "start.png"
        if opening_lead_seconds:
            start = output / (row["stem"] + "-opening.png")
            opening_seek = max(0., row["battle_start_s"] - opening_lead_seconds)
            if not start.exists():
                subprocess.run([str(ffmpeg), "-v", "error", "-threads", "1", "-ss", str(opening_seek),
                                "-i", str(video), "-frames:v", "1", "-threads", "1", str(start)],
                               check=True, capture_output=True, timeout=120)
        target = output / (row["stem"] + "-tail.png")
        seek = max(0., row["duration"] - .18)
        if not target.exists():
            subprocess.run([str(ffmpeg), "-v", "error", "-threads", "1", "-ss", str(seek),
                            "-i", str(video), "-frames:v", "1", "-threads", "1", str(target)],
                           check=True, capture_output=True, timeout=120)
        rows.append(dict(row, start_path=str(start), start_sha256=sha256_file(start),
                         tail_path=str(target), tail_sha256=sha256_file(target),
                         requested_opening_seek_video_seconds=max(0., row["battle_start_s"] - opening_lead_seconds),
                         requested_tail_seek_video_seconds=seek,
                         completion_status="pending_visual_review"))
        (output/"progress.json").write_text(json.dumps({"decoded": i+1, "planned": len(source)})+"\n")
    sheets = []
    for page, offset in enumerate(range(0, len(rows), 5)):
        sheet = Image.new("RGB", (1800, 480), "white")
        draw = ImageDraw.Draw(sheet)
        for column, row in enumerate(rows[offset:offset+5]):
            x = column*360
            draw.text((x+5, 5), f"{offset+column}: {row['stem'][:15]}", fill="black")
            draw.text((x+5, 22), row["split"]+" | opening / actual file tail", fill="black")
            for side, name in enumerate(("start_path", "tail_path")):
                with Image.open(row[name]) as frame:
                    image = frame.convert("RGB").resize((180, 400))
                sheet.paste(image, (x+side*180, 48))
        path = output/f"contact-{page:02d}.jpg"
        sheet.save(path, quality=95)
        sheets.append({"path": str(path), "sha256": sha256_file(path), "first_row": offset})
    report = {"schema": "arena-alpha-completion-review-input-v1", "inputs_sha256": sha256_file(inputs),
              "frames": rows, "sheets": sheets, "automatic_completion_claim": False,
              "sampling": "Source battle_start_s minus opening_lead_seconds and actual container duration minus 0.18s; no game-tick conversion",
              "opening_lead_seconds": opening_lead_seconds}
    (output/"review-input.json").write_text(json.dumps(report, indent=2)+"\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--inputs", type=Path)
    mode.add_argument("--annotations", type=Path, help="Reproduce committed image-bound review from a fresh frozen corpus")
    parser.add_argument("--corpus", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--ffmpeg", type=Path, default=Path(__file__).resolve().parents[1]/".cache/native-tools/bin/ffmpeg")
    parser.add_argument("--opening-lead-seconds", type=float, default=0.)
    args = parser.parse_args(argv)
    if args.annotations:
        if args.corpus is None:
            parser.error("--annotations requires --corpus")
        report = reproduce_review(args.annotations, args.corpus, args.output, args.ffmpeg)
        print(json.dumps({key: value for key, value in report.items() if key != "games"}))
        return
    if not 0 <= args.opening_lead_seconds <= 10:
        raise ValueError("Opening lead must be within 0..10 seconds")
    report = prepare(args.inputs, args.output, args.ffmpeg, opening_lead_seconds=args.opening_lead_seconds)
    print(json.dumps({"games_prepared": len(report["frames"]), "completion_claim": False}))


if __name__ == "__main__":
    main()

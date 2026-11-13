"""Conservative fixed-layout OCR of visible ranks in the frozen deck screenshots.

This reads the recorded screen, not a current leaderboard or account identity.
Templates come from explicitly manually inspected source images. Uncertain or
absent text stays unknown; the source's arena label cannot override this gate.
"""
from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path

import numpy as np
from PIL import Image

from .alignment import sha256_file
from .alpha_data import cached_file, source_name, validate_plan, verify_file

EXAMPLES = (
    ("20260729-235656_0100010101010101", ("20", "14")),
    ("20260730-003023_0100010101010111", ("90", "181")),
    ("20260731-021316_0100010001010111", ("64", "93")),
    ("20260801-030305_0100010101010111", ("108", "167")),
    ("20260801-140157_0100011001010111", ("79", "56")),
)


def image_mask(path: Path) -> np.ndarray:
    pixels = np.asarray(Image.open(path).convert("RGB"))
    if pixels.shape != (407, 991, 3):
        raise ValueError("Rank OCR supports only the audited 991x407 deck screenshot layout")
    return (pixels.max(-1) - pixels.min(-1) < 25) & (pixels.mean(-1) < 140)


def dice(a, b) -> float:
    return float(2 * np.logical_and(a, b).sum() / max(1, a.sum() + b.sum()))


def locate_number(mask, seat, hash_template):
    xs = range(140, 240) if seat == 0 else range(790, 890)
    best = max((dice(mask[93:116, x:x+14], hash_template), x) for x in xs)
    score, x = best
    roi = mask[93:116, x+14:x+94]
    columns = roi.sum(0) > 1
    spans, start = [], None
    for i, value in enumerate(np.r_[columns, False]):
        if value and start is None:
            start = i
        if not value and start is not None:
            spans.append((start, i)); start = None
    selected = []
    for left, right in spans:
        if (not selected and left > 6) or (selected and left-selected[-1][1] > 5) or right-left > 20:
            break
        selected.append((left, right))
    glyphs = []
    for left, right in selected:
        crop = roi[:, left:right]
        rows = np.where(crop.any(1))[0]
        if len(rows):
            crop = crop[rows[0]:rows[-1]+1]
            glyphs.append(np.asarray(Image.fromarray((crop*255).astype(np.uint8)).resize(
                (20, 24), Image.Resampling.NEAREST)) > 0)
    return score, mask[93:116, x-100:x-9], glyphs


def audit_ranks(corpus: Path, plan_path: Path, *, additional_corpora: list[Path] | None = None) -> dict:
    roots = [corpus, *(additional_corpora or [])]
    plan = json.loads(plan_path.read_text())
    validate_plan(plan)
    records = {source_name(record): record for record in plan["files"]}
    for game in plan["games"]:
        name = game["stem"] + "_deck.png"
        verify_file(cached_file(name, roots), records[name])
    first = image_mask(cached_file(EXAMPLES[0][0] + "_deck.png", roots))
    hash_template = first[93:116, 194:208]
    templates = {str(digit): [] for digit in range(10)}
    prefixes, examples = [], []
    for stem, numbers in EXAMPLES:
        path = cached_file(stem + "_deck.png", roots)
        mask = image_mask(path)
        for seat, number in enumerate(numbers):
            score, prefix, glyphs = locate_number(mask, seat, hash_template)
            if len(glyphs) != len(number) or score < .75:
                raise ValueError(f"Template segmentation failed for {stem} seat {seat}")
            prefixes.append(prefix)
            for digit, glyph in zip(number, glyphs):
                templates[digit].append(glyph)
        examples.append({"stem": stem, "visible_rank_strings": numbers,
                         "image_sha256": sha256_file(path), "method": "manual visual inspection"})
    if any(not examples for examples in templates.values()):
        raise ValueError("Missing digit template")
    rows = []
    for game in plan["games"]:
        path = cached_file(game["stem"] + "_deck.png", roots)
        mask, players = image_mask(path), []
        for seat in (0, 1):
            hash_score, prefix, glyphs = locate_number(mask, seat, hash_template)
            prefix_score = max(dice(prefix, template) for template in prefixes)
            digits, scores, margins = [], [], []
            for glyph in glyphs:
                ranked = sorted((max(dice(glyph, template) for template in values), digit)
                                for digit, values in templates.items())[::-1]
                digits.append(ranked[0][1]); scores.append(ranked[0][0])
                margins.append(ranked[0][0] - ranked[1][0])
            accepted = bool(hash_score > .82 and prefix_score > .84 and scores and
                            min(scores) > .86 and min(margins) > .035)
            rank = int("".join(digits)) if accepted else None
            if rank == 0:
                rank, accepted = None, False
            players.append({"seat": seat, "visible_rank": rank,
                            "accepted_template_read": accepted, "raw_text": "".join(digits),
                            "hash_similarity": hash_score, "prefix_similarity": prefix_score,
                            "minimum_digit_similarity": min(scores) if scores else None,
                            "minimum_digit_margin": min(margins) if margins else None})
        rows.append({"stem": game["stem"], "split": game["split"], "players": players,
                     "image_sha256": sha256_file(path),
                     "template_calibration_example": game["stem"] in {row[0] for row in EXAMPLES}})
    return {"schema": "arena-visible-rank-audit-v1", "rows": rows,
            "plan_sha256": sha256_file(plan_path), "revision": plan["revision"],
            "method": "fixed-layout font-template OCR; recorded rank only, not account lookup",
            "examples": examples, "rank_read_games": sum(any(p["visible_rank"] for p in r["players"]) for r in rows),
            "both_players_rank_read_games": sum(all(p["visible_rank"] for p in r["players"]) for r in rows),
            "rank_read_games_per_split": dict(Counter(r["split"] for r in rows if any(p["visible_rank"] for p in r["players"]))),
            "human_action_labels_generated": False,
            "limitation": "Rank OCR calibrated on five screenshots including source splits; these images are not an independent OCR test. Actor/decision test remains separate."}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--corpus", type=Path, required=True)
    parser.add_argument("--plan", type=Path, default=Path(__file__).with_name("manifests") / "alphaclash-ultimate80-v1.json")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--additional-corpus", type=Path, action="append", default=[])
    args = parser.parse_args(argv)
    if args.output.exists():
        raise FileExistsError(args.output)
    report = audit_ranks(args.corpus, args.plan, additional_corpora=args.additional_corpus)
    args.output.write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps({key: value for key, value in report.items() if key not in ("rows", "examples")}))


if __name__ == "__main__":
    main()

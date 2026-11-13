"""Freeze an expanded-data experiment without reusing already observed test games."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def freeze_unseen_test_index(index: str | Path, previous: str | Path, output: str | Path) -> dict:
    index, previous, output = Path(index).resolve(), Path(previous).resolve(), Path(output).resolve()
    if not output.is_relative_to(ROOT) or output.exists():
        raise ValueError("Use a new experiment index inside the repository")
    original = json.loads(index.read_text())
    lock = json.loads(previous.read_text())
    if original.get("schema_version") != 1 or lock.get("schema_version") != 1:
        raise ValueError("Unsupported source or previous-test lock schema")
    old = {row["game_id"]: row for row in lock["games"]}
    if len(old) != len(lock["games"]) or not old:
        raise ValueError("Previous test identities must be nonempty and unique")
    old_sources = {row["source_game_id"]: row["game_id"] for row in old.values()}
    old_hashes = {row["source_sha256"]: row["game_id"] for row in old.values()}
    if len(old_sources) != len(old) or len(old_hashes) != len(old):
        raise ValueError("Previous test source identities must be unique")
    retained, withheld, seen = [], [], set()
    for row in original["games"]:
        row = dict(row)
        for reference in (old_sources.get(row["source_game_id"]), old_hashes.get(row["source_sha256"])):
            if reference is not None and reference != row["game_id"]:
                raise ValueError("Previously evaluated source was renamed to a different game identity")
        if row["game_id"] in old:
            evidence = old[row["game_id"]]
            if row["split"] != "test":
                raise ValueError("Previously evaluated test game moved into training or validation")
            if row["source_sha256"] != evidence["source_sha256"] or row["source_game_id"] != evidence["source_game_id"]:
                raise ValueError("Previous test identity disagrees with expanded source")
            withheld.append(row["game_id"])
            seen.add(row["game_id"])
            continue
        # Resolve against the original index before relocating its subset.
        path = Path(row["path"])
        path = path if path.is_absolute() else index.parent / path
        path = path.resolve()
        if not path.is_relative_to(ROOT) or not path.is_file():
            raise ValueError("Numeric sequence file must exist inside repository")
        row["path"] = str(path)
        retained.append(row)
    if seen != set(old):
        raise ValueError("Expanded reconstruction does not include all earlier test identities")
    new_tests = {row["game_id"] for row in retained if row["split"] == "test"}
    if not new_tests:
        raise ValueError("No untouched test games remain")
    result = dict(original, games=retained)
    result["provenance"] = dict(original.get("provenance", {}), experiment_test_selection={
        "expanded_index_sha256": hashlib.sha256(index.read_bytes()).hexdigest(),
        "previous_test_lock_sha256": hashlib.sha256(previous.read_bytes()).hexdigest(),
        "withheld_previously_evaluated_test_games": sorted(seen),
        "withheld_teacher_sequences": len(withheld),
        "untouched_test_games": len(new_tests),
        "rule": "Keep all original train/validation rows; test only new game identities. No score-based selection.",
        "full_reconstruction_preserved": str(index.relative_to(ROOT)),
    })
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result["provenance"]["experiment_test_selection"]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", required=True)
    parser.add_argument("--previous", default="configs/arena-lab/previously-evaluated-test-v1.json")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    print(json.dumps(freeze_unseen_test_index(args.index, args.previous, args.output), indent=2))


if __name__ == "__main__":
    main()

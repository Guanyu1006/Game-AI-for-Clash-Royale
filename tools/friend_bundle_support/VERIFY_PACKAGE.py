"""Verify a delivered snapshot without installing anything or using the network."""
import hashlib
import json
from pathlib import Path, PurePosixPath
import sys

ROOT = Path(__file__).resolve().parent


def verify():
    manifest = json.loads((ROOT / "PACKAGE_MANIFEST.json").read_text(encoding="utf-8"))
    checked = 0
    for row in manifest["files"]:
        relative = PurePosixPath(row["path"])
        if relative.is_absolute() or ".." in relative.parts or ":" in row["path"]:
            raise ValueError("Unsafe member path")
        path = ROOT.joinpath(*relative.parts).resolve()
        path.relative_to(ROOT.resolve())
        if not path.is_file() or path.is_symlink():
            raise ValueError("Missing/unsafe file: " + row["path"])
        data = path.read_bytes()
        if len(data) != row["bytes"] or hashlib.sha256(data).hexdigest() != row["sha256"]:
            raise ValueError("Changed file: " + row["path"])
        checked += 1
    return {"status": "pass", "files_checked": checked,
            "scope": "Snapshot integrity only; no native runtime or policy-strength claim."}


if __name__ == "__main__":
    print(json.dumps(verify(), ensure_ascii=False, indent=2))

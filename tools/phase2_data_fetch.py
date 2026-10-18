"""Fetch pinned public IL_Replay metadata and selected small Parquet shards."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
META = ROOT / "third_party/phase2/dataset_metadata/IL_Replay"
OUT = ROOT / "outputs/phase2/data"
REPO = "VanguardX101/IL_Replay"
API = f"https://huggingface.co/api/datasets/{REPO}"


def digest(path):
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1048576), b""):
            hasher.update(block)
    return hasher.hexdigest()


def fetch(url, target, expected_sha256=None, max_bytes=500_000_000):
    target = target.resolve()
    target.relative_to(ROOT)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_file() and (not expected_sha256 or digest(target) == expected_sha256):
        return {"url": url, "path": target.relative_to(ROOT).as_posix(), "bytes": target.stat().st_size,
                "sha256": digest(target), "status": "cached_verified" if expected_sha256 else "cached"}
    partial = target.with_name(target.name + ".partial")
    last_error = None
    for attempt in range(3):
        try:
            request = urllib.request.Request(url, headers={"User-Agent": "RoyaleResearch/phase2; public source audit"})
            with urllib.request.urlopen(request, timeout=40) as response:
                declared = int(response.headers.get("Content-Length", "0"))
                if declared > max_bytes:
                    raise ValueError(f"Declared {declared} exceeds budget {max_bytes}")
                count = 0
                start = time.monotonic()
                with partial.open("wb") as handle:
                    while True:
                        block = response.read(1048576)
                        if not block:
                            break
                        count += len(block)
                        if count > max_bytes or time.monotonic() - start > 240:
                            raise ValueError("Transfer budget exceeded")
                        handle.write(block)
                if declared and count != declared:
                    raise ValueError(f"Incomplete download {count}/{declared}")
                actual = digest(partial)
                if expected_sha256 and actual != expected_sha256:
                    raise ValueError("SHA256 mismatch")
                partial.replace(target)
                result = {"url": url, "path": target.relative_to(ROOT).as_posix(), "bytes": count,
                          "sha256": actual, "status": "downloaded", "attempt": attempt + 1}
                with (OUT / "download_log.jsonl").open("a", encoding="utf-8") as log:
                    log.write(json.dumps(result, ensure_ascii=False) + "\n")
                return result
        except Exception as error:
            last_error = repr(error)
    result = {"url": url, "path": target.relative_to(ROOT).as_posix(), "status": "failed", "error": last_error}
    with (OUT / "download_log.jsonl").open("a", encoding="utf-8") as log:
        log.write(json.dumps(result, ensure_ascii=False) + "\n")
    raise RuntimeError(result)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--shard", help="Exact repository path after inspecting metadata")
    parser.add_argument("--max-mib", type=int, default=150)
    args = parser.parse_args()
    META.mkdir(parents=True, exist_ok=True)
    OUT.mkdir(parents=True, exist_ok=True)
    records = []
    # Lock the first seen public revision. Refresh requires an explicit new audit,
    # not a silent update of a frozen experiment.
    repo_info = META / "repo_info.json"
    records.append(fetch(API, repo_info, max_bytes=5_000_000))
    info = json.loads(repo_info.read_text(encoding="utf-8"))
    revision = info["sha"]
    base = f"https://huggingface.co/datasets/{REPO}/resolve/{revision}/"
    siblings = {item["rfilename"] for item in info.get("siblings", [])}
    for name in ("README.md", "manifest.json", "SHA256SUMS", "compatibility.json", "LICENSE", "LICENSE.md", ".gitattributes"):
        if name in siblings:
            records.append(fetch(base + name, META / name, max_bytes=20_000_000))
    tree = META / "tree.json"
    records.append(fetch(API + f"/tree/{revision}?recursive=true&limit=1000", tree, max_bytes=20_000_000))
    if args.shard:
        if args.shard not in siblings or not args.shard.endswith(".parquet"):
            raise ValueError("Shard is not a pinned repository Parquet file")
        checks = {}
        sums = META / "SHA256SUMS"
        if sums.is_file():
            for line in sums.read_text(encoding="utf-8").splitlines():
                parts = line.split()
                if len(parts) >= 2:
                    checks[parts[-1].lstrip("*").removeprefix("./")] = parts[0]
        expected = checks.get(args.shard)
        if expected is None:
            raise ValueError("No published SHA256 for selected shard")
        published = json.loads((META / "manifest.json").read_text(encoding="utf-8"))
        shard_record = next(item for item in published["files"] if item["path"] == args.shard)
        if shard_record["bytes"] > args.max_mib * 1048576:
            raise ValueError("Published shard size exceeds explicit download budget")
        if shutil.disk_usage(ROOT).free < shard_record["bytes"] * 4 + 100_000_000:
            raise ValueError("Insufficient workspace disk budget for shard, partial and parsing")
        records.append(fetch(base + args.shard, ROOT / "data/phase2/raw/IL_Replay" / revision / args.shard,
                             expected_sha256=expected, max_bytes=args.max_mib * 1048576))
    previous_path = OUT / "acquisition.json"
    previous = json.loads(previous_path.read_text(encoding="utf-8")) if previous_path.is_file() else {}
    if previous and previous.get("revision") != revision:
        raise ValueError("Existing acquisition report has a different dataset revision")
    by_path = {record["path"]: record for record in previous.get("artifacts", [])}
    by_path.update({record["path"]: record for record in records})
    result = {"repository": REPO, "revision": revision, "dataset_license_metadata": info.get("cardData", {}).get("license", "not_declared"),
              "license_files": sorted(name for name in siblings if "license" in name.lower()),
              "parquet_files": sorted(name for name in siblings if name.endswith(".parquet")), "artifacts": list(by_path.values())}
    (OUT / "acquisition.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({**{key: result[key] for key in ("repository", "revision", "dataset_license_metadata", "license_files")},
                      "published_parquet_files": len(result["parquet_files"]), "selected_shard": args.shard,
                      "artifacts_registered": len(result["artifacts"]), "report": "outputs/phase2/data/acquisition.json"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

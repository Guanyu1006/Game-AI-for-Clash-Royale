"""Fetch small actual expert replay samples and a pinned game-specific ONNX model.

Never unpickle replay data or execute downloaded model/source. Validate source blob
identity, compression/container structure and record any unavailable asset.
"""
from pathlib import Path
import argparse
import ast
import hashlib
import io
import json
import lzma
import shutil
import struct
from urllib.parse import quote
from research_fetch import ROOT, fetch, sha, save_json

def git_blob_sha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--replay-count",type=int,default=2)
    parser.add_argument("--all-replays",action="store_true",help="explicit large download, about 2.02GB compressed at pinned version")
    parser.add_argument("--skip-model",action="store_true")
    args = parser.parse_args()
    folder = ROOT / "references" / "library" / "assets"
    folder.mkdir(exist_ok=True)
    sources = []
    replay_dir = ROOT / "references" / "projects" / "wty-yy__Clash-Royale-Replay-Dataset"
    tree = json.loads((replay_dir / "upstream_tree.json").read_text(encoding="utf-8"))
    repo, commit = "wty-yy/Clash-Royale-Replay-Dataset", tree["sha"]
    ordered = sorted([x for x in tree["tree"] if x["type"] == "blob" and x["path"].endswith(".npy.xz")], key=lambda x:x["size"])
    small = ordered if args.all_replays else ordered[:max(0,args.replay_count)]
    missing_bytes = sum(x["size"] for x in small if not (folder/repo.replace("/","__")/x["path"]).exists())
    if missing_bytes*2 > shutil.disk_usage(ROOT).free:
        raise ValueError("Insufficient disk for requested replay download and validation")
    for item in small:
        sources.append({"kind": "expert_replay_sample", "repository": repo, "commit": commit, **item,
                        "maximum_bytes": max(2_000_000,item["size"]+1), "license": "No LICENSE in saved tree; research sample, reuse terms require source owner clarification"})
    detection_dir = ROOT / "references/projects/wty-yy__Clash-Royale-Detection-Dataset"
    detection_tree = json.loads((detection_dir / "upstream_tree.json").read_text(encoding="utf-8"))
    images = [x for x in detection_tree["tree"] if x["type"]=="blob" and x["path"].startswith("images/part2/OYASSU_20210528_episodes/1/") and x["path"].endswith(".jpg")][:2]
    annotations = [x for x in detection_tree["tree"] if x["path"] in ("images/part2/val_annotation_mini.txt","Clash Royale dataset annotation.md")]
    for item in images+annotations:
        sources.append({"kind":"detection_image_sample" if item["path"].endswith(".jpg") else "detection_annotation_reference",
                        "repository":"wty-yy/Clash-Royale-Detection-Dataset","commit":detection_tree["sha"],**item,
                        "maximum_bytes":2_000_000,"license":"MIT repository; game imagery provenance separately recorded by source",
                        "not_verified":"sample frames not necessarily part of mini validation split; no accuracy claim"})
    native_repo, native_commit = "itzik123/ClashRoyaleAi", "2f172925a15b17f973291f41a789b19a29f0fc43"
    model_path = "perception/clashroyalebuildabot/models/units_M_480x352.onnx"
    native_treepath = ROOT / "references" / "projects" / "itzik123__ClashRoyaleAi" / "upstream_tree.json"
    if native_treepath.exists() and not args.skip_model:
        native_tree = json.loads(native_treepath.read_text(encoding="utf-8"))
        model = next(x for x in native_tree["tree"] if x["path"] == model_path)
        sources.append({"kind": "game_unit_detector_onnx", "repository": native_repo, "commit": native_commit, **model,
                        "maximum_bytes": 40_000_000, "license": "Model embedded metadata: AGPL-3.0; surrounding repository MIT; dataset rights need separate review",
                        "not_verified": "game patch/classes/faction compatibility and actual device accuracy"})
    records = []
    for spec in sources:
        url = f"https://raw.githubusercontent.com/{spec['repository']}/{spec['commit']}/{quote(spec['path'])}"
        local = folder / (spec["repository"].replace("/", "__")) / spec["path"]
        local.parent.mkdir(parents=True, exist_ok=True)
        record = {**spec, "url": url, "accessed": "2026-10-13", "execution_status": "not_executed_or_unpickled",
                  "disk_free_before": shutil.disk_usage(ROOT).free, "download_status": "pending"}
        try:
            data = local.read_bytes() if local.exists() else fetch(url, spec["maximum_bytes"])[0]
            if len(data) != spec["size"] or git_blob_sha(data) != spec["sha"]:
                raise ValueError("Size or Git object SHA1 mismatch against pinned source tree")
            local.write_bytes(data)
            record.update(download_status="downloaded", local=local.relative_to(ROOT).as_posix(), sha256=sha(data), bytes=len(data),
                          verification_status="pinned_git_blob_identity_verified")
            if spec["kind"] == "expert_replay_sample":
                with lzma.LZMAFile(io.BytesIO(data)) as compressed:
                    unpacked = compressed.read(100_000_001)
                if len(unpacked) > 100_000_000 or not unpacked.startswith(b"\x93NUMPY"):
                    raise ValueError("Replay expands beyond budget or is not NPY")
                version = unpacked[6:8]
                header_size = 2 if version == b"\x01\x00" else 4
                headerlen = int.from_bytes(unpacked[8:8+header_size], "little")
                header = ast.literal_eval(unpacked[8+header_size:8+header_size+headerlen].decode("latin-1").strip())
                record.update(npy_version=list(version), npy_header=header, uncompressed_bytes=len(unpacked),
                              compatibility="object dtype requires pickle; payload deliberately not deserialized" if "O" in str(header.get("descr")) else "numeric NPY; not executed")
                record["verification_status"] = "git_blob_xz_and_npy_header_verified_no_pickle"
            elif spec["kind"] == "game_unit_detector_onnx":
                record["verification_status"] = "git_blob_identity_and_size_verified_onnx_not_executed"
                smoke = ROOT / "outputs/research/vision_model_smoke.json"
                if smoke.exists():
                    verified = json.loads(smoke.read_text(encoding="utf-8"))
                    if verified.get("sha256") == record["sha256"]:
                        record.update(execution_status=verified["status"],smoke_report=smoke.relative_to(ROOT).as_posix())
            elif spec["kind"] == "detection_image_sample":
                if not data.startswith(b"\xff\xd8"):
                    raise ValueError("Expected JPEG header")
                record["verification_status"] = "git_blob_identity_and_jpeg_header_verified"
        except Exception as error:
            record.update(download_status="failed", error=repr(error))
        records.append(record)
        save_json(ROOT / "references" / "library" / "assets_manifest.json", {"items": records, "accessed": "2026-10-13"})
        print(json.dumps({k:record.get(k) for k in ("kind", "path", "download_status", "bytes", "verification_status", "error")}, ensure_ascii=False), flush=True)
    save_json(ROOT / "outputs" / "research" / "assets_summary.json", {"items": len(records), "downloaded":sum(x["download_status"]=="downloaded" for x in records),
                                                                           "failed":sum(x["download_status"]!="downloaded" for x in records)})

if __name__ == "__main__":
    main()

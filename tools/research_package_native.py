"""Reconstruct immutable upstream snapshot from agent's raw-blob manifest.

Local patched files are replaced by pinned upstream downloads. Compiled files and
unlisted files are excluded. Original per-file hashes prove provenance.
"""
import io
import json
from pathlib import Path
import zipfile
from research_fetch import ROOT, PROJ, sha, fetch, save_json, project, rebuild
from research_catalog import PROJECTS

def main():
    repo = "itzik123/ClashRoyaleAi"
    spec = next(s for s in PROJECTS if s[0] == repo)
    source = ROOT / "third_party" / "ClashRoyaleAi"
    provenance = json.loads((source / "SOURCE_COMMIT.json").read_text(encoding="utf-8"))
    pinned = provenance["commit"]
    upstream = json.loads((ROOT / "outputs" / "native_engine_download.json").read_text(encoding="utf-8"))
    directory = PROJ / repo.replace("/", "__")
    directory.mkdir(exist_ok=True)
    restored, omitted, included = [], [], []
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as archive:
        for item in upstream:
            expected = item.get("sha256")
            if not expected:
                omitted.append(item)
                continue
            path = item["path"]
            local = source / path
            data = local.read_bytes() if local.exists() else b""
            if sha(data) != expected:
                cache = directory / "upstream_restored" / path
                data = cache.read_bytes() if cache.exists() else fetch(f"https://raw.githubusercontent.com/{repo}/{pinned}/{path}", 50_000_000)[0]
                if sha(data) != expected:
                    raise ValueError(f"Original upstream hash mismatch: {path}")
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_bytes(data)
                restored.append(path)
            archive.writestr("ClashRoyaleAi-" + pinned + "/" + path, data)
            included.append({"path": path, "sha256": expected})
    path = directory / ("source-" + pinned + ".zip")
    path.write_bytes(buff.getvalue())
    details = {"repository": repo, "commit": pinned, "method": "pinned raw blobs reconstructed from verified per-file manifest",
               "included": included, "restored_local_patches_to_original": restored, "omitted": omitted}
    save_json(directory / "source_provenance.json", details)
    # Existing source API metadata remains fixed to the same commit.
    result = project(spec)
    result.update(download_scope="upstream_raw_blob_snapshot_excluding_skipped_large_artifacts",
                  source_files_downloaded=len(included), omitted=omitted,
                  source_provenance=str((directory / "source_provenance.json").relative_to(ROOT)).replace("\\", "/"),
                  execution_status="native engine compiled and tested separately; see docs/audits and outputs/native_engine*",
                  acquisition_method=details["method"], archive_url=None)
    save_json(directory / "record.json", result)
    print(json.dumps({"files": len(included), "restored": restored, "omitted": omitted, "archive_sha256": sha(path.read_bytes())}, ensure_ascii=False, indent=2))
    rebuild()

if __name__ == "__main__":
    main()

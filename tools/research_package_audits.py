"""Package independently verified upstream snapshots without local audit patches."""
import hashlib
import io
import json
import zipfile
from research_catalog import PROJECTS
from research_fetch import ROOT, PROJ, fetch, sha, save_json, project, rebuild

def gitsha(data):
    return hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()

def main():
    manifest = json.loads((ROOT / "outputs/audit_candidates/source_inventory_manifest.json").read_text(encoding="utf-8"))
    repos = {"crforge": "voonhous/crforge", "clash-royale-gym": "MSU-AI/clash-royale-gym"}
    for source in manifest["sources"]:
        if source["name"] not in repos:
            continue
        repo, commit = repos[source["name"]], source["commit"]
        dest = PROJ / repo.replace("/", "__")
        dest.mkdir(exist_ok=True)
        if not (dest / "commit.json").exists():
            save_json(dest / "commit.json", json.loads(fetch(f"https://api.github.com/repos/{repo}/commits/{commit}", 8_000_000)[0]))
        buff = io.BytesIO()
        included, omitted, normalized = [], [], []
        tree_entries = []
        with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as archive:
            for item in source["files"]:
                expected, path = item["expected_git_blob_sha1"], item["path"]
                tree_entries.append({"type":"blob","path":path,"sha":expected})
                local = ROOT / source["path"] / path
                if not item["status"].startswith("verified") or not local.exists():
                    omitted.append(path)
                    continue
                data = local.read_bytes()
                if gitsha(data) != expected:
                    candidate = data.replace(b"\r\n", b"\n")
                    if gitsha(candidate) != expected:
                        raise ValueError("Upstream identity mismatch: " + path)
                    data = candidate
                    normalized.append(path)
                included.append({"path":path,"git_blob_sha1":expected,"sha256":sha(data)})
                archive.writestr(repo.split("/")[1]+"-"+commit+"/"+path,data)
        (dest / ("source-"+commit+".zip")).write_bytes(buff.getvalue())
        save_json(dest / "upstream_tree.json", {"sha":commit,"tree":tree_entries})
        save_json(dest / "source_provenance.json", {"commit":commit,"included":included,"omitted":omitted,"normalized_windows_crlf":normalized,
                                                     "all_included_git_blob_hashes_match":True,"source_manifest":"outputs/audit_candidates/source_inventory_manifest.json"})
        spec = next(x for x in PROJECTS if x[0]==repo)
        result = project(spec)
        result.update(download_scope="audited_upstream_source_snapshot", omitted=omitted,
                      acquisition_method="local independently verified source, Windows CRLF normalized only after matching original Git blob hash",
                      execution_status="crforge compiled, 974 tests passed and 3 full episodes" if source["name"]=="crforge" else "Python3.11 install passed; gym.make/reset/step failed due upstream skeleton",
                      audit_document="docs/audits/CRFORGE.md" if source["name"]=="crforge" else "docs/audits/MSU_GYM.md",
                      source_provenance=(dest/"source_provenance.json").relative_to(ROOT).as_posix(),archive_url=None)
        save_json(dest / "record.json", result)
        print(json.dumps({"repo":repo,"included":len(included),"omitted":len(omitted),"sha256":result["sha256"]}))
    print(json.dumps(rebuild()))

if __name__ == "__main__":
    main()

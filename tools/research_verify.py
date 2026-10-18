"""Offline acceptance of corpus minimums and every delivered artifact's integrity.

Unlike strict --verify in the downloader, known unavailable optional sources are
reported without failing the 30-paper/15-source-project acceptance target.
"""
import ast
import hashlib
import json
from pathlib import Path
import re
import zipfile
from research_fetch import ROOT, rebuild, save_json

def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    summary = rebuild()
    manifest = json.loads((ROOT/"references/library/manifest.json").read_text(encoding="utf-8"))
    failures, verified = [], 0
    for item in manifest["items"]:
        if item["download_status"] != "downloaded":
            continue
        path = ROOT/item["local"]
        if not path.exists() or sha(path)!=item["sha256"]:
            failures.append("missing/hash mismatch: "+item["local"])
            continue
        if item["kind"]=="paper":
            if not path.read_bytes().startswith(b"%PDF") or item.get("pages",0)<1 or not item.get("authors"):
                failures.append("invalid PDF/metadata: "+item["id"])
            if item.get("extracted_text") and sha(ROOT/item["extracted_text"])!=item["text_sha256"]:
                failures.append("text hash mismatch: "+item["id"])
        else:
            with zipfile.ZipFile(path) as archive:
                if archive.testzip():
                    failures.append("ZIP CRC mismatch: "+item["repository"])
            if not re.fullmatch(r"[0-9a-f]{40}",item.get("commit","")):
                failures.append("project is not pinned: "+item["repository"])
        verified+=1
    assets = json.loads((ROOT/"references/library/assets_manifest.json").read_text(encoding="utf-8"))["items"]
    for asset in assets:
        if asset["download_status"]=="downloaded" and sha(ROOT/asset["local"])!=asset["sha256"]:
            failures.append("asset hash mismatch: "+asset["path"])
    deep = (ROOT/"docs/research/DEEP_READS.md").read_text(encoding="utf-8")
    deep_count=len(re.findall(r"^## \d+\.",deep,re.M))
    refs = []
    for filename in ("docs/research/INDEX.md","docs/research/DEEP_READS.md","docs/research/DATASETS.md","docs/LEARNING_PATH.md"):
        text=(ROOT/filename).read_text(encoding="utf-8")
        for target in re.findall(r"\]\(<([^>]+)>\)",text):
            if target.startswith("D:/") and not Path(target.split(":",2)[0]+":"+target.split(":",2)[1]).exists():
                failures.append("broken local link: "+target)
        refs.append(filename)
    minimums = summary["pdf_parsed"]>=30 and summary["source_archives_downloaded"]>=15 and deep_count>=10
    report={**summary,"verified_artifacts":verified,"assets_downloaded":sum(a["download_status"]=="downloaded" for a in assets),
            "deep_reads":deep_count,"minimums_30_papers_15_projects_10_deep_reads":minimums,
            "integrity_failures":failures,"status":"passed" if minimums and not failures else "failed",
            "known_unavailable_or_partial":summary["failures"],"not_claimed":"paper chess-strength replication, complete datasets, or true-game detector accuracy"}
    save_json(ROOT/"outputs/research/corpus_acceptance.json",report)
    print(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if report["status"]=="passed" else 1

if __name__=="__main__":
    raise SystemExit(main())

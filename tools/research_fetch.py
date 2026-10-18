"""Resumable primary-source PDF/source corpus. No files outside this project.

Usage: python tools/research_fetch.py [--papers|--projects|--verify]
Read-only metadata/PDF/source download; third-party source is never executed here.
"""
from __future__ import annotations
import argparse
import hashlib
import http.client
import html
import io
import json
import os
from pathlib import Path
import re
import shutil
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed

from research_catalog import PAPERS, PAPER_URLS, PROJECTS

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "references" / "library"
PROJ = ROOT / "references" / "projects"
OUT = ROOT / "outputs" / "research"
DATE = "2026-10-13"
OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))
SOURCE_ONLY = False
for directory in [LIB, PROJ, OUT, ROOT / ".cache" / "research", ROOT / "tmp" / "research"]:
    directory.mkdir(parents=True, exist_ok=True)
os.environ["TMP"] = os.environ["TEMP"] = str(ROOT / "tmp" / "research")
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"

def sha(data):
    return hashlib.sha256(data).hexdigest()

def save_json(path, obj):
    temp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    temp.write_text(json.dumps(obj, indent=2, ensure_ascii=False), encoding="utf-8")
    temp.replace(path)

def fetch(url, max_bytes=350_000_000):
    headers = {"User-Agent": "RoyaleResearchLab/0.2 (public research archive)", "Accept": "*/*"}
    for attempt in range(3):
        try:
            # Raw blobs/metadata work through the configured proxy on this host;
            # large codeload transfers are the path known to need a direct opener.
            operation = OPENER.open if "codeload.github.com" in url else urllib.request.urlopen
            with operation(urllib.request.Request(url, headers=headers), timeout=35) as response:
                size = response.headers.get("Content-Length")
                if size and int(size) > max_bytes:
                    raise ValueError(f"Resource exceeds configured limit {max_bytes}")
                chunks, total, started = [], 0, time.monotonic()
                while True:
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise ValueError("Streaming size limit exceeded")
                    if time.monotonic() - started > 120:
                        raise ValueError("Transfer exceeded 120-second total budget; retry is resumable by artifact")
                    chunks.append(chunk)
                return b"".join(chunks), dict(response.headers), response.geturl()
        except (urllib.error.URLError, TimeoutError, OSError, http.client.IncompleteRead, http.client.RemoteDisconnected) as error:
            if attempt == 2:
                raise
            time.sleep(2 + attempt * 3)

def rel(path):
    return path.relative_to(ROOT).as_posix()

def meta_values(page, name):
    return [html.unescape(x) for x in re.findall(r'<meta\s+name="' + name + r'"\s+content="(.*?)"', page)]

def paper(spec):
    key, arxiv, expected, group, problem, method, result, limitation, application = spec
    directory = LIB / key
    directory.mkdir(exist_ok=True)
    priorpath = directory / "record.json"
    prior = json.loads(priorpath.read_text(encoding="utf-8")) if priorpath.exists() else {}
    record = {"kind": "paper", "id": key, "title": expected, "group": group,
              "arxiv": arxiv, "accessed": DATE, "download_status": "pending",
              "verification_status": "not_checked", "license": "publisher terms; see saved source metadata",
              "problem_io": problem, "method": method, "author_evaluation": result,
              "limitation": limitation, "application": application, "locally_reproduced": False}
    try:
        if arxiv:
            source_url = f"https://arxiv.org/abs/{arxiv}"
            pagepath = directory / "source.html"
            pageblob = pagepath.read_bytes() if pagepath.exists() else fetch(source_url, 2_000_000)[0]
            pagepath.write_bytes(pageblob)
            page = pageblob.decode("utf-8")
            title = meta_values(page, "citation_title")
            if title:
                record["title"] = title[0]
            record["authors"] = meta_values(page, "citation_author")
            dates = meta_values(page, "citation_date")
            record["year"] = dates[0][:4] if dates else "unknown"
            versions = re.findall(re.escape(arxiv) + r"v(\d+)", page)
            version = "v" + str(max(map(int, versions))) if versions else "v1"
            record["version"] = arxiv + version
            record["doi"] = "10.48550/arXiv." + arxiv
            licenses = re.findall(r'href="([^"]+)"[^>]*>\s*view license', page)
            if licenses:
                record["license_url"] = licenses[0]
                record["license"] = licenses[0]
            pdf_url = f"https://arxiv.org/pdf/{arxiv}{version}"
            record["source_metadata"] = rel(pagepath)
            record["source_metadata_sha256"] = sha(pageblob)
        else:
            source_url = pdf_url = PAPER_URLS[key]
            record["version"] = "publisher/openreview source retrieved on " + DATE
            record["authors"] = {"seat": ["Tianyu Liu", "Zijie Zheng", "Hongchang Li", "Kaigui Bian", "Lingyang Song"],
                                 "pomcp": ["David Silver", "Joel Veness"],
                                 "r2d2": ["Steven Kapturowski", "Georg Ostrovski", "John Quan", "Rémi Munos", "Will Dabney"]}[key]
            record["year"] = {"seat": "2019", "pomcp": "2010", "r2d2": "2019"}[key]
            record["doi"] = {"seat": "10.24963/ijcai.2019/631", "pomcp": None, "r2d2": None}[key]
        record["url"], record["pdf_url"] = source_url, pdf_url
        pdfpath = directory / (key + ".pdf")
        old_files = {"seat": "ijcai2019-seat.pdf", "katacr": "katacr-2504.04783v1.pdf",
                     "decision_transformer": "decision-transformer-2106.01345.pdf", "ppo": "ppo-1707.06347.pdf"}
        if not pdfpath.exists() and key in old_files:
            old = ROOT / "references" / "papers" / old_files[key]
            if old.exists():
                shutil.copyfile(old, pdfpath)
                record["reused_from"] = rel(old)
                # Older files may be a different version; provenance is explicit.
                record["pdf_version"] = "prior_download_unversioned" if key in ("ppo", "decision_transformer") else record["version"]
        blob = pdfpath.read_bytes() if pdfpath.exists() else fetch(pdf_url, 80_000_000)[0]
        if not blob.startswith(b"%PDF"):
            raise ValueError("Not a PDF (magic-byte check)")
        pdfpath.write_bytes(blob)
        record.update(download_status="downloaded", local=rel(pdfpath), sha256=sha(blob), bytes=len(blob))
        try:
            from pypdf import PdfReader
            reader = PdfReader(io.BytesIO(blob))
            texts = [(p.extract_text() or "") for p in reader.pages]
            textpath = directory / "extracted.txt"
            textpath.write_text("\n".join(f"\n--- PDF PAGE {i+1} ---\n{txt}" for i, txt in enumerate(texts)), encoding="utf-8")
            record.update(verification_status="pdf_parsed_text_extracted", pages=len(texts),
                          text_chars=sum(map(len, texts)), extracted_text=rel(textpath), text_sha256=sha(textpath.read_bytes()))
            if arxiv:
                found = re.search(r"arXiv:\s*" + re.escape(arxiv) + r"v\d+", texts[0])
                if found:
                    record["pdf_version"] = re.sub(r"arXiv:\s*", "", found.group())
                    record["pdf_version_evidence"] = "parsed PDF first-page arXiv identifier"
            if key == "seat":
                record["author_block_raw"] = texts[0][:1000]
        except ImportError:
            textpath = directory / "extracted.txt"
            if (prior.get("sha256") == record["sha256"] and textpath.exists()
                    and prior.get("text_sha256") == sha(textpath.read_bytes())):
                for field in ("pages", "text_chars", "extracted_text", "text_sha256", "pdf_version", "pdf_version_evidence"):
                    if field in prior:
                        record[field] = prior[field]
                record["verification_status"] = "pdf_parsed_text_extracted"
                record["cached_text_used"] = True
            else:
                record["verification_status"] = "pdf_magic_sha_only_no_parser"
        except Exception as error:
            record["verification_status"] = "parse_failed"
            record["parse_error"] = repr(error)
    except Exception as error:
        record.update(download_status="failed", error=repr(error))
    save_json(directory / "record.json", record)
    return record

SOURCE_NEEDLES = {
    "KataCR": ["sar_builder", "action_builder", "state_builder", "offline/train", "combo_detect", "card_list"],
    "ClashRoyaleBuildABot": ["state", "controller", "bot", "action", "constants", "detector", "cards"],
    "ClashRoyaleAi": ["engine", "game_state", "bindings", "environment", "gym", "lookahead", "cards", "combat"],
    "Clash-Royale-Detection-Dataset": ["generator", "constant", "label", "dataset"],
    "Clash-Royale-Replay-Dataset": ["README", "dataset", "replay"],
    "stable-baselines3": ["stable_baselines3/ppo/ppo.py", "rollout", "buffers.py", "on_policy_algorithm", "policies.py"],
    "stable-baselines3-contrib": ["ppo_mask.py", "ppo_recurrent.py", "maskable", "recurrent"],
    "cleanrl": ["cleanrl/ppo.py", "cleanrl/ppo_atari", "cleanrl/ppo_continuous"],
    "Gymnasium": ["gymnasium/core.py", "spaces/discrete.py", "env_checker.py", "vector_env.py"],
    "PettingZoo": ["utils/env.py", "parallel_test.py", "api_test.py", "conversions.py"],
    "open_spiel": ["python/algorithms/mcts.py", "psro_v2", "nfsp.py", "policy.py", "spiel.h"],
    "imitation": ["algorithms/bc.py", "algorithms/dagger.py", "algorithms/adversarial/gail.py", "data/types.py"],
    "d3rlpy": ["iql", "cql", "dataset", "qlearning/base.py"],
    "Minari": ["data_collector", "minari_dataset", "episode_data", "dataset_metadata"],
    "mctx": ["_src/search.py", "_src/policies.py", "_src/base.py", "_src/tree.py"],
    "pomdp-py": ["pomcp", "po_uct", "basics.pyx", "belief", "tiger_problem.py"],
    "ultralytics": ["engine/model.py", "engine/predictor.py", "models/yolo/detect/train.py", "trackers/byte_tracker.py"],
    "PaddleOCR": ["_pipelines/ocr.py", "_models/text_detection", "_models/text_recognition", "rec_postprocess", "db_postprocess"],
    "ByteTrack": ["tracker/byte_tracker.py", "tracker/kalman_filter.py", "tracker/matching.py", "basetrack.py"],
}

def source_subset(repo, pinned, directory):
    treepath = directory / "upstream_tree.json"
    if treepath.exists():
        tree = json.loads(treepath.read_text(encoding="utf-8"))
    else:
        tree = json.loads(fetch(f"https://api.github.com/repos/{repo}/git/trees/{pinned}?recursive=1", 20_000_000)[0])
        save_json(treepath, tree)
    needles = SOURCE_NEEDLES.get(repo.split("/")[1], [])
    eligible = [x for x in tree["tree"] if x["type"] == "blob" and x.get("size", 0) < 1_000_000]
    def priority(item):
        path, basename = item["path"], Path(item["path"]).name.lower()
        if "/" not in path and basename.startswith(("license", "copying", "readme", "requirements", "pyproject", "setup")):
            return 0
        if any(n in path for n in needles) and path.endswith((".py", ".pyx", ".h", ".hpp", ".cpp")):
            return 1
        if basename.startswith(("license", "copying")):
            return 2
        return 3
    selected = sorted([x for x in eligible if priority(x) < 3], key=lambda x: (priority(x), x["path"]))[:24]
    errors, downloaded = [], []
    def one(item):
        cachepath = directory / "source_subset" / item["path"]
        cachepath.parent.mkdir(parents=True, exist_ok=True)
        try:
            if not cachepath.exists():
                data = fetch(f"https://raw.githubusercontent.com/{repo}/{pinned}/{item['path']}", 2_000_000)[0]
                cachepath.write_bytes(data)
            return item, cachepath.read_bytes(), None
        except Exception as error:
            return item, None, repr(error)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(one, selected))
    buff = io.BytesIO()
    with zipfile.ZipFile(buff, "w", zipfile.ZIP_DEFLATED) as subset:
        for item, data, error in results:
            if error:
                errors.append({"path": item["path"], "error": error})
            else:
                subset.writestr(repo.split("/")[1] + "-" + pinned + "/" + item["path"], data)
                downloaded.append(item["path"])
    if not downloaded:
        raise ValueError("No selected source files could be downloaded")
    return buff.getvalue(), {"download_scope": "fixed_code_license_dependency_subset",
        "source_files_selected": len(selected), "source_files_downloaded": len(downloaded),
        "subset_download_errors": errors, "source_paths": downloaded,
        "omitted": "unselected source, media, datasets and pretrained models; complete upstream git tree saved",
        "archive_url": None, "source_blob_base": f"https://raw.githubusercontent.com/{repo}/{pinned}/"}

def project(spec):
    repo, group, modules, dependencies, cost = spec
    directory = PROJ / repo.replace("/", "__")
    directory.mkdir(exist_ok=True)
    record = {"kind": "project", "repository": repo, "title": repo, "url": "https://github.com/" + repo,
              "group": group, "reusable_modules": modules, "dependencies": dependencies, "modification_cost": cost,
              "accessed": DATE, "download_status": "pending", "execution_status": "not_installed_or_executed_by_research_fetch",
              "local_validation": "source archive CRC, path safety, source inventory and license discovery only"}
    try:
        metadata = directory / "commit.json"
        if metadata.exists():
            commit = json.loads(metadata.read_text(encoding="utf-8"))
        else:
            commit = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits?per_page=1", 8_000_000)[0])[0]
            save_json(metadata, commit)
        pinned = commit["sha"]
        record.update(commit=pinned, latest_commit_date=commit["commit"]["committer"]["date"],
                      author=commit["commit"]["author"]["name"])
        if repo == "wty-yy/Clash-Royale-Replay-Dataset" and (directory / "upstream_tree.json").exists():
            tree = json.loads((directory / "upstream_tree.json").read_text(encoding="utf-8"))
            datafiles = [x for x in tree["tree"] if x["type"] == "blob"]
            record.update(download_status="partial_dataset", verification_status="fixed_tree_saved_actual_samples_in_assets_manifest",
                          download_scope="data_only_repository; complete tree plus small samples",
                          dataset_files=len(datafiles), dataset_bytes=sum(x.get("size",0) for x in datafiles),
                          license="No LICENSE in fixed upstream tree", tree=rel(directory / "upstream_tree.json"),
                          assets_manifest="references/library/assets_manifest.json",
                          remaining="complete compressed expert dataset approximately 2.02GB not downloaded; object NPY payload not converted",
                          execution_status="not_unpickled_or_used_for_training")
            save_json(directory / "record.json", record)
            return record
        url = f"https://codeload.github.com/{repo}/zip/{pinned}"
        path = directory / f"source-{pinned}.zip"
        record["download_scope"] = "full_source_archive"
        if path.exists():
            blob = path.read_bytes()
            priorpath = directory / "record.json"
            if priorpath.exists():
                prior = json.loads(priorpath.read_text(encoding="utf-8"))
                for field in ("download_scope", "source_files_selected", "source_files_downloaded", "omitted", "source_blob_base", "archive_url",
                              "execution_status", "audit_document", "source_provenance", "acquisition_method"):
                    if field in prior:
                        record[field] = prior[field]
        elif SOURCE_ONLY or repo == "Pbatch/ClashRoyaleBuildABot":
            blob, scope = source_subset(repo, pinned, directory)
            record.update(scope)
        else:
            blob = fetch(url)[0]
        archive = zipfile.ZipFile(io.BytesIO(blob))
        bad = archive.testzip()
        if bad:
            raise ValueError("ZIP CRC error: " + bad)
        for info in archive.infolist():
            parts = Path(info.filename).parts
            if Path(info.filename).is_absolute() or ".." in parts or info.filename.startswith("/"):
                raise ValueError("Unsafe archive path")
        path.write_bytes(blob)
        names = archive.namelist()
        blob_checks = []
        treepath = directory / "upstream_tree.json"
        if treepath.exists():
            expected = {x["path"]: x.get("sha") for x in json.loads(treepath.read_text(encoding="utf-8"))["tree"] if x["type"] == "blob"}
            for name in names:
                if name.endswith("/") or "/" not in name:
                    continue
                upstream_path = name.split("/", 1)[1]
                if upstream_path in expected:
                    data = archive.read(name)
                    gitsha = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
                    if gitsha != expected[upstream_path]:
                        raise ValueError("Git source blob identity mismatch: " + upstream_path)
                    blob_checks.append({"path": upstream_path, "git_blob_sha1": gitsha, "sha256": sha(data)})
        if blob_checks:
            save_json(directory / "source_file_checksums.json", blob_checks)
            record["verified_source_blob_count"] = len(blob_checks)
            record["source_file_checksums"] = rel(directory / "source_file_checksums.json")
        licenses = [name for name in names if Path(name).name.lower() in
                    ("license", "license.txt", "license.md", "copying", "copying.md", "copying.txt")]
        # Store license/readme verbatim separately; no third-party code execution.
        artifacts = []
        for name in licenses + [n for n in names if n.lower().endswith("/readme.md") and n.count("/") == 1]:
            data = archive.read(name)
            outfile = directory / "metadata" / name.split("/", 1)[-1]
            outfile.parent.mkdir(parents=True, exist_ok=True)
            outfile.write_bytes(data)
            artifacts.append({"archive_path": name, "local": rel(outfile), "sha256": sha(data)})
        license_short = "NO LICENSE FILE FOUND: reuse not authorized by this archive"
        if licenses:
            lic = archive.read(licenses[0]).decode("utf-8", errors="replace")
            license_short = next((name for marker, name in [("GNU AFFERO", "AGPL"), ("GNU GENERAL PUBLIC", "GPL"),
                                 ("Apache License", "Apache-2.0"), ("MIT License", "MIT"),
                                 ("Permission is hereby granted", "MIT-style"), ("Redistribution and use", "BSD-style")]
                                 if marker in lic), "see saved license text")
        inventory = {"files": len([n for n in names if not n.endswith("/")]),
                     "python_sources": sum(n.endswith(".py") for n in names),
                     "cpp_sources": sum(n.endswith((".cpp", ".hpp", ".cc", ".h")) for n in names),
                     "archive_paths": names}
        save_json(directory / "inventory.json", inventory)
        record.update(download_status="downloaded", verification_status="zip_crc_and_safe_paths_checked",
                      archive_url=record.get("archive_url", url), local=rel(path), sha256=sha(blob), bytes=len(blob),
                      files=inventory["files"], python_sources=inventory["python_sources"], cpp_sources=inventory["cpp_sources"],
                      license=license_short, license_files=licenses, metadata_files=artifacts,
                      inventory=rel(directory / "inventory.json"))
    except Exception as error:
        record.update(download_status="failed", error=repr(error))
    save_json(directory / "record.json", record)
    return record

def rebuild():
    records = [json.loads(p.read_text(encoding="utf-8")) for folder in (LIB, PROJ) for p in sorted(folder.glob("*/record.json"))]
    manifest = {"schema": 1, "accessed": DATE, "root": str(ROOT), "items": records,
                "disclaimer": "Downloaded/parsed != locally reproduced. Original author claims are separated from local validation."}
    save_json(LIB / "manifest.json", manifest)
    papers = [r for r in records if r["kind"] == "paper"]
    projects = [r for r in records if r["kind"] == "project"]
    summary = {"papers": len(papers), "pdf_downloaded": sum(r["download_status"] == "downloaded" for r in papers),
               "pdf_parsed": sum(r.get("verification_status") == "pdf_parsed_text_extracted" for r in papers),
               "projects": len(projects), "source_archives_downloaded": sum(r["download_status"] == "downloaded" for r in projects),
               "full_source_archives": sum(r["download_status"] == "downloaded" and r.get("download_scope") == "full_source_archive" for r in projects),
               "source_subsets": sum(r["download_status"] == "downloaded" and r.get("download_scope") != "full_source_archive" for r in projects),
               "failures": [r.get("id", r.get("repository")) for r in records if r["download_status"] != "downloaded"],
               "total_downloaded_bytes": sum(r.get("bytes", 0) for r in records)}
    save_json(OUT / "download_summary.json", summary)
    return summary

def verify():
    summary = rebuild()
    records = json.loads((LIB / "manifest.json").read_text(encoding="utf-8"))["items"]
    bad = []
    for record in records:
        if record["download_status"] != "downloaded":
            continue
        path = ROOT / record["local"]
        if not path.exists() or sha(path.read_bytes()) != record["sha256"]:
            bad.append(record.get("id", record.get("repository")))
    summary["sha_mismatch"] = bad
    summary["verified_files"] = sum(r["download_status"] == "downloaded" for r in records)
    save_json(OUT / "verification.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 1 if bad or summary["failures"] else 0

def main():
    global SOURCE_ONLY
    parser = argparse.ArgumentParser()
    parser.add_argument("--papers", action="store_true")
    parser.add_argument("--projects", action="store_true")
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--only", help="comma-separated paper ids or owner/repository names")
    parser.add_argument("--source-only", action="store_true", help="download fixed important source/license subsets instead of full archives")
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    SOURCE_ONLY = args.source_only
    if args.verify:
        raise SystemExit(verify())
    all_items = not args.papers and not args.projects
    jobs = [("paper", spec) for spec in PAPERS] if args.papers or all_items else []
    if args.projects or all_items:
        jobs += [("project", spec) for spec in PROJECTS]
    if args.only:
        wanted = set(args.only.split(","))
        jobs = [(kind, spec) for kind, spec in jobs if spec[0] in wanted]
    with (OUT / "download_log.jsonl").open("a", encoding="utf-8") as log:
        def run(job):
            kind, spec = job
            before = time.perf_counter()
            record = paper(spec) if kind == "paper" else project(spec)
            return kind, before, record
        pool = ThreadPoolExecutor(max_workers=max(1, args.workers))
        futures = [pool.submit(run, job) for job in jobs]
        for future in as_completed(futures):
            kind, before, record = future.result()
            event = {"id": record.get("id", record.get("repository")), "kind": kind,
                     "status": record["download_status"], "seconds": round(time.perf_counter() - before, 3),
                     "bytes": record.get("bytes", 0), "error": record.get("error")}
            log.write(json.dumps(event, ensure_ascii=False) + "\n")
            log.flush()
            print(json.dumps(event, ensure_ascii=False), flush=True)
            rebuild()
            if kind == "paper":
                time.sleep(0.8)
        pool.shutdown()
    print(json.dumps(rebuild(), ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()

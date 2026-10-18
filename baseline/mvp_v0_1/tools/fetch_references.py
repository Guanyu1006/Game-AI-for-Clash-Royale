"""Download small pinned source excerpts and papers; all writes remain local."""
from pathlib import Path
import hashlib
import json
import urllib.request

ROOT = Path(__file__).resolve().parent.parent
def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "RoyaleAILab-research"})
    with urllib.request.urlopen(req, timeout=60) as response:
        return response.read()

def main():
    manifest = []
    specs = [
        ("wty-yy/KataCR", "master", ["LICENSE", "katacr/constants/card_list.py"]),
        ("Pbatch/ClashRoyaleBuildABot", "main", ["LICENSE.md"]),
        ("itzik123/ClashRoyaleAi", "main", ["LICENSE"]),
    ]
    for repo, branch, files in specs:
        sha = json.loads(fetch(f"https://api.github.com/repos/{repo}/commits/{branch}"))["sha"]
        for name in files:
            url = f"https://raw.githubusercontent.com/{repo}/{sha}/{name}"
            try:
                blob = fetch(url)
            except Exception as error:
                manifest.append({"repository": repo, "commit": sha, "path": name, "error": str(error)})
                continue
            dest = ROOT / "references" / "upstream" / repo.split("/")[1] / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            manifest.append({"repository": repo, "commit": sha, "url": url,
                             "local": dest.relative_to(ROOT).as_posix(),
                             "sha256": hashlib.sha256(blob).hexdigest(), "executed": False})
    papers = [
        ("ijcai2019-seat.pdf", "https://www.ijcai.org/proceedings/2019/0631.pdf"),
        ("katacr-2504.04783v1.pdf", "https://arxiv.org/pdf/2504.04783v1"),
        ("decision-transformer-2106.01345.pdf", "https://arxiv.org/pdf/2106.01345"),
        ("ppo-1707.06347.pdf", "https://arxiv.org/pdf/1707.06347"),
    ]
    for name, url in papers:
        try:
            blob = fetch(url)
            if not blob.startswith(b"%PDF"):
                raise ValueError("Expected PDF")
            dest = ROOT / "references" / "papers" / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(blob)
            manifest.append({"url": url, "local": dest.relative_to(ROOT).as_posix(),
                             "sha256": hashlib.sha256(blob).hexdigest()})
        except Exception as error:
            manifest.append({"url": url, "error": str(error)})
    (ROOT / "references").mkdir(exist_ok=True)
    (ROOT / "references" / "manifest.json").write_text(
        json.dumps({"accessed": "2026-10-13", "items": manifest}, indent=2), encoding="utf-8")
    print(json.dumps(manifest, indent=2))

if __name__ == "__main__":
    main()

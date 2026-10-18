"""Download fixed upstream source archives to workspace and record provenance."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
CANDIDATES = {
    'crforge': ('voonhous/crforge', 'b41cf9b6276945152ec4c869ce56f98a5fa3f513'),
    'KataCR': ('wty-yy/KataCR', '36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8'),
    'SlashRoyaleLegacy': ('avoidr/SlashRoyaleLegacy', 'a47d02474efc755427c8416b15e96daa9331d993'),
}

def download(entry):
    name, (repo, commit) = entry
    target = ROOT / 'third_party' / 'audit_candidates' / name
    archive = ROOT / '.cache' / 'audit' / (name + '-' + commit + '.zip')
    archive.parent.mkdir(parents=True, exist_ok=True)
    url = f'https://codeload.github.com/{repo}/zip/{commit}'
    row = {'name': name, 'repo': repo, 'commit': commit, 'url': url,
           'access_date': '2026-10-13', 'path': str(target.relative_to(ROOT)),
           'archive': str(archive.relative_to(ROOT)),
           'git_clone_attempt': 'cancelled after several minutes without source transfer; fixed-commit archive fallback'}
    try:
        req = urllib.request.Request(url, headers={'User-Agent': 'RoyaleResearchAudit/1.0'})
        # The Windows registry proxy stalled large codeload bodies during this audit.
        # A request-local direct opener avoids changing system network settings.
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(req, timeout=40) as response, archive.open('wb') as f:
            shutil.copyfileobj(response, f)
        row['sha256'] = hashlib.sha256(archive.read_bytes()).hexdigest()
        row['bytes'] = archive.stat().st_size
        with zipfile.ZipFile(archive) as z:
            prefix = z.namelist()[0]
            for member in z.infolist():
                rel = Path(member.filename).relative_to(prefix)
                destination = target / rel
                if member.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                else:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, destination.open('wb') as out:
                        shutil.copyfileobj(src, out)
        row['status'] = 'downloaded_zip_valid'
        (target / 'UPSTREAM_PIN.json').write_text(json.dumps(row, indent=2), encoding='utf-8')
    except Exception as exc:
        row['status'] = 'failed'
        row['error'] = repr(exc)
    print(json.dumps(row), flush=True)
    return row

if __name__ == '__main__':
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        rows = list(pool.map(download, CANDIDATES.items()))
    out = ROOT / 'outputs' / 'audit_candidates' / 'source_manifest.json'
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(rows, indent=2), encoding='utf-8')

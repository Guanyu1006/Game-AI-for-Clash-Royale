"""Fixed-commit source-only GitHub snapshot, omitting binary media/client assets."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
from pathlib import Path
import urllib.request
import subprocess

from audit_download_candidates import ROOT, CANDIDATES

EXTENSIONS = {'.py', '.java', '.cs', '.csproj', '.sln', '.json', '.kts', '.toml',
              '.properties', '.jar', '.bat', '.sh', '.md', '.txt', '.js', '.csv', '.yml', '.yaml', '.xml'}
SPECIAL = {'LICENSE', 'NOTICE', 'gradlew', 'requirements.txt', '.gitignore'}

def fetch(name):
    repo, commit = CANDIDATES[name]
    target = ROOT / 'third_party' / 'audit_candidates' / name
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    request = urllib.request.Request(f'https://api.github.com/repos/{repo}/git/trees/{commit}?recursive=1',
                                    headers={'User-Agent': 'RoyaleResearchAudit/1.0'})
    # API metadata works via the user's configured proxy; raw blobs work direct.
    gitdir = ROOT / 'third_party/audit_candidates' / (name + '_git_sparse')
    if (gitdir / '.git').exists():
        raw = subprocess.check_output(['git', '-C', str(gitdir), 'ls-tree', '-r', commit], text=True)
        entries = []
        for line in raw.splitlines():
            meta, path = line.split('\t', 1)
            mode, kind, sha = meta.split()
            entries.append({'type':kind, 'sha':sha, 'path':path})
        tree = {'tree':entries}
    else:
        with urllib.request.urlopen(request, timeout=20) as response:
            tree = json.load(response)
    allfiles = [n for n in tree['tree'] if n['type'] == 'blob']
    chosen = [n for n in allfiles if (Path(n['path']).suffix.lower() in EXTENSIONS or Path(n['path']).name in SPECIAL)
              and n.get('size', 0) < 8_000_000 and not n['path'].endswith('package-lock.json')]
    manifest = {'name': name, 'repo': repo, 'commit': commit, 'access_date': '2026-10-13',
                'status': 'source_snapshot', 'snapshot_selection': 'source and configuration <8MB; binary media/client assets omitted',
                'all_blob_count': len(allfiles), 'selected_blob_count': len(chosen), 'files': [],
                'omitted': [n for n in allfiles if n not in chosen]}
    def one(n):
        rel = n['path']
        row = {'path': rel, 'git_blob_sha': n['sha'], 'bytes_expected': n.get('size')}
        try:
            out = target / rel
            if out.exists():
                existing = out.read_bytes()
                existing_sha = hashlib.sha1(b'blob '+str(len(existing)).encode()+b'\0'+existing).hexdigest()
                if existing_sha == n['sha']:
                    row.update(status='verified_existing_source', bytes=len(existing), sha256=hashlib.sha256(existing).hexdigest())
                    return row
            req = urllib.request.Request(f'https://raw.githubusercontent.com/{repo}/{commit}/{rel}',
                                         headers={'User-Agent': 'RoyaleResearchAudit/1.0'})
            data = opener.open(req, timeout=20).read()
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            row.update(status='downloaded', bytes=len(data), sha256=hashlib.sha256(data).hexdigest())
        except Exception as e:
            row.update(status='failed', error=repr(e))
        return row
    with concurrent.futures.ThreadPoolExecutor(max_workers=12) as pool:
        pending = [pool.submit(one, n) for n in chosen]
        for i, future in enumerate(concurrent.futures.as_completed(pending)):
            row = future.result()
            manifest['files'].append(row)
            if i % 25 == 0:
                print(name, i, '/', len(chosen), row['status'], flush=True)
    target.mkdir(parents=True, exist_ok=True)
    (target / 'UPSTREAM_PIN.json').write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    out = ROOT / 'outputs' / 'audit_candidates' / (name + '_source_manifest.json')
    out.write_text(json.dumps(manifest, indent=2), encoding='utf-8')
    print(name, 'complete', sum(r['status'] in ('downloaded', 'verified_existing_source') for r in manifest['files']), flush=True)

if __name__ == '__main__':
    import sys
    for name in sys.argv[1:] or CANDIDATES:
        fetch(name)

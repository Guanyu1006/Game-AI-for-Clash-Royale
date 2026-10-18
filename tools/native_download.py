"""Fetch a fixed public engine snapshot; all cache/output paths are local."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
COMMIT = '2f172925a15b17f973291f41a789b19a29f0fc43'
DEST = ROOT / 'third_party' / 'ClashRoyaleAi'
TREE = json.loads((ROOT / 'outputs' / 'native_engine_tree.json').read_text(encoding='utf-8'))['tree']

def fetch(entry):
    name = entry['path']
    out = DEST / name
    if out.exists() and out.stat().st_size == entry.get('size'):
        return {'path': name, 'status': 'existing', 'sha256': hashlib.sha256(out.read_bytes()).hexdigest()}
    error = None
    for attempt in range(4):
        try:
            url = f'https://raw.githubusercontent.com/itzik123/ClashRoyaleAi/{COMMIT}/{name}'
            req = urllib.request.Request(url, headers={'User-Agent': 'RoyaleResearch/1.0'})
            data = urllib.request.urlopen(req, timeout=35).read()
            if len(data) != entry['size']:
                raise ValueError(f'wrong size: {len(data)} != {entry["size"]}')
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_bytes(data)
            return {'path': name, 'status': 'downloaded', 'sha256': hashlib.sha256(data).hexdigest()}
        except Exception as exc:
            error = f'{type(exc).__name__}: {exc}'
            time.sleep(attempt + 1)
    print(f'FAILED {name}: {error}', flush=True)
    return {'path': name, 'status': 'failed', 'error': error}

if __name__ == '__main__':
    # No bundled model is needed for headless native physics. Preserve all source,
    # tests, documentation and small fixtures; large media/model weights are manifest-only.
    entries = [x for x in TREE if x['type'] == 'blob' and x.get('size', 0) < 5_000_000]
    entries.sort(key=lambda x: 0 if x['path'].startswith(('include/', 'src/', 'tests/')) else 1)
    results = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for result in pool.map(fetch, entries):
            results.append(result)
            if len(results) % 25 == 0:
                print(f'{len(results)}/{len(entries)}', flush=True)
                (ROOT / 'outputs' / 'native_engine_download.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    for x in TREE:
        if x['type'] == 'blob' and x.get('size', 0) >= 5_000_000:
            results.append({'path':x['path'], 'status':'not_downloaded_large_asset', 'size':x['size']})
    (ROOT / 'outputs' / 'native_engine_download.json').write_text(json.dumps(results, indent=2), encoding='utf-8')
    (DEST / 'SOURCE_COMMIT.json').write_text(json.dumps({'repository':'https://github.com/itzik123/ClashRoyaleAi', 'commit':COMMIT, 'acquired':'2026-10-13', 'method':'GitHub tree API + pinned raw blobs', 'manifest':'outputs/native_engine_download.json'}, indent=2), encoding='utf-8')
    print('completed', len(results), flush=True)

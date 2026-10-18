"""Budget and acquire the full paired-renderer content delta, not just logic.

Public static files only. First run --plan to obtain actual Content-Lengths;
--fetch refuses an incomplete plan, over-budget sizes, changed manifests or
any content SHA1 mismatch unless the independently pinned upstream SHA256
matches exactly. Such a disagreement is retained. Every accepted output
also receives a SHA256 lock.
"""
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed
import argparse
import hashlib
import json
import shutil
import time
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://45.95.201.23/patch-cr'
CONTENT = '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82'
OUT = ROOT / 'outputs/phase2/assets'
DEST = ROOT / 'third_party/phase2/runtime-assets/full-update'
PLAN = OUT / 'full_delta_plan.json'
PROXY = None
STRONG_LOCKS = {}


def sha256(path):
    with path.open('rb') as stream: return hashlib.file_digest(stream, 'sha256').hexdigest()


def manifest_delta():
    fingerprint = ROOT / 'third_party/phase2/runtime-assets/candidates/patch-cr.json'
    current = json.loads(fingerprint.read_bytes())
    if current['sha'] != CONTENT or current['version'] != '15.535.86': raise ValueError('Wrong content identity')
    apk = ROOT / 'third_party/phase2/firstlight/user-provided/nulls-royale.apk'
    with zipfile.ZipFile(apk) as archive: previous = json.loads(archive.read('assets/fingerprint.json'))
    old = {f['file']: f['sha'] for f in previous['files']}
    delta = [f for f in current['files'] if old.get(f['file']) != f['sha']]
    for entry in delta:
        name = PurePosixPath(entry['file'])
        if name.is_absolute() or '..' in name.parts or ':' in entry['file'] or '\\' in entry['file']:
            raise ValueError('Unsafe content path')
    if len({f['file'] for f in delta}) != len(delta): raise ValueError('Duplicate content paths')
    return delta, sha256(fingerprint)


def opener(): return urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY} if PROXY else {}))


def metadata(entry):
    url = f'{BASE}/{CONTENT}/{entry["file"]}'
    record = {**entry, 'url': url}
    try:
        request = urllib.request.Request(url, method='HEAD', headers={'User-Agent': 'phase2-public-asset-audit/1.0'})
        with opener().open(request, timeout=18) as response:
            size = int(response.headers.get('Content-Length', '-1'))
            if response.status != 200 or size < 0: raise ValueError('No bounded content length')
            record.update(bytes=size, status='planned')
    except Exception as exc: record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    return record


def fetch(entry):
    path = DEST / entry['file']; path.parent.mkdir(parents=True, exist_ok=True)
    existing = ROOT / 'third_party/phase2/runtime-assets/runtime-update' / entry['file']
    record = dict(entry); start = time.monotonic()
    try:
        if path.is_file() and path.stat().st_size == entry['bytes']:
            data = path.read_bytes(); status = 'cached'
        elif existing.is_file() and existing.stat().st_size == entry['bytes']:
            data = existing.read_bytes(); status = 'reused_verified_logic'
        else:
            request = urllib.request.Request(entry['url'], headers={'User-Agent': 'phase2-public-asset-audit/1.0'})
            with opener().open(request, timeout=25) as response:
                data = bytearray()
                while True:
                    block = response.read(256 * 1024)
                    if not block: break
                    data.extend(block)
                    if len(data) > entry['bytes'] or time.monotonic() - start > 240:
                        raise ValueError('Resource exceeds planned size/time budget')
                data = bytes(data)
            status = 'downloaded'
        sha1_matches = hashlib.sha1(data).hexdigest() == entry['sha']
        actual_sha256 = hashlib.sha256(data).hexdigest()
        strong = STRONG_LOCKS.get(entry['file'])
        strong_matches = actual_sha256 == strong if strong else None
        if len(data) != entry['bytes'] or (not sha1_matches and strong_matches is not True) or strong_matches is False:
            raise ValueError('Content size/hash differs from pinned references')
        temp = path.with_name(path.name + '.partial'); temp.write_bytes(data); temp.replace(path)
        record.update(status=status, sha256=actual_sha256, content_sha1_matches=sha1_matches,
                      upstream_sha256_matches=strong_matches,
                      verification='upstream_sha256_exact_manifest_sha1_disagreement' if not sha1_matches else 'public_content_sha1',
                      path=path.relative_to(ROOT).as_posix())
    except Exception as exc: record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    record['wall_seconds'] = time.monotonic() - start
    return record


def main():
    global PROXY, STRONG_LOCKS
    parser = argparse.ArgumentParser(); parser.add_argument('--fetch', action='store_true'); parser.add_argument('--plan', action='store_true')
    parser.add_argument('--max-mib', type=int, default=768)
    parser.add_argument('--proxy', choices=['http://127.0.0.1:17890'])
    args = parser.parse_args(); PROXY = args.proxy
    locks = json.loads((ROOT / 'third_party/phase2/firstlight/native_runner/data/competitive/manifest.json').read_text(encoding='utf-8'))['source_files']
    STRONG_LOCKS = {name.removeprefix('runtime-update/'): sha for name, sha in locks.items() if name.startswith('runtime-update/')}
    OUT.mkdir(parents=True, exist_ok=True)
    delta, fingerprint_sha = manifest_delta()
    if args.fetch:
        plan = json.loads(PLAN.read_text(encoding='utf-8'))
        if plan['fingerprint_sha256'] != fingerprint_sha or plan['failed'] or len(plan['files']) != len(delta):
            raise ValueError('A complete matching metadata plan is required')
        total = sum(e['bytes'] for e in plan['files'])
        if total > args.max_mib * 1024**2 or any(e['bytes'] > 128 * 1024**2 for e in plan['files']):
            raise ValueError('Delta exceeds explicit total/per-file budget')
        if shutil.disk_usage(ROOT).free < total * 3 + 1024**3: raise ValueError('Insufficient workspace disk budget')
        jobs, function, workers = plan['files'], fetch, 4
    else:
        old = json.loads(PLAN.read_text(encoding='utf-8')) if PLAN.is_file() else {}
        valid = {e['file']: e for e in old.get('files', []) if e['status'] == 'planned'} if old.get('fingerprint_sha256') == fingerprint_sha else {}
        jobs = [e for e in delta if e['file'] not in valid]; function, workers = metadata, 8
    records = list(valid.values()) if not args.fetch else []
    started = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(function, e) for e in jobs]
        for future in as_completed(futures):
            record = future.result(); records.append(record)
            with (OUT / ('full_delta_fetch.jsonl' if args.fetch else 'full_delta_plan.jsonl')).open('a', encoding='utf-8') as log:
                log.write(json.dumps(record) + '\n')
            if record['status'] == 'failed' or len(records) % 25 == 0:
                print(json.dumps({'processed': len(records), 'total': len(delta), 'file': record['file'], 'status': record['status']}), flush=True)
    report = {'schema_version': 'royale-phase2-full-content-delta.v1', 'mode': 'fetch' if args.fetch else 'plan',
              'content_manifest_sha1': CONTENT, 'content_version': '15.535.86', 'fingerprint_sha256': fingerprint_sha,
              'files': sorted(records, key=lambda e: e['file']), 'file_count': len(records),
              'failed': sum(e['status'] == 'failed' for e in records),
              'total_bytes': sum(e.get('bytes', 0) for e in records), 'wall_seconds': time.monotonic() - started,
              'native_execution': False}
    output = OUT / 'full_delta_fetch.json' if args.fetch else PLAN
    output.write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'files'}), flush=True)
    if report['failed']: raise SystemExit(2)
    if args.fetch:
        shutil.copyfile(ROOT / 'third_party/phase2/runtime-assets/candidates/patch-cr.json', DEST / 'fingerprint.json')


if __name__ == '__main__':
    main()

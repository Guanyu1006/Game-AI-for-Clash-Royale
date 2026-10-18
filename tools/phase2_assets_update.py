"""Acquire pinned content delta from a public static CDN; verify every byte.

No account, login packet, remote battle, or downloaded script is used. HTTP
assets are accepted against the upstream pinned SHA256; the public content
manifest SHA1 is checked separately and disagreements remain explicit. A different fingerprint representation is retained
as a mismatch rather than silently substituted for the upstream fingerprint.
"""
from pathlib import Path, PurePosixPath
from concurrent.futures import ThreadPoolExecutor, as_completed
import hashlib
import argparse
import json
import shutil
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
BASE = 'http://45.95.201.23/patch-cr'
CONTENT = '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82'
MAX_FILE = 32 * 1024 * 1024
OUT = ROOT / 'outputs/phase2/assets'
DEST = ROOT / 'third_party/phase2/runtime-assets/runtime-update'
PROXY = None


def digest(data, algorithm='sha256'):
    return hashlib.new(algorithm, data).hexdigest()


def download_one(relative, expected, content_entry):
    name = PurePosixPath(relative)
    if name.is_absolute() or '..' in name.parts or '\\' in relative or ':' in relative:
        raise ValueError('Invalid pinned resource path')
    path = DEST / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    url = f'{BASE}/{CONTENT}/{relative}'
    row = {'file': relative, 'url': url, 'expected_sha256': expected,
           'expected_sha1': content_entry.get('sha') if content_entry else None}
    start = time.monotonic()
    try:
        if path.is_file() and digest(path.read_bytes()) == expected:
            data = path.read_bytes(); row['status'] = 'cached_verified'
        elif relative == 'fingerprint.json':
            derived = ROOT / 'third_party/phase2/runtime-assets/derived/fingerprint.json'
            data = derived.read_bytes()
            if digest(data) != expected: raise ValueError('Derived representation does not match pinned SHA256')
            row.update(status='derived_representation_verified',
                       representation_evidence='outputs/phase2/assets/dex_fingerprint_representation_audit.json',
                       raw_cdn_sha256=digest((ROOT / 'third_party/phase2/runtime-assets/candidates/patch-cr.json').read_bytes()))
        else:
            partial = path.with_name(path.name + '.partial')
            offset = partial.stat().st_size if partial.is_file() else 0
            headers = {'User-Agent': 'phase2-public-asset-audit/1.0'}
            if offset: headers['Range'] = f'bytes={offset}-'
            request = urllib.request.Request(url, headers=headers)
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({'http': PROXY, 'https': PROXY} if PROXY else {}))
            with opener.open(request, timeout=25) as response:
                resumed = offset and response.status == 206
                if offset and not resumed: raise ValueError('Partial retained; server did not honor resume Range')
                size = (offset if resumed else 0) + int(response.headers.get('Content-Length', '0'))
                if size > MAX_FILE: raise ValueError('File exceeds 32MiB budget')
                with partial.open('ab' if resumed else 'wb') as stream:
                    while True:
                        block = response.read(128 * 1024)
                        if not block: break
                        stream.write(block)
                        if stream.tell() > MAX_FILE or time.monotonic() - start > 900:
                            raise ValueError('Resource transfer budget exceeded; partial retained')
                if size and size != partial.stat().st_size: raise ValueError('Truncated file; partial retained')
                data = partial.read_bytes()
            row['status'] = 'downloaded'
        actual = digest(data); actual_sha1 = digest(data, 'sha1')
        row.update(bytes=len(data), sha256=actual, sha1=actual_sha1,
                   upstream_sha256_matches=actual == expected,
                   content_sha1_matches=actual_sha1 == row['expected_sha1'] if content_entry else None)
        # Keep mismatches for diagnosis under a separate suffix, never as a
        # resource accepted by the build. The root fingerprint is also kept.
        suffix = '' if actual == expected else '.mismatch'
        saved = path.with_name(path.name + suffix)
        saved.write_bytes(data)
        row['path'] = saved.relative_to(ROOT).as_posix()
        if row['content_sha1_matches'] is False:
            row['warning'] = 'Public manifest SHA1 disagrees; upstream independently pinned SHA256 checked separately'
        if actual != expected:
            row['status'] = 'hash_mismatch'
    except Exception as exc:
        row.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    row['wall_seconds'] = time.monotonic() - start
    return row


def main():
    global PROXY
    parser = argparse.ArgumentParser(); parser.add_argument('--proxy', choices=['http://127.0.0.1:17890'])
    args = parser.parse_args(); PROXY = args.proxy
    OUT.mkdir(parents=True, exist_ok=True); DEST.mkdir(parents=True, exist_ok=True)
    if shutil.disk_usage(ROOT).free < 3 * 1024**3:
        raise ValueError('Need 3GiB free for bounded update preparation')
    fingerprint_path = ROOT / 'third_party/phase2/runtime-assets/candidates/patch-cr.json'
    fingerprint = json.loads(fingerprint_path.read_bytes())
    if fingerprint['sha'] != CONTENT or fingerprint['version'] != '15.535.86':
        raise ValueError('Public content identity does not match pinned version')
    files = {row['file']: row for row in fingerprint['files']}
    if len(files) != len(fingerprint['files']): raise ValueError('Duplicate paths in public manifest')
    expected = json.loads((ROOT / 'third_party/phase2/firstlight/native_runner/data/competitive/manifest.json').read_text(encoding='utf-8'))['source_files']
    required = {name.removeprefix('runtime-update/'): sha for name, sha in expected.items() if name.startswith('runtime-update/')}
    missing = set(required) - set(files) - {'fingerprint.json'}
    if missing: raise ValueError(f'Required resources absent from content manifest: {sorted(missing)}')
    records = []; start = time.monotonic()
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(download_one, name, sha, files.get(name)) for name, sha in required.items()]
        for future in as_completed(futures):
            row = future.result(); records.append(row)
            with (OUT / 'update_download.jsonl').open('a', encoding='utf-8') as log:
                log.write(json.dumps(row) + '\n')
            print(json.dumps({'done': len(records), 'total': len(required), 'file': row['file'], 'status': row['status'], 'bytes': row.get('bytes')}), flush=True)
    report = {'schema_version': 'royale-phase2-content-download.v1', 'public_base': BASE,
              'discovery_source': 'https://huggingface.co/spaces/crsvsdd/nulls_modloader/blob/main/app.py (published patch-bs sibling path)',
              'content_manifest_sha1': CONTENT, 'content_version': fingerprint['version'],
              'public_fingerprint_sha256': digest(fingerprint_path.read_bytes()),
              'required': len(required), 'sha256_matched': sum(bool(r.get('upstream_sha256_matches')) for r in records),
              'failed': sum(r['status'] in ('failed', 'hash_mismatch') for r in records),
              'public_sha1_disagreements': sum(r.get('content_sha1_matches') is False for r in records),
              'downloaded_bytes': sum(r.get('bytes', 0) for r in records), 'wall_seconds': time.monotonic() - start,
              'transport_proxy': PROXY,
              'records': sorted(records, key=lambda r: r['file']), 'native_execution': False}
    (OUT / 'update_download.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({k: v for k, v in report.items() if k != 'records'}), flush=True)
    if report['failed']: raise SystemExit(2)


if __name__ == '__main__':
    main()

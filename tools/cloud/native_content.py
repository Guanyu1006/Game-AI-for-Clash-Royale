"""恢复固定原生更新资源；默认只读核验，fetch 才访问已审查静态 CDN。"""
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import sys
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
BASE = 'http://45.95.201.23/patch-cr'
CONTENT = '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82'
PREFIX = 'third_party/phase2/runtime-assets/full-update/'
MAX_TOTAL = 160 * 1024 * 1024
MAX_FILE = 32 * 1024 * 1024
MAX_RAW_FINGERPRINT = 3 * 1024 * 1024


@dataclass(frozen=True)
class FingerprintLock:
    raw_sha256: str
    derived_sha256: str
    derived_bytes: int
    content: str = CONTENT
    version: str = '15.535.86'
    files: int = 15456


FINGERPRINT = FingerprintLock(
    '3be42f6304f175eab5034f75047ff195e31827c32de576313cdecad0f41d46c0',
    'e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339',
    1839172)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def file_sha256(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def unique_json(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('Duplicate fingerprint JSON key')
        result[key] = value
    return result


def derive_fingerprint(raw, lock=FINGERPRINT):
    """Only the audited escaped-slash representation change is accepted."""
    if sha256(raw) != lock.raw_sha256:
        raise ValueError('Raw fingerprint SHA256 differs from its exact audited lock')
    source = json.loads(raw, object_pairs_hook=unique_json)
    if (list(source) != ['files', 'version', 'sha'] or source['version'] != lock.version
            or source['sha'] != lock.content or len(source['files']) != lock.files):
        raise ValueError('Fingerprint content/schema differs from the audited lock')
    names = [item['file'] for item in source['files']]
    if len(set(names)) != len(names):
        raise ValueError('Duplicate fingerprint resource paths')
    derived = json.dumps(source, ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode()
    if (derived != raw.replace(b'\\/', b'/')
            or json.loads(derived, object_pairs_hook=unique_json) != source
            or sha256(derived) != lock.derived_sha256 or len(derived) != lock.derived_bytes):
        raise ValueError('Derived fingerprint differs from exact bytes or JSON values')
    return derived


class RejectRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError('Static content redirects are not permitted')


class ContentRestorer:
    """No login, game service, RPC, remote code execution, or native certification."""
    def __init__(self, manifest='data/phase2/version_manifest.json', *, root=ROOT):
        self.root = Path(root).resolve()
        self.root.relative_to(ROOT)
        self.manifest_path = self.local(manifest)
        self.manifest_sha256 = file_sha256(self.manifest_path)
        data = json.loads(self.manifest_path.read_text(encoding='utf-8-sig'))
        if (data.get('schema') != 'royale-phase2-version.v1'
                or data.get('content_manifest_sha1') != CONTENT
                or data.get('content_version') != '15.535.86'):
            raise ValueError('Only the explicitly pinned 15.535.86 content is supported')
        self.assets = []
        for item in data['assets']:
            if item.get('role') not in ('paired_content', 'content_fingerprint'):
                continue
            path = item['path']
            if not isinstance(path, str) or not path.startswith(PREFIX):
                raise ValueError('Unexpected pinned content destination')
            relative = path.removeprefix(PREFIX)
            name = PurePosixPath(relative)
            if (not relative or name.is_absolute() or '..' in name.parts
                    or '\\' in relative or ':' in relative or '?' in relative or '#' in relative):
                raise ValueError('Unsafe content resource path')
            self.local(path)
            if type(item['bytes']) is not int or not 0 <= item['bytes'] <= MAX_FILE:
                raise ValueError('Content resource exceeds the 32 MiB per-file bound')
            digest = item['sha256']
            if (not isinstance(digest, str) or len(digest) != 64
                    or any(c not in '0123456789abcdef' for c in digest)):
                raise ValueError('Every accepted resource requires a SHA256 lock')
            if item['role'] == 'content_fingerprint' and (
                    relative != 'fingerprint.json' or digest != FINGERPRINT.derived_sha256
                    or item['bytes'] != FINGERPRINT.derived_bytes):
                raise ValueError('Fingerprint target differs from the exact audited representation')
            self.assets.append({**item, 'relative': relative,
                                'url': f'{BASE}/{CONTENT}/{relative}'})
        if not self.assets or len({r['path'] for r in self.assets}) != len(self.assets):
            raise ValueError('Missing content assets or duplicate destinations')

    def local(self, value):
        path = (self.root / value).resolve()
        path.relative_to(self.root)
        return path

    def inspect(self):
        records = []
        for asset in self.assets:
            path = self.local(asset['path'])
            record = {**asset, 'verified': False, 'status': 'missing'}
            if path.exists():
                if path.is_file():
                    record.update(actual_sha256=file_sha256(path), actual_bytes=path.stat().st_size)
                    record['verified'] = (record['actual_sha256'] == asset['sha256']
                                          and record['actual_bytes'] == asset['bytes'])
                record['status'] = 'cached_verified' if record['verified'] else 'existing_conflict_preserved'
            records.append(record)
        conflicts = sum(r['status'] == 'existing_conflict_preserved' for r in records)
        missing = sum(r['status'] == 'missing' for r in records)
        return {'schema': 'native-content-inspection.v1', 'created_utc': datetime.now(timezone.utc).isoformat(),
                'status': 'ready_content' if not missing and not conflicts else 'incomplete',
                'manifest': str(self.manifest_path.relative_to(self.root)),
                'manifest_sha256': self.manifest_sha256, 'required': len(records),
                'verified': sum(r['verified'] for r in records), 'missing': missing, 'conflicts': conflicts,
                'locked_bytes': sum(r['bytes'] for r in records), 'records': records,
                'network_requests': 0, 'rpc_calls': 0, 'native_execution_verified': False}

    def _fetch_one(self, asset, output, opener):
        record = {**asset, 'verified': False, 'status': 'failed', 'network_bytes': 0}
        stage = output / 'partials' / sha256(asset['path'].encode())
        raw_path = stage.with_suffix('.raw')
        started = time.monotonic()
        try:
            stage.parent.mkdir(exist_ok=True)
            # Recheck the cache immediately before each request.
            destination = self.local(asset['path'])
            if destination.exists():
                record.update(actual_sha256=file_sha256(destination), actual_bytes=destination.stat().st_size)
                record['verified'] = (record['actual_sha256'] == asset['sha256']
                                      and record['actual_bytes'] == asset['bytes'])
                record['status'] = 'cached_verified' if record['verified'] else 'existing_conflict_preserved'
                return record
            fingerprint = asset['role'] == 'content_fingerprint'
            limit = MAX_RAW_FINGERPRINT if fingerprint else asset['bytes']
            request = urllib.request.Request(asset['url'], headers={'User-Agent': 'RoyaleLab-fixed-content/1.0'})
            record['request_attempted'] = True
            with opener.open(request, timeout=15) as response:
                record.update(http_status=response.status, final_url=response.geturl())
                if response.status != 200 or response.geturl() != asset['url']:
                    raise ValueError('Unexpected static content response or redirect')
                if int(response.headers.get('Content-Length', '0')) > limit:
                    raise ValueError('Content-Length exceeds the locked transfer bound')
                digest = hashlib.sha256()
                with (raw_path if fingerprint else stage).open('xb') as stream:
                    while record['network_bytes'] <= limit:
                        block = response.read(min(128 * 1024, limit + 1 - record['network_bytes']))
                        if not block:
                            break
                        stream.write(block)
                        digest.update(block)
                        record['network_bytes'] += len(block)
                        if time.monotonic() - started > 180:
                            raise TimeoutError('Resource transfer wall budget exceeded')
            if record['network_bytes'] > limit:
                raise ValueError('Response body exceeds its locked transfer bound')
            if fingerprint:
                derived = derive_fingerprint(raw_path.read_bytes())
                with stage.open('xb') as stream:
                    stream.write(derived)
                record.update(raw_sha256=digest.hexdigest(), raw_path=str(raw_path.relative_to(self.root)),
                              derivation='Audited JSON escaped-slash representation only')
            record.update(actual_sha256=file_sha256(stage), actual_bytes=stage.stat().st_size)
            if record['actual_sha256'] != asset['sha256'] or record['actual_bytes'] != asset['bytes']:
                raise ValueError('Content SHA256 or size differs from the pinned manifest')
            destination.parent.mkdir(parents=True, exist_ok=True)
            try:
                os.link(stage, destination)  # Atomic publish; existing files cannot be overwritten.
                record['status'] = 'downloaded_verified'
            except FileExistsError:
                if destination.is_file() and file_sha256(destination) == asset['sha256'] and destination.stat().st_size == asset['bytes']:
                    record['status'] = 'cached_verified'
                else:
                    record['status'] = 'existing_conflict_preserved'
            record['verified'] = record['status'] in ('downloaded_verified', 'cached_verified')
            if record['verified']:
                stage.unlink()
        except Exception as exc:
            record['error'] = f'{type(exc).__name__}: {exc}'
            record['retained_files'] = [str(p.relative_to(self.root)) for p in (stage, raw_path) if p.exists()]
        finally:
            record['wall_seconds'] = time.monotonic() - started
        return record

    def fetch(self, output, *, max_mib=160, workers=2, opener=None):
        if type(max_mib) is not int or not 1 <= max_mib <= 160:
            raise ValueError('max_mib must be an integer from 1 to 160')
        if type(workers) is not int or workers not in (1, 2):
            raise ValueError('Only one or two download workers are permitted')
        inspection = self.inspect()
        missing = [r for r in inspection['records'] if r['status'] == 'missing']
        reservation = sum(MAX_RAW_FINGERPRINT + 1 if a['role'] == 'content_fingerprint'
                          else a['bytes'] + 1 for a in missing)
        if reservation > max_mib * 1024 * 1024:
            raise ValueError('Missing resources exceed the explicit aggregate transfer reservation')
        directory = self.local(output)
        directory.mkdir(parents=True, exist_ok=False)
        records = [r for r in inspection['records'] if r['status'] != 'missing']
        report = {'schema': 'native-content-fetch.v1', 'started_utc': datetime.now(timezone.utc).isoformat(),
                  'status': 'running', 'manifest_sha256': self.manifest_sha256,
                  'source': {'base': BASE, 'content': CONTENT}, 'required': len(self.assets),
                  'max_mib': max_mib, 'reserved_network_bytes': reservation, 'workers': workers,
                  'retry_policy': 'No automatic retry; stop scheduling after two consecutive failures',
                  'native_execution_verified': False, 'rpc_calls': 0}
        streak = 0
        def save(status):
            report.update(status=status, records=records, verified=sum(r['verified'] for r in records),
                          processed=len(records), network_bytes=sum(r.get('network_bytes', 0) for r in records),
                          network_requests=sum(bool(r.get('request_attempted')) for r in records))
            _write_new_or_replace(directory / 'report.json', report)
            return report
        save('running')
        if inspection['conflicts']:
            return save('existing_conflict_preserved')
        opener = opener or urllib.request.build_opener(urllib.request.ProxyHandler({}), RejectRedirect())
        def receive(asset):
            nonlocal streak
            result = self._fetch_one(asset, directory, opener)
            records.append(result)
            streak = 0 if result['verified'] else streak + 1
            with (directory / 'records.jsonl').open('a', encoding='utf-8') as stream:
                stream.write(json.dumps(result, ensure_ascii=False) + '\n')
            save('running')
        # A few small exact resource probes precede a new bulk transfer.
        selected = sorted(missing, key=lambda a: a['bytes'])
        probes, remaining = selected[:3], iter(selected[3:])
        for asset in probes:
            receive(asset)
            if streak >= 2:
                return save('stopped_after_failures')
        if any(not r['verified'] for r in records):
            return save('stopped_after_failed_probe')
        stopped = False
        with ThreadPoolExecutor(max_workers=workers) as pool:
            pending = {}
            def enqueue():
                if stopped:
                    return
                asset = next(remaining, None)
                if asset is not None:
                    pending[pool.submit(self._fetch_one, asset, directory, opener)] = asset
            for _ in range(workers):
                enqueue()
            while pending:
                done, _ = wait(pending, return_when=FIRST_COMPLETED)
                for future in done:
                    pending.pop(future)
                    result = future.result()
                    records.append(result)
                    streak = 0 if result['verified'] else streak + 1
                    with (directory / 'records.jsonl').open('a', encoding='utf-8') as stream:
                        stream.write(json.dumps(result, ensure_ascii=False) + '\n')
                    save('running')
                    stopped = stopped or streak >= 2
                    enqueue()
        return save('completed' if len(records) == len(self.assets) and all(r['verified'] for r in records)
                    else 'stopped_after_failures')


def _write_new_or_replace(path, value):
    temporary = path.with_suffix('.json.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    temporary.replace(path)


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', nargs='?', choices=('inspect', 'fetch'), default='inspect')
    parser.add_argument('--manifest', default='data/phase2/version_manifest.json')
    parser.add_argument('--output', help='New repository-contained evidence directory; required for fetch')
    parser.add_argument('--max-mib', type=int, default=160)
    parser.add_argument('--workers', type=int, choices=(1, 2), default=2)
    args = parser.parse_args(argv)
    try:
        restorer = ContentRestorer(args.manifest)
        if args.command == 'fetch':
            if not args.output:
                parser.error('fetch requires --output pointing to a new directory')
            report = restorer.fetch(args.output, max_mib=args.max_mib, workers=args.workers)
        else:
            report = restorer.inspect()
            if args.output:
                output = restorer.local(args.output)
                output.mkdir(parents=True, exist_ok=False)
                _write_new_or_replace(output / 'report.json', report)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if report['status'] in ('ready_content', 'completed') else 2
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(json.dumps({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                          'native_execution_verified': False}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

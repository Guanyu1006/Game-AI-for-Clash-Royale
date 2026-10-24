"""从显式版本清单生成固定原生资源包；仅本地读取、校验和打包。"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from royale_phase2.versioning import canonical_hash
from tools.cloud.native_content import FINGERPRINT, unique_json

PREFIX = 'third_party/phase2/runtime-assets/full-update/'
CATALOG = 'third_party/phase2/firstlight/native_runner/data/competitive/'
CATALOG_SHA256 = '699c0c77cc5d2ec9a475ec0d10875f9e38710af9fc5472cc3431276384c89b78'
SOURCE_COMMIT = '71944e3993b594b3cc89b38768b5e817dfbdbe0e'
CATALOG_NAMES = {'card_logic.json.gz', 'card_specs.json.gz', 'card_support.json',
                 'effects.json.gz', 'form_evidence.json', 'manifest.json',
                 'model_catalogs.json.gz', 'projectiles.json.gz', 'runtime.json'}


def read(path):
    return json.loads(local(path).read_text(encoding='utf-8-sig'), object_pairs_hook=unique_json)


def record(path):
    path = local(path)
    with path.open('rb') as stream:
        value = hashlib.file_digest(stream, 'sha256').hexdigest()
    return {'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size, 'sha256': value}


def write(path, data):
    local(path).write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def checked_asset(row):
    name = row['path']
    if (not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_./-]+', name)
            or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts
            or str(PurePosixPath(name)) != name):
        raise ValueError('Unsafe or noncanonical asset path')
    raw = ROOT / name
    if raw.is_symlink() or raw != local(name):
        raise ValueError('Asset symlinks are not accepted: ' + name)
    if type(row.get('bytes')) is not int or row['bytes'] < 0:
        raise ValueError('Explicit nonnegative asset size is required')
    if not re.fullmatch(r'[0-9a-f]{64}', row.get('sha256', '')):
        raise ValueError('Exact SHA256 is required')
    actual = record(name)
    if any(actual[key] != row[key] for key in ('bytes', 'sha256')):
        raise ValueError('Asset SHA256/size changed: ' + name)
    return actual


def prepare_plan(manifest):
    locked = read(manifest)
    fingerprint = canonical_hash({k: v for k, v in locked.items() if k != 'fingerprint'})
    if (locked.get('schema') != 'royale-phase2-version.v1'
            or locked.get('runtime_kind') != 'native_game' or locked.get('ticks_per_second') != 20
            or locked.get('content_version') != FINGERPRINT.version
            or locked.get('content_manifest_sha1') != FINGERPRINT.content
            or locked.get('fingerprint') != fingerprint):
        raise ValueError('An exact canonical fixed-version native manifest is required')
    if locked.get('source_commits', {}).get('firstlight3/FirstLight_CR') != SOURCE_COMMIT:
        raise ValueError('The fixed FirstLight source catalog commit is required')
    catalogs = [row for row in locked['assets'] if row.get('role') == 'source_extracted_catalog']
    if (len(catalogs) != len(CATALOG_NAMES)
            or {row['path'] for row in catalogs} != {CATALOG + name for name in CATALOG_NAMES}):
        raise ValueError('The complete nine-file fixed source catalog is required')
    for row in catalogs:
        if row['path'] == CATALOG + 'manifest.json' and row['sha256'] != CATALOG_SHA256:
            raise ValueError('Source catalog differs from its reviewed fixed hash')
        checked_asset(row)
    catalog = read(CATALOG + 'manifest.json')
    if catalog.get('version') != 'competitive-static-data.v1':
        raise ValueError('Unexpected source catalog schema')
    for row in catalogs:
        name = PurePosixPath(row['path']).name
        if name == 'manifest.json':
            continue
        key = name.removesuffix('.gz').removesuffix('.json')
        expected = catalog['files'][key]
        value, size = hashlib.sha256(), 0
        opener = gzip.open if name.endswith('.gz') else open
        with opener(local(row['path']), 'rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                size += len(block)
                if size > expected['bytes']:
                    raise ValueError('Catalog exceeds its fixed unpacked size: ' + name)
                value.update(block)
        if size != expected['bytes'] or value.hexdigest() != expected['sha256']:
            raise ValueError('Catalog differs from its fixed unpacked identity: ' + name)
    resources = [row for row in locked['assets'] if row.get('role') in ('paired_content', 'content_fingerprint')]
    if (len(resources) != 652 or len({row['path'] for row in resources}) != 652
            or sum(row['role'] == 'paired_content' for row in resources) != 651
            or sum(row['role'] == 'content_fingerprint' for row in resources) != 1):
        raise ValueError('Exactly 651 paired files and one fingerprint are required')
    for row in resources:
        if not row['path'].startswith(PREFIX):
            raise ValueError('Unexpected fixed content directory')
        checked_asset(row)
    fp_row = next(row for row in resources if row['role'] == 'content_fingerprint')
    if (fp_row['path'] != PREFIX + 'fingerprint.json'
            or fp_row['sha256'] != FINGERPRINT.derived_sha256
            or fp_row['bytes'] != FINGERPRINT.derived_bytes):
        raise ValueError('Exact canonical content fingerprint bytes are required')
    fp = read(fp_row['path'])
    if fp.get('version') != FINGERPRINT.version or fp.get('sha') != FINGERPRINT.content:
        raise ValueError('Fingerprint content identity mismatch')
    source_rows = fp['files']
    hashes = {row['file']: row['sha'] for row in source_rows}
    if len(hashes) != len(source_rows):
        raise ValueError('Duplicate fingerprint resource names')
    files = []
    for row in sorted(resources, key=lambda item: item['path']):
        if row['role'] != 'paired_content':
            continue
        name = row['path'][len(PREFIX):]
        expected = hashes.get(name, '')
        if not re.fullmatch(r'[0-9a-f]{40}', expected):
            raise ValueError('Paired resource is absent from the fixed content fingerprint: ' + name)
        pinned = catalog.get('source_files', {}).get('runtime-update/' + name)
        if pinned is not None and pinned != row['sha256']:
            raise ValueError('Resource differs from source catalog SHA256: ' + name)
        files.append({'file': name, 'sha': expected, 'bytes': row['bytes']})
    return {'content_version': FINGERPRINT.version, 'content_manifest_sha1': FINGERPRINT.content,
            'source': record(manifest), 'files': files}, fingerprint


def create_payload(manifest, output):
    folder = local(output)
    if folder.exists():
        raise FileExistsError('Use a new output directory; prior packages and failures are preserved')
    folder.mkdir(parents=True)
    report = {'schema': 'cloud-native-payload.v1', 'status': 'failed',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'network_calls': 0,
              'adb_commands': 0, 'native_probe_rpc_calls': 0, 'native_execution_verified': False}
    try:
        plan, fingerprint = prepare_plan(manifest)
        plan_path = folder / 'plan.json'; write(plan_path, plan)
        archive = folder / 'runtime-update.tar'
        command = [str(ROOT / '.venv-cloud/bin/python'), str(ROOT / 'tools/phase2_native_pack_update.py'),
                   '--resources', PREFIX.rstrip('/'), '--plan', str(plan_path), '--output', str(archive)]
        report.update(manifest=plan['source'], version_fingerprint=fingerprint,
                      verified_resource_files=652, verified_catalog_files=9, command=command)
        process = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=180, check=False)
        (folder / 'pack.stdout.log').write_bytes(process.stdout)
        (folder / 'pack.stderr.log').write_bytes(process.stderr)
        report['returncode'] = process.returncode
        if process.returncode != 0:
            raise RuntimeError('Existing native payload packer failed; inspect pack.stderr.log')
        receipt_path = archive.with_suffix('.tar.json')
        receipt = read(receipt_path)
        if (receipt.get('schema') != 'phase2-native-update-package.v1' or receipt.get('file_count') != 652
                or receipt.get('plan_sha256') != record(plan_path)['sha256']
                or receipt.get('upstream_source_manifest_sha256') != CATALOG_SHA256):
            raise ValueError('Packer receipt does not bind the reviewed plan/catalog')
        if record(manifest) != plan['source']:
            raise ValueError('Manifest changed during packaging')
        report.update(status='packaged', plan=record(plan_path), receipt=record(receipt_path),
                      archive={'path': archive.relative_to(ROOT).as_posix(), **receipt['archive_hashes']},
                      scope='Verified local resource package only; Android installation and execution are not implied')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        write(folder / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    report = create_payload(args.manifest, args.output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'packaged' else 2


if __name__ == '__main__':
    raise SystemExit(main())

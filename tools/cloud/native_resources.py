"""Bounded, resumable native resource acquisition; never launches a game.

Pinned game assets remain distinct from installed Android runtime evidence.
This module never accepts SDK licenses or substitutes a newer game version.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tempfile
import time
import urllib.error
import urllib.request
import urllib.parse
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = 'outputs/native_loop_20261020/resource_setup'
MANIFEST = 'data/phase2/version_manifest.json'
RESOURCES = {
    'apk': {
        'url': 'https://tempweb.nullsusercontent.com/fpapk/nr_15.535.13_release_ff1c6c29.apk',
        'path': 'third_party/phase2/firstlight/user-provided/nulls-royale.apk',
        'bytes': 794497101,
        'sha256': 'bbe2b3beae3de8fff17ead1da8aedcfac55d03bf2095f61a6dbec11b768f301a',
        'identity_source': MANIFEST,
    },
    'probe': {
        'url': 'https://raw.githubusercontent.com/Jason-XII/clash-royale-battle-engine/70a08af6807fa4d59a11c7d52bf6272a96afe30c/prebuilt/libcrprobe.so',
        'path': 'third_party/phase2/jason/prebuilt/libcrprobe.so',
        'bytes': 6346600,
        'sha256': 'd526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190',
        'identity_source': MANIFEST,
    },
}

# Debian bookworm's signed InRelease -> Packages.xz -> SHA256 chain was verified
# with /usr/share/keyrings/debian-archive-keyring.gpg. Versions stay pinned here.
# This is open-source adb, not Google's SDK installer/license acceptance flow.
DEBIAN_ADB_PACKAGES = [
    ('a/android-platform-tools/adb_29.0.6-28_amd64.deb', 193808, 'a12693b0fab2edd8389017ce4589f59d11770dcdb585f090f3eff26f4a7d2dbc'),
    ('a/android-platform-external-boringssl/android-libboringssl_13.0.0+r24-2_amd64.deb', 675736, '638b90834022a5dfe46c13778481d1d22b9b54eabee26a7bddf83253c14c009b'),
    ('a/android-platform-tools/android-libbase_29.0.6-28_amd64.deb', 46332, 'c651582c9beb229b6a4581eef5d03796293aa2da3b602eafc5427b198060819f'),
    ('a/android-platform-tools/android-libcutils_29.0.6-28_amd64.deb', 34800, '12ac0a99beb683269d77af09bf80ddd7b8c428fda442c05070ea3ff50429718c'),
    ('a/android-platform-tools/android-liblog_29.0.6-28_amd64.deb', 40340, 'd495761ec2dc87070f6ac75c771d221d05e2be387cbaeab7e7c7fcd740ce4ce2'),
    ('libb/libbsd/libbsd0_0.11.7-2_amd64.deb', 117416, 'bb31cc8b40f962a85b2cec970f7f79cc704a1ae4bad24257a822055404b2c60b'),
    ('libm/libmd/libmd0_1.0.4-2_amd64.deb', 29456, '03539fd30c509e27101d13a56e52eda9062bdf1aefe337c07ab56def25a13eab'),
    ('libu/libusb-1.0/libusb-1.0-0_1.0.26-1_amd64.deb', 62592, '0a8a6c4a7d944538f2820cbde2a313f2fe6f94c21ffece9e6f372fc2ab8072e1'),
]


def local(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Resource paths must remain inside repository')
    return path


def digest(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write_report(path, data):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def fetch_resource(spec, max_bytes, *, timeout=30, opener=None, progress=None):
    """Download only a locked identity; preserve different files and partials.

    Resumption with HTTP 200 rechecks every repeated prefix byte before append.
    A complete mismatching partial is retained for component audit, not blessed.
    """
    started = time.monotonic()
    report = {'schema': 'native-resource-fetch.v1', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'url': spec['url'], 'path': spec['path'], 'expected_bytes': spec['bytes'],
              'expected_sha256': spec['sha256'], 'budget_bytes': max_bytes,
              'native_execution_verified': False, 'success': False}
    try:
        requested = ROOT / spec['path']
        if requested.is_symlink() or requested.with_name(requested.name + '.partial').is_symlink():
            raise ValueError('Resource symlinks are preserved and never promoted')
        destination = local(spec['path'])
        partial = local(str(destination.relative_to(ROOT)) + '.partial')
        report['partial'] = str(partial.relative_to(ROOT))
        expected = spec['bytes']
        if type(expected) is not int or expected < 1 or type(max_bytes) is not int or max_bytes < expected:
            raise ValueError('Resource exceeds explicit byte budget')
        if not re.fullmatch('[0-9a-f]{64}', spec['sha256']):
            raise ValueError('A pinned SHA256 identity is required')
        parsed = urllib.parse.urlsplit(spec['url'])
        if parsed.scheme != 'https' or parsed.username or parsed.password:
            raise ValueError('Public HTTPS resource URL required')
        if destination.exists():
            actual = digest(destination)
            report.update(bytes=destination.stat().st_size, sha256=actual, cached=True)
            if destination.stat().st_size != expected or actual != spec['sha256']:
                raise ValueError('Different existing resource preserved')
            report['success'] = True
            return report
        destination.parent.mkdir(parents=True, exist_ok=True)
        initial_partial = partial.stat() if partial.exists() else None
        offset = initial_partial.st_size if initial_partial else 0
        if offset > expected:
            raise ValueError('Oversized partial preserved')
        if offset == expected:
            actual = digest(partial)
            report.update(bytes=offset, sha256=actual)
            if actual != spec['sha256']:
                raise ValueError('Complete partial SHA256 mismatch; preserved for component audit')
        else:
            headers = {'User-Agent': 'RoyaleLab-native-resources/1.0'}
            if offset:
                headers['Range'] = f'bytes={offset}-'
            request = urllib.request.Request(spec['url'], headers=headers)
            open_request = opener or urllib.request.urlopen
            with open_request(request, timeout=timeout) as response:
                report.update(http_status=response.status, final_url=response.url,
                              content_length=response.headers.get('Content-Length'))
                length = response.headers.get('Content-Length')
                if length is not None and (not length.isdigit() or int(length) > max_bytes):
                    raise ValueError('HTTP body exceeds byte budget')
                if offset and response.status == 206:
                    value = response.headers.get('Content-Range', '')
                    match = re.fullmatch(r'bytes (\d+)-(\d+)/(\d+)', value)
                    if not match or (int(match[1]), int(match[3])) != (offset, expected):
                        raise ValueError('HTTP resume offset or total differs from locked resource')
                    report['resume_mode'] = 'http-range'
                elif response.status == 200:
                    if offset:
                        with partial.open('rb') as previous:
                            remaining = offset
                            while remaining:
                                block = response.read(min(1024 * 1024, remaining))
                                if not block or previous.read(len(block)) != block:
                                    raise ValueError('Repeated HTTP 200 prefix differs; partial preserved')
                                remaining -= len(block)
                        report['resume_mode'] = 'verified-http-200-prefix'
                else:
                    raise ValueError('Unexpected HTTP status for download')
                last = time.monotonic()
                with partial.open('r+b' if initial_partial is not None else 'xb') as stream:
                    current = os.fstat(stream.fileno())
                    if initial_partial is not None and (
                            current.st_size != offset or current.st_ino != initial_partial.st_ino
                            or current.st_dev != initial_partial.st_dev
                            or current.st_mtime_ns != initial_partial.st_mtime_ns):
                        raise ValueError('Partial changed concurrently; preserved without writing')
                    stream.seek(offset)
                    while True:
                        block = response.read(min(1024 * 1024, expected + 1 - stream.tell()))
                        if not block:
                            break
                        if stream.tell() + len(block) > expected:
                            raise ValueError('Resource exceeded locked size; partial preserved')
                        stream.write(block)
                        if progress and time.monotonic() - last > 10:
                            stream.flush()
                            progress({'downloaded_bytes': stream.tell(), 'expected_bytes': expected})
                            last = time.monotonic()
            actual = digest(partial)
            report.update(bytes=partial.stat().st_size, sha256=actual)
            if partial.stat().st_size != expected:
                raise ValueError('Truncated resource; partial preserved for resumption')
            if actual != spec['sha256']:
                raise ValueError('SHA256 mismatch; partial preserved for component audit')
        # Do not replace an existing file created while this request ran.
        if destination.exists():
            raise ValueError('Destination appeared during download; partial preserved')
        partial.rename(destination)
        report.update(success=True, bytes=expected, sha256=spec['sha256'])
    except (OSError, ValueError, urllib.error.URLError) as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        if isinstance(error, urllib.error.HTTPError):
            report['http_status'] = error.code
    finally:
        report['elapsed_seconds'] = time.monotonic() - started
    return report


def inspect_resources(manifest_path=MANIFEST):
    manifest = json.loads(local(manifest_path).read_text(encoding='utf-8'))
    roles = {}
    for asset in manifest['assets']:
        path = local(asset['path'])
        role = roles.setdefault(asset['role'], {'matched': 0, 'missing': 0, 'different': 0, 'missing_bytes': 0})
        if not path.is_file():
            role['missing'] += 1
            role['missing_bytes'] += asset.get('bytes', 0)
        elif digest(path) == asset['sha256']:
            role['matched'] += 1
        else:
            role['different'] += 1
    return {'schema': 'native-resource-inventory.v1', 'manifest': manifest_path, 'roles': roles,
            'native_execution_verified': False, 'rpc_calls': 0,
            'google_platform_tools': {'automatic_license_acceptance': False,
                'status': 'Requires explicit SDK license acceptance; use project-local Debian open-source adb instead'},
            'fetchable_locked_resources': RESOURCES}


def extract_locked_apk_components(archive_path, *, manifest_path=MANIFEST, max_bytes=100_000_000):
    """Restore only individual hash-matched locked base resources and libg.

    The container APK may have an unrecognized whole hash. In that case it
    remains unapproved; exact component hashes are independently checked.
    """
    archive_path = local(archive_path)
    manifest = json.loads(local(manifest_path).read_text(encoding='utf-8'))
    prefix = 'third_party/phase2/firstlight/user-provided/base-assets/'
    fallbacks = {row['verified_cached_copy'].replace('\\', '/'): row['actual_runtime_source'].split('original_apk/', 1)[1]
                 for row in manifest.get('unchanged_source_fallbacks', [])}
    selected = []
    for asset in manifest['assets']:
        path = asset['path'].replace('\\', '/')
        if path.startswith(prefix):
            selected.append((asset, path[len(prefix):]))
        elif asset['role'] == 'native_library' and '/user-provided/' in path:
            selected.append((asset, path.split('/user-provided/', 1)[1]))
        elif path in fallbacks:
            selected.append((asset, fallbacks[path]))
    expected_bytes = sum(a.get('bytes', 0) for a, _ in selected)
    if expected_bytes > max_bytes:
        raise ValueError('Component extraction exceeds explicit byte budget')
    archive_hash = digest(archive_path)
    approved = {a['sha256'] for a in manifest['assets'] if a['role'] == 'original_apk'}
    rows = []
    with zipfile.ZipFile(archive_path) as archive:
        for asset, entry in selected:
            row = {'path': asset['path'], 'entry': entry, 'expected_sha256': asset['sha256']}
            destination = local(asset['path'])
            if destination.is_file():
                row['status'] = 'existing_match' if digest(destination) == asset['sha256'] else 'existing_different_preserved'
            elif entry not in archive.namelist():
                row['status'] = 'missing_entry'
            else:
                info = archive.getinfo(entry)
                if info.file_size != asset.get('bytes'):
                    row['status'] = 'size_mismatch'
                else:
                    body = archive.read(entry)
                    row.update(bytes=len(body), sha256=hashlib.sha256(body).hexdigest())
                    if row['sha256'] != asset['sha256']:
                        row['status'] = 'hash_mismatch'
                    else:
                        destination.parent.mkdir(parents=True, exist_ok=True)
                        try:
                            with destination.open('xb') as stream:
                                stream.write(body)
                            row['status'] = 'restored_match'
                        except FileExistsError:
                            row['status'] = 'appeared_during_extraction_preserved'
            rows.append(row)
    return {'schema': 'native-resource-component-restore.v1', 'archive': str(archive_path.relative_to(ROOT)),
            'archive_sha256': archive_hash, 'archive_whole_identity_matches': archive_hash in approved,
            'success': all(r['status'] in ('existing_match', 'restored_match') for r in rows),
            'matched_components': sum(r['status'] in ('existing_match', 'restored_match') for r in rows),
            'total_components': len(rows), 'components': rows,
            'native_execution_verified': False, 'rpc_calls': 0}


def install_adb(*, max_bytes=2_000_000):
    """Extract pinned Debian adb packages locally; run no maintainer scripts."""
    if platform.system() != 'Linux' or platform.machine() != 'x86_64' or not shutil.which('dpkg-deb'):
        raise ValueError('Project-local Debian adb requires Linux x86_64 and existing dpkg-deb')
    if sum(row[1] for row in DEBIAN_ADB_PACKAGES) > max_bytes:
        raise ValueError('ADB package set exceeds explicit byte budget')
    base = local('.cache/native-tools')
    base.mkdir(parents=True, exist_ok=True)
    records = []
    destination = local('.cache/native-tools/debian-adb')
    destination.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='adb-extract-', dir=base) as stage:
        stage = Path(stage)
        for filename, size, sha in DEBIAN_ADB_PACKAGES:
            spec = {'url': 'https://deb.debian.org/debian/pool/main/' + filename,
                    'path': '.cache/native-tools/downloads/' + Path(filename).name,
                    'bytes': size, 'sha256': sha}
            record = fetch_resource(spec, max_bytes)
            records.append(record)
            if not record['success']:
                raise ValueError('ADB package acquisition failed: ' + record['error'])
            result = subprocess.run(['dpkg-deb', '-x', str(local(spec['path'])), str(stage)],
                                    capture_output=True, text=True, timeout=30)
            if result.returncode:
                raise ValueError('ADB package extraction failed: ' + result.stderr[:1000])
        # Preserve any different existing local tool file instead of overwriting.
        for source in sorted(stage.rglob('*')):
            target = destination / source.relative_to(stage)
            local(target)
            if source.is_dir() and not source.is_symlink():
                target.mkdir(parents=True, exist_ok=True)
            elif source.is_symlink():
                link = os.readlink(source)
                if Path(link).is_absolute():
                    raise ValueError('Absolute tool symlink rejected')
                local(target.parent / link)
                if target.is_symlink() and os.readlink(target) == link:
                    continue
                if target.exists() or target.is_symlink():
                    raise ValueError('Different existing tool symlink preserved')
                target.parent.mkdir(parents=True, exist_ok=True)
                target.symlink_to(link)
            else:
                if target.exists():
                    if digest(target) != digest(source):
                        raise ValueError('Different existing tool file preserved')
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source, target)
    wrapper = local('.cache/native-tools/bin/adb')
    wrapper.parent.mkdir(parents=True, exist_ok=True)
    script = '''#!/bin/sh
TOOLS_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
export LD_LIBRARY_PATH="$TOOLS_DIR/debian-adb/usr/lib/x86_64-linux-gnu/android:$TOOLS_DIR/debian-adb/usr/lib/x86_64-linux-gnu${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
export ANDROID_USER_HOME="$TOOLS_DIR/android"
export ANDROID_SDK_HOME="$TOOLS_DIR/android"
export ADB_VENDOR_KEYS="$TOOLS_DIR/android/keys"
export ADB_MDNS_AUTO_CONNECT=0
unset ADB_SERVER_SOCKET
export ANDROID_ADB_SERVER_PORT=5038
export TMPDIR="$TOOLS_DIR/tmp"
mkdir -p "$ANDROID_USER_HOME" "$ADB_VENDOR_KEYS" "$TMPDIR"
exec "$TOOLS_DIR/debian-adb/usr/bin/adb" "$@"
'''
    if wrapper.exists() and wrapper.read_text() != script:
        # This file is the generated entrypoint owned by this installer, but
        # retain previous bytes for review before upgrading its environment.
        backup = wrapper.with_name('adb.previous-' + digest(wrapper)[:16])
        if not backup.exists():
            shutil.copy2(wrapper, backup)
    wrapper.write_text(script, encoding='utf-8')
    wrapper.chmod(0o755)
    version = subprocess.run([str(wrapper), 'version'], capture_output=True, text=True, timeout=10)
    return {'schema': 'native-adb-install.v1', 'success': version.returncode == 0,
            'wrapper': str(wrapper.relative_to(ROOT)), 'wrapper_sha256': digest(wrapper),
            'packages': records, 'download_bytes': sum(row[1] for row in DEBIAN_ADB_PACKAGES),
            'version': {'returncode': version.returncode, 'stdout': version.stdout, 'stderr': version.stderr},
            'source': 'https://packages.debian.org/bookworm/adb',
            'sdk_license_accepted': False, 'system_packages_installed': False,
            'native_execution_verified': False, 'rpc_calls': 0}


def fetch_catalogs(*, max_bytes=2_000_000, manifest_path=MANIFEST):
    """Recover the small source-extracted catalogs from their pinned commit."""
    manifest = json.loads(local(manifest_path).read_text(encoding='utf-8'))
    prefix = 'third_party/phase2/firstlight/'
    commit = manifest['source_commits']['firstlight3/FirstLight_CR']
    if not re.fullmatch('[0-9a-f]{40}', commit):
        raise ValueError('Pinned catalog source commit is required')
    specs = []
    for row in manifest['assets']:
        if row['role'] == 'source_extracted_catalog' and row['path'].startswith(prefix):
            name = row['path'][len(prefix):]
            if '..' in Path(name).parts:
                raise ValueError('Unsafe catalog source path')
            specs.append({'url': 'https://raw.githubusercontent.com/Jaasssoooonnnnn/FirstLight_CR/' + commit + '/' + name,
                          'path': row['path'], 'bytes': row['bytes'], 'sha256': row['sha256']})
    if sum(spec['bytes'] for spec in specs) > max_bytes:
        raise ValueError('Catalog set exceeds explicit byte budget')
    records = [fetch_resource(spec, max_bytes) for spec in specs]
    return {'schema': 'native-catalog-fetch.v1', 'success': all(row['success'] for row in records),
            'source_commit': commit, 'total_bytes': sum(spec['bytes'] for spec in specs),
            'matched_catalogs': sum(row['success'] for row in records), 'catalogs': records,
            'native_execution_verified': False, 'rpc_calls': 0}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    inspect_parser = sub.add_parser('inspect')
    inspect_parser.add_argument('--output', default=OUTPUT + '/resource-inventory.json')
    fetch_parser = sub.add_parser('fetch')
    fetch_parser.add_argument('resource', choices=sorted(RESOURCES))
    fetch_parser.add_argument('--max-bytes', type=int, default=10_000_000)
    fetch_parser.add_argument('--output')
    extract_parser = sub.add_parser('extract-apk')
    extract_parser.add_argument('--archive', default=RESOURCES['apk']['path'] + '.partial')
    extract_parser.add_argument('--max-bytes', type=int, default=100_000_000)
    extract_parser.add_argument('--output', default=OUTPUT + '/apk-components.json')
    adb_parser = sub.add_parser('install-adb')
    adb_parser.add_argument('--max-bytes', type=int, default=2_000_000)
    adb_parser.add_argument('--output', default=OUTPUT + '/adb-install.json')
    catalogs_parser = sub.add_parser('fetch-catalogs')
    catalogs_parser.add_argument('--max-bytes', type=int, default=2_000_000)
    catalogs_parser.add_argument('--output', default=OUTPUT + '/catalogs-fetch.json')
    args = parser.parse_args(argv)
    if args.command == 'inspect':
        report = inspect_resources()
    elif args.command == 'fetch':
        report = fetch_resource(RESOURCES[args.resource], args.max_bytes,
                                progress=lambda value: print(json.dumps(value), flush=True))
        args.output = args.output or OUTPUT + '/' + args.resource + '-fetch.json'
    elif args.command == 'extract-apk':
        report = extract_locked_apk_components(args.archive, max_bytes=args.max_bytes)
    elif args.command == 'install-adb':
        report = install_adb(max_bytes=args.max_bytes)
    else:
        report = fetch_catalogs(max_bytes=args.max_bytes)
    write_report(args.output, report)
    brief = {key: value for key, value in report.items() if key not in ('components', 'fetchable_locked_resources')}
    print(json.dumps(brief, ensure_ascii=False, indent=2))
    return 1 if report.get('success') is False else 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Experimental repository-local software Android preparation; never boots a VM.

Only immutable public package/image identities in the lock are downloaded. This
does not install host packages, load host kernel modules, or certify game support.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[2]
LOCK = ROOT / 'configs/native/software_android.lock.json'
MAX_DOWNLOAD = 2 * 1024**3
MIN_FREE = 10 * 1024**3


def local(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT) or path == ROOT:
        raise ValueError('Paths must remain below the current repository')
    return path


def sha256(path):
    with Path(path).open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def write(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n')


def load_lock():
    lock = json.loads(LOCK.read_text())
    if lock['schema'] != 'experimental-software-android-lock.v1':
        raise ValueError('Unsupported software Android lock')
    specs = [*lock['debian']['packages'], lock['android']['layer']]
    if sum(row['bytes'] for row in specs) > MAX_DOWNLOAD:
        raise ValueError('Locked downloads exceed 2 GiB')
    for row in specs:
        if (not re.fullmatch(r'[0-9a-f]{64}', row['sha256'])
                or type(row['bytes']) is not int or row['bytes'] <= 0
                or Path(row['filename']).name != row['filename']
                or not row['url'].startswith('https://')):
            raise ValueError('Invalid locked artifact')
    return lock


def check_space():
    free = shutil.disk_usage(ROOT).free
    if free < MIN_FREE:
        raise ValueError('At least 10 GiB repository filesystem free space is required')
    return free


def check_platform():
    if platform.system() != 'Linux' or platform.machine() not in ('x86_64', 'AMD64'):
        raise ValueError('This experiment currently supports Linux x86_64 only')


def new_attempt(output, operation):
    parent = local(output)
    parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    attempt = parent / (stamp + '-' + operation)
    attempt.mkdir(exist_ok=False)
    return attempt


def valid_file(path, spec):
    return (not path.is_symlink() and path.is_file()
            and path.stat().st_size == spec['bytes'] and sha256(path) == spec['sha256'])


def _opener(spec):
    headers = {'User-Agent': 'RoyaleLab-experimental-software-android/1'}
    if spec['url'].startswith('https://registry-1.docker.io/v2/redroid/redroid/blobs/'):
        # Anonymous pull grant remains in memory and is never written to reports.
        url = ('https://auth.docker.io/token?service=registry.docker.io'
               '&scope=repository:redroid/redroid:pull')
        with urllib.request.urlopen(url, timeout=30) as response:
            token = json.load(response)['token']
        headers['Authorization'] = 'Bearer ' + token
    return urllib.request.Request(spec['url'], headers=headers)


def download_one(spec, destination):
    if destination.exists() or destination.is_symlink():
        if not valid_file(destination, spec):
            raise ValueError('Different existing artifact preserved: ' + str(destination))
        return {'filename': spec['filename'], 'cached': True,
                'bytes': spec['bytes'], 'sha256': spec['sha256']}
    partial = destination.with_name(destination.name + '.partial')
    # A partial is retained as evidence; retry in a new cache or inspect it first.
    if partial.exists() or partial.is_symlink():
        raise ValueError('Previous partial preserved: ' + str(partial))
    destination.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    value = hashlib.sha256()
    with urllib.request.urlopen(_opener(spec), timeout=60) as source, partial.open('xb') as target:
        while block := source.read(1024 * 1024):
            count += len(block)
            if count > spec['bytes']:
                raise ValueError('Artifact exceeds locked download size')
            target.write(block)
            value.update(block)
    if count != spec['bytes'] or value.hexdigest() != spec['sha256']:
        raise ValueError('Artifact SHA256/size mismatch; partial preserved')
    partial.rename(destination)
    return {'filename': spec['filename'], 'cached': False,
            'bytes': count, 'sha256': value.hexdigest()}


def acquire(cache, lock):
    check_platform()
    check_space()
    specs = [*lock['debian']['packages'], lock['android']['layer']]
    rows = []
    for spec in specs:
        directory = cache / ('debs' if 'name' in spec else 'images')
        rows.append(download_one(spec, directory / spec['filename']))
        print('VERIFIED', spec['filename'], flush=True)
    return {'artifacts': rows, 'locked_download_bytes': sum(row['bytes'] for row in specs)}


def archive_name(value):
    path = PurePosixPath(value)
    if path.is_absolute() or '..' in path.parts or '\x00' in value or '\n' in value or '|' in value:
        raise ValueError('Unsafe archive path: ' + repr(value))
    return path


def _plain_parents(destination, target):
    for parent in [target.parent, *target.parent.parents]:
        if parent == destination.parent:
            break
        if parent.is_symlink():
            raise ValueError('Refusing to follow archive parent symlink')
    target.parent.mkdir(parents=True, exist_ok=True)


def extract_rootfs(layer, destination, ownership):
    """Unpack regular content before creating symlinks; never dereference them.

    Absolute symlinks belong to the guest namespace and are stored as links only.
    Ownership is restored in the VM, never by chown on the Codespaces host.
    """
    if destination.exists() or destination.is_symlink():
        raise ValueError('Existing rootfs preserved: ' + str(destination))
    destination.mkdir(parents=True)
    links, directories, records = [], [], []
    expanded = 0
    with tarfile.open(layer, 'r:gz') as archive:
        for member in archive:
            relative = archive_name(member.name)
            if str(relative) == '.':
                continue
            if any(part.startswith('.wh.') for part in relative.parts):
                raise ValueError('OCI whiteouts need explicit multi-layer support')
            target = destination.joinpath(*relative.parts)
            _plain_parents(destination, target)
            records.append(f'{member.uid}|{member.gid}|{member.mode:o}|{relative.as_posix()}')
            if member.isdir():
                target.mkdir(exist_ok=True)
                directories.append((target, member.mode))
            elif member.issym() or member.islnk():
                links.append((target, member.linkname, member.issym()))
            elif member.isfile():
                expanded += member.size
                if expanded > 2 * 1024**3:
                    raise ValueError('Expanded rootfs exceeds 2 GiB bound')
                with archive.extractfile(member) as source, target.open('xb') as output:
                    shutil.copyfileobj(source, output, 1024 * 1024)
                target.chmod(member.mode & 0o7777)
            else:
                raise ValueError('Unsupported archive special file; extraction retained')
    for target, linkname, symbolic in links:
        _plain_parents(destination, target)
        if symbolic:
            target.symlink_to(linkname)
        else:
            link = destination.joinpath(*archive_name(linkname).parts)
            _plain_parents(destination, link)
            if link.is_symlink() or not link.is_file():
                raise ValueError('Hardlink must reference a regular extracted file')
            os.link(link, target, follow_symlinks=False)
    for target, mode in reversed(directories):
        target.chmod(mode & 0o7777)
    ownership.write_text('\n'.join(records) + '\n')
    return {'expanded_regular_file_bytes': expanded, 'archive_records': len(records),
            'ownership_sha256': sha256(ownership), 'archive_links_followed': False}


def unpack(cache, lock, attempt):
    check_platform()
    check_space()
    if not shutil.which('dpkg-deb'):
        raise ValueError('Host dpkg-deb is required; no global installation is performed')
    root = cache / 'packages'
    if root.exists():
        raise ValueError('Existing package extraction preserved; use a fresh cache')
    # Validate the complete artifact set before any extraction.
    for spec in lock['debian']['packages']:
        if not valid_file(cache / 'debs' / spec['filename'], spec):
            raise ValueError('Missing or mismatching Debian package: ' + spec['filename'])
    spec = lock['android']['layer']
    layer = cache / 'images' / spec['filename']
    if not valid_file(layer, spec):
        raise ValueError('Android layer SHA256/size mismatch')
    root.mkdir(parents=True)
    for index, spec in enumerate(lock['debian']['packages']):
        with (attempt / f'dpkg-{index:03d}.log').open('w') as log:
            subprocess.run(['dpkg-deb', '-x', str(cache / 'debs' / spec['filename']), str(root)],
                           check=True, stdout=log, stderr=subprocess.STDOUT)
    report = extract_rootfs(layer, cache / 'rootfs', cache / 'ownership.tsv')
    report['package_count'] = len(lock['debian']['packages'])
    report['host_package_installation'] = False
    write(cache / 'unpacked.json', {'schema': 'experimental-software-android-unpacked.v1',
                                  'lock_sha256': sha256(LOCK), **report})
    return report


def build_image(cache, lock, attempt):
    check_platform()
    check_space()
    tool = shutil.which('mke2fs')
    if not tool:
        raise ValueError('Host mke2fs is required; no global installation is performed')
    receipt = json.loads((cache / 'unpacked.json').read_text())
    if receipt['lock_sha256'] != sha256(LOCK):
        raise ValueError('Extraction receipt refers to a different artifact lock')
    if receipt['ownership_sha256'] != sha256(cache / 'ownership.tsv'):
        raise ValueError('Extraction ownership metadata changed')
    target = cache / 'android.ext4'
    if target.exists() or target.is_symlink():
        raise ValueError('Existing guest image preserved; use a fresh cache')
    with target.open('xb') as stream:
        stream.truncate(lock['defaults']['disk_bytes'])
    command = [tool, '-q', '-t', 'ext4', '-F', '-E', 'no_copy_xattrs',
               '-d', str(cache / 'rootfs'), str(target)]
    with (attempt / 'mke2fs.log').open('w') as log:
        subprocess.run(command, check=True, stdout=log, stderr=subprocess.STDOUT)
    report = {'path': str(target.relative_to(ROOT)), 'bytes': target.stat().st_size,
              'source_ownership_sha256': receipt['ownership_sha256'],
              'lock_sha256': sha256(LOCK), 'mke2fs_command': command,
              'ownership_restored_in_guest': True}
    write(cache / 'image.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['doctor', 'download', 'unpack', 'image'])
    parser.add_argument('--cache', default='.cache/native-tools/software-android')
    parser.add_argument('--output', default='outputs/cloud/software-android')
    args = parser.parse_args()
    cache, lock = local(args.cache), load_lock()
    if args.command == 'doctor':
        report = {'schema': 'experimental-software-android-doctor.v1', 'experimental': True,
                  'platform': platform.system() + ' ' + platform.machine(),
                  'free_bytes': shutil.disk_usage(ROOT).free,
                  'minimum_free_bytes': MIN_FREE, 'locked_download_bytes': lock['locked_download_bytes'],
                  'host_tools': {name: shutil.which(name) for name in ('dpkg-deb', 'mke2fs')},
                  'cache': str(cache.relative_to(ROOT)), 'native_game_execution_verified': False}
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    attempt = new_attempt(args.output, args.command)
    report = {'schema': 'experimental-software-android-prepare.v1', 'operation': args.command,
              'experimental': True, 'success': False, 'android_boot_verified': False,
              'native_game_execution_verified': False, 'native_rpc_calls': 0}
    try:
        cache.mkdir(parents=True, exist_ok=True)
        operation = {'download': lambda: acquire(cache, lock),
                     'unpack': lambda: unpack(cache, lock, attempt),
                     'image': lambda: build_image(cache, lock, attempt)}[args.command]
        report.update(operation())
        report['success'] = True
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    write(attempt / 'report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report['success']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()

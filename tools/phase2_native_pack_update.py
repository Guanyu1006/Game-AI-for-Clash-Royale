"""Package a fully verified public content delta for a local Android instance.

This tool never downloads files and never substitutes missing resources.  The
destination remains under the project.  A package is not a runtime success.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path, PurePosixPath
import tarfile

ROOT = Path(__file__).resolve().parents[1]
FINGERPRINT_SHA256 = 'e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339'
CONTENT_SHA1 = '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82'


def project_path(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT.resolve()):
        raise ValueError('Every input/output must stay inside this project')
    return path


def safe_name(name):
    path = PurePosixPath(name)
    if not name or path.is_absolute() or '..' in path.parts or '\\' in name or '\n' in name or '\r' in name:
        raise ValueError(f'Unsafe content path: {name!r}')
    return name


def hashes(path):
    sha1 = hashlib.sha1(); sha256 = hashlib.sha256(); size = 0
    with path.open('rb') as stream:
        for data in iter(lambda: stream.read(1024 * 1024), b''):
            sha1.update(data); sha256.update(data); size += len(data)
    return {'bytes': size, 'sha1': sha1.hexdigest(), 'sha256': sha256.hexdigest()}


def add_bytes(archive, name, data):
    info = tarfile.TarInfo(name); info.size = len(data); info.mode = 0o644
    info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ''
    archive.addfile(info, io.BytesIO(data))


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--resources', default='third_party/phase2/runtime-assets/runtime-update')
    p.add_argument('--plan', default='outputs/phase2/assets/full_delta_plan.json')
    p.add_argument('--output', default='outputs/phase2/native/runtime-update.tar')
    args = p.parse_args()
    resources = project_path(args.resources); plan_path = project_path(args.plan); output = project_path(args.output)
    plan = json.loads(plan_path.read_text(encoding='utf-8'))
    if plan['content_version'] != '15.535.86' or plan['content_manifest_sha1'] != CONTENT_SHA1:
        raise ValueError('Only the pinned supported content is accepted by this package tool')
    fingerprint = resources / 'fingerprint.json'
    fp_hashes = hashes(fingerprint)
    if fp_hashes['sha256'] != FINGERPRINT_SHA256:
        raise ValueError('Exact canonical supported fingerprint bytes are missing')
    manifest = json.loads(fingerprint.read_text(encoding='utf-8'))
    if manifest['version'] != '15.535.86' or manifest['sha'] != CONTENT_SHA1:
        raise ValueError('Fingerprint content identity mismatch')
    expected = {safe_name(row['file']): row['sha'] for row in plan['files']}
    source_manifest_path = ROOT / 'third_party/phase2/firstlight/native_runner/data/competitive/manifest.json'
    source_manifest = json.loads(source_manifest_path.read_text(encoding='utf-8'))['source_files']
    rows = []
    for name in sorted(expected):
        path = resources / name
        if path.is_symlink() or not path.resolve().is_relative_to(resources.resolve()):
            raise ValueError(f'Resource symlink/outside source folder: {name}')
        actual = hashes(path)
        exception = None
        if actual['sha1'] != expected[name]:
            # The public fingerprint metadata for this exact blob disagrees
            # with the independently pinned FirstLight component.  Do not
            # weaken SHA1 checks for any other unknown download.
            pinned_sha256 = source_manifest.get('runtime-update/' + name)
            if name != 'assets.scdb' or actual['sha256'] != pinned_sha256:
                raise ValueError(f'Content SHA1 mismatch without exact pinned SHA256 exception: {name}')
            exception = 'public content SHA1 metadata disagrees; exact pinned upstream SHA256 matches'
        rows.append({'file': name, 'expected_content_sha1': expected[name],
                     'sha1_metadata_exception': exception, **actual})
    rows.append({'file': 'fingerprint.json', **fp_hashes})
    receipt = {'schema': 'phase2-native-update-package.v1', 'runtime_executed': False,
               'content_version': '15.535.86', 'content_manifest_sha1': CONTENT_SHA1,
               'plan': str(plan_path.relative_to(ROOT)), 'plan_sha256': hashes(plan_path)['sha256'],
               'upstream_source_manifest': str(source_manifest_path.relative_to(ROOT)),
               'upstream_source_manifest_sha256': hashes(source_manifest_path)['sha256'],
               'resource_folder': str(resources.relative_to(ROOT)), 'files': rows,
               'file_count': len(rows), 'bytes': sum(row['bytes'] for row in rows)}
    output.parent.mkdir(parents=True, exist_ok=True)
    partial = output.with_suffix(output.suffix + '.partial')
    checksums = ''.join(f"{row['sha256']}  {row['file']}\n" for row in rows).encode()
    with tarfile.open(partial, mode='w', format=tarfile.USTAR_FORMAT) as archive:
        for row in rows:
            path = resources / row['file']
            info = tarfile.TarInfo('update/' + row['file']); info.size = row['bytes']; info.mode = 0o644
            info.mtime = 0; info.uid = info.gid = 0; info.uname = info.gname = ''
            with path.open('rb') as stream:
                archive.addfile(info, stream)
        add_bytes(archive, 'SHA256SUMS', checksums)
        add_bytes(archive, 'manifest.json', json.dumps(receipt, separators=(',', ':')).encode())
    partial.replace(output)
    receipt.update(archive=str(output.relative_to(ROOT)), archive_hashes=hashes(output))
    receipt_path = output.with_suffix(output.suffix + '.json')
    receipt_path.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'package': str(output.relative_to(ROOT)), 'file_count': len(rows),
                      'content_bytes': receipt['bytes'], 'sha256': receipt['archive_hashes']['sha256']}), flush=True)


if __name__ == '__main__':
    main()

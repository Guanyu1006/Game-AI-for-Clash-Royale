"""Freeze a separately named local APK build without changing the baseline lock.

This runtime-only profile certifies local asset identity. It deliberately does
not certify datasets, Android installation, a running process or battle physics.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from royale_phase2.versioning import canonical_hash, file_hash, verify_manifest


CHECKS = ('input_components', 'libg', 'probe', 'signature', 'signature_v2',
          'signature_v3', 'zip_crc', 'patch', 'manifest_native_extraction',
          'update_not_embedded')


def read(path):
    return json.loads(local(path).read_text(encoding='utf-8-sig'))


def reference(path):
    target = local(path)
    return {'path': target.relative_to(ROOT).as_posix(), 'sha256': file_hash(target),
            'bytes': target.stat().st_size}


def _build_artifact(receipt, name):
    artifact = receipt[name]
    actual = reference(artifact['path'])
    if actual['sha256'] != artifact['sha256'] or actual['bytes'] != artifact['bytes']:
        raise ValueError('Build artifact changed: ' + name)
    return actual


def create_runtime_version(build_receipt, output, *, source_manifest='data/phase2/version_manifest.json',
                           identity_path=None):
    target = local(output)
    if target == local(source_manifest) or target.exists():
        raise FileExistsError('Use a new version manifest; the baseline is immutable')
    source = read(source_manifest)
    if (source.get('schema') != 'royale-phase2-version.v1'
            or source.get('runtime_kind') != 'native_game' or source.get('ticks_per_second') != 20):
        raise ValueError('Only the existing pinned 20 Hz native profile is supported')
    original_fingerprint = canonical_hash({k: v for k, v in source.items() if k != 'fingerprint'})
    if source.get('fingerprint') != original_fingerprint:
        raise ValueError('Source manifest fingerprint mismatch')
    receipt = read(build_receipt)
    if receipt.get('schema') != 'cloud-native-apk-build.v1' or receipt.get('success') is not True:
        raise ValueError('A verified local APK build receipt is required')
    if any(receipt.get('checks', {}).get(name) is not True for name in CHECKS):
        raise ValueError('Every APK static/signature check must pass before freezing a new version')
    original_apk = _build_artifact(receipt, 'input_apk')
    offline_apk = _build_artifact(receipt, 'output_apk')
    libraries = {a['sha256'] for a in source['assets'] if a['role'] == 'native_library'}
    probes = {a['sha256'] for a in source['assets'] if a['role'] == 'native_probe'}
    for name, allowed in (('input_apk', libraries), ('output_apk', libraries)):
        with zipfile.ZipFile(local(receipt[name]['path'])) as archive:
            if hashlib.sha256(archive.read('lib/arm64-v8a/libg.so')).hexdigest() not in allowed:
                raise ValueError('APK libg differs from the preserved native engine')
            if name == 'output_apk':
                if hashlib.sha256(archive.read('lib/arm64-v8a/libcrprobe.so')).hexdigest() not in probes:
                    raise ValueError('Output probe differs from the preserved protocol')
                if archive.testzip() is not None:
                    raise ValueError('Output APK CRC failure')
    assets = []
    for row in source['assets']:
        role = row['role']
        if role == 'dataset_lock':
            continue
        if role in ('original_apk', 'offline_apk'):
            artifact = original_apk if role == 'original_apk' else offline_apk
            assets.append({**deepcopy(row), **artifact})
            continue
        actual = reference(row['path'])
        if actual['sha256'] != row['sha256'] or actual['bytes'] != row['bytes']:
            raise ValueError('Preserved runtime component changed: ' + row['path'])
        assets.append(deepcopy(row))
    if {row['role'] for row in assets}.issuperset({'original_apk', 'offline_apk', 'native_library', 'native_probe'}) is False:
        raise ValueError('Runtime profile must include both APKs, libg and probe')
    identity = local(identity_path) if identity_path else target.with_name(target.stem + '-android-identity.json')
    manifest = {**deepcopy(source), 'assets': assets, 'fingerprint': None,
        'profile': 'separate_local_build_runtime_identity',
        'source_baseline_manifest': reference(source_manifest), 'local_build_receipt': reference(build_receipt),
        'actual_original_apk_sha256': original_apk['sha256'],
        'original_apk_whole_hash_matches_upstream': original_apk['sha256'] == source.get('original_apk_upstream_sha256'),
        'offline_apk_sha256': offline_apk['sha256'],
        'runtime_identity_receipt': identity.relative_to(ROOT).as_posix(),
        'local_game_patch': {'producer': 'tools/cloud/build_native_apk.py',
                             'build_receipt': reference(build_receipt),
                             'scope': 'Separately signed local APK; exact preserved libg and probe; Android execution unverified'},
        'dataset_scope': 'Runtime-only asset profile; no dataset acceptance is implied. Collect new version-bound trajectories.',
        'source_dataset_locks_excluded_from_runtime_profile': [deepcopy(a) for a in source['assets'] if a['role'] == 'dataset_lock'],
        'platform': 'Android ARM64 runtime required; this local build has not been installed or run on Android',
        'native_execution_verified': False,
        'historical_checkpoints_compatible': False}
    manifest['fingerprint'] = canonical_hash({k: v for k, v in manifest.items() if k != 'fingerprint'})
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open('x', encoding='utf-8') as stream:
        json.dump(manifest, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    verified = verify_manifest(target)
    return {'schema': 'cloud-native-runtime-version.v1', 'manifest': reference(target),
            'version_fingerprint': verified['fingerprint'], 'assets': len(assets),
            'asset_identity_verified': True, 'native_execution_verified': False,
            'android_identity_required': identity.relative_to(ROOT).as_posix(),
            'historical_checkpoints_compatible': False,
            'scope': 'New local APK and runtime assets only; actual Android identity and battle acceptance still required'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build-receipt', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--source-manifest', default='data/phase2/version_manifest.json')
    parser.add_argument('--identity-path')
    args = parser.parse_args(argv)
    report = create_runtime_version(args.build_receipt, args.output,
        source_manifest=args.source_manifest, identity_path=args.identity_path)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

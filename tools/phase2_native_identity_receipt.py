"""Validate actual isolated-Android evidence and produce minimal-probe identity.

The minimal upstream probe has no attest command.  This explicit alternative
uses recorded live Android root SHA256 checks and process mappings, retaining
the raw evidence hashes.  It never claims native attestation is supported.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
LIBG = '110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
PROBE = 'd526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190'
APK = '97bad257c5d6e0499d2fe7e674f1f4de24fc28c19a8456c574dd8a8e8ea42c9a'
FP = 'e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339'


def local(value):
    path = (ROOT / value).resolve()
    if not path.is_relative_to(ROOT):
        raise ValueError('Evidence must remain within the project')
    return path


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--maps-report', default='outputs/phase2/secondary/android_update86_loaded_identity.stdout.log')
    p.add_argument('--install-report', default='outputs/phase2/secondary/native_update86_install_namespace.stdout.log')
    p.add_argument('--package-receipt', default='outputs/phase2/native/runtime-update.tar.json')
    p.add_argument('--output', default='outputs/phase2/native/runtime86_adb_identity.json')
    args = p.parse_args()
    map_path = local(args.maps_report); install_path = local(args.install_report)
    package_path = local(args.package_receipt); output = local(args.output)
    maps = map_path.read_text(encoding='utf-8'); installed = install_path.read_text(encoding='utf-8')
    package = json.loads(package_path.read_text(encoding='utf-8'))
    for digest, name in [(LIBG, 'libg.so'), (PROBE, 'libcrprobe.so'), (APK, 'base.apk')]:
        rows = [line for line in maps.splitlines() if line.startswith(digest + '  ') and line.endswith('/' + name)]
        if len(rows) != 1:
            raise ValueError(f'Exactly one installed {name} SHA256 proof required')
        filename = rows[0].split(None, 1)[1]
        if not any(line.endswith(filename) and re.match(r'^[0-9a-f]+-[0-9a-f]+ ', line) for line in maps.splitlines()):
            raise ValueError(f'Installed {name} was not mapped in the actual process')
    if '/data/data/nullsroyale.rel.free/update/assets.scdb' not in maps:
        raise ValueError('Exact update assets.scdb mapping not present')
    if FP + '  /data/user/0/nullsroyale.rel.free/update/fingerprint.json' not in maps:
        raise ValueError('Runtime fingerprint SHA256 does not match supported exact bytes')
    expected_names = {row['file'] for row in package['files']}
    checked_names = {line.removesuffix(': OK') for line in installed.splitlines() if line.endswith(': OK')}
    if checked_names != expected_names or len(expected_names) != 652:
        raise ValueError('All packaged 652 files require actual installed SHA256 success')
    summary = json.loads(installed.strip().splitlines()[-1])
    if (summary['installed_fingerprint_sha256'] != FP or summary['fingerprint_version'] != '15.535.86'
            or summary['fingerprint_sha'] != '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82'
            or summary['archive_sha256'] != package['archive_hashes']['sha256']):
        raise ValueError('Android installed update identity differs from the package')
    pid = int(re.search(r'^NATIVE_PID=(\d+)$', maps, re.MULTILINE)[1])
    receipt = {'schema': 'phase2-native-adb-identity.v1',
               'created_utc': datetime.now(timezone.utc).isoformat(),
               'actual_adb_verified': True, 'loaded_libg_verified': True, 'content_verified': True,
               'method': 'live SSH Android container-root files/maps inspection; not native attestation',
               'libg_sha256': LIBG, 'probe_sha256': PROBE, 'apk_sha256': APK,
               'content_version': '15.535.86', 'content_fingerprint_sha256': FP,
               'content_manifest_sha1': summary['fingerprint_sha'], 'installed_update_file_count': 652,
               'installed_update_bytes': summary['installed_bytes'], 'pid': pid,
               'source_evidence': [{'path': str(path.relative_to(ROOT)),
                                    'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
                                   for path in [map_path, install_path, package_path]],
               'limitations': ['minimal profile does not expose native attest, rich observation or snapshot restore',
                               'receipt refers to the isolated process/files measured in the source evidence; renew after changes']}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    print(json.dumps({'identity_receipt': str(output.relative_to(ROOT)), 'pid': pid,
                      'mapped_libg_verified': True, 'content_version': receipt['content_version']}))


if __name__ == '__main__':
    main()

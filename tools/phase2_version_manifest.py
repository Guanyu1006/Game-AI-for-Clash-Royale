"""Lock the acquired native build and paired complete content by actual hashes.

Identity is checked independently from battle/fidelity acceptance. Runtime
receipts are references, not mutable PID/timing fields inside the fingerprint.
"""
from pathlib import Path
import hashlib
import json
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, target
from royale_phase2.versioning import canonical_hash, file_hash, verify_manifest


def main():
    configure()
    assets = []; seen = set()

    def add(path, role, expected=None):
        path = Path(path); relative = path.relative_to(ROOT).as_posix() if path.is_absolute() else path.as_posix()
        actual = file_hash(relative)
        if expected and actual != expected: raise ValueError(f'Pinned component changed: {relative}')
        if relative not in seen:
            assets.append({'path': relative, 'role': role, 'sha256': actual, 'bytes': (ROOT / relative).stat().st_size})
            seen.add(relative)
        return actual

    release = json.loads((ROOT / 'third_party/phase2/firstlight/native_runner/supported_engine.json').read_text(encoding='utf-8'))
    manifest = json.loads((ROOT / 'third_party/phase2/firstlight/native_runner/data/competitive/manifest.json').read_text(encoding='utf-8'))
    delta = json.loads((ROOT / 'outputs/phase2/assets/full_delta_fetch.json').read_text(encoding='utf-8'))
    if delta['file_count'] != 651 or delta['failed'] or delta['content_manifest_sha1'] != '41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82':
        raise ValueError('Complete verified paired update required')
    apk = add('third_party/phase2/firstlight/user-provided/nulls-royale.apk', 'original_apk')
    offline_apk = add('outputs/phase2/native/offline-native-smoke.apk', 'offline_apk')
    add('third_party/phase2/firstlight/user-provided/lib/arm64-v8a/libg.so', 'native_library', release['libg_sha256'])
    add('third_party/phase2/jason/prebuilt/libcrprobe.so', 'native_probe', 'd526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190')
    for row in delta['files']:
        add(row['path'], 'paired_content', row['sha256'])
    add('third_party/phase2/runtime-assets/full-update/fingerprint.json', 'content_fingerprint', release['runtime_fingerprint_sha256'])
    unchanged_source_fallbacks = []
    for name, expected in manifest['source_files'].items():
        if name.startswith('runtime-update/'):
            resource = name.removeprefix('runtime-update/')
            path = 'third_party/phase2/runtime-assets/full-update/' + resource
            if not (ROOT / path).is_file():
                # A cached upstream update may retain an unchanged asset. The
                # complete delta correctly leaves that file in the base APK.
                with zipfile.ZipFile(ROOT / 'third_party/phase2/firstlight/user-provided/nulls-royale.apk') as archive:
                    actual = hashlib.sha256(archive.read('assets/' + resource)).hexdigest()
                if actual != expected: raise ValueError('Missing source resource is not identical in base APK: ' + resource)
                path = 'third_party/phase2/runtime-assets/runtime-update/' + resource
                unchanged_source_fallbacks.append({'resource': resource, 'actual_runtime_source': 'original_apk/assets/' + resource,
                                                   'sha256': actual, 'verified_cached_copy': path})
        elif name.startswith(release['resource_directory'] + '/'):
            path = 'third_party/phase2/firstlight/user-provided/base-assets/' + name.removeprefix(release['resource_directory'] + '/')
        else: raise ValueError(f'Unknown source root: {name}')
        add(path, 'pinned_logic_resource', expected)
    catalog = ROOT / 'third_party/phase2/firstlight/native_runner/data/competitive'
    for path in sorted(catalog.iterdir()):
        if path.is_file() and path.suffix in ('.json', '.gz'): add(path, 'source_extracted_catalog')
    for path in ['third_party/phase2/dataset_metadata/IL_Replay/repo_info.json',
                 'data/phase2/frozen_manifest.json']:
        add(path, 'dataset_lock')
    lock = {'schema': 'royale-phase2-version.v1', 'runtime_kind': 'native_game',
            'client_package': 'nullsroyale.rel.free', 'client_build': '15.535.13', 'client_version_code': 150535013,
            'content_version': '15.535.86', 'content_manifest_sha1': delta['content_manifest_sha1'],
            'platform': 'Android12 arm64-v8a application via Redroid x86_64 libndk_translation on project QEMU-WHPX guest',
            'abi': 'arm64-v8a', 'ticks_per_second': 20,
            'tick_basis': 'Pinned probe advances native 50ms ticks; source replay labels use 20Hz; command boundary is audited separately',
            'time_origin': 'native configured replay tick0, headless first playable capture tick90; source convention remains an explicit reconstruction hypothesis',
            'coordinates': {'units': 'native integer world units; 1000 per tile in source conversion',
                            'x_bounds': [0, 17999], 'y_bounds': [0, 31999],
                            'origin': 'native world frame; no implicit seat flip',
                            'seat_transform': 'source data_i mapping in pinned converter; retain source and native owner separately'},
            'command_order': 'Source order retained for tied ticks; actual injector/consumption receipts assessed per action',
            'source_commits': {'Jason-XII/clash-royale-battle-engine': '70a08af6807fa4d59a11c7d52bf6272a96afe30c',
                               'firstlight3/FirstLight_CR': '71944e3993b594b3cc89b38768b5e817dfbdbe0e'},
            'original_apk_upstream_sha256': release['apk_sha256'], 'actual_original_apk_sha256': apk,
            'original_apk_whole_hash_matches_upstream': apk == release['apk_sha256'],
            'component_identity_basis': 'Actual libg and 242 pinned base/update source resource hashes match; original APK archive representation differs',
            'unchanged_source_fallbacks': unchanged_source_fallbacks,
            'offline_apk_sha256': offline_apk,
            'local_game_patch': {'producer': 'tools/phase2_native_build_apk.py',
                                 'producer_sha256': file_hash('tools/phase2_native_build_apk.py'),
                                 'scope': 'Local loader/probe bootstrap; game libg unchanged'},
            'card_id_mapping': 'Native global IDs from pinned source_extracted_catalog files; no first-stage IDs',
            'levels_towers_evolutions_abilities': 'Per-episode native replay configuration plus source-level fidelity guard; mixed-level normalization is rejected',
            'balance_date': 'unknown', 'source_official_build_match': 'unknown',
            'dataset_revision': '059d43a02138a34b1b3009cc2acc7630fb99a638',
            'dataset_schema': 'royale-phase2-source-episode.v1',
            'dataset_scope': 'Frozen100formal+20development from one pinned852-game shard; main runtime compatibility must be checked separately',
            'runtime_identity_receipt': 'outputs/phase2/native/runtime86_adb_identity.json',
            'capabilities_scope': 'Minimal profile; native attest/rich observation/complete snapshot not exposed. Do not infer supported mechanisms from catalog size.',
            'rights': 'Open wrapper licenses do not grant game/library/resource redistribution rights. Proprietary assets excluded from GPT bundle.',
            'assets': sorted(assets, key=lambda row: row['path'])}
    lock['fingerprint'] = canonical_hash(lock)
    dest = target('data/phase2/version_manifest.json')
    data = json.dumps(lock, ensure_ascii=False, indent=2)
    if dest.exists() and dest.read_text(encoding='utf-8') != data:
        raise ValueError('Existing version lock differs; use a separately named version and regenerate dependent trajectories')
    dest.write_text(data, encoding='utf-8')
    verified = verify_manifest(dest)
    report = {'schema': 'royale-phase2-version-verification.v1', 'fingerprint': verified['fingerprint'],
              'verified_assets': len(assets), 'pinned_source_resources': len(manifest['source_files']),
              'full_delta_files': delta['file_count'], 'original_apk_archive_match': lock['original_apk_whole_hash_matches_upstream'],
              'native_execution_claim': False, 'claim': verified['claim']}
    target('outputs/phase2/version-verification.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

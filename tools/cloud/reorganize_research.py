"""Copy reviewed research inputs into the current layout, preserving history.

The default is a read-only plan.  --apply creates missing targets only after
every source, digest and target has passed preflight.  It never moves a source,
starts Android, trains a model, or changes a pinned native asset reference.
"""
from __future__ import annotations

import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path, PurePosixPath, PureWindowsPath
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
DEFAULT_SOURCES = 'research/migration_sources.json'
SCHEMA = 'royale-research-migration.v1'
MAX_INPUT_BYTES = 256 * 1024**2


def safe_path(value):
    """Require normalized project-relative paths and reject every symlink."""
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('需要规范的仓库内相对路径')
    path = PurePosixPath(value)
    if (path.is_absolute() or PureWindowsPath(value).drive or
            value != path.as_posix() or any(part in ('.', '..') for part in path.parts) or
            path.as_posix() == '.'):
        raise ValueError('拒绝仓库外或非规范路径: ' + value)
    cursor = ROOT
    for index, part in enumerate(path.parts):
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('迁移路径不允许符号链接: ' + value)
        if cursor.exists() and index < len(path.parts) - 1 and not cursor.is_dir():
            raise ValueError('迁移路径的父目录不是目录: ' + value)
    return ROOT / path


def sha_bytes(payload):
    return hashlib.sha256(payload).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n').encode('utf-8')


def payload_for(path, expected=None):
    source = safe_path(path)
    if not source.is_file():
        raise FileNotFoundError('迁移源不存在: ' + path)
    if source.stat().st_size > MAX_INPUT_BYTES:
        raise ValueError('单个迁移输入超过 256 MiB: ' + path)
    payload = source.read_bytes()
    if expected is not None and sha_bytes(payload) != expected:
        raise ValueError('迁移源 SHA256 不匹配: ' + path)
    return payload


def document(path, expected=None):
    return json.loads(payload_for(path, expected).decode('utf-8-sig'))


def reference(path, payload):
    return {'path': path, 'sha256': sha_bytes(payload), 'bytes': len(payload)}


def _matches(path, payload):
    return path.is_file() and path.stat().st_size == len(payload) and path.read_bytes() == payload


def _operation(operations, source, target, payload, transform, source_payload=None):
    safe_path(source); destination = safe_path(target)
    if source == target or any(item['target']['path'] == target for item in operations):
        raise ValueError('源与目标相同，或目标重复: ' + target)
    source_payload = payload_for(source) if source_payload is None else source_payload
    status = 'planned'
    if destination.exists():
        if not destination.is_file():
            status = 'conflict'
        else:
            status = 'reused' if _matches(destination, payload) else 'conflict'
    operations.append({'source': reference(source, source_payload),
                       'target': reference(target, payload), 'status': status,
                       'transform': transform, '_payload': payload})


def _validate_dataset(path):
    from royale_phase3.data_contract import load_dataset
    data, meta = load_dataset(path)
    return meta, {'path': path, 'loader': 'royale_phase3.data_contract.load_dataset',
                  'rows': len(data), 'split_rows': dict(Counter(row['split'] for row in data)),
                  'version_fingerprint_sha256': meta['version_fingerprint_sha256'],
                  'test_evaluated': False, 'scope': '数据完整性检查；没有训练或评估 test'}


def _validate_checkpoint(path, version, profile):
    from royale_phase3.learning import StatefulPolicy
    policy = StatefulPolicy.load(path, version, profile)
    architecture = policy.metadata['config']['architecture']
    if (architecture.get('variant') != 'C' or not architecture.get('recurrent') or
            any(architecture.get(name) for name in ('grid', 'world', 'opponent', 'hazard'))):
        raise ValueError('迁移参考模型必须保持现有 GRU C 契约')
    return {'path': path, 'loader': 'royale_phase3.learning.StatefulPolicy.load',
            'strict_tensor_state_loaded': True, 'architecture': architecture,
            'version_fingerprint_sha256': version, 'inference_performed': False}


def plan_migration(sources_path=DEFAULT_SOURCES):
    """Read and validate sources; return receipt plus private planned payloads."""
    sources = document(sources_path)
    if sources.get('schema') != 'royale-research-migration-sources.v1':
        raise ValueError('不支持的迁移源配置')
    operations = []; datasets = {}; validations = []
    for item in sources['datasets']:
        if item['id'] in datasets:
            raise ValueError('数据集编号重复')
        source_payload = payload_for(item['source'], item.get('source_sha256'))
        meta, validation = _validate_dataset(item['source']); validations.append(validation)
        rows_payload = payload_for(meta['rows']['path'], meta['rows']['sha256'])
        derived = deepcopy(meta)
        derived['rows']['path'] = item['target_rows']
        target_payload = encoded(derived)
        _operation(operations, meta['rows']['path'], item['target_rows'], rows_payload,
                   'byte_identical_rows')
        _operation(operations, item['source'], item['target_metadata'], target_payload,
                   'metadata_rows_path_only; original source/provenance fields retained', source_payload)
        datasets[item['id']] = {'metadata': reference(item['target_metadata'], target_payload),
                                 'rows': reference(item['target_rows'], rows_payload), 'value': derived}

    item = sources['checkpoint']; selected = datasets[item['dataset_id']]
    source_payload = payload_for(item['source'], item.get('source_sha256'))
    checkpoint = json.loads(source_payload.decode('utf-8-sig'))
    version = selected['value']['version_fingerprint_sha256']
    profile = selected['value']['observation_profile']
    validations.append(_validate_checkpoint(item['source'], version, profile))
    weights = checkpoint['weights']; weights_payload = payload_for(weights['path'], weights['sha256'])
    if checkpoint['config']['rows']['sha256'] != selected['rows']['sha256']:
        raise ValueError('参考模型的训练行与迁移的原生数据不一致')
    derived = deepcopy(checkpoint)
    # Preserve the original model/optimizer arrays. Only metadata references and
    # its self-digest change; this is a relocation, not new training.
    derived['weights'] = {'path': item['target_weights'], 'sha256': sha_bytes(weights_payload)}
    derived['config']['dataset'] = {k: selected['metadata'][k] for k in ('path', 'sha256')}
    derived['config']['rows'] = {k: selected['rows'][k] for k in ('path', 'sha256')}
    derived['artifact_relocation'] = {
        'schema': 'royale-artifact-relocation.v1', 'training_performed': False,
        'source_checkpoint': reference(item['source'], source_payload),
        'source_weights': reference(weights['path'], weights_payload),
        'original_dataset': checkpoint['config']['dataset'],
        'original_rows': checkpoint['config']['rows'],
        'scope': '路径重定位；NPZ 原字节、模型参数和原实验指标保持不变'}
    from royale_phase3.common import digest
    derived.pop('checkpoint_sha256', None)
    derived['checkpoint_sha256'] = digest(derived)
    _operation(operations, weights['path'], item['target_weights'], weights_payload, 'byte_identical_weights')
    _operation(operations, item['source'], item['target_metadata'], encoded(derived),
               'checkpoint_references_and_self_digest; source hashes retained', source_payload)

    item = sources['native_manifest']
    manifest_payload = payload_for(item['source'], item.get('source_sha256'))
    manifest = json.loads(manifest_payload.decode('utf-8-sig'))
    if (manifest.get('schema') != 'royale-phase2-version.v1' or
            manifest.get('fingerprint') != digest({k: v for k, v in manifest.items() if k != 'fingerprint'}) or
            manifest.get('fingerprint') != version):
        raise ValueError('原生清单的版本指纹不完整或与参考模型不匹配')
    _operation(operations, item['source'], item['target'], manifest_payload,
               'byte_identical_native_manifest; pinned references intentionally unchanged')
    assets = []
    for asset in manifest['assets']:
        path = safe_path(asset['path'])
        assets.append({'path': asset['path'], 'sha256': asset['sha256'], 'role': asset.get('role'),
                       'present': path.is_file(), 'bytes': path.stat().st_size if path.is_file() else None})

    media = sources.get('media'); media_receipt = {'status': 'not_configured'}
    if media:
        if not safe_path(media['source_receipt']).is_file() and media.get('optional'):
            media_receipt = {'status': 'unavailable', 'reason': '可选历史录像回执不存在'}
        else:
            source_receipt_payload = payload_for(media['source_receipt'], media.get('source_receipt_sha256'))
            recording = json.loads(source_receipt_payload.decode('utf-8-sig'))
            if (recording.get('source') != 'actual_android_frameoutput' or
                    recording.get('status') != 'partial' or recording.get('recording_complete') is not False or
                    recording.get('natural_terminal') is not False):
                raise ValueError('初始化片段来源/partial 状态与预期不符，拒绝重新标记')
            video_path = recording['path']
            video_payload = payload_for(video_path, recording['sha256'])
            media_receipt = {
                'schema': 'royale-saved-media-relocation.v1', 'status': 'partial',
                'coverage': 'initialization_only', 'recording_complete': False,
                'natural_terminal': False, 'complete_game': False,
                'source': 'actual_android_frameoutput',
                'video': reference(media['target_video'], video_payload),
                'source_video': reference(video_path, video_payload),
                'source_recording_receipt': reference(media['source_receipt'], source_receipt_payload),
                'label': '真实 Android 初始化片段；预热超时，未形成完整对局录像',
                'browser_seek_verified': recording.get('browser_seek_verified', False),
                'scope': '仅迁移已保存的原生片段，不代表完整对局、实时互动或棋力验收'}
            _operation(operations, video_path, media['target_video'], video_payload, 'byte_identical_partial_video')
            _operation(operations, media['source_receipt'], media['target_receipt'], encoded(media_receipt),
                       'derived_partial_initialization_label; original receipt retained', source_receipt_payload)

    public = [{k: v for k, v in item.items() if k != '_payload'} for item in operations]
    conflicts = [item['target']['path'] for item in public if item['status'] == 'conflict']
    receipt = {
        'schema': SCHEMA, 'created_utc': datetime.now(timezone.utc).isoformat(),
        'status': 'blocked' if conflicts else 'planned', 'mode': 'plan',
        'sources': reference(sources_path, payload_for(sources_path)), 'files': public,
        'conflicts': conflicts, 'source_validation': validations, 'target_validation': [],
        'source_files_preserved': True, 'training_performed': False, 'native_rpc_calls': 0,
        'external_pinned_assets': {'count': len(assets), 'copied': False,
            'hashes_reverified': False, 'present_count': sum(item['present'] for item in assets),
            'known_bytes': sum(item['bytes'] or 0 for item in assets), 'assets': assets,
            'scope': '原生清单按原字节复制；固定资产仍由原路径与哈希标识，未复制 APK/VM/cache。存在性为只读 stat，不是原生执行验收。'},
        'media': media_receipt,
        'limitations': ['来源与训练 provenance 保留历史路径；运行读取的新 metadata/rows/weights 已重定位。',
                        '原生资产路径属于固定版本身份，保持原状；当前布局不是自带 APK/VM 的独立分发包。']}
    return receipt, operations, sources


def _create_missing(path, payload):
    destination = safe_path(path)
    # Recheck after preflight and immediately before exclusive creation.
    if destination.exists():
        if _matches(destination, payload):
            return 'reused'
        raise FileExistsError('目标冲突，拒绝覆盖: ' + path)
    cursor = ROOT
    for part in destination.parent.relative_to(ROOT).parts:
        cursor /= part
        if cursor.is_symlink():
            raise ValueError('迁移路径不允许符号链接: ' + path)
        cursor.mkdir(exist_ok=True)
        if not cursor.is_dir():
            raise ValueError('目标父路径不是目录')
    safe_path(path)
    with destination.open('xb') as stream:
        stream.write(payload); stream.flush(); os.fsync(stream.fileno())
    if destination.read_bytes() != payload:
        raise IOError('新目标写入后校验失败，保留现场: ' + path)
    return 'created'


def apply_migration(receipt, operations, sources):
    if receipt['conflicts']:
        raise FileExistsError('存在目标冲突；本次没有写入任何迁移目标')
    receipt['mode'] = 'apply'; receipt['status'] = 'applying'
    # Check every source and destination again before the first write.
    for item in operations:
        payload_for(item['source']['path'], item['source']['sha256'])
        destination = safe_path(item['target']['path'])
        if destination.exists() and not _matches(destination, item['_payload']):
            raise FileExistsError('目标在计划后发生冲突: ' + item['target']['path'])
    for index, item in enumerate(operations):
        receipt['files'][index]['status'] = _create_missing(item['target']['path'], item['_payload'])
    for item in sources['datasets']:
        _, validation = _validate_dataset(item['target_metadata'])
        receipt['target_validation'].append(validation)
    checkpoint = sources['checkpoint']
    selected = next(item for item in sources['datasets'] if item['id'] == checkpoint['dataset_id'])
    meta = document(selected['target_metadata'])
    receipt['target_validation'].append(_validate_checkpoint(checkpoint['target_metadata'],
        meta['version_fingerprint_sha256'], meta['observation_profile']))
    receipt['status'] = 'completed'
    return receipt


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--sources', default=DEFAULT_SOURCES)
    parser.add_argument('--apply', action='store_true', help='创建缺少的新目标；不移动或覆盖历史')
    parser.add_argument('--receipt', help='将完整回执写入新的仓库内相对路径')
    args = parser.parse_args(argv)
    receipt = {'schema': SCHEMA, 'mode': 'apply' if args.apply else 'plan', 'status': 'failed'}
    code = 0
    try:
        if args.receipt:
            path = safe_path(args.receipt)
            if path.exists():
                raise FileExistsError('回执文件已存在，请使用新路径: ' + args.receipt)
        receipt, operations, sources = plan_migration(args.sources)
        if args.receipt and any(args.receipt in (row['source']['path'], row['target']['path'])
                                for row in operations):
            raise ValueError('回执路径不能与迁移源或目标相同')
        if receipt['conflicts']:
            code = 2
        elif args.apply:
            apply_migration(receipt, operations, sources)
    except (OSError, ValueError, KeyError, RuntimeError) as exc:
        receipt.update(status='failed', error=f'{type(exc).__name__}: {exc}'); code = 2
    if args.receipt:
        try:
            _create_missing(args.receipt, encoded(receipt))
        except (OSError, ValueError) as exc:
            print(f'无法写入回执: {type(exc).__name__}: {exc}', file=sys.stderr); code = 2
    print(json.dumps({'status': receipt['status'], 'mode': receipt['mode'],
        'files': [{k: row[k] for k in ('target', 'status')} for row in receipt.get('files', [])],
        'conflicts': receipt.get('conflicts', []), 'receipt': args.receipt,
        'error': receipt.get('error'), 'native_rpc_calls': 0}, ensure_ascii=False, indent=2))
    return code


if __name__ == '__main__':
    raise SystemExit(main())

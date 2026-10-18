"""Build an explicitly allowlisted stage-two understanding ZIP, <=500000000B.

The default --self-check mode writes an inventory only. --build must be invoked
deliberately after the final acceptance report exists. Proprietary runtime/data
and secrets never enter the payload; stage-one deliveries are never overwritten.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
LIMIT=500_000_000
TEXT_EXT={'.py','.ps1','.md','.txt','.json','.jsonl','.toml','.yaml','.yml','.cpp','.h','.inc','.S','.java'}
DENY_EXT={'.apk','.so','.dll','.exe','.pyd','.qcow2','.img','.iso','.vhd','.vhdx','.key','.pem','.p12','.pfx','.jks','.keystore','.parquet','.csv','.scdb','.bank','.sc','.sc3d','.pt','.onnx','.mp4','.png','.jpg','.jpeg','.gif','.webp','.zip','.tar','.gz'}
SECRET_PATTERNS=[re.compile(rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),
                 re.compile(rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),re.compile(rb'\bAKIA[A-Z0-9]{16}\b'),
                 re.compile(rb'\bsk-[A-Za-z0-9_-]{30,}\b'),re.compile(rb'(?i)\bAuthorization\s*[:=]\s*[\"\']?Bearer\s+[A-Za-z0-9._-]{20,}')]
SOURCE_TOOL_EXCLUDES={'tools/phase2_native_build_apk.py'} # Contains the local debug signing password; preserve only its hash in version metadata.
OPEN_SOURCE_FILES=[
    'native_runner/probe/cr_replay_probe.cpp','native_runner/probe/minimal_capture.inc',
    'native_runner/probe/engine_instance.inc','native_runner/probe/probe_json.h',
    'native_runner/probe/native_touch_interceptor.cpp','native_runner/probe/native_touch_interceptor.h',
    'native_runner/contracts.py','native_runner/attestation.py','native_runner/visibility_runtime.py',
    'native_runner/cr_native_env.py','native_runner/offline_install.py','native_runner/offline_build.py',
]


def local(value):
    p=(ROOT/value).resolve()
    if not p.is_relative_to(ROOT):raise ValueError('Every input/output must remain inside the project')
    return p


def sha(data):return hashlib.sha256(data).hexdigest()
def encoded(value):return json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False).encode('utf-8')
def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def name_safe(name):
    p=PurePosixPath(name)
    return bool(name and not p.is_absolute() and '..' not in p.parts and '\\' not in name and ':' not in name)


def inspect_payload(name,data):
    if not name_safe(name):raise ValueError(f'Unsafe ZIP name: {name}')
    path=PurePosixPath(name);suffix=path.suffix
    if suffix.lower() in {e.lower() for e in DENY_EXT}:raise ValueError(f'Forbidden file type: {name}')
    if data[:4]==b'\x7fELF' or data[:2]==b'MZ' or data[:8]==b'QFI\xfb\x00\x00\x00\x03':raise ValueError(f'Native executable/disk image bytes: {name}')
    if any(pattern.search(data) for pattern in SECRET_PATTERNS):raise ValueError(f'Possible live credential/private key in: {name}')
    parts={p.lower() for p in path.parts}
    if parts & {'.cache','.venv','.venv-phase2-data','__pycache__','user-provided','runtime-assets'} or 'native_runner/data/' in name:
        raise ValueError(f'Forbidden mutable/proprietary directory: {name}')
    if any(p in {'id_rsa','id_ed25519','guest-key','credentials','credentials.json','.env'} for p in parts):
        raise ValueError(f'Secret-bearing filename: {name}')
    if path.name.lower() in {'actions.json','actions.jsonl','states.jsonl','decisions.jsonl','payload.json','payload_json.json'}:
        raise ValueError(f'Raw timeline/state/payload file is not a share artifact: {name}')


def reject_raw_data(value):
    if isinstance(value,dict):
        for key,child in value.items():
            if key in {'payload_json','raw_event','raw_payload','source_actions','cmd','evt'} and child:
                raise ValueError(f'Raw source/game data field is not an acceptance/manifest summary: {key}')
            if key=='actions' and isinstance(child,(list,dict)) and child:
                raise ValueError('Full actions must be replaced by scalar statistics before sharing')
            reject_raw_data(child)
    elif isinstance(value,list):
        for child in value:reject_raw_data(child)


def redact_acceptance(value):
    """Keep acceptance scalars/evidence references while removing raw timelines."""
    blocked={'payload_json','raw_event','raw_payload','source_actions','cmd','evt','actions','decisions','states','wire'}
    if isinstance(value,dict):
        result={}
        for key,child in value.items():
            if key in blocked:
                continue
            result[key]=redact_acceptance(child)
        return result
    if isinstance(value,list):
        return [redact_acceptance(child) for child in value]
    return value


class Payload:
    def __init__(self):self.files={};self.records=[];self.omissions=[]
    def add_bytes(self,name,data,origin='generated',source=None):
        inspect_payload(name,data)
        if name in self.files:raise ValueError(f'Duplicate allowlist name: {name}')
        self.files[name]=data;self.records.append({'path':name,'bytes':len(data),'sha256':sha(data),'origin':origin,
                                                **({'source':source} if source else {})})
    def add_file(self,path,origin='first_party_source'):
        path=Path(path);rel=path.relative_to(ROOT).as_posix()
        if path.is_symlink():raise ValueError(f'Symlink is not an allowlisted source: {rel}')
        if path.stat().st_size>5_000_000:raise ValueError(f'Text source unexpectedly exceeds5MB: {rel}')
        self.add_bytes(rel,path.read_bytes(),origin=origin,source=rel)


def source_allowlist(payload):
    for path in sorted((ROOT/'royale_phase2').glob('*.py')):payload.add_file(path)
    for path in sorted((ROOT/'phase2').glob('*.md')):payload.add_file(path,origin='first_party_documentation')
    for pattern in ['phase2*.py','phase2*.ps1']:
        for path in sorted((ROOT/'tools').glob(pattern)):
            rel=path.relative_to(ROOT).as_posix()
            if rel in SOURCE_TOOL_EXCLUDES:payload.omissions.append({'path':rel,'reason':'debug signing password in source; proprietary build not part of understanding ZIP'});continue
            payload.add_file(path)
    for path in sorted((ROOT/'tools/phase2_native_src').glob('*')):
        if path.is_file() and path.suffix in {'.cpp','.h','.java'}:payload.add_file(path)
    for pattern in ['test_phase2*.py','phase2*.py']:
        for path in sorted((ROOT/'tests').glob(pattern)):payload.add_file(path,origin='first_party_test')
    for name in ['version_manifest.json','frozen_manifest.json','action_subspace_proof.json']:
        path=ROOT/'data/phase2'/name
        if path.exists():payload.add_file(path,origin='versioned_manifest_only')
        else:payload.omissions.append({'path':str(path.relative_to(ROOT)),'reason':'not yet generated'})
    # Exact small generated learning/split manifests, never raw payloads/decisions.
    for rel in ['outputs/phase2/learning/split_manifest.json','data/phase2/learning_split.json',
                'outputs/phase2/learning_contracts/report.json']:
        path=ROOT/rel
        if path.is_file():payload.add_file(path,origin='learning_manifest')


def open_wrappers(payload):
    base=ROOT/'third_party/phase2/firstlight';acquisition=base/'ACQUISITION.json'
    if not acquisition.exists():return
    meta=read(acquisition)
    for name in ['LICENSE','NOTICE']:
        path=base/name
        if path.exists():payload.add_file(path,origin='open_wrapper_license')
    commit=meta.get('commit') or meta.get('sha') or meta.get('pinned_commit')
    payload.add_bytes('third_party/phase2/firstlight/SHARE_SOURCE_IDENTITY.json',encoded({
        'repository':'https://gitlab.com/firstlight3/FirstLight_CR','commit':commit,
        'source_acquisition_sha256':sha(acquisition.read_bytes()),'license_scope':'Selected wrapper source under included Apache2/LICENSE and NOTICE; no game library or resource grant',
        'selection':OPEN_SOURCE_FILES}),origin='open_wrapper_identity')
    for name in OPEN_SOURCE_FILES:
        path=base/name
        if path.is_file():payload.add_file(path,origin='limited_open_wrapper_source')
        else:payload.omissions.append({'path':str(path.relative_to(ROOT)),'reason':'not present in pinned source'})
    payload.omissions.append({'path':'third_party/phase2/jason','reason':'Root wrapper redistribution license not confirmed; commit/link only, not copied'})


def quality_statistics(payload,patterns):
    rows=[]
    for pattern in patterns:
        for path in sorted(ROOT.glob(pattern)):
            if not path.is_file() or not path.resolve().is_relative_to(ROOT):continue
            source=read(path)
            rows.append({'source_report_sha256':sha(path.read_bytes()),'anonymized_episode':sha(path.parent.name.encode())[:24],
                         'schema':source.get('schema',source.get('schema_version')),'quality':source.get('quality'),
                         'completed':source.get('completed'),'native_execution':source.get('native_execution'),
                         'strict_source_levels':source.get('strict_source_levels'),'counts':source.get('counts'),
                         'status':source.get('status'),'error_kind':str(source.get('error','')).split(':',1)[0] if source.get('error') else None})
    payload.add_bytes('reports/native_episode_quality.jsonl',b''.join(json.dumps(row,separators=(',',':')).encode()+b'\n' for row in rows),origin='derived_scalar_statistics')
    return len(rows)


def derived_samples(payload,episodes,max_samples=3):
    samples=[]
    for value in episodes:
        folder=local(value);a=folder/'actions.json';d=folder/'decisions.jsonl';e=folder/'episode.json';w=folder/'wire.jsonl'
        if not all(p.is_file() for p in [a,d,e,w]):raise ValueError(f'Actual native sample evidence incomplete: {value}')
        action_rows=read(a)['actions'];consumed={r['request_id']:r for r in action_rows if r.get('feedback',{}).get('card_consumed') is True}
        if not consumed:continue
        hashes={p.name:sha(p.read_bytes()) for p in [a,d,e,w]}
        with d.open(encoding='utf-8') as stream:
            for line in stream:
                row=json.loads(line)
                for label in row.get('labels',[]):
                    action=consumed.get(label.get('request_id'))
                    if not action:continue
                    actor=deepcopy_actor(row['actor_observation']);feedback=action['feedback'];event=feedback['card_event']
                    samples.append({'sample_id':sha(action['request_id'].encode())[:24],
                        'actor':actor,'actual_native_consumption':{'execution_tick':event[1],'native_owner':event[2],
                            'root_card_id':action['card_id'],'event_card_id':event[3],'cost':event[4],
                            'matched_by':feedback.get('matched_by'),'effective_form_identity_unknown':feedback.get('effective_form_identity')=='unknown'},
                        'source_evidence_sha256':hashes,
                        'scope':'Few anonymized derived own/public actor and actual consumption facts; no raw payload, full action timeline, source IDs, names or coordinates copied'})
                    if len(samples)>=max_samples:break
                if len(samples)>=max_samples:break
        if len(samples)>=max_samples:break
    payload.add_bytes('examples/real_native_actor_consumption.jsonl',b''.join(json.dumps(row,separators=(',',':')).encode()+b'\n' for row in samples),origin='few_real_anonymized_derived_samples')
    return len(samples)


def deepcopy_actor(actor):
    from royale_phase2.trajectories import validate_actor
    validate_actor(actor)
    # Serialize only the versioned actor whitelist, never adjacent row metadata.
    return json.loads(json.dumps(actor))


def checkpoint(payload,value):
    if not value:return None
    path=local(value)
    if not path.is_file():raise ValueError('Explicit checkpoint is missing')
    meta=read(path);weights=local(meta['weights']['path']);data=weights.read_bytes()
    if len(data)>100_000_000 or sha(data)!=meta['weights']['sha256']:raise ValueError('Checkpoint weights missing, changed or oversized')
    if weights.suffix!='.npz':raise ValueError('Only audited numeric NumPy BC weights are allowed')
    import numpy as np
    import io
    with np.load(io.BytesIO(data),allow_pickle=False) as archive:
        for name in archive.files:
            array=archive[name]
            if array.dtype.kind!='f' or not np.isfinite(array).all():raise ValueError('Object/non-finite checkpoint tensor')
    payload.add_file(path,origin='explicit_numeric_bc_checkpoint')
    payload.add_bytes(weights.relative_to(ROOT).as_posix(),data,origin='explicit_numeric_bc_weights')
    return {'checkpoint':path.relative_to(ROOT).as_posix(),'file_sha256':sha(path.read_bytes()),
            'weights_sha256':sha(data),'profile':meta.get('profile'),'version_fingerprint_sha256':meta.get('version_fingerprint_sha256')}


def acceptance_readme(payload,path,stats,samples,model):
    if path:
        path=local(path);original=path.read_bytes();report=read(path);redacted=redact_acceptance(report);reject_raw_data(redacted)
        if len(original)>2_000_000:raise ValueError('Acceptance report is unexpectedly large')
        redacted_bytes=encoded(redacted)
        payload.add_bytes('reports/actual_acceptance.json',redacted_bytes,origin='redacted_actual_acceptance_report',source=path.relative_to(ROOT).as_posix())
        status=json.dumps(redacted,ensure_ascii=False,indent=2)
        provenance=f"读取实际报告 `{path.relative_to(ROOT).as_posix()}`，原始SHA256 `{sha(original)}`；包内报告已移除raw timeline/action字段，仅保留门状态、计数、哈希和错误类别。"
    else:
        status=json.dumps({'status':'acceptance_report_not_supplied','claim':'Not a final acceptance delivery'},indent=2)
        provenance='尚未提供最终验收报告，不能据此声明任何阶段已通过。'
    readme=f'''# 第二阶段项目理解包\n\n{provenance}\n\n本包用于阅读原生平台代码、证据边界和后续优化位置。它不是包含游戏运行资源的即用私服分发，也不覆盖第一阶段交付。\n\n## 实际验收状态（原样读取，不由打包器推断通过）\n\n```json\n{status}\n```\n\n## 包含与省略\n\n包含项目自己的phase2代码/工具/测试/中文文档、版本与来源锁、逐局精简质量统计{stats}行、最多3条真实脱敏派生样例（本次{samples}条），以及显式指定的BC模型。模型记录：`{json.dumps(model,ensure_ascii=False)}`。\n\n排除环境/缓存/VM、APK、libg/probe及所有二进制运行库、游戏资源和catalog内容、原始大数据/payload/完整actions、签名库、SSH私钥和凭据。部分开放FirstLight封装源码附Apache2许可证及固定commit；其许可证不覆盖游戏素材。Jason根wrapper许可未确认，未复制。\n\n入口：`phase2/NATIVE_ENGINE_GUIDE.md`、`phase2/NATIVE_AUDIT.md`、`phase2/SHARING.md`、`royale_phase2/backend.py`、`royale_phase2/agent_env.py`。缺失运行材料必须在原项目按身份锁重新准备，不能生成假资源或偷偷降级近似引擎。\n\n当前WAIT/Fireball/Arrows动作子空间不等于完整卡组策略；两法术离手后存在WAIT锁定。内部机制回归不等于官方轨迹忠实度。实际通过与未通过项以本包实际acceptance报告及相应文档范围为准。\n\n`PACKAGE_MANIFEST.json`列出每个payload文件SHA256/大小/来源；外部pack report列ZIP SHA256、CRC与安全检查。原始大证据只通过项目相对路径/哈希引用，不因未打入本包变成已随包提供。\n'''
    payload.add_bytes('GPT_README.md',readme.encode('utf-8'),origin='generated_from_actual_acceptance')


def verify_zip(path):
    path=local(path)
    if path.stat().st_size>LIMIT:raise ValueError('ZIP exceeds500000000 bytes')
    with zipfile.ZipFile(path) as archive:
        names=archive.namelist()
        if len(names)!=len(set(names)) or sum(i.file_size for i in archive.infolist())>LIMIT:raise ValueError('Duplicate/oversized uncompressed ZIP')
        if archive.testzip() is not None:raise ValueError('ZIP CRC readback failed')
        manifest=json.loads(archive.read('PACKAGE_MANIFEST.json'))
        expected={row['path']:row for row in manifest['files']}
        if set(names)!=set(expected)|{'PACKAGE_MANIFEST.json'}:raise ValueError('ZIP contents differ from allowlist manifest')
        for name in names:
            data=archive.read(name);inspect_payload(name,data)
            if name in expected and (len(data)!=expected[name]['bytes'] or sha(data)!=expected[name]['sha256']):raise ValueError(f'Payload SHA mismatch: {name}')
    return {'zip':path.relative_to(ROOT).as_posix(),'bytes':path.stat().st_size,'sha256':sha(path.read_bytes()),
            'crc_readback':'pass','all_file_sha256':'pass','sensitive_and_proprietary_file_scan':'pass','payload_files':len(expected)}


def safety_self_test(out):
    """Synthetic packaging checks only; they never claim native execution."""
    checks=[]
    for name,data in [('game.apk',b'x'),('core.so',b'\x7fELF'),('safe.md',b'MZxx'),
                      ('../escape.md',b'x'),('.cache/file.md',b'x'),('raw/actions.json',b'{}'),
                      ('key.txt',b'-----BEGIN '+b'OPENSSH PRIVATE KEY-----')]:
        try:inspect_payload(name,data)
        except ValueError:checks.append({'case':name,'rejected':True})
        else:raise AssertionError(f'Unsafe package member accepted: {name}')
    sample=(ROOT/'phase2/SHARING.md').read_bytes();name='phase2/SHARING.md'
    manifest={'schema':'phase2-builder-canary-only.v1','files':[{'path':name,'bytes':len(sample),'sha256':sha(sample)}]}
    canary=out/'crc_canary.zip'
    with zipfile.ZipFile(canary,'x',compression=zipfile.ZIP_STORED) as archive:
        archive.writestr(name,sample);archive.writestr('PACKAGE_MANIFEST.json',encoded(manifest))
    verified=verify_zip(canary);checks.append({'case':'actual CRC/SHA readback of own documentation canary','verified':verified})
    damaged=out/'crc_canary_corrupt.zip';blob=bytearray(canary.read_bytes());start=blob.find(sample)
    assert start>=0;blob[start]^=1;damaged.write_bytes(blob)
    try:verify_zip(damaged)
    except (ValueError,zipfile.BadZipFile):checks.append({'case':'corrupted canary rejected','rejected':True})
    else:raise AssertionError('Corrupted ZIP accepted')
    report={'schema':'phase2-share-builder-safety-tests.v1','passed':True,'final_package_created':False,
            'native_rpc_connections':0,'scope':'Canary packaging/denylist verification, not project acceptance','checks':checks}
    (out/'report.json').write_bytes(encoded(report));return report


def main():
    p=argparse.ArgumentParser();p.add_argument('--build',action='store_true');p.add_argument('--self-check',action='store_true')
    p.add_argument('--self-test',action='store_true',help='Synthetic denylist plus own-documentation CRC canary, not a final package')
    p.add_argument('--manifest',action='append',default=[],help='Additional explicit small source/learning/split manifest JSON')
    p.add_argument('--acceptance');p.add_argument('--checkpoint');p.add_argument('--sample-episode',action='append',default=[])
    p.add_argument('--quality-glob',action='append',default=[]);p.add_argument('--verify-zip');p.add_argument('--name')
    args=p.parse_args()
    if args.self_test:
        folder=ROOT/'outputs/share'/('phase2_builder_selftest_'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ'))
        folder.mkdir(parents=True,exist_ok=False);report=safety_self_test(folder)
        print(json.dumps({'passed':report['passed'],'final_package_created':False,'report':str((folder/'report.json').relative_to(ROOT))}));return
    if args.verify_zip:print(json.dumps(verify_zip(args.verify_zip),ensure_ascii=False));return
    if args.build and not args.acceptance:raise ValueError('--build requires an actual final/current acceptance report')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');name=args.name or 'phase2_context_'+stamp
    if not re.fullmatch(r'phase2_[A-Za-z0-9_-]+',name):raise ValueError('A new phase2_ output name is required')
    out=ROOT/'outputs/share';out.mkdir(parents=True,exist_ok=True)
    payload=Payload();source_allowlist(payload);open_wrappers(payload)
    for value in args.manifest:
        path=local(value);data=path.read_bytes();parsed=read(path);reject_raw_data(parsed)
        if path.suffix!='.json' or len(data)>5_000_000 or not any(k in path.stem.lower() for k in ('manifest','split','version')):
            raise ValueError('Extra manifest must be an explicit small manifest/split/version JSON')
        payload.add_bytes('reports/manifests/'+sha(value.encode())[:8]+'_'+path.name,data,origin='explicit_learning_source_manifest',source=path.relative_to(ROOT).as_posix())
    stats=quality_statistics(payload,args.quality_glob or ['outputs/phase2/replays/formal/*/episode.json'])
    sample_count=derived_samples(payload,args.sample_episode);model=checkpoint(payload,args.checkpoint)
    acceptance_readme(payload,args.acceptance,stats,sample_count,model)
    total=sum(map(len,payload.files.values()))
    if total>LIMIT:raise ValueError('Allowlisted payload exceeds500000000 bytes before compression')
    manifest={'schema':'phase2-understanding-package-manifest.v1','created_utc':datetime.now(timezone.utc).isoformat(),
              'scope':'Stage2 only; explicit allowlist, proprietary resources and large raw actions excluded',
              'file_count':len(payload.records),'uncompressed_payload_bytes':total,'files':payload.records,'omissions':payload.omissions,
              'actual_acceptance_supplied':bool(args.acceptance),'quality_rows':stats,'real_derived_sample_rows':sample_count,
              'checkpoint':model}
    if not args.build:
        report={'schema':'phase2-share-builder-self-check.v1','final_zip_created':False,'rpc_connections':0,
                'allowlist_scan':'pass','sensitive_scan':'pass','planned_payload_bytes':total,'manifest':manifest,
                'pending':['Actual final acceptance report/model/samples must be supplied at final build as appropriate','CRC and ZIP SHA only run after --build']}
        path=out/(name+'_self_check.json');path.write_bytes(encoded(report));print(json.dumps({'self_check':'pass','final_zip_created':False,'planned_files':len(payload.files),'planned_bytes':total,'report':path.relative_to(ROOT).as_posix()}));return
    zip_path=out/(name+'.zip')
    if zip_path.exists():raise FileExistsError('Never overwrite an existing stage-two or stage-one ZIP')
    partial=out/(name+'.zip.partial')
    with zipfile.ZipFile(partial,'x',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for member,data in sorted(payload.files.items()):archive.writestr(member,data)
        archive.writestr('PACKAGE_MANIFEST.json',encoded(manifest))
    partial.replace(zip_path);report=verify_zip(zip_path)
    report.update(schema='phase2-share-package-verification.v1',manifest_sha256=sha(encoded(manifest)),stage='phase2',checkpoint=model)
    (out/(name+'_verification.json')).write_bytes(encoded(report))
    (out/(name+'_manifest.json')).write_bytes(encoded(manifest))
    print(json.dumps(report,ensure_ascii=False),flush=True)


if __name__=='__main__':main()

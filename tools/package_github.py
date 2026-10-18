"""Build an allowlisted GitHub/Codespaces snapshot, without touching any remote."""
import argparse
import ast
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path,PurePosixPath
import re
import shutil
import sys
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
TEXT={'.py','.ps1','.cmd','.sh','.md','.txt','.json','.html','.toml','.lock','.bib','.cjs','.js','.yml','.yaml','.cpp','.h','.hpp','.inc','.c','.java','.xml','.properties','.gradle'}
SKIP={'.git','__pycache__','node_modules','user-provided','runtime-assets'}
PATTERNS=[('private_key',rb'-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----'),('github_token',rb'\bgh[pousr]_[A-Za-z0-9]{30,}\b'),('openai_token',rb'\bsk-(?:proj-)?[A-Za-z0-9_-]{35,}\b'),('aws_access_key',rb'\bAKIA[A-Z0-9]{16}\b')]

def sha(data):return hashlib.sha256(data).hexdigest()
def canonical(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
def safe(name):
    p=PurePosixPath(name)
    return not p.is_absolute() and '..' not in p.parts and ':' not in name and '\\' not in name and not set(p.parts)&SKIP

def build(destination):
    stage=(ROOT/destination).resolve();stage.relative_to(ROOT)
    if stage.exists():raise FileExistsError('Use a new immutable migration folder')
    stage.mkdir(parents=True)
    payload={};origins={};transforms=[]
    def add(name,data,origin):
        if not safe(name):raise ValueError('Unsafe payload name: '+name)
        if len(data)>40_000_000:raise ValueError('GitHub file budget exceeded: '+name)
        if name.endswith('.py'):
            text=data.decode('utf-8-sig')
            text,n=re.subn(r"(?m)^(\s*)password\s*=\s*(['\"])[^'\"]+\2\s*$",r"\1password=__import__('os').environ['ROYALE_LOCAL_SIGNING_PASSWORD']",text)
            if n:transforms.append({'path':name,'transformation':'Replace local debug signing password with environment variable; original local script retained'})
            data=text.encode('utf-8');ast.parse(data,filename=name)
        for label,pattern in PATTERNS:
            if re.search(pattern,data):raise ValueError('Credential-pattern hit '+label+' in '+name)
        if data[:4]==b'\x7fELF' or data[:2]==b'MZ':raise ValueError('Executable not allowed: '+name)
        payload[name]=data;origins[name]=origin
    def file(path):add(path.relative_to(ROOT).as_posix(),path.read_bytes(),'current_project_source')
    # Start with the already verified friend release for compact historic inputs.
    release=ROOT/'outputs/share/RoyaleAI_NewSystem_20261018_215518.zip'
    release_report=json.loads(release.with_suffix('.verification.json').read_text(encoding='utf-8'))
    if sha(release.read_bytes())!=release_report['sha256']:raise ValueError('Historical friend release hash changed')
    with zipfile.ZipFile(release) as archive:
        for info in archive.infolist():
            if info.is_dir():continue
            name='/'.join(PurePosixPath(info.filename).parts[1:])
            if not name or name=='PACKAGE_MANIFEST.json' or name.startswith(('friend_runs/','reproduction/')):continue
            data=archive.read(info)
            if name.startswith(('data/','models/','outputs/','references/','third_party/')):
                add('shared_assets/project/'+name,data,'reviewed_historical_release')
            elif name.startswith(('royale_','docs/','phase2/','phase3/','examples/','tools/','tests')):
                add(name,data,'reviewed_historical_release_source')
        numeric={
          'arrays':('data/friend_numeric/training_arrays.npz','shared_assets/numeric/training_arrays.npz'),
          'metadata':('models/friend_bc/checkpoint.json','shared_assets/numeric/checkpoint.json'),
          'weights':('models/friend_bc/weights.npz','shared_assets/numeric/weights.npz'),
          'samples':('examples/friend_native_samples.json','shared_assets/numeric/samples.json')}
        top=PurePosixPath(archive.infolist()[0].filename).parts[0]
        for _,(src,dst) in numeric.items():add(dst,archive.read(top+'/'+src),'reviewed_derived_public_numeric_input')
    # Overlay current owned source. Original folders and all local environments stay untouched.
    for folder in ('royale_ai','royale_lab','royale_phase2','royale_phase3','docs','phase2','phase3','examples','tools','tests','tests_phase2','tests_phase3','tests_platform','tests_cloud','.devcontainer','.vscode','.github'):
        for path in sorted((ROOT/folder).rglob('*')):
            if not path.is_file() or path.is_symlink() or set(path.relative_to(ROOT).parts)&SKIP:continue
            if path.suffix in TEXT or path.name in ('LICENSE','NOTICE','Dockerfile'):
                if path.stat().st_size<=5_000_000:file(path)
    # Preserve the original MVP as source, without its old generated artifacts.
    baseline=ROOT/'baseline/mvp_v0_1'
    if baseline.exists():
        for path in baseline.rglob('*'):
            if path.is_file() and not set(path.relative_to(baseline).parts)&{'outputs','models','.venv','__pycache__'} and path.suffix in TEXT and path.stat().st_size<1_000_000:file(path)
    for path in ROOT.iterdir():
        if path.is_file() and (path.suffix in TEXT or path.name in ('.gitignore','.gitattributes','.dockerignore')):file(path)
    # One genuine phase3 dataset and one saved rule-distilled GRU, stored once and restored to ignored paths.
    project_inputs=[
      'data/phase3/native/pilot-003/metadata.json','data/phase3/native/pilot-003/rows.jsonl',
      'data/phase3/native/pilot-003/readback.json','data/phase3/native/pilot-003/data_audit.json',
      'models/phase3/pilot-003-D-distill-weight8-seed31001/epoch-0002.json',
      'models/phase3/pilot-003-D-distill-weight8-seed31001/epoch-0002.npz',
      'models/phase3/pilot-003-D-distill-weight8-seed31001/report.json']
    for name in project_inputs:
        path=ROOT/name
        if not path.is_file():raise FileNotFoundError(name)
        add('shared_assets/project/'+name,path.read_bytes(),'native_self_collected_public_teacher_pilot')
    records=[]
    for name,data in payload.items():
        if not name.startswith('shared_assets/'):continue
        rec={'source':name,'bytes':len(data),'sha256':sha(data),'origin':origins[name]}
        if name.startswith('shared_assets/project/'):rec['destination']=name[len('shared_assets/project/'):]
        records.append(rec)
    counts=release_report['numeric_cache_counts']
    phase3=json.loads((ROOT/'data/phase3/native/pilot-003/metadata.json').read_text(encoding='utf-8'))
    assets={'schema':'github-reviewed-portable-assets.v1','files':records,'numeric':{k:v[1] for k,v in numeric.items()},
      'numeric_counts':counts,'phase3':{'metadata':project_inputs[0],'checkpoint':project_inputs[4]},'phase3_counts':phase3['counts'],
      'native_runtime_included':False,'official_fidelity_claim':False,
      'scope':'Saved public numeric learning inputs, weights, source/quality indices and reviewable historic summaries. Not original game resources or a new native acceptance.'}
    add('shared_assets/MANIFEST.json',json.dumps(assets,ensure_ascii=False,indent=2).encode(),'generated_manifest')
    excluded={'schema':'github-migration-exclusions.v1','original_project_preserved':True,
      'categories':{'environments_and_caches':'.venv*, .cache, .tmp, tmp remain local and are re-created from cloud profiles',
      'native_runtime':'VM disks, APK/libg/resources, compiled probes/native extensions remain local; missing asset identities are retained in version manifest/EXTERNAL_ASSETS',
      'raw_data_and_media':'Large original replays, source Parquet, screenshots/videos, full raw traces and downloaded papers remain local; reviewed pilot/indices/summaries included',
      'credentials':'No private keys, tokens, account files, keystores or Git metadata are copied'},
      'source_release':{'path':str(release.relative_to(ROOT)),'sha256':release_report['sha256']},
      'transformations':transforms,'all_current_models_migrated':False,
      'model_scope':'Historical NumPy models from reviewed release, plus one runnable phase3 GRU; other local experiments stay in original project'}
    add('MIGRATION_EXCLUSIONS.json',json.dumps(excluded,ensure_ascii=False,indent=2).encode(),'generated_exclusion_record')
    for name,data in payload.items():
        path=stage/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data)
    manifest={'schema':'github-migration-snapshot.v1','created_utc':datetime.now(timezone.utc).isoformat(),
      'files':[{'path':name,'bytes':len(data),'sha256':sha(data),'origin':origins[name]} for name,data in sorted(payload.items())],
      'total_bytes':sum(map(len,payload.values())),'remote_upload_performed':False,'codespaces_hosted_run_verified':False}
    (stage/'MIGRATION_MANIFEST.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    destination=stage.with_suffix('.zip')
    with zipfile.ZipFile(destination,'w',zipfile.ZIP_DEFLATED,compresslevel=7) as archive:
        for name in sorted([*payload,'MIGRATION_MANIFEST.json']):archive.write(stage/name,'RoyaleLab_GitHub/'+name)
    with zipfile.ZipFile(destination) as archive:
        if archive.testzip() is not None:raise ValueError('ZIP CRC failure')
    checksum=sha(destination.read_bytes());destination.with_suffix('.zip.sha256').write_text(checksum+'  '+destination.name+'\n',encoding='ascii')
    report={'folder':str(stage.relative_to(ROOT)),'archive':str(destination.relative_to(ROOT)),
      'zip_bytes':destination.stat().st_size,'payload_files':len(payload)+1,'uncompressed_bytes':manifest['total_bytes'],
      'sha256':checksum,'secret_pattern_scan':'pass','native_executables_included':0,'original_project_deleted_or_moved':False,
      'native_game_runtime_verified':False,'hosted_codespaces_verified':False}
    (stage.parent/(stage.name+'.report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/github_migration/RoyaleLab_GitHub_20261018')
    build(p.parse_args().output)

"""Save reviewable upstream-to-local patch and exact local build provenance."""
from pathlib import Path
import concurrent.futures
import difflib
import hashlib
import json
import sys
import sysconfig
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
ENGINE=ROOT/'third_party/ClashRoyaleAi'
COMMIT='2f172925a15b17f973291f41a789b19a29f0fc43'
VERSION='ClashRoyaleAi@2f172925a15b+lab-calibrated-v2'
ORIGINAL=ROOT/'.cache/native_original_blobs'
ORIGINAL.mkdir(parents=True,exist_ok=True)
originals=json.loads((ROOT/'outputs/native_engine_download.json').read_text(encoding='utf-8'))
modified=[]
for row in originals:
    path=ENGINE/row['path']
    if path.exists() and row.get('sha256') and hashlib.sha256(path.read_bytes()).hexdigest()!=row['sha256']:
        modified.append(row)

def fetch_original(row):
    path=ORIGINAL/row['path'];errors=[]
    for _ in range(3):
        try:
            if path.exists() and hashlib.sha256(path.read_bytes()).hexdigest()==row['sha256']:return row
            data=urllib.request.urlopen(f'https://raw.githubusercontent.com/itzik123/ClashRoyaleAi/{COMMIT}/'+row['path'],timeout=25).read()
            if hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Original SHA256 mismatch')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);return row
        except Exception as exc:errors.append(str(exc))
    return {**row,'error':errors}

with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:restored=list(pool.map(fetch_original,modified))
patch=[]
for row in restored:
    if row.get('error'):continue
    name=row['path'];before=(ORIGINAL/name).read_text(encoding='utf-8').splitlines(keepends=True)
    after=(ENGINE/name).read_text(encoding='utf-8').splitlines(keepends=True)
    patch.extend(difflib.unified_diff(before,after,fromfile='a/'+name,tofile='b/'+name))
new=['include/core/NativeLabBindings.h','include/core/NativeNumericBindings.h',
     'tests/core/test_local_crown_calibration.cpp','tests/core/test_local_economy_legality.cpp','tests/core/test_local_numeric_data.cpp']
for name in new:
    patch.extend(difflib.unified_diff([], (ENGINE/name).read_text(encoding='utf-8').splitlines(keepends=True),fromfile='/dev/null',tofile='b/'+name))
(ENGINE/'LOCAL_PATCH.patch').write_text(''.join(patch),encoding='utf-8')
module=next((ENGINE/'python_ai').glob('clash_royale_env*'+sysconfig.get_config_var('EXT_SUFFIX')))
manifest={'repository':'https://github.com/itzik123/ClashRoyaleAi','commit':COMMIT,'version':VERSION,
    'module_path':str(module.relative_to(ROOT)).replace('\\','/'),'module_sha256':hashlib.sha256(module.read_bytes()).hexdigest(),
    'python':sys.version,'abi':sysconfig.get_config_var('EXT_SUFFIX'),'pybind11':'3.0.1','catch2':'3.7.1',
    'build_command':'powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_native.ps1 -Tests',
    'compiler':'Existing MSVC 19.43.34809.0 (Visual Studio 2022 Community), C++17 /O2 /MD',
    'local_patch':'third_party/ClashRoyaleAi/LOCAL_PATCH.patch',
    'modified_upstream_files':[{'path':r['path'],'upstream_sha256':r['sha256'],'local_sha256':hashlib.sha256((ENGINE/r['path']).read_bytes()).hexdigest(),'original_recovered':not bool(r.get('error'))} for r in restored],
    'new_adapter_test_files':new,'calibration':{'elixir_per_tick':1/28,'double_tick':1200,'triple_tick':2400,'regulation_tick':1800,
        'source':'crforge@b41cf9b community decoded data + native/Java differential fixtures','official_parity':'unverified'},
    'source_download_failures':['git clone timeout','proxy codeload archive incomplete at 24641536 bytes','direct codeload archive IncompleteRead at 15728640 bytes'],
    'source_acquisition':'847 pinned raw GitHub blobs with SHA256 manifest; bundled 30MB ONNX omitted from source directory but independently downloaded in references/library/assets',
    'independent_large_asset':{'path':'references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx',
        'sha256':hashlib.sha256((ROOT/'references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx').read_bytes()).hexdigest()},
    'numeric_configuration':{'file':'data/platform/balance_v2.json','schema':'data/platform/balance_v2.schema.json',
        'unsupported_parameter_inventory':'data/platform/unsupported_numeric_parameters.json','registry_rows':173,
        'editable_registry_rows':171,'excluded_registry_ids':[164,165],'field_types':25,
        'api':['export_numeric_stats(defaults=False)','apply_numeric_overrides(payload,reset=True)'],
        'default_balance_hash':hashlib.sha256(json.dumps(json.loads((ROOT/'data/platform/balance_v2.json').read_text(encoding='utf-8'))['cards'],sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'staged_build_command':'powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_native.ps1 -OutputFolder .cache/native_numeric_stage -Tests',
        'promotion':'Staged tested .pyd and Catch2 executable copied to the main module/build locations after service shutdown',
        'default_equivalence':'outputs/native_engine_numeric_replay_equivalence.json',
        'changed_numeric_battle_tests':'outputs/native_engine_numeric_stats.json'},
    'build_fallback':'Installed CMake 4.0.0-rc2 and project-local CMake 3.31.10 both returned 0xc0000409 at configuration; existing MSVC direct compilation succeeded'}
out=ROOT/'outputs/native_engine';out.mkdir(exist_ok=True)
(out/'build_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
source=json.loads((ENGINE/'SOURCE_COMMIT.json').read_text(encoding='utf-8'))
source.update(local_version=VERSION,local_patch='LOCAL_PATCH.patch',build_manifest='outputs/native_engine/build_manifest.json')
(ENGINE/'SOURCE_COMMIT.json').write_text(json.dumps(source,indent=2),encoding='utf-8')
print(json.dumps({'modified_files':len(modified),'originals_recovered':sum(not r.get('error') for r in restored),'patch_bytes':(ENGINE/'LOCAL_PATCH.patch').stat().st_size,'module_sha256':manifest['module_sha256']},indent=2))

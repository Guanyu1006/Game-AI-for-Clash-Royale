"""Build a local smoke APK from an explicitly audited publisher variant.

Uses the fixed prebuilt probe, existing JDK and Apache apksig. This does not
assert equality to upstream's different whole-APK hash, fetch runtime content,
start Android, or connect to any game service.
"""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/native'
CACHE=ROOT/'.cache/phase2/native_apk'
FIRST=ROOT/'third_party/phase2/firstlight'
APK=FIRST/'user-provided/nulls-royale.apk'
PROBE=ROOT/'third_party/phase2/jason/prebuilt/libcrprobe.so'
APKTOOL=ROOT/'.cache/phase2/native_tools/apktool_2.12.1.jar'
APKSIG=ROOT/'.cache/phase2/native_tools/apksig-8.8.2.jar'
EXPECTED_APKTOOL='66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa'
EXPECTED_LIBG='110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
EXPECTED_PROBE='d526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190'

def digest(path):
    with Path(path).open('rb') as stream:return hashlib.file_digest(stream,'sha256').hexdigest()

def command(args,label):
    log=OUT/f'apk_{label}.log'
    started=time.time()
    print(json.dumps({'step':label,'log':str(log.relative_to(ROOT))}),flush=True)
    with log.open('w',encoding='utf-8') as stream:
        result=subprocess.run(list(map(str,args)),cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
    if result.returncode:raise RuntimeError(f'{label} failed exit {result.returncode}; see {log}')
    return {'step':label,'exit_code':result.returncode,'elapsed_s':time.time()-started,'log':str(log.relative_to(ROOT))}

def main():
    p=argparse.ArgumentParser();p.add_argument('--decode-only',action='store_true');a=p.parse_args()
    OUT.mkdir(parents=True,exist_ok=True);CACHE.mkdir(parents=True,exist_ok=True)
    temp=ROOT/'.tmp/phase2/java';temp.mkdir(parents=True,exist_ok=True)
    java=shutil.which('java');javac=shutil.which('javac');keytool=shutil.which('keytool')
    if not all([java,javac,keytool]):raise RuntimeError('An existing JDK is required; no system installation is performed')
    assert digest(APKTOOL)==EXPECTED_APKTOOL,'Apktool source fingerprint mismatch'
    assert digest(PROBE)==EXPECTED_PROBE,'Probe source fingerprint mismatch'
    matches=json.loads((OUT/'base_asset_comparison.json').read_text(encoding='utf-8'))
    assert matches['matches']==matches['total']==159,'Publisher APK component audit incomplete'
    with zipfile.ZipFile(APK) as source:
        assert hashlib.sha256(source.read('lib/arm64-v8a/libg.so')).hexdigest()==EXPECTED_LIBG,'Unsupported core libg'
    report={'input_apk_sha256':digest(APK),'upstream_whole_apk_hash_matches':False,
        'variant':'publisher APK container differs; exact core library and159 pinned base components match',
        'libg_sha256':EXPECTED_LIBG,'probe_sha256':EXPECTED_PROBE,'apktool_sha256':EXPECTED_APKTOOL,
        'apksig_version':'8.8.2','apksig_sha256':digest(APKSIG),'java':java,
        'runtime_content':'Not embedded or downloaded by this build; version must be separately attested',
        'steps':[],'runtime_executed':False}
    java_args=[java,f'-Djava.io.tmpdir={temp}','-Dfile.encoding=UTF-8']
    decoded=CACHE/'decoded';framework=CACHE/'framework'
    if not (decoded/'apktool.yml').exists():
        report['steps'].append(command([*java_args,'-jar',APKTOOL,'d','-p',framework,APK,'-o',decoded],'decode'))
    if a.decode_only:
        (OUT/'apk_build_state.json').write_text(json.dumps(report,indent=2),encoding='utf-8');return
    spec=importlib.util.spec_from_file_location('firstlight_offline_build',FIRST/'native_runner/offline_build.py')
    builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)
    game=decoded/'smali/com/supercell/clashroyale/GameApp.smali'
    if '"crprobe"' not in game.read_text(encoding='utf-8'):builder.patch(decoded)
    destination=decoded/'lib/arm64-v8a/libcrprobe.so';destination.parent.mkdir(parents=True,exist_ok=True)
    shutil.copyfile(PROBE,destination)
    manifest=decoded/'AndroidManifest.xml'
    text=manifest.read_text(encoding='utf-8')
    # Let Android extract ARM libs, avoiding page-alignment assumptions in a
    # custom local signer/ZIP. This changes packaging, not native game bytes.
    import re
    if 'android:extractNativeLibs=' in text:
        text=re.sub(r'android:extractNativeLibs="[^"]+"','android:extractNativeLibs="true"',text)
    else:text=text.replace('<application ','<application android:extractNativeLibs="true" ',1)
    manifest.write_text(text,encoding='utf-8')
    unsigned=CACHE/'native-smoke-unsigned.apk'
    with zipfile.ZipFile(APKTOOL) as jar:
        (APKTOOL.parent/'aapt2_64.exe').write_bytes(jar.read('prebuilt/windows/aapt2_64.exe'))
    wrapper=APKTOOL.parent/'aapt_wrapper.exe'
    if not wrapper.exists():
        report['steps'].append(command(['powershell','-NoProfile','-ExecutionPolicy','Bypass','-File',
            ROOT/'tools/phase2_native_build_aapt_wrapper.ps1'],'compile_aapt_wrapper'))
    report['steps'].append(command([*java_args,'-jar',APKTOOL,'b','-a',wrapper,'-p',framework,decoded,'-o',unsigned],'rebuild'))
    classes=ROOT/'.cache/phase2/native_signer';classes.mkdir(parents=True,exist_ok=True)
    report['steps'].append(command([javac,'-encoding','UTF-8',f'-J-Djava.io.tmpdir={temp}',
        '-cp',APKSIG,'-d',classes,ROOT/'tools/phase2_native_src/Phase2Sign.java'],'compile_signer'))
    signing=ROOT/'.cache/phase2/native_signing';signing.mkdir(parents=True,exist_ok=True)
    keystore=signing/'local-debug.p12'
    # Generated local debug material is not an account credential and must
    # remain excluded from public share bundles, together with the APK.
    password=__import__('os').environ['ROYALE_LOCAL_SIGNING_PASSWORD']
    if not keystore.exists():
        report['steps'].append(command([keytool,f'-J-Djava.io.tmpdir={temp}','-genkeypair','-noprompt',
            '-keystore',keystore,'-storetype','PKCS12','-alias','phase2','-storepass',password,
            '-keypass',password,'-keyalg','RSA','-keysize','2048','-validity','3650',
            '-dname','CN=Local Phase2 Research,O=Local Offline Research'],'create_debug_key'))
    final=OUT/'offline-native-smoke.apk'
    classpath=str(classes)+';'+str(APKSIG)
    report['steps'].append(command([*java_args,'-cp',classpath,'Phase2Sign',unsigned,final,keystore,'phase2',password],'sign_verify'))
    with zipfile.ZipFile(final) as result:
        assert hashlib.sha256(result.read('lib/arm64-v8a/libg.so')).hexdigest()==EXPECTED_LIBG
        assert hashlib.sha256(result.read('lib/arm64-v8a/libcrprobe.so')).hexdigest()==EXPECTED_PROBE
        report['zip_crc_error']=result.testzip()
    assert report['zip_crc_error'] is None
    report.update(output_apk=str(final.relative_to(ROOT)),output_sha256=digest(final),output_bytes=final.stat().st_size)
    (OUT/'apk_build_state.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:report[k] for k in ['output_apk','output_sha256','output_bytes','runtime_executed']}),flush=True)

if __name__=='__main__':main()

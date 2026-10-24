"""Build a separately identified offline APK candidate on Linux, without Android.

All tools and generated debug material stay inside this repository. A static
build receipt never certifies an Android process or a playable native match.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import subprocess
import sys
import tarfile
import time
import zipfile

if __package__ in (None, ''):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from tools.cloud.native_resources import ROOT, digest, fetch_resource, local, write_report

TOOLS = [
    {'name': 'jdk17', 'url': 'https://github.com/adoptium/temurin17-binaries/releases/download/jdk-17.0.20.1%2B1/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz',
     'path': '.cache/native-tools/downloads/OpenJDK17U-jdk_x64_linux_hotspot_17.0.20.1_1.tar.gz',
     'bytes': 193252603, 'sha256': '3808d1d15e3ec6bd5b84057fb5d84c33d8a1536a258146bcea2e603fc726e08e'},
    {'name': 'apktool', 'url': 'https://github.com/iBotPeaches/Apktool/releases/download/v2.12.1/apktool_2.12.1.jar',
     'path': '.cache/native-tools/downloads/apktool_2.12.1.jar', 'bytes': 25926183,
     'sha256': '66cf4524a4a45a7f56567d08b2c9b6ec237bcdd78cee69fd4a59c8a0243aeafa'},
    {'name': 'apksig', 'url': 'https://dl.google.com/dl/android/maven2/com/android/tools/build/apksig/8.8.2/apksig-8.8.2.jar',
     'path': '.cache/native-tools/downloads/apksig-8.8.2.jar', 'bytes': 506969,
     'sha256': 'c070ed1394629d74641aa0906f60b2ffa1ee77e6366a1f93437f59717b1aeb89'},
]
FIRSTLIGHT = 'third_party/phase2/firstlight'
DEFAULT_INPUT = FIRSTLIGHT + '/user-provided/nulls-royale.apk.partial'
PROBE = 'third_party/phase2/jason/prebuilt/libcrprobe.so'
MANIFEST = 'data/phase2/version_manifest.json'
JDK = '.cache/native-tools/jdk17/jdk-17.0.20.1+1'
LIBG_SHA256 = '110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
PROBE_SHA256 = 'd526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190'


def artifact(path):
    path = local(path)
    return {'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size, 'sha256': digest(path)}


def code_proof_archive(apk, directory):
    """Copy exact built DEX/manifest/resources to a small decoding input."""
    target = local(directory) / 'built-code-proof.apk'
    with zipfile.ZipFile(local(apk)) as original, zipfile.ZipFile(target, 'x', zipfile.ZIP_DEFLATED) as proof:
        for name in ('AndroidManifest.xml', 'resources.arsc', 'classes.dex'):
            proof.writestr(name, original.read(name))
    return target


def verify_code_proof(apk, proof, dex):
    with zipfile.ZipFile(local(apk)) as archive:
        signed_hash = hashlib.sha256(archive.read('classes.dex')).hexdigest()
    with zipfile.ZipFile(local(proof)) as archive:
        proof_hash = hashlib.sha256(archive.read('classes.dex')).hexdigest()
    extracted_hash = digest(local(dex))
    return {'signed_apk_classes_sha256': signed_hash, 'proof_classes_sha256': proof_hash,
            'extracted_dex_sha256': extracted_hash,
            'all_three_dex_hashes_exact': signed_hash == proof_hash == extracted_hash}


def check_input_components(apk, manifest_path=MANIFEST):
    """Check the APK's pinned bytes directly, not a potentially stale report."""
    manifest = json.loads(local(manifest_path).read_text(encoding='utf-8'))
    prefix = FIRSTLIGHT + '/user-provided/base-assets/'
    entries = []
    fallbacks = {row['verified_cached_copy']: row['actual_runtime_source'].split('original_apk/', 1)[1]
                 for row in manifest.get('unchanged_source_fallbacks', [])}
    with zipfile.ZipFile(local(apk)) as archive:
        for row in manifest['assets']:
            if row['path'].startswith(prefix):
                name = row['path'][len(prefix):]
            elif row['role'] == 'native_library':
                name = 'lib/arm64-v8a/libg.so'
            elif row['path'] in fallbacks:
                name = fallbacks[row['path']]
            else:
                continue
            info = archive.getinfo(name)
            if info.file_size != row['bytes']:
                raise ValueError('Input component size mismatch: ' + name)
            actual = hashlib.sha256(archive.read(name)).hexdigest()
            if actual != row['sha256']:
                raise ValueError('Input component SHA256 mismatch: ' + name)
            entries.append({'entry': name, 'sha256': actual, 'bytes': info.file_size})
    if len(entries) < 160:
        raise ValueError('Incomplete fixed-version input component contract')
    return entries


def verify_patch_tree(source):
    """Inspect actual smali control flow at the patched offline loader gate."""
    source = local(source)
    game = source / 'com/supercell/clashroyale/GameApp.smali'
    titan = source / 'com/supercell/titan/GameApp.smali'
    text = game.read_text(encoding='utf-8')
    method = re.search(r'\.method public final a\(\)Z\n(.*?)\.end method', text, re.S)
    if not method:
        raise ValueError('Offline loader gate method missing')
    executable = [line.strip() for line in method[1].splitlines()
                  if line.strip() and not line.strip().startswith(('.', '#', ':'))]
    expected = ['const-string v0, "crprobe"',
                'invoke-static {v0}, Ljava/lang/System;->loadLibrary(Ljava/lang/String;)V',
                'const/4 v0, 0x0', 'return v0']
    if executable[:4] != expected:
        raise ValueError('Offline loader gate must load probe and immediately return false')
    text = titan.read_text(encoding='utf-8')
    ordered = ['const-string v3, "scid_sdk"', 'const-string v3, "g"',
               'const-string v3, "crprobe"',
               'invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V']
    positions = [text.find(token) for token in ordered]
    checks = {'offline_gate': True,
              'post_libg_bootstrap': 'invoke-static {}, Lcom/supercell/titan/GameApp;->nativeBootstrapProbe()V' in text,
              'post_libg_bootstrap_order': all(position >= 0 for position in positions) and positions == sorted(positions),
              'bootstrap_native_declaration': '.method private static native nativeBootstrapProbe()V' in text,
              'probe_library_load': 'const-string v3, "crprobe"' in text,
              'scid_library_load': 'const-string v3, "scid_sdk"' in text}
    if not all(checks.values()):
        raise ValueError('Titan post-libg bootstrap patch is incomplete')
    return checks


def prepare_tools(max_bytes):
    if sum(tool['bytes'] for tool in TOOLS) > max_bytes:
        raise ValueError('Pinned tool set exceeds explicit download budget')
    records = []
    for spec in TOOLS:
        record = fetch_resource(spec, max_bytes, progress=lambda value: print(json.dumps(value), flush=True))
        records.append(record)
        if not record['success']:
            raise ValueError('Tool acquisition failed: ' + record['error'])
    jdk = local(JDK)
    if not (jdk / 'bin/java').is_file():
        target = jdk.parent
        target.mkdir(parents=True, exist_ok=True)
        with tarfile.open(local(TOOLS[0]['path'])) as archive:
            archive.extractall(target, filter='data')
    return records


SIGNER = r'''import com.android.apksig.ApkSigner;
import com.android.apksig.ApkVerifier;
import java.io.File;
import java.io.FileInputStream;
import java.security.KeyStore;
import java.security.PrivateKey;
import java.security.cert.X509Certificate;
import java.util.ArrayList;
import java.util.List;
public class CloudNativeSign {
    public static void main(String[] args) throws Exception {
        if (args.length != 3) throw new IllegalArgumentException("input output generated-debug-keystore");
        char[] password = System.getenv("ROYALE_BUILD_SIGNING_PASSWORD").toCharArray();
        KeyStore store = KeyStore.getInstance("PKCS12");
        try (FileInputStream input = new FileInputStream(args[2])) { store.load(input, password); }
        PrivateKey key = (PrivateKey) store.getKey("cloud-debug", password);
        List<X509Certificate> certificates = new ArrayList<>();
        for (java.security.cert.Certificate c : store.getCertificateChain("cloud-debug")) certificates.add((X509Certificate)c);
        ApkSigner.SignerConfig signer = new ApkSigner.SignerConfig.Builder("cloud-local-debug", key, certificates).build();
        new ApkSigner.Builder(List.of(signer)).setInputApk(new File(args[0])).setOutputApk(new File(args[1]))
            .setMinSdkVersion(24).setV1SigningEnabled(true).setV2SigningEnabled(true).setV3SigningEnabled(true)
            .setCreatedBy("Royale cloud native candidate; no runtime verification").build().sign();
        ApkVerifier.Result result = new ApkVerifier.Builder(new File(args[1])).setMinCheckedPlatformVersion(24).build().verify();
        ApkVerifier.Result v1 = new ApkVerifier.Builder(new File(args[1])).setMinCheckedPlatformVersion(23).setMaxCheckedPlatformVersion(23).build().verify();
        System.out.println("verified=" + result.isVerified());
        System.out.println("v1=" + (v1.isVerified() && v1.isVerifiedUsingV1Scheme()));
        System.out.println("v2=" + result.isVerifiedUsingV2Scheme());
        System.out.println("v3=" + result.isVerifiedUsingV3Scheme());
        System.out.println("errors=" + result.getErrors());
        if (!result.isVerified() || !result.isVerifiedUsingV2Scheme()
            || !result.isVerifiedUsingV3Scheme()) throw new IllegalStateException("APK signature verification failed");
    }
}
'''


def build(output, input_apk=DEFAULT_INPUT, tool_budget=300 * 1024**2):
    output = local(output)
    if output.exists():
        raise ValueError('Use a new build output directory; existing evidence is preserved')
    output.mkdir(parents=True)
    report = {'schema': 'cloud-native-apk-build.v1', 'created_utc': datetime.now(timezone.utc).isoformat(),
              'success': False, 'checks': {}, 'commands': [], 'native_execution_verified': False,
              'rpc_calls': 0, 'sdk_license_accepted': False, 'old_manifest_modified': False,
              'resources': 'separately prepared existing-device-update; not embedded',
              'output': str(output.relative_to(ROOT))}
    snapshot = output / 'build-source.py'
    snapshot.write_bytes(Path(__file__).read_bytes())
    report['build_source_snapshot'] = artifact(snapshot)
    report['supported_android'] = 'Android12/current profile; runtime not verified'
    manifest_before = digest(local(MANIFEST))
    env = os.environ.copy()
    temporary = output / 'tmp'
    temporary.mkdir()
    env.update(TMPDIR=str(temporary), XDG_CACHE_HOME=str(local('.cache/native-tools/xdg')),
               ANDROID_USER_HOME=str(local('.cache/native-tools/android')))
    def save():
        write_report(output / 'report.json', report)
    def command(label, args, timeout=900):
        log = output / (label + '.log')
        record = {'label': label, 'command': list(map(str, args)), 'log': str(log.relative_to(ROOT))}
        report['commands'].append(record)
        started = time.monotonic()
        print(json.dumps({'step': label, 'log': str(log.relative_to(ROOT))}), flush=True)
        try:
            with log.open('w', encoding='utf-8') as stream:
                result = subprocess.run(list(map(str, args)), cwd=ROOT, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            record['returncode'] = result.returncode
            if result.returncode:
                raise ValueError(label + ' failed; see ' + str(log.relative_to(ROOT)))
        finally:
            record['elapsed_seconds'] = time.monotonic() - started
            if log.exists():
                record['log_sha256'] = digest(log)
            save()
        return log.read_text(encoding='utf-8')
    try:
        report['input_apk'] = artifact(input_apk)
        manifest = json.loads(local(MANIFEST).read_text(encoding='utf-8'))
        report['input_whole_identity_matches'] = any(
            row['role'] == 'original_apk' and row['sha256'] == report['input_apk']['sha256']
            for row in manifest['assets'])
        components = check_input_components(input_apk)
        write_report(output / 'input-components.json', components)
        report['checks']['input_components'] = True
        report['input_component_count'] = len(components)
        if digest(local(PROBE)) != PROBE_SHA256:
            raise ValueError('Pinned probe SHA256 mismatch')
        report['tools'] = prepare_tools(tool_budget)
        java = local(JDK + '/bin/java')
        javac = local(JDK + '/bin/javac')
        keytool = local(JDK + '/bin/keytool')
        java_args = [java, '-Xmx1536m', '-Djava.io.tmpdir=' + str(temporary), '-Dfile.encoding=UTF-8']
        apktool = local(TOOLS[1]['path'])
        apksig = local(TOOLS[2]['path'])
        command('java-version', [java, '-version'], 15)
        framework = output / 'framework'
        decoded = output / 'decoded'
        command('decode', [*java_args, '-jar', apktool, 'd', '-j', '2', '-p', framework,
                           local(input_apk), '-o', decoded])
        patch_path = local(FIRSTLIGHT + '/native_runner/offline_build.py')
        spec = importlib.util.spec_from_file_location('cloud_pinned_offline_build', patch_path)
        upstream = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(upstream)
        patch_files = [decoded / 'smali/com/supercell/clashroyale/GameApp.smali',
                       decoded / 'smali/com/supercell/titan/GameApp.smali']
        report['patch_source'] = artifact(patch_path)
        report['smali_before'] = [artifact(path) for path in patch_files]
        upstream.patch(decoded)
        report['patch_before_build_checks'] = verify_patch_tree(decoded / 'smali')
        report['smali_after'] = [artifact(path) for path in patch_files]
        target = decoded / 'lib/arm64-v8a/libcrprobe.so'
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(local(PROBE), target)
        manifest_path = decoded / 'AndroidManifest.xml'
        text = manifest_path.read_text(encoding='utf-8')
        if 'android:extractNativeLibs=' in text:
            text = re.sub(r'android:extractNativeLibs="[^"]+"', 'android:extractNativeLibs="true"', text)
        else:
            text = text.replace('<application ', '<application android:extractNativeLibs="true" ', 1)
        manifest_path.write_text(text, encoding='utf-8')
        aapt = output / 'aapt2-linux-x64'
        with zipfile.ZipFile(apktool) as archive:
            aapt.write_bytes(archive.read('prebuilt/linux/aapt2_64'))
        aapt.chmod(0o755)
        report['aapt2'] = artifact(aapt)
        unsigned = output / 'unsigned.apk'
        command('rebuild', [*java_args, '-jar', apktool, 'b', '-j', '2', '-a', aapt,
                            '-p', framework, decoded, '-o', unsigned])
        signer_source = output / 'CloudNativeSign.java'
        signer_source.write_text(SIGNER, encoding='utf-8')
        classes = output / 'signer-classes'
        classes.mkdir()
        command('compile-signer', [javac, '-encoding', 'UTF-8', '-cp', apksig, '-d', classes, signer_source], 60)
        signing = output / 'generated-debug-signing'
        signing.mkdir(mode=0o700)
        keystore = signing / 'debug.p12'
        env['ROYALE_BUILD_SIGNING_PASSWORD'] = secrets.token_urlsafe(24)
        command('create-debug-key', [keytool, '-genkeypair', '-noprompt', '-keystore', keystore,
                '-storetype', 'PKCS12', '-alias', 'cloud-debug', '-storepass:env', 'ROYALE_BUILD_SIGNING_PASSWORD',
                '-keypass:env', 'ROYALE_BUILD_SIGNING_PASSWORD', '-keyalg', 'RSA', '-keysize', '2048',
                '-validity', '3650', '-dname', 'CN=Cloud Offline Debug,O=Local Research'], 60)
        final = output / 'offline-native-candidate.apk'
        signature = command('sign-and-verify', [*java_args, '-cp', str(classes) + os.pathsep + str(apksig),
                                              'CloudNativeSign', unsigned, final, keystore], 180)
        for key, token in [('signature', 'verified=true'), ('signature_v1', 'v1=true'),
                           ('signature_v2', 'v2=true'), ('signature_v3', 'v3=true')]:
            report['checks'][key] = token in signature.splitlines()
        with zipfile.ZipFile(final) as archive:
            report['checks']['zip_crc'] = archive.testzip() is None
            report['checks']['libg'] = hashlib.sha256(archive.read('lib/arm64-v8a/libg.so')).hexdigest() == LIBG_SHA256
            report['checks']['probe'] = hashlib.sha256(archive.read('lib/arm64-v8a/libcrprobe.so')).hexdigest() == PROBE_SHA256
            report['checks']['update_not_embedded'] = not any(name.startswith('assets/firstlight/') for name in archive.namelist())
            main_dex = output / 'built-classes.dex'
            main_dex.write_bytes(archive.read('classes.dex'))
        verify_smali = output / 'built-smali'
        proof = code_proof_archive(final, output)
        report['built_code_proof'] = artifact(proof)
        report['built_classes_dex'] = artifact(main_dex)
        report['built_dex_evidence'] = verify_code_proof(final, proof, main_dex)
        report['checks']['built_dex_input_exact'] = report['built_dex_evidence']['all_three_dex_hashes_exact']
        command('verify-built-offline-patch', [*java_args, '-jar', apktool, 'd', '-r', '--no-assets',
                '-j', '2', '-p', framework, proof, '-o', verify_smali], 180)
        report['built_patch_checks'] = verify_patch_tree(verify_smali / 'smali')
        report['checks']['patch'] = all(report['built_patch_checks'].values())
        xml = command('verify-built-manifest', [aapt, 'dump', 'xmltree', final, '--file', 'AndroidManifest.xml'], 30)
        report['checks']['manifest_native_extraction'] = bool(re.search(r'extractNativeLibs[^\n]*(?:0xffffffff|true)', xml))
        report['output_apk'] = artifact(final)
        report['checks']['old_manifest_unchanged'] = digest(local(MANIFEST)) == manifest_before
        report['required_checks'] = [key for key in report['checks'] if key != 'signature_v1']
        report['signature_v1_scope'] = 'Separate cryptographic API23 check only, not an Android23 runtime compatibility claim'
        report['success'] = all(report['checks'][key] for key in report['required_checks'])
        if not report['success']:
            raise ValueError('One or more static APK checks failed')
        report['build_scope'] = 'New component-verified publisher variant; not old locked APK, not Android runtime acceptance'
        write_report(final.with_suffix('.build.json'), report)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
        print(json.dumps({'build_failed': report['error'], 'output': report['output']}), flush=True)
    finally:
        env.pop('ROYALE_BUILD_SIGNING_PASSWORD', None)
        save()
    return report


VERIFIER = r'''import com.android.apksig.ApkVerifier;
import java.io.File;
public class CloudNativeVerify {
    public static void main(String[] args) throws Exception {
        ApkVerifier.Result r = new ApkVerifier.Builder(new File(args[0])).setMinCheckedPlatformVersion(24).build().verify();
        ApkVerifier.Result v1 = new ApkVerifier.Builder(new File(args[0])).setMinCheckedPlatformVersion(23).setMaxCheckedPlatformVersion(23).build().verify();
        System.out.println("verified=" + r.isVerified());
        System.out.println("v1=" + (v1.isVerified() && v1.isVerifiedUsingV1Scheme()));
        System.out.println("v2=" + r.isVerifiedUsingV2Scheme());
        System.out.println("v3=" + r.isVerifiedUsingV3Scheme());
        System.out.println("errors=" + r.getErrors());
        System.out.println("v1_errors=" + v1.getErrors());
        if (!r.isVerified() || !r.isVerifiedUsingV2Scheme() || !r.isVerifiedUsingV3Scheme())
            throw new IllegalStateException("Android12 profile signature validation failed");
    }
}
'''


def verify_existing(output):
    """Freshly verify the signed bytes, preserving previous failed evidence."""
    output = local(output)
    previous_path = output / 'report.json'
    previous = json.loads(previous_path.read_text(encoding='utf-8'))
    target = output / 'offline-native-candidate.apk'
    receipt_path = target.with_suffix('.build.json')
    work = output / ('verification-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    work.mkdir()
    if receipt_path.exists():
        receipt_path = work / 'offline-native-candidate.build.json'
    snapshot = work / 'verification-source.py'
    snapshot.write_bytes(Path(__file__).read_bytes())
    report = {'schema': 'cloud-native-apk-build.v1', 'success': False,
              'created_utc': datetime.now(timezone.utc).isoformat(),
              'prior_build_report': artifact(previous_path), 'verification_source_snapshot': artifact(snapshot),
              'input_apk': artifact(previous['input_apk']['path']), 'output_apk': artifact(target),
              'input_whole_identity_matches': previous['input_whole_identity_matches'],
              'supported_android': 'Android12/current profile; runtime not verified',
              'native_execution_verified': False, 'rpc_calls': 0, 'checks': {}, 'commands': [],
              'sdk_license_accepted': False, 'old_manifest_modified': False,
              'resources': 'separately prepared existing-device-update; not embedded',
              'original_build_result': 'Original report preserved; this command independently verifies actual signed APK bytes',
              'verification_output': str(work.relative_to(ROOT)),
              'build_receipt': str(receipt_path.relative_to(ROOT))}
    manifest_before = digest(local(MANIFEST))
    env = os.environ.copy()
    temporary = work / 'tmp'
    temporary.mkdir()
    env.update(TMPDIR=str(temporary), ANDROID_USER_HOME=str(local('.cache/native-tools/android')))
    def save():
        write_report(work / 'report.json', report)
    def command(label, args, timeout=180):
        log = work / (label + '.log')
        item = {'label': label, 'command': list(map(str, args)), 'log': str(log.relative_to(ROOT))}
        report['commands'].append(item)
        print(json.dumps({'step': label, 'log': item['log']}), flush=True)
        started = time.monotonic()
        try:
            with log.open('w', encoding='utf-8') as stream:
                result = subprocess.run(list(map(str, args)), cwd=ROOT, env=env, stdout=stream,
                                        stderr=subprocess.STDOUT, timeout=timeout)
            item['returncode'] = result.returncode
            if result.returncode:
                raise ValueError(label + ' failed; see ' + item['log'])
        finally:
            item['elapsed_seconds'] = time.monotonic() - started
            if log.exists():
                item['log_sha256'] = digest(log)
            save()
        return log.read_text(encoding='utf-8')
    try:
        if report['input_apk'] != previous['input_apk']:
            raise ValueError('Input APK changed since original build')
        report['input_component_count'] = len(check_input_components(report['input_apk']['path']))
        report['checks']['input_components'] = True
        report['tools'] = prepare_tools(300 * 1024**2)
        java = local(JDK + '/bin/java')
        javac = local(JDK + '/bin/javac')
        apktool = local(TOOLS[1]['path'])
        apksig = local(TOOLS[2]['path'])
        java_args = [java, '-Xmx1536m', '-Djava.io.tmpdir=' + str(temporary), '-Dfile.encoding=UTF-8']
        source = work / 'CloudNativeVerify.java'
        source.write_text(VERIFIER, encoding='utf-8')
        classes = work / 'verifier-classes'
        classes.mkdir()
        command('compile-independent-verifier', [javac, '-cp', apksig, '-d', classes, source], 60)
        signature = command('verify-signatures', [*java_args, '-cp', str(classes) + os.pathsep + str(apksig),
                                                'CloudNativeVerify', target])
        for key, token in [('signature', 'verified=true'), ('signature_v1', 'v1=true'),
                           ('signature_v2', 'v2=true'), ('signature_v3', 'v3=true')]:
            report['checks'][key] = token in signature.splitlines()
        report['signature_v1_scope'] = 'Separate cryptographic API23 check only; runtime support remains Android12 profile'
        with zipfile.ZipFile(target) as archive:
            report['checks']['zip_crc'] = archive.testzip() is None
            report['checks']['libg'] = hashlib.sha256(archive.read('lib/arm64-v8a/libg.so')).hexdigest() == LIBG_SHA256
            report['checks']['probe'] = hashlib.sha256(archive.read('lib/arm64-v8a/libcrprobe.so')).hexdigest() == PROBE_SHA256
            report['checks']['update_not_embedded'] = not any(name.startswith('assets/firstlight/') for name in archive.namelist())
            dex = work / 'built-classes.dex'
            dex.write_bytes(archive.read('classes.dex'))
        smali = work / 'built-smali'
        proof = code_proof_archive(target, work)
        report['built_code_proof'] = artifact(proof)
        report['built_classes_dex'] = artifact(dex)
        report['built_dex_evidence'] = verify_code_proof(target, proof, dex)
        report['checks']['built_dex_input_exact'] = report['built_dex_evidence']['all_three_dex_hashes_exact']
        command('verify-built-offline-patch', [*java_args, '-jar', apktool, 'd', '-r', '--no-assets',
                '-j', '2', '-p', work / 'framework', proof, '-o', smali])
        report['built_patch_checks'] = verify_patch_tree(smali / 'smali')
        report['checks']['patch'] = all(report['built_patch_checks'].values())
        aapt = work / 'aapt2-linux-x64'
        with zipfile.ZipFile(apktool) as archive:
            aapt.write_bytes(archive.read('prebuilt/linux/aapt2_64'))
        aapt.chmod(0o755)
        report['aapt2'] = artifact(aapt)
        xml = command('verify-built-manifest', [aapt, 'dump', 'xmltree', target, '--file', 'AndroidManifest.xml'], 30)
        report['checks']['manifest_native_extraction'] = bool(re.search(r'extractNativeLibs[^\n]*(?:0xffffffff|true)', xml))
        report['checks']['output_unchanged_during_verification'] = artifact(target) == report['output_apk']
        report['checks']['old_manifest_unchanged'] = digest(local(MANIFEST)) == manifest_before
        report['required_checks'] = [key for key in report['checks'] if key != 'signature_v1']
        report['success'] = all(report['checks'][key] for key in report['required_checks'])
        if not report['success']:
            raise ValueError('One or more actual signed APK static checks failed')
        report['build_scope'] = 'New component-verified publisher variant; not old locked APK, not Android runtime acceptance'
        write_report(receipt_path, report)
    except Exception as error:
        report['error'] = type(error).__name__ + ': ' + str(error)
    finally:
        save()
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input-apk', default=DEFAULT_INPUT)
    parser.add_argument('--output', default='outputs/native_loop_20261020/apk_build/run-' + datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ'))
    parser.add_argument('--tool-budget-mib', type=int, default=300)
    parser.add_argument('--verify-existing', metavar='BUILD_DIRECTORY',
                        help='Independently verify existing signed APK; preserve original failed report')
    args = parser.parse_args(argv)
    report = verify_existing(args.verify_existing) if args.verify_existing else build(
        args.output, args.input_apk, args.tool_budget_mib * 1024**2)
    print(json.dumps({key: value for key, value in report.items() if key in
                     ('schema', 'success', 'checks', 'input_apk', 'output_apk', 'error', 'native_execution_verified')}, indent=2))
    return 0 if report['success'] else 1


if __name__ == '__main__':
    raise SystemExit(main())

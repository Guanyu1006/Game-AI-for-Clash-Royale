"""Alternative javac build for audited crforge source; leaves upstream untouched."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'third_party/audit_candidates/crforge_git_sparse'
OUT = ROOT / 'outputs/audit_candidates/crforge_build'
JARS = ROOT / '.cache/audit/maven'
ARTIFACTS = [
 ('org.projectlombok', 'lombok', '1.18.46'),
 ('com.fasterxml.jackson.core', 'jackson-databind', '2.22.2'),
 ('com.fasterxml.jackson.core', 'jackson-core', '2.22.2'),
 ('com.fasterxml.jackson.core', 'jackson-annotations', '2.22'),
 ('org.slf4j', 'slf4j-api', '2.0.18'),
 ('org.slf4j', 'slf4j-simple', '2.0.18'),
 ('org.zeromq', 'jeromq', '0.6.0'),
 ('org.junit.platform', 'junit-platform-console-standalone', '6.1.3'),
 ('org.assertj', 'assertj-core', '3.27.7'),
 ('org.mockito', 'mockito-core', '5.23.0'),
 ('net.bytebuddy', 'byte-buddy', '1.17.8'),
 ('net.bytebuddy', 'byte-buddy-agent', '1.17.8'),
 ('org.objenesis', 'objenesis', '3.3'),
]

def download(item):
    group, artifact, version = item
    path = JARS / (artifact + '-' + version + '.jar')
    url = f'https://repo.maven.apache.org/maven2/{group.replace(".", "/")}/{artifact}/{version}/{path.name}'
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    if not path.exists() or path.stat().st_size < 1000:
        with opener.open(url, timeout=30) as res, path.open('wb') as out:
            shutil.copyfileobj(res, out)
    return {'url': url, 'path': str(path.relative_to(ROOT)), 'bytes': path.stat().st_size,
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}

def run(command, name, timeout=180):
    start = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=timeout)
    (OUT / (name + '.log')).write_bytes(result.stdout + b'\n' + result.stderr)
    print(name, result.returncode, round(time.perf_counter()-start,2), flush=True)
    if result.returncode:
        raise RuntimeError(name + ' failed; see log')
    return result

if __name__ == '__main__':
    OUT.mkdir(parents=True, exist_ok=True)
    JARS.mkdir(parents=True, exist_ok=True)
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        rows = list(pool.map(download, ARTIFACTS))
    (OUT / 'dependency_manifest.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')
    classes = OUT / 'main_classes'
    classes.mkdir(exist_ok=True)
    test_classes = OUT / 'test_classes'
    test_classes.mkdir(exist_ok=True)
    java_home = Path(os.environ.get('JAVA_HOME', r'C:\Users\63047\AppData\Local\Programs\Microsoft\jdk-21.0.7.6-hotspot'))
    cp = ';'.join(str(JARS / Path(row['path']).name) for row in rows)
    sources = []
    for module in ['core', 'data', 'gym-bridge']:
        sources += list((SOURCE / module / 'src/main/java').rglob('*.java'))
    # Preserve source while adapting only the Windows path-to-binary-name scanner.
    ledger = SOURCE / 'core/src/main/java/org/crforge/core/fidelity/FidelityLedger.java'
    patched = OUT / 'patches/org/crforge/core/fidelity/FidelityLedger.java'
    patched.parent.mkdir(parents=True, exist_ok=True)
    original_text = ledger.read_text(encoding='utf-8')
    patched_text = original_text.replace(".replace('/', '.')", ".replace('/', '.').replace('\\\\', '.')")
    patched.write_text(patched_text, encoding='utf-8')
    sources = [p for p in sources if p != ledger] + [patched]
    (OUT / 'windows_compatibility_patch.txt').write_text(
        "FidelityLedger.toBinaryName: add .replace('\\\\', '.') after slash replacement.\n"
        "Original test run: 971/974 passed, scanner tests fail on Windows paths.\n"
        "No gameplay rules changed; upstream source remains pristine.\n", encoding='utf-8')
    args = OUT / 'sources.txt'
    args.write_text('\n'.join('"' + str(p).replace('\\', '/') + '"' for p in sources), encoding='utf-8')
    run([str(java_home / 'bin/javac.exe'), '-encoding', 'UTF-8', '--release', '17', '-cp', cp,
         '-processorpath', str(JARS / 'lombok-1.18.46.jar'), '-d', str(classes), '@'+str(args)], 'javac_main')
    shutil.copytree(SOURCE / 'data/src/main/resources', classes, dirs_exist_ok=True)
    tests = []
    for module in ['core', 'data', 'gym-bridge']:
        tests += list((SOURCE / module / 'src/test/java').rglob('*.java'))
        resources = SOURCE / module / 'src/test/resources'
        if resources.exists():
            shutil.copytree(resources, test_classes, dirs_exist_ok=True)
    args.write_text('\n'.join('"' + str(p).replace('\\', '/') + '"' for p in tests), encoding='utf-8')
    cp = str(classes) + ';' + cp
    run([str(java_home / 'bin/javac.exe'), '-encoding', 'UTF-8', '--release', '17', '-cp', cp,
         '-processorpath', str(JARS / 'lombok-1.18.46.jar'), '-d', str(test_classes), '@'+str(args)], 'javac_tests')
    run([str(java_home / 'bin/java.exe'), '-Duser.home='+str(ROOT / '.tmp'), '-cp', str(test_classes)+';'+cp,
         'org.junit.platform.console.ConsoleLauncher', 'execute', '--scan-classpath',
         '--disable-banner', '--details=summary', '--reports-dir='+str(OUT / 'junit_reports')], 'junit', timeout=300)
    run([str(java_home / 'bin/java.exe'), '-cp', cp,
         'org.crforge.core.fidelity.FidelityLedger', '--all'], 'fidelity')
    (OUT / 'classpath.txt').write_text(cp, encoding='utf-8')
    (OUT / 'build_summary.json').write_text(json.dumps({'java':str(java_home), 'source':str(SOURCE.relative_to(ROOT)),
          'main_java_files':len(sources), 'test_java_files':len(tests),
          'method':'manual javac --release 17, upstream dependency versions, isolated FidelityLedger Windows path patch; no upstream edits',
          'status':'compiled_and_tests_passed'}, indent=2), encoding='utf-8')

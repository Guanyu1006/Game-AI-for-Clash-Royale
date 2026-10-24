"""只读取指定 Android 设备，核对显式版本后生成实际进程身份回执。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import shutil
import socket
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from royale_phase2.versioning import verify_manifest

CONTENT_PREFIX = 'third_party/phase2/runtime-assets/full-update/'
PACKAGE = re.compile(r'[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+\Z')
SERIAL = re.compile(r'[A-Za-z0-9][A-Za-z0-9._:-]{0,159}\Z')
ANDROID_PATH = re.compile(r'/data/[A-Za-z0-9_./~+=@%-]+\Z')


def digest(data):
    return hashlib.sha256(data).hexdigest()


def reference(path):
    path = local(path)
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': digest(path.read_bytes())}


def write(path, data):
    path = local(path)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
    temporary.replace(path)


def section(text, name):
    start, end = name + '_BEGIN\n', name + '_END'
    if text.count(start) != 1 or text.count(end) != 1:
        raise ValueError('Missing or repeated Android evidence section: ' + name)
    return text.split(start, 1)[1].split(end, 1)[0].strip()


def mapped_paths(text):
    paths = set()
    for line in text.splitlines():
        match = re.fullmatch(r'[0-9a-f]+-[0-9a-f]+\s+\S+\s+[0-9a-f]+\s+\S+\s+\d+\s+(.+)', line)
        if match:
            paths.add(match.group(1))
    return paths


def process_start(stat):
    close = stat.rfind(')')
    if close < 0 or len(stat[close + 1:].split()) < 20:
        raise ValueError('Incomplete /proc process start-time evidence')
    return int(stat[close + 1:].split()[19])


def readonly_server_version(port):
    """Query only host:version; never ask ADB to start or kill its server."""
    def receive(connection, count):
        data = b''
        while len(data) < count:
            block = connection.recv(count - len(data))
            if not block:
                raise RuntimeError('Incomplete existing ADB server response')
            data += block
        return data
    with socket.create_connection(('127.0.0.1', port), timeout=3) as connection:
        service = b'host:version'
        connection.sendall(f'{len(service):04x}'.encode() + service)
        if receive(connection, 4) != b'OKAY':
            raise RuntimeError('Existing ADB server rejected its read-only version query')
        length = int(receive(connection, 4), 16)
        if not 1 <= length <= 32:
            raise RuntimeError('Over-limit ADB server version reply')
        return receive(connection, length)


class Journal:
    def __init__(self, folder, adb, serial, port, runner=None):
        self.folder, self.adb, self.serial, self.port = folder, adb, serial, port
        self.runner = runner
        self.records = []

    def run(self, label, argv, script=None, timeout=30, server_query=False):
        index = len(self.records) + 1
        prefix = self.folder / f'{index:02d}-{label}'
        record = {'label': label, 'command': argv, 'timeout_seconds': timeout,
                  'transport': 'adb-server-host-version' if server_query else 'subprocess',
                  'started_utc': datetime.now(timezone.utc).isoformat()}
        if script is not None:
            script_path = prefix.with_suffix('.stdin.sh')
            script_path.write_bytes(script.encode('utf-8'))
            record['stdin'] = reference(script_path)
        start = time.monotonic()
        stdout = stderr = b''
        try:
            if self.runner is not None:
                result = self.runner(label, argv, script, timeout)
                stdout, stderr = result.stdout, result.stderr
                record['exit_code'] = result.returncode
            elif server_query:
                stdout = readonly_server_version(self.port)
                record['exit_code'] = 0
            else:
                result = subprocess.run(argv, input=script.encode() if script is not None else None,
                                        capture_output=True, timeout=timeout, cwd=ROOT, check=False)
                stdout, stderr = result.stdout, result.stderr
                record['exit_code'] = result.returncode
        except Exception as exc:
            if isinstance(exc, subprocess.TimeoutExpired):
                stdout, stderr = exc.stdout or b'', exc.stderr or b''
            record.update(exit_code=-1, error=f'{type(exc).__name__}: {exc}')
            stderr += str(exc).encode()
        finally:
            stdout_path, stderr_path = prefix.with_suffix('.stdout.log'), prefix.with_suffix('.stderr.log')
            stdout_path.write_bytes(stdout)
            stderr_path.write_bytes(stderr)
            record.update(stdout=reference(stdout_path), stderr=reference(stderr_path),
                          wall_seconds=time.monotonic() - start)
            self.records.append(record)
            write(self.folder / 'commands.json', self.records)
        if record['exit_code'] != 0:
            raise RuntimeError(f'{label} unavailable: {record.get("error") or stderr.decode(errors="replace")[:1000]}')
        return stdout.decode('utf-8', errors='strict').replace('\r\n', '\n')

    def device(self, label, args, script=None, timeout=30):
        version = self.run('server-version-' + label,
            ['readonly-adb-server', '127.0.0.1', str(self.port), 'host:version'], server_query=True).strip()
        if not re.fullmatch(r'[0-9a-fA-F]{4}', version) or int(version, 16) != self.client_version:
            raise RuntimeError('ADB server/client version mismatch; refusing an ADB command that could restart its server')
        # Explicit -H marks the server remote, preventing ADB's local auto-start.
        return self.run(label, [self.adb, '-H', '127.0.0.1', '-P', str(self.port),
                               '-s', self.serial, *args], script, timeout)


def discovery_script(package):
    return f'''set -eu
uid="$(id -u)"
printf 'UID=%s\\n' "$uid"
[ "$uid" = 0 ] || exit 31
printf 'APK_BEGIN\\n'
pm path {shlex.quote(package)}
printf 'APK_END\\n'
pid="$(pidof {shlex.quote(package)})"
case "$pid" in ''|*[!0-9]*) exit 32;; esac
printf 'PID=%s\\n' "$pid"
printf 'STAT_BEGIN\\n'
cat "/proc/$pid/stat"
printf '\\nSTAT_END\\n'
printf 'MAPS_BEGIN\\n'
cat "/proc/$pid/maps"
printf 'MAPS_END\\n'
'''


def inventory(text, package):
    if not re.search(r'^UID=0$', text, re.MULTILINE):
        raise ValueError('An already-rooted read-only Android shell is required')
    package_paths = section(text, 'APK').splitlines()
    if len(package_paths) != 1 or not package_paths[0].startswith('package:'):
        raise ValueError('Exactly one installed standalone package APK is required')
    apk = package_paths[0].removeprefix('package:')
    pid_values = re.findall(r'^PID=(\d+)$', text, re.MULTILINE)
    if len(pid_values) != 1 or int(pid_values[0]) <= 0:
        raise ValueError('Exactly one current Android application PID is required')
    paths = mapped_paths(section(text, 'MAPS'))
    if not ANDROID_PATH.fullmatch(apk) or not apk.startswith('/data/app/') or not apk.endswith('/base.apk') or apk not in paths:
        raise ValueError('Installed package APK is not mapped in the current process')
    package_roots = (str(PurePosixPath(apk).parent) + '/lib/',
                     f'/data/user/0/{package}/', f'/data/data/{package}/')
    libraries = {}
    for filename in ('libg.so', 'libcrprobe.so'):
        selected = [p for p in paths if p.endswith('/' + filename)
                    and ANDROID_PATH.fullmatch(p) and p.startswith(package_roots)]
        if len(selected) != 1:
            raise ValueError('Exactly one actual mapped package library is required: ' + filename)
        libraries[filename] = selected[0]
    resources = [p for p in paths if p in (f'/data/user/0/{package}/update/assets.scdb',
                                         f'/data/data/{package}/update/assets.scdb')]
    if len(resources) != 1:
        raise ValueError('Mapped package update/assets.scdb is required')
    return {'apk': apk, 'pid': int(pid_values[0]), 'start_ticks': process_start(section(text, 'STAT')),
            'libg': libraries['libg.so'], 'probe': libraries['libcrprobe.so'],
            'update_root': str(PurePosixPath(resources[0]).parent), 'maps': paths}


def verification_script(package, found, paths):
    quoted = ' '.join(shlex.quote(p) for p in paths)
    return f'''set -eu
[ "$(id -u)" = 0 ] || exit 31
[ "$(pidof {shlex.quote(package)})" = {found['pid']} ] || exit 32
printf 'HASHES_BEGIN\\n'
sha256sum {quoted}
printf 'HASHES_END\\n'
printf 'SIZES_BEGIN\\n'
wc -c {quoted}
printf 'SIZES_END\\n'
''' + discovery_script(package)


def parse_hashes(text, paths):
    hashes = {}
    for line in section(text, 'HASHES').splitlines():
        match = re.fullmatch(r'([0-9a-f]{64})\s+(\S+)', line)
        if not match or match[2] in hashes:
            raise ValueError('Missing, duplicate or malformed installed SHA256 evidence')
        hashes[match[2]] = match[1]
    sizes = {}
    for line in section(text, 'SIZES').splitlines():
        match = re.fullmatch(r'\s*(\d+)\s+(\S+)', line)
        if not match:
            raise ValueError('Malformed installed byte-size evidence')
        if match[2] == 'total':
            continue
        if match[2] in sizes:
            raise ValueError('Duplicate installed byte-size evidence')
        sizes[match[2]] = int(match[1])
    if set(hashes) != set(paths) or set(sizes) != set(paths):
        raise ValueError('Installed evidence must cover every expected path exactly once')
    return hashes, sizes


def collect_identity(manifest, output, *, serial, adb, adb_port=5037, root_shell='adbd',
                     identity_output=None, runner=None):
    if not isinstance(serial, str) or not SERIAL.fullmatch(serial):
        raise ValueError('Explicit serial must contain only a device identifier, never shell syntax')
    if type(adb_port) is not int or not 1 <= adb_port <= 65535 or root_shell not in ('adbd', 'su0'):
        raise ValueError('Invalid explicit ADB endpoint or root-shell choice')
    folder = local(output)
    folder.mkdir(parents=True, exist_ok=False)
    report = {'schema': 'cloud-native-identity-check.v1', 'status': 'unavailable',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'device_serial': serial,
              'evidence_scope': 'interface_only' if runner is not None else 'actual_adb_readonly',
              'native_execution_verified': False, 'native_probe_rpc_calls': 0,
              'identity_written': False, 'commands': []}
    write(folder / 'report.json', report)
    journal = None
    try:
        version = verify_manifest(manifest)
        locked = version['manifest']
        if locked['ticks_per_second'] != 20 or not PACKAGE.fullmatch(locked.get('client_package', '')):
            raise ValueError('A pinned 20 Hz manifest with a strict Android package name is required')
        package = locked['client_package']
        selected = {}
        for role in ('offline_apk', 'native_library', 'native_probe', 'content_fingerprint'):
            candidates = [r for r in locked['assets'] if r.get('role') == role]
            if len(candidates) != 1:
                raise ValueError('Exactly one manifest asset is required for ' + role)
            selected[role] = candidates[0]
        update = [r for r in locked['assets'] if r.get('role') in ('paired_content', 'content_fingerprint')]
        if len(update) != 652 or len({r['path'] for r in update}) != 652:
            raise ValueError('Exactly 651 paired files and one fingerprint are required')
        for row in update:
            name = row['path'].removeprefix(CONTENT_PREFIX)
            if not row['path'].startswith(CONTENT_PREFIX) or not name or PurePosixPath(name).is_absolute() or '..' in PurePosixPath(name).parts or not re.fullmatch(r'[A-Za-z0-9_./-]+', name):
                raise ValueError('Unsafe or unexpected locked update resource path')
        receipt_path = local(identity_output or locked['runtime_identity_receipt'])
        if receipt_path.exists():
            raise FileExistsError('Identity history is preserved; choose a new identity path/version')
        adb_path = Path(adb)
        if not adb_path.is_absolute():
            adb_path = local(adb) if '/' in str(adb) or '\\' in str(adb) else Path(shutil.which(str(adb)) or '')
        if not adb_path.is_file():
            raise FileNotFoundError('Explicit ADB executable is unavailable')
        report.update(version_fingerprint=version['fingerprint'], manifest=reference(manifest),
                      adb_executable={'path': str(adb_path), 'sha256': digest(adb_path.read_bytes())})
        journal = Journal(folder, str(adb_path), serial, adb_port, runner)
        client = journal.run('adb-version', [str(adb_path), 'version'])
        match = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', client)
        if not match:
            raise ValueError('Cannot determine explicit ADB client protocol version')
        journal.client_version = int(match[1])
        if journal.device('get-state', ['get-state']).strip() != 'device':
            raise RuntimeError('Specified Android serial is not in the ready device state')
        shell = ['shell', '-T', *(['su', '0'] if root_shell == 'su0' else []), 'sh', '-s']
        first = journal.device('discover-runtime', shell, discovery_script(package))
        found = inventory(first, package)
        remote = {found['apk']: selected['offline_apk'], found['libg']: selected['native_library'],
                  found['probe']: selected['native_probe']}
        for row in update:
            remote[found['update_root'] + '/' + row['path'].removeprefix(CONTENT_PREFIX)] = row
        verified = journal.device('verify-installed-files', shell,
            verification_script(package, found, list(remote)), timeout=180)
        after = inventory(verified, package)
        if any(after[key] != found[key] for key in ('pid', 'start_ticks', 'apk', 'libg', 'probe', 'update_root')):
            raise ValueError('Application process or loaded paths changed during identity capture')
        hashes, sizes = parse_hashes(verified, remote)
        for path, row in remote.items():
            if hashes[path] != row['sha256'] or sizes[path] != row['bytes']:
                raise ValueError('Installed mapped asset differs from manifest SHA256/size: ' + path)
        report.update(checks_passed=True, installed_update_files=652,
                      installed_update_bytes=sum(r['bytes'] for r in update),
                      process={'pid': found['pid'], 'start_ticks': found['start_ticks']})
        if runner is not None:
            report.update(status='interface_only', reason='Injected test runner cannot certify actual ADB identity')
        else:
            evidence = [reference(manifest)]
            for command in journal.records:
                evidence.extend(command[key] for key in ('stdout', 'stderr', 'stdin') if key in command)
            receipt = {'schema': 'phase2-native-adb-identity.v1',
                'created_utc': datetime.now(timezone.utc).isoformat(),
                'actual_adb_verified': True, 'loaded_libg_verified': True, 'content_verified': True,
                'method': 'Explicit-serial read-only ADB root shell files/maps inspection; not live native probe attestation',
                'libg_sha256': selected['native_library']['sha256'],
                'probe_sha256': selected['native_probe']['sha256'],
                'apk_sha256': selected['offline_apk']['sha256'],
                'content_version': locked['content_version'],
                'content_fingerprint_sha256': selected['content_fingerprint']['sha256'],
                'content_manifest_sha1': locked['content_manifest_sha1'],
                'installed_update_file_count': 652, 'installed_update_bytes': sum(r['bytes'] for r in update),
                'pid': found['pid'], 'process_start_ticks': found['start_ticks'],
                'device_serial': serial, 'version_fingerprint': version['fingerprint'],
                'mapped_paths': {k: found[k] for k in ('apk', 'libg', 'probe', 'update_root')},
                'source_evidence': evidence,
                'limitations': ['Recorded process identity; renew after process, package or update changes.',
                                'No live probe attest, reset, card execution or battle acceptance was performed.']}
            receipt_path.parent.mkdir(parents=True, exist_ok=True)
            with receipt_path.open('x', encoding='utf-8') as stream:
                json.dump(receipt, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
            report.update(status='verified_runtime_identity', identity_written=True, identity=reference(receipt_path))
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}')
    finally:
        report['commands'] = journal.records if journal else []
        write(folder / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--adb', required=True)
    parser.add_argument('--adb-port', type=int, default=5037)
    parser.add_argument('--root-shell', choices=('adbd', 'su0'), default='adbd')
    parser.add_argument('--output', required=True, help='新的仓库内证据目录')
    parser.add_argument('--identity-output', help='默认使用显式manifest的runtime_identity_receipt；已有文件不覆盖')
    args = parser.parse_args(argv)
    report = collect_identity(args.manifest, args.output, serial=args.serial, adb=args.adb,
        adb_port=args.adb_port, root_shell=args.root_shell, identity_output=args.identity_output)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['identity_written'] else 2


if __name__ == '__main__':
    raise SystemExit(main())

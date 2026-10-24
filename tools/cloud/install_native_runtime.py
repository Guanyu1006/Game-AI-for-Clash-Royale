"""审计原生 APK/资源并生成安装计划；仅 --execute 才操作显式 Android 设备。"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import shlex
import sys
import tarfile
import uuid
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import local
from tools.cloud import build_native_apk as build
from tools.cloud.native_identity import Journal, SERIAL, reference, write
from tools.cloud.native_version import CHECKS
from tools.phase2_native_pack_update import CONTENT_SHA1, FINGERPRINT_SHA256

PACKAGE = 'nullsroyale.rel.free'
SAFE_NAME = re.compile(r'[A-Za-z0-9_./-]+\Z')


def file_record(path):
    path = local(path)
    value = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(block)
    return {'path': path.relative_to(ROOT).as_posix(), 'bytes': path.stat().st_size,
            'sha256': value.hexdigest()}


def exact_file(path, row, label):
    actual = file_record(path)
    if any(actual[key] != row[key] for key in ('bytes', 'sha256')):
        raise ValueError('SHA256/size mismatch: ' + label)
    return actual


def safe_name(name):
    path = PurePosixPath(name)
    if (not SAFE_NAME.fullmatch(name) or path.is_absolute() or '..' in path.parts
            or str(path) != name or name in ('.', '')):
        raise ValueError('Unsafe update path: ' + repr(name))
    return name


def validate_inputs(apk, build_report, payload, payload_receipt):
    receipt = json.loads(local(build_report).read_text())
    if receipt.get('schema') != 'cloud-native-apk-build.v1' or receipt.get('success') is not True:
        raise ValueError('A successful verified APK build receipt is required')
    if any(receipt.get('checks', {}).get(key) is not True for key in CHECKS):
        raise ValueError('Every required static APK check must pass')
    apk_record = exact_file(apk, receipt['output_apk'], 'output APK')
    exact_file(receipt['input_apk']['path'], receipt['input_apk'], 'source APK')
    components = build.check_input_components(receipt['input_apk']['path'])
    with zipfile.ZipFile(local(apk)) as archive:
        if len(archive.namelist()) != len(set(archive.namelist())) or archive.testzip() is not None:
            raise ValueError('APK duplicate entry or CRC failure')
        for name, expected in [('libg.so', build.LIBG_SHA256), ('libcrprobe.so', build.PROBE_SHA256)]:
            if hashlib.sha256(archive.read('lib/arm64-v8a/' + name)).hexdigest() != expected:
                raise ValueError('Pinned native library mismatch: ' + name)
        if any(name.startswith('assets/update/') for name in archive.namelist()):
            raise ValueError('This install profile requires separate update resources')
    resource = json.loads(local(payload_receipt).read_text())
    if (resource.get('schema') != 'phase2-native-update-package.v1'
            or resource.get('content_version') != '15.535.86'
            or resource.get('content_manifest_sha1') != CONTENT_SHA1):
        raise ValueError('Only the pinned fixed-version update payload is supported')
    payload_record = exact_file(payload, resource['archive_hashes'], 'update archive')
    for path_key, hash_key in [('plan', 'plan_sha256'), ('upstream_source_manifest', 'upstream_source_manifest_sha256')]:
        if file_record(resource[path_key])['sha256'] != resource[hash_key]:
            raise ValueError('Payload source reference changed: ' + path_key)
    rows = resource['files']
    if len(rows) != 652 or resource.get('file_count') != 652:
        raise ValueError('Exactly 651 content files plus one fingerprint are required')
    names = [safe_name(row['file']) for row in rows]
    if len(set(names)) != len(names) or 'fingerprint.json' not in names:
        raise ValueError('Duplicate or missing update resource')
    for row in rows:
        if not isinstance(row['bytes'], int) or row['bytes'] < 0 or not re.fullmatch('[0-9a-f]{64}', row['sha256']):
            raise ValueError('Invalid resource size or SHA256')
        exact_file(local(resource['resource_folder']) / row['file'], row, row['file'])
    with tarfile.open(local(payload)) as archive:
        members = archive.getmembers()
        expected = {'update/' + name for name in names} | {'SHA256SUMS', 'manifest.json'}
        if (len(members) != len(expected) or {m.name for m in members} != expected
                or any(not m.isfile() for m in members)):
            raise ValueError('Archive must contain only the exact regular update files and metadata')
        for row in rows:
            member = archive.getmember('update/' + row['file'])
            if member.size != row['bytes']:
                raise ValueError('Archive member size mismatch: ' + row['file'])
            value = hashlib.sha256()
            with archive.extractfile(member) as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    value.update(block)
            if value.hexdigest() != row['sha256']:
                raise ValueError('Archive member SHA256 mismatch: ' + row['file'])
        embedded = json.load(archive.extractfile('manifest.json'))
        if embedded != {k: v for k, v in resource.items() if k not in ('archive', 'archive_hashes')}:
            raise ValueError('Embedded payload manifest differs from its receipt')
        sums = ''.join(f"{row['sha256']}  {row['file']}\n" for row in rows).encode()
        if archive.extractfile('SHA256SUMS').read() != sums:
            raise ValueError('Embedded checksum list differs from its receipt')
        fp_bytes = archive.extractfile('update/fingerprint.json').read()
        fp = json.loads(fp_bytes)
        if (hashlib.sha256(fp_bytes).hexdigest() != FINGERPRINT_SHA256
                or fp.get('version') != resource['content_version'] or fp.get('sha') != CONTENT_SHA1):
            raise ValueError('Exact canonical content fingerprint is required')
    if sum(row['bytes'] for row in rows) != resource['bytes']:
        raise ValueError('Payload byte total mismatch')
    return {'apk': apk_record, 'build_report': file_record(build_report),
            'payload': payload_record, 'payload_receipt': file_record(payload_receipt),
            'source_component_count': len(components), 'update_files': 652,
            'update_bytes': resource['bytes'], 'rows': rows}


def scripts(checked, remote, token):
    preflight = f'''set -eu
[ "$(id -u)" = 0 ] || {{ echo 'Already-rooted Android shell required' >&2; exit 31; }}
[ "$(am get-current-user)" = 0 ] || {{ echo 'Android user 0 required' >&2; exit 32; }}
[ "$(getprop sys.boot_completed)" = 1 ] || {{ echo 'Android boot incomplete' >&2; exit 33; }}
[ ! -e {shlex.quote(remote)} ] || exit 34
mkdir -m 700 {shlex.quote(remote)}
printf 'PREFLIGHT_COMPLETE\\n'
'''
    rows = checked['rows']
    size_lines = ''.join(f"{row['bytes']} {row['file']}\n" for row in rows)
    stat_args = ' '.join(shlex.quote(row['file']) for row in rows)
    apk, payload = checked['apk'], checked['payload']
    install = f'''set -eu
[ "$(id -u)" = 0 ] || exit 31
[ "$(am get-current-user)" = 0 ] || exit 32
[ "$(getprop sys.boot_completed)" = 1 ] || exit 33
remote={shlex.quote(remote)}
package={PACKAGE}
data=/data/user/0/{PACKAGE}
stage="$data/update.stage-{token}"
backup="$data/update.backup-{token}"
check_file() {{
  [ -f "$1" ] && [ ! -L "$1" ] || return 40
  [ "$(stat -c %s "$1")" = "$3" ] || return 41
  actual="$(sha256sum "$1")"
  [ "${{actual%% *}}" = "$2" ] || return 42
}}
check_file "$remote/offline-native-candidate.apk" {apk['sha256']} {apk['bytes']}
check_file "$remote/runtime-update.tar" {payload['sha256']} {payload['bytes']}
pm install --user 0 --abi arm64-v8a -r "$remote/offline-native-candidate.apk" > "$remote/pm-install.log" 2>&1 || {{ cat "$remote/pm-install.log"; exit 43; }}
cat "$remote/pm-install.log"
grep -qx Success "$remote/pm-install.log" || exit 44
installed="$(pm path --user 0 "$package")"
case "$installed" in package:/data/app/*/base.apk) ;; *) exit 45;; esac
installed="${{installed#package:}}"
check_file "$installed" {apk['sha256']} {apk['bytes']}
libdir="${{installed%/base.apk}}/lib/arm64"
check_file "$libdir/libg.so" {build.LIBG_SHA256} 27554368
check_file "$libdir/libcrprobe.so" {build.PROBE_SHA256} 6346600
am force-stop --user 0 "$package"
[ -d "$data" ] && [ ! -L "$data" ] || exit 46
owner="$(stat -c '%u:%g' "$data")"
case "$owner" in ''|*[!0-9:]*) exit 47;; esac
[ "${{owner%:*}}" -ge 10000 ] || exit 48
[ ! -e "$stage" ] && [ ! -e "$backup" ] && [ ! -L "$data/update" ] || exit 49
mkdir "$remote/extract"
tar -xf "$remote/runtime-update.tar" -C "$remote/extract"
cat > "$remote/expected-sizes.txt" <<'ROYALE_EXPECTED_SIZES'
{size_lines}ROYALE_EXPECTED_SIZES
verify_tree() {{
  tree="$1"
  [ "$(find "$tree" -type f | wc -l | tr -d ' ')" = 652 ] || return 50
  [ -z "$(find "$tree" -type l)" ] || return 51
  (cd "$tree" && sha256sum -c "$remote/extract/SHA256SUMS")
  (cd "$tree" && stat -c '%s %n' {stat_args}) > "$remote/actual-sizes.txt"
  cmp "$remote/expected-sizes.txt" "$remote/actual-sizes.txt"
}}
mv "$remote/extract/update" "$stage"
verify_tree "$stage"
chown -R "$owner" "$stage"
if [ -e "$data/update" ]; then mv "$data/update" "$backup"; fi
if ! mv "$stage" "$data/update"; then
  if [ -d "$backup" ] && [ ! -e "$data/update" ]; then mv "$backup" "$data/update"; fi
  exit 52
fi
restorecon -RF "$data/update"
verify_tree "$data/update"
[ "$(stat -c '%u:%g' "$data/update")" = "$owner" ] || exit 53
printf 'UPDATE_OWNER=%s\\nUPDATE_BACKUP=%s\\nINSTALL_COMPLETE\\n' "$owner" "$backup"
'''
    return preflight, install


def install_runtime(output, *, serial, adb, adb_port, apk, build_report, payload,
                    payload_receipt, execute=False, root_shell='adbd', guest_source_dir=None, runner=None):
    if not SERIAL.fullmatch(serial) or not 1 <= adb_port <= 65535:
        raise ValueError('Explicit safe serial and valid local ADB server port required')
    if root_shell not in ('adbd', 'su0'):
        raise ValueError('Unsupported root shell mode')
    if guest_source_dir is not None:
        if (not re.fullmatch(r'/data/local/tmp/[A-Za-z0-9_./-]+', guest_source_dir)
                or '..' in PurePosixPath(guest_source_dir).parts
                or str(PurePosixPath(guest_source_dir)) != guest_source_dir):
            raise ValueError('Guest source must be a strict /data/local/tmp directory path')
    folder, adb = local(output), local(adb)
    if folder.exists():
        raise FileExistsError('Use a new output directory; installation history is preserved')
    if not adb.is_file():
        raise FileNotFoundError('Explicit project-local ADB executable is required')
    folder.mkdir(parents=True)
    report = {'schema': 'cloud-native-runtime-install.v1', 'status': 'failed',
              'created_utc': datetime.now(timezone.utc).isoformat(), 'device_serial': serial,
              'execute_requested': execute, 'native_execution_verified': False,
              'activity_starts': 0, 'native_probe_rpc_calls': 0, 'network_changes': 0,
              'installed_verified': False, 'commands': []}
    journal = None
    try:
        checked = validate_inputs(apk, build_report, payload, payload_receipt)
        token = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]
        remote = '/data/local/tmp/royale-native-install-' + token
        preflight, script = scripts(checked, remote, token)
        for name, value in [('preflight.sh', preflight), ('install.sh', script)]:
            (folder / name).write_text(value, encoding='utf-8')
        prefix = [str(adb), '-H', '127.0.0.1', '-P', str(adb_port), '-s', serial]
        shell = ['shell', '-T', *(['su', '0'] if root_shell == 'su0' else []), 'sh', '-s']
        operations = [{'label': 'preflight', 'argv': prefix + shell, 'stdin': 'preflight.sh'}]
        if guest_source_dir is None:
            operations.extend([
                {'label': 'push-apk', 'argv': prefix + ['push', str(local(apk)), remote + '/offline-native-candidate.apk']},
                {'label': 'push-update', 'argv': prefix + ['push', str(local(payload)), remote + '/runtime-update.tar']}])
        else:
            copy_script = f'''set -eu
[ "$(id -u)" = 0 ] || exit 31
source={shlex.quote(guest_source_dir)}
remote={shlex.quote(remote)}
[ -d "$source" ] && [ ! -L "$source" ] || exit 35
[ -d "$remote" ] && [ ! -L "$remote" ] || exit 36
for name in offline-native-candidate.apk runtime-update.tar; do
  [ -f "$source/$name" ] && [ ! -L "$source/$name" ] || exit 37
  [ ! -e "$remote/$name" ] && [ ! -L "$remote/$name" ] || exit 38
  cp "$source/$name" "$remote/$name"
done
printf 'GUEST_COPY_COMPLETE\\n'
'''
            (folder / 'stage-guest.sh').write_text(copy_script, encoding='utf-8')
            operations.append({'label': 'stage-guest-artifacts', 'argv': prefix + shell, 'stdin': 'stage-guest.sh'})
        operations.append({'label': 'install-runtime', 'argv': prefix + shell, 'stdin': 'install.sh'})
        plan = {k: v for k, v in checked.items() if k != 'rows'}
        plan.update(device_serial=serial, adb_port=adb_port, root_shell=root_shell,
            adb=file_record(adb), remote_workspace=remote,
            guest_source_dir=guest_source_dir,
            old_update_backup='/data/user/0/' + PACKAGE + '/update.backup-' + token,
            preflight_script=reference(folder / 'preflight.sh'), install_script=reference(folder / 'install.sh'),
            operations=operations,
            scope='APK/update installation only; no Activity start, probe call, identity or battle verification')
        write(folder / 'plan.json', plan)
        report.update(local_inputs_verified=True, plan=reference(folder / 'plan.json'),
                      update_files=652, evidence_scope='interface_only' if runner else 'local_static')
        if not execute:
            report['status'] = 'planned'
        else:
            journal = Journal(folder, str(adb), serial, adb_port, runner)
            version = journal.run('adb-version', [str(adb), 'version'])
            match = re.search(r'Android Debug Bridge version \d+\.\d+\.(\d+)', version)
            if not match:
                raise ValueError('Cannot determine explicit ADB client protocol version')
            journal.client_version = int(match[1])
            if journal.device('get-state', ['get-state']).strip() != 'device':
                raise RuntimeError('Specified Android device is not ready')
            for operation in plan['operations']:
                args = operation['argv'][len(prefix):]
                content = (folder / operation['stdin']).read_text() if 'stdin' in operation else None
                result = journal.device(operation['label'], args, content, timeout=900)
                if operation['label'] == 'preflight' and 'PREFLIGHT_COMPLETE' not in result.splitlines():
                    raise RuntimeError('Android preflight did not complete')
                if operation['label'] == 'stage-guest-artifacts' and 'GUEST_COPY_COMPLETE' not in result.splitlines():
                    raise RuntimeError('Guest artifact staging did not complete')
                if operation['label'] == 'install-runtime' and 'INSTALL_COMPLETE' not in result.splitlines():
                    raise RuntimeError('Android installation did not complete')
            report.update(status='interface_only' if runner else 'installed_verified',
                          installed_verified=runner is None,
                          evidence_scope='interface_only' if runner else 'actual_adb_install')
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
    finally:
        report['commands'] = journal.records if journal else []
        write(folder / 'report.json', report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('serial', 'adb', 'apk', 'build-report', 'payload', 'payload-receipt', 'output'):
        parser.add_argument('--' + name, required=True)
    parser.add_argument('--adb-port', type=int, required=True)
    parser.add_argument('--root-shell', choices=('adbd', 'su0'), default='adbd')
    parser.add_argument('--guest-source-dir', help='可选：guest中仅包含两件工件的只读共享目录，替代ADB push')
    parser.add_argument('--execute', action='store_true')
    args = vars(parser.parse_args(argv))
    output = args.pop('output')
    report = install_runtime(output, **args)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] in ('planned', 'installed_verified', 'interface_only') else 2


if __name__ == '__main__':
    raise SystemExit(main())

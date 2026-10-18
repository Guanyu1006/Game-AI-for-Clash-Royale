"""Start, inspect and stop the existing project-local native Android runtime.

No download, install, battle reset or battle advance is performed. ``status``
uses only process/port inspection, SSH read commands and the native ``status``
RPC. Every command and failure is retained below outputs/platform_next/runtime.
"""
from __future__ import annotations

import argparse
import base64
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import socket
import struct
import subprocess
import sys
import time
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
BASE = ROOT / '.cache/phase2/secondary_runtime'
OUT = ROOT / 'outputs/platform_next/runtime'
SSH = Path('C:/Windows/System32/OpenSSH/ssh.exe')
QEMU = BASE / 'qemu/qemu-system-x86_64.exe'
PORTS = (22225, 22226, 55555, 26789)
APP = 'nullsroyale.rel.free'
ACTIVITY = APP + '/com.supercell.clashroyale.GameApp'


def local(path):
    target = (ROOT / path).resolve()
    if not target.is_relative_to(ROOT):
        raise ValueError(f'Path escapes project: {path}')
    return target


def save(path, value):
    path = local(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def norm(value):
    return str(value).replace('\\', '/').casefold()


def matches_vm(process):
    """Require the exact project executable and the pinned disk/network contract."""
    line = norm(process.get('CommandLine') or '')
    executable = norm(process.get('ExecutablePath') or '')
    exact_executable = executable == norm(QEMU) or line.startswith('"' + norm(QEMU) + '" ') or line.startswith(norm(QEMU) + ' ')
    disk = '.cache/phase2/secondary_runtime/linux/disk.qcow2'
    return bool(process.get('ProcessId') and exact_executable
                and re.search(r'-name\s+"?phase2-linux(?:"|\s|$)', line)
                and disk in line and 'restrict=on' in line and 'restrict=off' not in line
                and re.search(r'-accel\s+"?whpx(?:"|\s|$)', line)
                and '127.0.0.1:22225-:22' in line and '127.0.0.1:55555-:5555' in line
                and '127.0.0.1:22226' in line)


def matches_tunnel(process):
    line = norm(process.get('CommandLine') or '')
    name = norm(process.get('Name') or '')
    return bool(process.get('ProcessId') and name == 'ssh.exe'
                and norm(BASE / 'guest-key') in line
                and '127.0.0.1:26789:127.0.0.1:26789' in line
                and 'researcher@127.0.0.1' in line
                and re.search(r'-p\s+"?22225(?:"|\s|$)', line)
                and re.search(r'(?:^|\s)-n(?:\s|$)', line))


def ssh_args():
    return [str(SSH), '-i', str(BASE / 'guest-key'), '-p', '22225',
            '-o', 'BatchMode=yes', '-o', 'ConnectTimeout=6',
            '-o', 'StrictHostKeyChecking=yes', '-o', 'HostKeyAlgorithms=ssh-ed25519',
            '-o', 'UpdateHostKeys=no',
            # OpenSSH parses this option as a whitespace-separated file list.
            # Keep it ASCII/relative under cwd=ROOT; an absolute project path
            # with spaces would silently select files outside the project.
            '-o', 'UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts',
            'researcher@127.0.0.1']


def creation_timestamp(process):
    value = process.get('CreationDate')
    if not isinstance(value, str):
        raise ValueError('Live QEMU creation time is unavailable; serial trust cannot be boot-bound')
    match = re.fullmatch(r'/Date\((\d+)(?:[+-]\d{4})?\)/', value)
    if match:
        return int(match.group(1)) / 1000
    try:
        return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except ValueError:
        raise ValueError('Unrecognized QEMU creation timestamp: ' + value) from None


def ed25519_identity(encoded):
    blob = base64.b64decode(encoded, validate=True)
    if (len(blob) != 51 or struct.unpack_from('>I', blob, 0)[0] != 11
            or blob[4:15] != b'ssh-ed25519' or struct.unpack_from('>I', blob, 15)[0] != 32):
        raise ValueError('ED25519 public key has an invalid SSH wire format')
    return {'algorithm': 'ssh-ed25519', 'public_key': encoded,
            'fingerprint': 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode('ascii').rstrip('=')}


def serial_ed25519(text, vm_created):
    """Validate the latest local serial key against its published fingerprint."""
    text = text.replace('\r\n', '\n').replace('\r', '')
    blocks = list(re.finditer(r'-----BEGIN SSH HOST KEY KEYS-----\s*\n(.*?)-----END SSH HOST KEY KEYS-----', text, re.S))
    if not blocks:
        raise ValueError('Local serial has no completed host public-key block yet')
    block = blocks[-1]
    keys = re.findall(r'^ssh-ed25519[ \t]+([A-Za-z0-9+/=]+)(?:[ \t]+[^\r\n]*)?$', block.group(1), re.M)
    if len(keys) != 1:
        raise ValueError('Expected exactly one ED25519 public key in the latest serial key block')
    encoded = keys[0]
    identity = ed25519_identity(encoded)
    fingerprint = identity['fingerprint']
    preceding = text[:block.start()]
    fingerprints = list(re.finditer(r'-----BEGIN SSH HOST KEY FINGERPRINTS-----(.*?)-----END SSH HOST KEY FINGERPRINTS-----', preceding, re.S))
    if not fingerprints:
        raise ValueError('Local serial key lacks a preceding completed fingerprint block')
    fp_block = fingerprints[-1]
    declared = re.findall(r'(SHA256:[A-Za-z0-9+/]+)[^\r\n]*\(ED25519\)', fp_block.group(1))
    if declared != [fingerprint]:
        raise ValueError('Local serial ED25519 public key and fingerprint disagree')
    # Cloud-init prints UTC wall time immediately before this boot's key block.
    # A stale previous boot's serial file must never authorize a replacement key.
    dates = re.findall(r"running 'modules:final' at ([^\r\n]+?)\.\s+Up", preceding[:fp_block.start()])
    if not dates:
        raise ValueError('Local serial does not identify the key block boot time')
    boot_time = parsedate_to_datetime(dates[-1])
    if boot_time.tzinfo is None or boot_time.timestamp() < vm_created - 5:
        raise ValueError('Local serial key block predates the verified QEMU process')
    return {**identity,
            'cloud_init_final_utc': boot_time.astimezone(timezone.utc).isoformat(),
            'evidence_excerpt': text[preceding.rfind("running 'modules:final'"):block.end()]}


def known_host_matches(name, target):
    if name == target:
        return True
    if not name.startswith('|1|'):
        return False
    try:
        _, version, salt, signature = name.split('|')
        expected = hmac.new(base64.b64decode(salt, validate=True), target.encode('ascii'), hashlib.sha1).digest()
        return version == '1' and hmac.compare_digest(expected, base64.b64decode(signature, validate=True))
    except (ValueError, TypeError):
        return False


def replace_project_host_key(previous, public_key):
    target = '[127.0.0.1]:22225'
    rows = []
    for line in previous.decode('utf-8-sig').splitlines():
        fields = line.split()
        if len(fields) >= 3 and not fields[0].startswith(('#', '@')) and fields[1] == 'ssh-ed25519':
            hosts = fields[0].split(',')
            remaining = [host for host in hosts if not known_host_matches(host, target)]
            if len(remaining) != len(hosts):
                if remaining:
                    rows.append(','.join(remaining) + ' ' + ' '.join(fields[1:]))
                continue
        rows.append(line)
    rows.append(target + ' ssh-ed25519 ' + public_key)
    return ('\n'.join(rows) + '\n').encode('utf-8')


def existing_project_pin(previous):
    """Select one unambiguous ED25519 pin for this exact project SSH endpoint."""
    keys = set()
    target = '[127.0.0.1]:22225'
    for line in previous.decode('utf-8-sig').splitlines():
        fields = line.split()
        if len(fields) < 3 or fields[0].startswith(('#', '@')) or fields[1] != 'ssh-ed25519':
            continue
        if any(known_host_matches(host, target) for host in fields[0].split(',')):
            keys.add(fields[2])
    if len(keys) != 1:
        return None
    return ed25519_identity(next(iter(keys)))


def verify_same_vm(runner, vm):
    processes, listeners = runner.inventory()
    current, _ = assert_no_conflicts(processes, listeners)
    if not current or current['ProcessId'] != vm['ProcessId'] or current.get('CreationDate') != vm.get('CreationDate'):
        raise RuntimeError('Owned QEMU identity changed during local host-key verification')


def authenticate_existing_project_pin(runner, vm, timeout=10):
    known_hosts = local('.cache/phase2/secondary_runtime/known_hosts')
    if not known_hosts.is_file():
        return None
    previous = known_hosts.read_bytes()
    try:
        identity = existing_project_pin(previous)
    except (UnicodeError, ValueError):
        return None
    if identity is None:
        return None
    # HostKeyAlgorithms and the one unambiguous endpoint entry ensure that a
    # successful strict SSH session actually authenticates this stored pin.
    attempt = runner.run('existing_project_known_hosts_strict_auth',
                         ssh_args() + ['cat /proc/sys/kernel/random/boot_id'], timeout)
    boot_id = runner.text(attempt)
    if attempt.get('exit_code') != 0 or not re.fullmatch(r'[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}', boot_id):
        return None
    verify_same_vm(runner, vm)
    if known_hosts.read_bytes() != previous:
        raise RuntimeError('Project known_hosts changed during strict host authentication')
    result = {**identity, 'trust': 'existing_project_known_hosts_strict_auth',
              'verified_qemu_pid': vm['ProcessId'], 'verified_qemu_created': vm['CreationDate'],
              'guest_boot_id': boot_id, 'known_hosts': str(known_hosts.relative_to(ROOT)),
              'known_hosts_changed': False, 'current_sha256': hashlib.sha256(previous).hexdigest(),
              'previous_backup': None, 'strict_host_key_checking': True, 'network_keyscan_used': False,
              'authentication_stdout': attempt['stdout'], 'authentication_stderr': attempt['stderr']}
    save(runner.folder / 'host_key_bootstrap.json', result)
    return result


def bootstrap_local_host_key(runner, vm, timeout=30):
    """Pin only a public key emitted by our CIM-verified local QEMU serial."""
    if not matches_vm(vm):
        raise ValueError('Cannot trust serial without an identity-verified project QEMU')
    serial_match = re.search(r'(?:^|\s)-serial\s+(?:"([^"]+)"|(\S+))', vm['CommandLine'])
    serial_option = next((s for s in serial_match.groups() if s is not None), '') if serial_match else ''
    if not serial_option.startswith('file:'):
        raise ValueError('Verified QEMU does not have a file-backed serial option')
    serial_path = local(serial_option[5:])
    if serial_path != local('.cache/phase2/secondary_runtime/linux/serial.log'):
        raise ValueError('Verified QEMU serial does not match the project runtime serial log')
    created = creation_timestamp(vm)
    deadline = time.monotonic() + timeout
    last_error = None
    while time.monotonic() < deadline:
        runner.ensure_background_alive()
        authenticated = authenticate_existing_project_pin(runner, vm, timeout=min(10, max(1, deadline-time.monotonic())))
        if authenticated:
            return authenticated
        try:
            raw = serial_path.read_bytes()
            identity = serial_ed25519(raw.decode('utf-8', errors='replace'), created)
            break
        except (OSError, ValueError) as exc:
            last_error = f'{type(exc).__name__}: {exc}'
        time.sleep(1)
    else:
        raise TimeoutError('Cannot bootstrap a verified local serial host key within 30s: ' + str(last_error))
    # Revalidate PID/creation time immediately before changing the local pin.
    verify_same_vm(runner, vm)
    known_hosts = local('.cache/phase2/secondary_runtime/known_hosts')
    previous = known_hosts.read_bytes() if known_hosts.exists() else b''
    replacement = replace_project_host_key(previous, identity['public_key'])
    changed = replacement != previous
    backup = None
    if changed:
        backup = runner.folder / 'known_hosts.before-local-serial-bootstrap'
        backup.write_bytes(previous)
        temporary = local('.cache/phase2/secondary_runtime/known_hosts.phase2-new.tmp')
        temporary.write_bytes(replacement)
        os.replace(temporary, known_hosts)
    excerpt = runner.folder / 'host_key_bootstrap.serial_evidence.txt'
    excerpt.write_text(identity.pop('evidence_excerpt'), encoding='utf-8')
    result = {**identity, 'trust': 'localserialtrust: key from the live identity-verified project QEMU serial, not network keyscan',
              'verified_qemu_pid': vm['ProcessId'], 'verified_qemu_created': vm['CreationDate'],
              'source_serial': str(serial_path.relative_to(ROOT)), 'source_serial_sha256': hashlib.sha256(raw).hexdigest(),
              'evidence_excerpt': str(excerpt.relative_to(ROOT)), 'known_hosts': str(known_hosts.relative_to(ROOT)),
              'known_hosts_changed': changed, 'previous_sha256': hashlib.sha256(previous).hexdigest(),
              'current_sha256': hashlib.sha256(replacement).hexdigest(),
              'previous_backup': str(backup.relative_to(ROOT)) if backup else None,
              'strict_host_key_checking': True, 'network_keyscan_used': False}
    save(runner.folder / 'host_key_bootstrap.json', result)
    return result


def qemu_args():
    # QEMU's Windows option parser cannot reliably open Chinese absolute paths.
    # CreateProcess receives the executable as Unicode; all QEMU file options
    # remain ASCII relative paths under the fixed project cwd.
    relative = Path('.cache/phase2/secondary_runtime')
    linux = relative / 'linux'
    return [str(QEMU), '-L', (relative / 'qemu/share').as_posix(), '-name', 'phase2-linux',
            '-machine', 'q35', '-accel', 'whpx',
            '-cpu', 'qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt,+cx16', '-smp', '4', '-m', '4096',
            '-smbios', 'type=1,serial=ds=nocloud',
            '-drive', f'if=pflash,format=raw,readonly=on,file={(relative / "qemu/share/edk2-x86_64-code.fd").as_posix()}',
            '-drive', f'if=pflash,format=raw,file={(linux / "uefi-vars.fd").as_posix()}',
            '-drive', f'file={(linux / "disk.qcow2").as_posix()},format=qcow2,if=virtio',
            '-drive', f'file={(linux / "seed.iso").as_posix()},media=cdrom,format=raw,readonly=on',
            '-device', 'virtio-net-pci,netdev=net0', '-netdev',
            'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:22225-:22,hostfwd=tcp:127.0.0.1:55555-:5555',
            '-display', 'none', '-serial', f'file:{(linux / "serial.log").as_posix()}', '-monitor', 'none',
            '-qmp', 'tcp:127.0.0.1:22226,server=on,wait=off', '-no-reboot']


def port_open(port):
    try:
        with socket.create_connection(('127.0.0.1', port), timeout=0.4):
            return True
    except OSError:
        return False


class Runner:
    def __init__(self, action):
        stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        self.folder = OUT / f'{stamp}-{action}-{os.getpid()}'
        self.folder.mkdir(parents=True, exist_ok=False)
        self.rows = []
        self.background_processes = []
        self.env = os.environ.copy()
        temporary = local('.tmp/platform_next/runtime')
        temporary.mkdir(parents=True, exist_ok=True)
        self.env.update(TEMP=str(temporary), TMP=str(temporary), PYTHONDONTWRITEBYTECODE='1', PYTHONUTF8='1')

    def run(self, label, argv, timeout=20, required=False):
        index = len(self.rows)
        stdout = self.folder / f'{index:03}-{label}.stdout.log'
        stderr = self.folder / f'{index:03}-{label}.stderr.log'
        row = {'label': label, 'argv': [str(v) for v in argv], 'timeout_seconds': timeout,
               'started_utc': datetime.now(timezone.utc).isoformat(), 'cwd': str(ROOT)}
        start = time.monotonic()
        try:
            with stdout.open('wb') as out, stderr.open('wb') as err:
                result = subprocess.run(row['argv'], cwd=ROOT, env=self.env, stdin=subprocess.DEVNULL,
                                        stdout=out, stderr=err, timeout=timeout,
                                        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
                row['exit_code'] = result.returncode
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
        row.update(wall_seconds=time.monotonic() - start,
                   stdout=str(stdout.relative_to(ROOT)), stderr=str(stderr.relative_to(ROOT)))
        self.rows.append(row)
        save(self.folder / 'commands.json', self.rows)
        if required and row.get('exit_code') != 0:
            raise RuntimeError(f'{label} failed; inspect {row["stderr"]} and {row["stdout"]}; {row.get("error", "")}')
        return row

    def text(self, row):
        return local(row['stdout']).read_text(encoding='utf-8-sig', errors='replace').strip()

    def powershell_json(self, label, command):
        prefix = "$ErrorActionPreference='Stop'; [Console]::OutputEncoding=[System.Text.UTF8Encoding]::new($false); "
        row = self.run(label, ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', prefix + command], 30)
        if row.get('exit_code') != 0:
            raise RuntimeError(f'Windows process/port inspection failed ({label}); permissions are not bypassed; see {row["stderr"]}')
        text = self.text(row)
        try:
            data = json.loads(text) if text else []
        except ValueError as exc:
            raise RuntimeError(f'Invalid Windows inspection output: {row["stdout"]}') from exc
        return data if isinstance(data, list) else [data]

    def inventory(self):
        processes = self.powershell_json('cim_processes',
            '@(Get-CimInstance Win32_Process | Where-Object { $_.Name -match "^(qemu-system-x86_64|ssh|python|pythonw|powershell|pwsh)\\.exe$" } | '
            'Select-Object ProcessId,ParentProcessId,Name,ExecutablePath,CommandLine,CreationDate) | ConvertTo-Json -Depth 4')
        listeners = self.powershell_json('tcp_listeners',
            '@(Get-NetTCPConnection -State Listen | Where-Object { $_.LocalPort -in @(22225,22226,55555,26789) } | '
            'Select-Object LocalAddress,LocalPort,OwningProcess) | ConvertTo-Json -Depth 4')
        return processes, listeners

    def launch(self, label, argv):
        logfile = self.folder / f'{label}.log'
        with logfile.open('ab') as log:
            process = subprocess.Popen([str(x) for x in argv], cwd=ROOT, env=self.env,
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result = {'pid': process.pid, 'command': [str(x) for x in argv],
                  'log': str(logfile.relative_to(ROOT)), 'started_utc': datetime.now(timezone.utc).isoformat()}
        save(OUT / (label + '.json'), result)
        self.background_processes.append((label, process, result['log']))
        time.sleep(1.0)
        if process.poll() is not None:
            raise RuntimeError(f'{label} exited with {process.returncode}; see {result["log"]}')
        return result

    def ensure_background_alive(self):
        for label, process, logfile in self.background_processes:
            if process.poll() is not None:
                raise RuntimeError(f'{label} exited with {process.returncode}; see {logfile}; readiness wait aborted')


def owned(processes, listeners):
    vms = [p for p in processes if matches_vm(p)]
    tunnels = [p for p in processes if matches_tunnel(p)]
    if len(vms) > 1 or len(tunnels) > 1:
        raise RuntimeError('Multiple matching project VMs or tunnels; refusing ambiguous ownership')
    vm = vms[0] if vms else None
    tunnel = tunnels[0] if tunnels else None
    conflicts = []
    for listener in listeners:
        port = int(listener['LocalPort'])
        expected = tunnel if port == 26789 else vm
        if listener.get('LocalAddress') != '127.0.0.1' or not expected or int(listener['OwningProcess']) != int(expected['ProcessId']):
            conflicts.append(listener)
    return vm, tunnel, conflicts


def assert_no_conflicts(processes, listeners):
    vm, tunnel, conflicts = owned(processes, listeners)
    if conflicts:
        raise RuntimeError(f'Unknown or non-loopback owner occupies project ports; no process was stopped: {conflicts}')
    # A project QEMU started with a different acceleration/network contract still
    # holds this disk even if it has not acquired its listening sockets yet.
    for proc in processes:
        line = norm(proc.get('CommandLine') or '')
        if norm(QEMU) in norm(proc.get('ExecutablePath') or '') and '/linux/disk.qcow2' in line and not matches_vm(proc):
            raise RuntimeError('Project disk belongs to a QEMU with an unexpected contract; refusing to start or stop it')
    return vm, tunnel


def inspect(runner):
    report = {'schema': 'phase2-platform-runtime.v1', 'action': 'status',
              'read_only_battle': True, 'rpc_commands_permitted': ['status'],
              'ports': {str(port): port_open(port) for port in PORTS},
              'rpc_mutation_performed': False, 'global_configuration_modified': False}
    try:
        processes, listeners = runner.inventory()
        vm, tunnel, conflicts = owned(processes, listeners)
        report.update(vm=vm, tunnel=tunnel, listeners=listeners, unknown_port_owners=conflicts,
                      windows_inspection='ok')
        if conflicts:
            report.update(status='conflict', error='Unknown/non-loopback listener; status will not connect to it')
            return report
        if not vm:
            report.update(status='stopped', native_ready=False)
            return report
        workers = native_workers(processes)
        if workers:
            # The native server has one session owner. A second status session
            # can block behind a long-lived renderer/agent connection, despite
            # status itself being read-only. Never probe a controller's runtime.
            report.update(status='busy', native_ready=None,
                          active_native_controllers=workers,
                          native_probe_skipped_reason='Active controller owns the single native RPC session; runtime readiness is not re-probed',
                          native_rpc_used=False)
            return report
        ssh = runner.run('readonly_guest', ssh_args() + [
            'printf "BOOT_ID="; cat /proc/sys/kernel/random/boot_id; '
            'sudo docker inspect --format "{{.State.Status}}" phase2-android; '
            'sudo docker exec phase2-android getprop sys.boot_completed'], 15)
        report['guest_readonly'] = {'exit_code': ssh.get('exit_code'), 'stdout': runner.text(ssh),
                                    'stderr_log': ssh['stderr']}
        if tunnel and report['ports']['26789']:
            from royale_phase2.probe import ProbeClient
            client = ProbeClient(timeout=5)
            try:
                report['native_status'] = client.status()
                report['native_ready'] = bool(report['native_status'].get('contentReady'))
            except Exception as exc:
                report['native_ready'] = False
                report['native_error'] = f'{type(exc).__name__}: {exc}'
            finally:
                client.close()
        else:
            report['native_ready'] = False
        report['status'] = 'ready' if report['native_ready'] else 'running_not_ready'
    except Exception as exc:
        report.update(status='inspection_failed', windows_inspection='failed_or_permission_limited',
                      error=f'{type(exc).__name__}: {exc}')
    return report


def wait_command(runner, label, command, predicate, seconds):
    deadline = time.monotonic() + seconds
    attempt = 0
    while time.monotonic() < deadline:
        runner.ensure_background_alive()
        attempt += 1
        row = runner.run(f'{label}_{attempt:02}', ssh_args() + [command], 12)
        if row.get('exit_code') == 0 and predicate(runner.text(row)):
            return row
        runner.ensure_background_alive()
        time.sleep(1)
    raise TimeoutError(f'{label} did not become ready within {seconds}s; all attempts retained')


def up(runner, args):
    required = [SSH, QEMU, BASE / 'guest-key', BASE / 'linux/disk.qcow2',
                BASE / 'linux/seed.iso', BASE / 'linux/uefi-vars.fd',
                BASE / 'qemu/share/edk2-x86_64-code.fd']
    missing = [str(p.relative_to(ROOT)) if p.is_relative_to(ROOT) else str(p) for p in required if not p.is_file()]
    if missing:
        raise FileNotFoundError('Existing runtime assets missing; no automatic install/download: ' + repr(missing))
    processes, listeners = runner.inventory()
    vm, tunnel = assert_no_conflicts(processes, listeners)
    result = {'schema': 'phase2-platform-runtime.v1', 'action': 'up',
              'vm_reused': bool(vm), 'tunnel_reused': bool(tunnel),
              'rpc_mutation_performed': False, 'global_configuration_modified': False}
    workers = native_workers(processes)
    if workers:
        if not vm or not tunnel:
            raise RuntimeError('Active native controller found with incomplete runtime; stop its own controller before lifecycle changes')
        result.update(status='busy', active_native_controllers=workers,
                      native_probe_skipped_reason='Existing controller owns the single native RPC session',
                      verified_after=inspect(runner))
        return result
    if not vm:
        result['vm_started'] = runner.launch('vm', qemu_args())
        processes, listeners = runner.inventory()
        vm, tunnel = assert_no_conflicts(processes, listeners)
        if not vm:
            raise RuntimeError('New QEMU could not be identity-verified before SSH bootstrap')
    result['host_key_bootstrap'] = bootstrap_local_host_key(runner, vm)
    wait_command(runner, 'ssh_ready', 'cat /proc/sys/kernel/random/boot_id', bool, args.boot_timeout)
    runner.run('android_start_existing', ssh_args() + ['sudo phase2-android-start'], 60, required=True)
    wait_command(runner, 'android_container_boot', 'sudo docker exec phase2-android getprop sys.boot_completed',
                 lambda text: text.strip() == '1', args.boot_timeout)
    wait_command(runner, 'adb_connect', 'adb connect 127.0.0.1:5555',
                 lambda text: 'connected to 127.0.0.1:5555' in text, args.boot_timeout)
    wait_command(runner, 'android_boot', 'adb -s 127.0.0.1:5555 shell getprop sys.boot_completed',
                 lambda text: text.strip() == '1', args.boot_timeout)
    pid = runner.run('app_pid', ssh_args() + [f'adb -s 127.0.0.1:5555 shell pidof {APP}'], 15)
    if pid.get('exit_code') == 0 and runner.text(pid):
        result['app_reused'] = True
    elif pid.get('exit_code') in (0, 1):
        runner.run('app_start', ssh_args() + [f'adb -s 127.0.0.1:5555 shell am start -n {ACTIVITY}'], 40, required=True)
        result['app_reused'] = False
    else:
        raise RuntimeError('Could not determine app process; refusing to blindly restart it')
    runner.run('adb_probe_forward', ssh_args() + ['adb -s 127.0.0.1:5555 forward tcp:26789 tcp:26789'], 20, required=True)
    processes, listeners = runner.inventory()
    vm, tunnel = assert_no_conflicts(processes, listeners)
    if not vm:
        raise RuntimeError('Project VM disappeared during startup')
    if not tunnel:
        command = ssh_args()[:-1] + ['-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
                                    '-o', 'ServerAliveCountMax=3', '-N', '-L',
                                    '127.0.0.1:26789:127.0.0.1:26789', 'researcher@127.0.0.1']
        result['tunnel_started'] = runner.launch('tunnel', command)
    from royale_phase2.probe import ProbeClient
    deadline = time.monotonic() + args.probe_timeout
    last_error = None
    while time.monotonic() < deadline:
        runner.ensure_background_alive()
        client = ProbeClient(timeout=5)
        try:
            status = client.status()
            if status.get('contentReady'):
                # Release our session before inspect opens its own status
                # connection. Keeping this open deadlocks the single-session
                # server until the second connection times out.
                client.close()
                result.update(status='ready', native_status=status)
                result['verified_after'] = inspect(runner)
                if result['verified_after']['status'] != 'ready':
                    raise RuntimeError('Final owned runtime check is not ready')
                return result
            last_error = 'status returned contentReady=false'
        except Exception as exc:
            last_error = f'{type(exc).__name__}: {exc}'
        finally:
            client.close()
        time.sleep(1)
    raise TimeoutError(f'Native content did not become ready: {last_error}; existing app was not force-stopped')


def native_workers(processes):
    names = r'phase2_(?:replay|evaluate_native|view|cohort|batch|native_fixtures|benchmark|offline_smoke)\.py'
    result = []
    for process in processes:
        if int(process['ProcessId']) == os.getpid():
            continue
        command = process.get('CommandLine') or ''
        live_console = (re.search(r'phase2_platform\.py(?:["\s]|$)', command, re.IGNORECASE)
                        and re.search(r'--mode(?:=|\s+)["\x27]?(?:agent|replay)(?:["\x27\s]|$)', command, re.IGNORECASE))
        if re.search(names, command, re.IGNORECASE) or live_console:
            result.append(process)
    return result


def stop_verified(runner, process, predicate, label):
    current, _ = runner.inventory()
    actual = next((p for p in current if int(p['ProcessId']) == int(process['ProcessId'])), None)
    if not actual:
        return {'already_exited': process['ProcessId']}
    if not predicate(actual) or actual.get('CreationDate') != process.get('CreationDate'):
        raise RuntimeError('Process identity changed before stop; refusing PID reuse')
    runner.run(label, ['taskkill.exe', '/PID', str(actual['ProcessId']), '/F'], 15, required=True)
    return {'stopped_pid': actual['ProcessId'], 'verified_commandline': actual['CommandLine']}


def down(runner, args):
    processes, listeners = runner.inventory()
    vm, tunnel = assert_no_conflicts(processes, listeners)
    workers = native_workers(processes)
    if workers:
        raise RuntimeError(f'Native worker processes are active; stop their own controller first: {[p["ProcessId"] for p in workers]}')
    report = {'schema': 'phase2-platform-runtime.v1', 'action': 'down', 'rpc_mutation_performed': False,
              'global_configuration_modified': False}
    if vm:
        try:
            report['host_key_bootstrap'] = bootstrap_local_host_key(runner, vm)
        except Exception as exc:
            if not args.force:
                raise
            report['forced_recovery_host_key_bootstrap_failure'] = f'{type(exc).__name__}: {exc}'
        # Ubuntu's unattended-upgrades shutdown helper can otherwise wait
        # 30 minutes in this intentionally network-isolated research guest.
        # The helper uses /run drop-ins and timer masks (next-boot expiry),
        # checks real apt/dpkg locks, and logs effective systemd readback.
        guest_source = local('tools/phase2_platform_guest_shutdown.py').read_text(encoding='utf-8')
        guest_command = "sudo python3 - --apply <<'PHASE2_GUEST_SHUTDOWN'\n" + guest_source + "\nPHASE2_GUEST_SHUTDOWN"
        preparation = runner.run('guest_bounded_shutdown_prepare', ssh_args() + [guest_command], 60)
        report['guest_shutdown_preparation'] = {'exit_code': preparation.get('exit_code'),
                                                'stdout': preparation['stdout'], 'stderr': preparation['stderr'],
                                                'runtime_configuration_only': True}
        if preparation.get('exit_code') != 0:
            if not args.force:
                raise RuntimeError('Guest shutdown preparation failed or real package locks are held; no shutdown was requested. '
                                   f'Inspect {preparation["stdout"]} and {preparation["stderr"]}')
            report['forced_recovery_preparation_failure_preserved'] = True
        row = runner.run('guest_sync_poweroff', ssh_args() + ['sync && sudo shutdown -h now'], 25)
        report['graceful_shutdown_command_exit'] = row.get('exit_code')
        deadline = time.monotonic() + args.shutdown_timeout
        while time.monotonic() < deadline:
            processes, _ = runner.inventory()
            if not any(int(p['ProcessId']) == int(vm['ProcessId']) and matches_vm(p) for p in processes):
                break
            time.sleep(2)
        else:
            if not args.force:
                raise TimeoutError('Guest did not finish graceful shutdown; no forced VM stop. Inspect logs or explicitly use down --force')
            report['forced_vm_stop'] = stop_verified(runner, vm, matches_vm, 'force_owned_vm')
    if tunnel:
        report['tunnel_stop'] = stop_verified(runner, tunnel, matches_tunnel, 'stop_owned_tunnel')
    report['verified_after'] = inspect(runner)
    if report['verified_after']['status'] != 'stopped':
        raise RuntimeError('Runtime did not stop cleanly; inspect report/logs')
    report['status'] = 'stopped'
    return report


@contextmanager
def mutation_lock():
    """OS-held file lock, released after crash; no stale PID deletion heuristic."""
    import msvcrt
    OUT.mkdir(parents=True, exist_ok=True)
    with (OUT / 'lifecycle.lock').open('a+b') as stream:
        stream.seek(0)
        if not stream.read(1):
            stream.write(b'0')
            stream.flush()
        stream.seek(0)
        try:
            msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError as exc:
            raise RuntimeError('Another platform up/down owns the lifecycle lock') from exc
        try:
            yield
        finally:
            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)


def self_test():
    vm = {'ProcessId': 123, 'Name': 'qemu-system-x86_64.exe', 'ExecutablePath': str(QEMU),
          'CommandLine': subprocess.list2cmdline(qemu_args()), 'CreationDate': '/Date(1792297160000)/'}
    tunnel = {'ProcessId': 124, 'Name': 'ssh.exe',
              'CommandLine': subprocess.list2cmdline(ssh_args()[:-1] + ['-N', '-L', '127.0.0.1:26789:127.0.0.1:26789', 'researcher@127.0.0.1'])}
    assert matches_vm(vm) and matches_tunnel(tunnel)
    ssh_configuration = ssh_args()
    assert 'UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts' in ssh_configuration
    assert 'StrictHostKeyChecking=yes' in ssh_configuration and 'StrictHostKeyChecking=no' not in ssh_configuration
    assert 'HostKeyAlgorithms=ssh-ed25519' in ssh_configuration
    blob = struct.pack('>I', 11) + b'ssh-ed25519' + struct.pack('>I', 32) + bytes(range(32))
    encoded = base64.b64encode(blob).decode('ascii')
    fingerprint = 'SHA256:' + base64.b64encode(hashlib.sha256(blob).digest()).decode('ascii').rstrip('=')
    synthetic_serial = ("cloud-init: running 'modules:final' at Sun, 18 Oct 2026 04:19:29 +0000. Up 7.0 seconds.\n"
                        '-----BEGIN SSH HOST KEY FINGERPRINTS-----\n256 ' + fingerprint + ' root@phase2-linux (ED25519)\n'
                        '-----END SSH HOST KEY FINGERPRINTS-----\n-----BEGIN SSH HOST KEY KEYS-----\n'
                        'ssh-ed25519 ' + encoded + ' root@phase2-linux\n-----END SSH HOST KEY KEYS-----\n')
    created = datetime(2026, 10, 18, 4, 19, 20, tzinfo=timezone.utc).timestamp()
    assert serial_ed25519(synthetic_serial.replace('\n', '\r\n'), created)['fingerprint'] == fingerprint
    invalid_serials = [(synthetic_serial.replace(fingerprint, 'SHA256:WRONG'), created),
                       (synthetic_serial.replace('ssh-ed25519 ' + encoded, 'ssh-ed25519 ' + encoded + '\nssh-ed25519 ' + encoded), created),
                       (synthetic_serial, created + 60)]
    for invalid, birth in invalid_serials:
        try:
            serial_ed25519(invalid, birth)
        except ValueError:
            pass
        else:
            raise AssertionError('Mismatched/ambiguous/stale serial host identity was accepted')
    previous = b'# preserved history comment\n[127.0.0.1]:22225,other-host ssh-ed25519 OLDKEY\n'
    replaced = replace_project_host_key(previous, encoded)
    assert b'other-host ssh-ed25519 OLDKEY' in replaced and b'# preserved history comment' in replaced
    assert replace_project_host_key(replaced, encoded) == replaced
    salt = b'self-test-local-salt'
    signature = hmac.new(salt, b'[127.0.0.1]:22225', hashlib.sha1).digest()
    hashed = '|1|' + base64.b64encode(salt).decode('ascii') + '|' + base64.b64encode(signature).decode('ascii')
    assert known_host_matches(hashed, '[127.0.0.1]:22225')
    assert not known_host_matches(hashed, '[127.0.0.1]:22226')
    assert existing_project_pin(replaced)['fingerprint'] == fingerprint
    assert existing_project_pin(b'unrelated-host ssh-ed25519 ' + encoded.encode() + b'\n') is None
    assert existing_project_pin(replaced + b'[127.0.0.1]:22225 ssh-ed25519 ANOTHERKEY\n') is None
    for option in qemu_args()[1:]:
        assert option.isascii(), 'QEMU file option must be ASCII under the Unicode project cwd'
    console_processes = [
        {'ProcessId': os.getpid() + 100 + index, 'CommandLine': command}
        for index, command in enumerate(('python tools/phase2_platform.py --mode agent',
                                         'python tools/phase2_platform.py --mode=replay',
                                         'python tools/phase2_platform.py --mode archive'))]
    assert native_workers(console_processes) == console_processes[:2]
    assert not matches_vm(dict(vm, ExecutablePath='C:/other/qemu.exe', CommandLine='qemu.exe -name phase2-linux'))
    assert not matches_vm(dict(vm, CommandLine=vm['CommandLine'].replace('restrict=on', 'restrict=off')))
    assert not matches_tunnel(dict(tunnel, CommandLine=tunnel['CommandLine'].replace(str(BASE / 'guest-key'), 'C:/other/key')))
    assert not matches_tunnel(dict(tunnel, CommandLine=tunnel['CommandLine'].replace('26789:127.0.0.1:26789', '26790:127.0.0.1:26790')))
    listeners = [{'LocalAddress': '127.0.0.1', 'LocalPort': 22225, 'OwningProcess': 123},
                 {'LocalAddress': '127.0.0.1', 'LocalPort': 26789, 'OwningProcess': 124}]
    assert assert_no_conflicts([vm, tunnel], listeners) == (vm, tunnel)
    # A diagnostic must not compete with a controller's long-lived native
    # connection. Mock all ports/inventory, so this check opens no sockets.
    from unittest.mock import patch
    class ExistingPinOfflineRunner:
        folder = OUT / 'offline-auth-not-created'
        def __init__(self, success): self.success = success; self.calls = []
        def inventory(self): return [vm, tunnel], listeners
        def run(self, label, argv, timeout):
            self.calls.append(argv)
            assert 'StrictHostKeyChecking=yes' in argv and argv[-1] == 'cat /proc/sys/kernel/random/boot_id'
            return {'exit_code': 0 if self.success else 255, 'stdout': 'offline.stdout', 'stderr': 'offline.stderr'}
        def text(self, row): return '11111111-2222-3333-4444-555555555555'
    pinned = replace_project_host_key(b'', encoded)
    successful = ExistingPinOfflineRunner(True)
    rejected = ExistingPinOfflineRunner(False)
    with patch.object(Path, 'is_file', return_value=True), patch.object(Path, 'read_bytes', return_value=pinned), \
         patch.dict(globals(), {'save': lambda *args, **kwargs: None}):
        trust = authenticate_existing_project_pin(successful, vm)
        assert trust['trust'] == 'existing_project_known_hosts_strict_auth' and trust['fingerprint'] == fingerprint
        assert authenticate_existing_project_pin(rejected, vm) is None
    assert len(successful.calls) == 1 and len(rejected.calls) == 1
    busy_runner = type('BusyOfflineRunner', (), {
        'inventory': lambda self: ([vm, tunnel, console_processes[0]], listeners)})()
    with patch.dict(globals(), {'port_open': lambda port: True}):
        busy = inspect(busy_runner)
    assert busy['status'] == 'busy' and busy['native_rpc_used'] is False

    # Exercise the real up control flow against a single-session fake server:
    # its final inspector opens a second session and must see the first closed.
    from royale_phase2 import probe as native_probe
    session_counts = {'active': 0, 'opened': 0}
    class FakeProbe:
        def __init__(self, **kwargs): self.connected = False
        def status(self):
            if session_counts['active']:
                raise RuntimeError('Fake single-session native server is already owned')
            self.connected = True
            session_counts['active'] += 1; session_counts['opened'] += 1
            return {'contentReady': True}
        def close(self):
            if self.connected:
                session_counts['active'] -= 1; self.connected = False
    class OfflineRunner:
        def inventory(self): return [vm, tunnel], listeners
        def run(self, *args, **kwargs): return {'exit_code': 0}
        def text(self, row): return '123'
        def ensure_background_alive(self): pass
    def final_inspection(runner):
        other = FakeProbe()
        try:
            other.status()
            return {'status': 'ready'}
        finally: other.close()
    with patch.object(Path, 'is_file', return_value=True), \
         patch.object(native_probe, 'ProbeClient', FakeProbe), \
         patch.dict(globals(), {'wait_command': lambda *args, **kwargs: {}, 'inspect': final_inspection,
                                'bootstrap_local_host_key': lambda *args, **kwargs: {'offline_mock': True}}):
        ready = up(OfflineRunner(), argparse.Namespace(boot_timeout=1, probe_timeout=1))
    assert ready['status'] == 'ready' and session_counts == {'active': 0, 'opened': 2}
    for conflict in [dict(listeners[0], OwningProcess=999), dict(listeners[0], LocalAddress='0.0.0.0')]:
        try:
            assert_no_conflicts([vm, tunnel], [conflict])
        except RuntimeError:
            pass
        else:
            raise AssertionError('Unknown/non-loopback owner accepted')
    try:
        local('../outside')
    except ValueError:
        pass
    else:
        raise AssertionError('Out-of-project path accepted')
    return {'schema': 'phase2-platform-runtime-self-test.v1', 'status': 'pass',
            'checks': ['owned_vm', 'isolated_network_required', 'owned_tunnel', 'wrong_key_rejected',
                       'wrong_port_rejected', 'unknown_listener_rejected', 'non_loopback_rejected', 'path_containment',
                       'qemu_ascii_relative_options', 'live_console_stop_guard',
                       'busy_controller_diagnostic_does_not_probe', 'up_closes_probe_before_final_inspection',
                       'relative_ascii_strict_known_hosts', 'serial_key_fingerprint_and_boot_binding',
                       'serial_ambiguity_and_stale_rejected', 'known_hosts_preservation_and_idempotence',
                       'existing_pin_strict_auth_without_new_serial', 'failed_strict_auth_cannot_authorize_pin'],
            'vm_started': False, 'rpc_used': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['status', 'up', 'down', 'self-test'])
    parser.add_argument('--boot-timeout', type=int, default=180, help='Per SSH/Android readiness phase, seconds')
    parser.add_argument('--probe-timeout', type=int, default=90)
    parser.add_argument('--shutdown-timeout', type=int, default=90)
    parser.add_argument('--force', action='store_true', help='down only: force only the verified project VM after graceful timeout')
    args = parser.parse_args()
    if min(args.boot_timeout, args.probe_timeout, args.shutdown_timeout) < 1:
        parser.error('Timeouts must be positive')
    if args.force and args.action != 'down':
        parser.error('--force is only valid with down')
    runner = Runner(args.action)
    try:
        if args.action == 'self-test':
            report = self_test()
        elif args.action == 'status':
            report = inspect(runner)
        else:
            with mutation_lock():
                report = up(runner, args) if args.action == 'up' else down(runner, args)
    except Exception as exc:
        report = {'schema': 'phase2-platform-runtime.v1', 'action': args.action, 'status': 'failed',
                  'error': f'{type(exc).__name__}: {exc}', 'traceback': traceback.format_exc(),
                  'partial_state_preserved': True, 'automatic_cleanup_or_unknown_process_kill': False}
    report['commands'] = runner.rows
    report['report_path'] = str((runner.folder / 'report.json').relative_to(ROOT))
    save(runner.folder / 'report.json', report)
    save(OUT / 'latest.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2), flush=True)
    return 0 if report['status'] in ('ready', 'stopped', 'pass', 'busy') else 1


if __name__ == '__main__':
    raise SystemExit(main())

"""Experimental TCG Android VM lifecycle; not native-game acceptance.

VM commands use a private disk copy (or explicit stopped-disk recovery) and only
the recorded QEMU process. Guest
kernel operations happen inside software emulation, never on the Codespaces host.
"""
from __future__ import annotations

import argparse
from collections import defaultdict
from contextlib import contextmanager
import fcntl
import gzip
import json
import os
from pathlib import Path
import re
import shlex
import shutil
import socket
import stat
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from tools.cloud import software_android as prepare

PORTS = (15555, 15556)
STAGING = ROOT / '.cache/native-tools/android-share'
MODULES = ('crc32c_generic', 'libcrc32c', 'sha256_generic', 'virtio_pci', 'virtio_blk',
           'virtio_net', 'ext4', 'binder_linux', '9p', '9pnet_virtio', 'loop',
           'dm_mod', 'dm_verity', 'overlay', 'fuse', 'tun', 'veth', 'nfnetlink', 'nf_conntrack',
           'ip_tables', 'iptable_filter', 'iptable_nat', 'xt_MASQUERADE',
           'xt_conntrack', 'xt_owner', 'xt_mark', 'xt_qtaguid')
RESUME_MIN_FREE = 1024 ** 3


def unlinked_local(value):
    """Repository path with no symlink component, including the final file."""
    raw = Path(value)
    raw = raw if raw.is_absolute() else ROOT / raw
    if '..' in raw.parts or not raw.is_relative_to(ROOT) or raw == ROOT:
        raise ValueError('Resume paths must remain below the current repository')
    current = ROOT
    for part in raw.relative_to(ROOT).parts:
        current /= part
        if current.is_symlink():
            raise ValueError('VM recovery paths must not contain symlinks: ' + str(current))
    return raw


@contextmanager
def start_lock(cache):
    """Two starts must not both pass the stopped-process/port checks."""
    path = unlinked_local(cache / '.vm-start.lock')
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(descriptor, 'a') as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise ValueError('Another VM start is already in progress') from error
        yield


def checked_record(cache, attempt):
    attempt = unlinked_local(attempt)
    record = json.loads(unlinked_local(attempt / 'process.json').read_text())
    disk = unlinked_local(record.get('mutable_disk', str(attempt / 'android.ext4')))
    command = record.get('command', [])
    if (record.get('schema') != 'experimental-software-android-vm.v1'
            or unlinked_local(record['attempt']) != attempt
            or record.get('qmp_socket') != str(attempt / 'qmp.sock')
            or record.get('adb_endpoint') != '127.0.0.1:15555'
            or record.get('serial_endpoint') != '127.0.0.1:15556'
            or type(record.get('pid')) is not int or record['pid'] <= 0
            or not str(record.get('process_starttime', '')).isdecimal()
            or not isinstance(command, list) or not command
            or not all(isinstance(part, str) for part in command)
            or command[0] != str(cache / 'packages/usr/bin/qemu-system-x86_64')
            or command.count('-drive') != 1 or command.count('-qmp') != 1):
        raise ValueError('Unrecognized or changed VM recovery process record')
    try:
        drive = command[command.index('-drive') + 1]
        qmp_argument = command[command.index('-qmp') + 1]
    except IndexError as error:
        raise ValueError('Incomplete VM recovery command') from error
    if (drive != 'file=' + str(disk) + ',format=raw,if=virtio,cache=writeback'
            or qmp_argument != 'unix:' + str(attempt / 'qmp.sock') + ',server=on,wait=off'):
        raise ValueError('VM recovery disk/QMP command differs from its process record')
    return record, disk


def record_stopped(record):
    # A reused PID is also refused. Recovery never guesses or terminates it.
    if process_identity(record['pid']) is not None:
        raise ValueError('Recorded PID is alive; stop/inspect it before recovery')
    path = unlinked_local(record['qmp_socket'])
    if not path.exists():
        return
    if not stat.S_ISSOCK(path.stat().st_mode):
        raise ValueError('Recorded QMP path is not a socket')
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(.3)
        try:
            client.connect(str(path))
        except (FileNotFoundError, ConnectionRefusedError):
            return
    raise ValueError('Recorded QMP socket is active; existing VM preserved')


def disk_unused(disk):
    """Reject a QEMU outside this cache that still names this private disk."""
    for entry in Path('/proc').iterdir():
        if not entry.name.isdecimal():
            continue
        try:
            arguments = (entry / 'cmdline').read_bytes().split(b'\0')
        except (FileNotFoundError, ProcessLookupError):
            continue
        if not arguments or not arguments[0].endswith(b'/qemu-system-x86_64'):
            continue
        for index, argument in enumerate(arguments[:-1]):
            if argument == b'-drive' and arguments[index + 1].startswith(b'file='):
                candidate = Path(os.fsdecode(arguments[index + 1][5:].split(b',', 1)[0]))
                if candidate.is_absolute() and candidate.resolve() == disk:
                    raise ValueError('Another QEMU still uses this mutable disk; existing VM preserved')


def resume_disk(cache, value, image):
    attempt = unlinked_local(value)
    _, disk = checked_record(cache, attempt)
    if disk.name != 'android.ext4':
        raise ValueError('Recovery requires the recorded private android.ext4')
    info = disk.stat()
    if (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
            or info.st_size != image['bytes'] or disk.samefile(cache / 'android.ext4')):
        raise ValueError('Recovery disk must be a private, unchanged-size image, separate from the base')
    # A resumed run can itself be resumed. Follow its receipts back to the first
    # private attempt, rather than assuming every run directory owns a disk.
    seen, current = set(), attempt
    for _ in range(64):
        if current in seen:
            raise ValueError('Cycle in VM disk recovery history')
        seen.add(current)
        owner, owner_disk = checked_record(cache, current)
        if owner_disk != disk:
            raise ValueError('VM recovery history names different mutable disks')
        record_stopped(owner)
        previous = owner.get('resumed_from')
        if current == disk.parent:
            if previous is not None:
                raise ValueError('Private disk owner must be an original copy attempt')
            break
        if not isinstance(previous, str):
            raise ValueError('Missing VM disk recovery history; specify its original owning attempt')
        current = unlinked_local(previous)
    else:
        raise ValueError('VM disk recovery history exceeds 64 attempts')
    disk_unused(disk)
    return disk, attempt


def process_identity(pid):
    try:
        # Fields after ')' start at field 3; starttime is field 22.
        tail = Path(f'/proc/{pid}/stat').read_text().rsplit(')', 1)[1].split()
        if tail[0] == 'Z':
            return None
        return tail[19]
    except (FileNotFoundError, ProcessLookupError):
        return None


def require_record(cache):
    record = json.loads((cache / 'active-vm.json').read_text())
    attempt = prepare.local(record['attempt'])
    if (record.get('schema') != 'experimental-software-android-vm.v1'
            or record['qmp_socket'] != str(attempt / 'qmp.sock')
            or record['adb_endpoint'] != '127.0.0.1:15555'
            or record['serial_endpoint'] != '127.0.0.1:15556'):
        raise ValueError('Unrecognized or changed VM process record')
    if process_identity(record['pid']) != record['process_starttime']:
        raise ValueError('Recorded VM is not running; refusing any other process')
    cmdline = Path(f"/proc/{record['pid']}/cmdline").read_bytes().split(b'\0')
    if (str(attempt / 'qmp.sock').encode() not in b'\0'.join(cmdline)
            or not cmdline[0].endswith(b'/qemu-system-x86_64')):
        raise ValueError('PID no longer identifies the recorded QEMU VM')
    return record


def ports_free():
    for port in PORTS:
        with socket.socket() as probe:
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            probe.bind(('127.0.0.1', port))


def wait_ports_free(timeout=5):
    deadline = time.monotonic() + timeout
    while True:
        try:
            ports_free()
            return
        except OSError:
            if time.monotonic() >= deadline:
                raise ValueError('ADB/serial loopback port is occupied; existing service preserved')
            time.sleep(.2)


def qmp(record, command):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
        client.settimeout(5)
        client.connect(record['qmp_socket'])
        stream = client.makefile('rwb', buffering=0)
        greeting = json.loads(stream.readline())
        for identity, operation in [('capabilities', 'qmp_capabilities'), ('command', command)]:
            stream.write(json.dumps({'execute': operation, 'id': identity}).encode() + b'\n')
            while True:
                line = stream.readline()
                if not line:
                    # quit may close its socket before delivering the return.
                    if operation == 'quit':
                        return {'greeting': greeting, 'result': 'connection closed after quit'}
                    raise ValueError('QMP closed before command reply')
                reply = json.loads(line)
                if reply.get('id') == identity:
                    if 'error' in reply:
                        raise ValueError('QMP rejected command: ' + str(reply['error']))
                    break
    return {'greeting': greeting, 'result': reply}


def stop(cache):
    record = require_record(cache)
    result = qmp(record, 'quit')
    deadline = time.monotonic() + 30
    while process_identity(record['pid']) == record['process_starttime']:
        if time.monotonic() >= deadline:
            raise ValueError('QMP quit sent but process has not exited; no forced kill performed')
        time.sleep(.2)
    wait_ports_free()
    report = {'schema': 'experimental-software-android-stop.v1',
              'pid': record['pid'], 'process_exited': True, 'ports_released': True, **result}
    prepare.write(prepare.local(record['attempt']) / 'stop.json', report)
    return report


def ownership_script(ownership, rootfs):
    groups, modes = defaultdict(list), defaultdict(list)
    for row in ownership.read_text().splitlines():
        uid, gid, mode, name = row.split('|', 3)
        prepare.archive_name(name)
        if not uid.isdecimal() or not gid.isdecimal() or not re.fullmatch('[0-7]{1,4}', mode):
            raise ValueError('Invalid archived ownership record')
        path = '/android/' + name
        groups[(uid, gid)].append(path)
        # The archive was extracted with links deferred. Inspect link identity
        # on the host, but never follow an Android absolute link there.
        source = rootfs.joinpath(*prepare.archive_name(name).parts)
        if not source.is_symlink():
            modes[mode].append(path)
    lines = ['#!/bin/sh', 'set -e']
    # chown can clear set-id bits: restore archived modes afterwards. Symlink
    # targets may be absolute in the guest; never chmod through those links.
    for (uid, gid), paths in sorted(groups.items()):
        for index in range(0, len(paths), 100):
            lines.append('chown -h ' + uid + ':' + gid + ' ' +
                         ' '.join(shlex.quote(path) for path in paths[index:index + 100]))
    for mode, paths in sorted(modes.items()):
        for index in range(0, len(paths), 100):
            lines.append('chmod ' + mode + ' ' +
                         ' '.join(shlex.quote(path) for path in paths[index:index + 100]))
    return '\n'.join(lines) + '\n'


def collect_modules(packages, version):
    source = packages / 'lib/modules' / version / 'kernel'
    available = {path.stem.replace('-', '_'): path for path in source.rglob('*.ko')}
    selected, ordered = set(), []

    def add(name):
        name = name.replace('-', '_')
        if name in selected or name not in available:
            return
        selected.add(name)
        path = available[name]
        match = re.search(b'(?:^|\x00)depends=([^\x00]*)', path.read_bytes())
        if match:
            for dependency in match[1].decode().split(','):
                if dependency:
                    add(dependency)
        ordered.append((name, path))

    for name in MODULES:
        add(name)
    for required in ('virtio_pci', 'virtio_blk', 'ext4', 'binder_linux', '9pnet_virtio', 'fuse'):
        if required not in selected:
            raise ValueError('Required guest kernel module missing: ' + required)
    return ordered


def cpio(guest, target):
    archive = bytearray()
    paths = [guest, *sorted(guest.rglob('*'))]
    for inode, path in enumerate(paths, 1):
        name = '.' if path == guest else path.relative_to(guest).as_posix()
        info = path.lstat()
        data = os.readlink(path).encode() if path.is_symlink() else path.read_bytes() if path.is_file() else b''
        fields = [inode, info.st_mode, 0, 0, 1, 0, len(data), 0, 0, 0, 0, len(name.encode()) + 1, 0]
        archive.extend(b'070701' + b''.join(f'{value:08x}'.encode() for value in fields) + name.encode() + b'\0')
        archive.extend(b'\0' * ((-len(archive)) % 4))
        archive.extend(data)
        archive.extend(b'\0' * ((-len(archive)) % 4))
    name = b'TRAILER!!!'
    fields = [len(paths) + 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, len(name) + 1, 0]
    archive.extend(b'070701' + b''.join(f'{value:08x}'.encode() for value in fields) + name + b'\0')
    archive.extend(b'\0' * ((-len(archive)) % 4))
    target.write_bytes(gzip.compress(archive, mtime=0))


def launch_android_script(timeout_multiplier=1):
    if type(timeout_multiplier) is not int or not 1 <= timeout_multiplier <= 20:
        raise ValueError('Timeout multiplier must be an integer from 1 to 20')
    return LAUNCH_ANDROID.replace('__HW_TIMEOUT_MULTIPLIER__', str(timeout_multiplier))


def make_initramfs(cache, lock, attempt, vcpus=1, timeout_multiplier=1):
    guest, packages = attempt / 'initramfs-root', cache / 'packages'
    for name in ('bin', 'proc', 'sys', 'dev', 'tmp', 'run', 'android', 'lib/modules'):
        (guest / name).mkdir(parents=True, exist_ok=True)
    busybox = packages / 'bin/busybox'
    shutil.copyfile(busybox, guest / 'bin/busybox')
    (guest / 'bin/busybox').chmod(0o755)
    # Only these static applets are needed; no host BusyBox is executed here.
    for name in ('sh', 'mount', 'umount', 'mkdir', 'insmod', 'uname', 'ls', 'chmod',
                 'chown', 'ip', 'grep', 'cat', 'seq', 'sleep', 'ps', 'head', 'cp', 'ln', 'unshare'):
        (guest / 'bin' / name).symlink_to('busybox')
    (guest / 'restore-ownership.sh').write_text(ownership_script(cache / 'ownership.tsv', cache / 'rootfs'))
    ordered = collect_modules(packages, lock['kernel_version'])
    loads = []
    for name, path in ordered:
        shutil.copyfile(path, guest / 'lib/modules' / path.name)
        argument = ' devices=binder,hwbinder,vndbinder' if name == 'binder_linux' else ''
        if name in ('virtio_pci', 'virtio_blk', 'ext4', 'binder_linux', '9pnet_virtio', 'fuse'):
            fallback = ' || { echo ROYALE_ANDROID_REQUIRED_MODULE_FAILED=' + name + '; exec sh; }'
        else:
            fallback = ' || echo ROYALE_ANDROID_OPTIONAL_MODULE_FAILED=' + name
        loads.append('insmod /lib/modules/' + path.name + argument + fallback)
    # Namespace/root transition follows the separately logged experiment. Its
    # presence does not certify Android boot; inspect actual properties/logs.
    (guest / 'launch-android.sh').write_text(launch_android_script(timeout_multiplier))
    suffix = INIT_SUFFIX.replace('__VM_CPUSET__', '0' if vcpus == 1 else '0-1')
    (guest / 'init').write_text(INIT_PREFIX + '\n'.join(loads) + suffix)
    (guest / 'init').chmod(0o755)
    initrd = attempt / 'android-initramfs.cpio.gz'
    cpio(guest, initrd)
    return initrd, [name for name, _ in ordered]


# Experimental namespace launch, not a chroot: Android's /proc/self/root must
# identify its own root filesystem. Android guest init has PID 1 in this namespace.
LAUNCH_ANDROID = '''#!/bin/sh
set -e
export PATH=/bin
mount --make-rprivate /
exec /bin/busybox switch_root /android /init qemu=1 androidboot.hardware=redroid androidboot.use_memfd=1 androidboot.redroid_gpu_mode=guest androidboot.redroid_width=360 androidboot.redroid_height=640 androidboot.redroid_fps=1 androidboot.selinux=permissive ro.hw_timeout_multiplier=__HW_TIMEOUT_MULTIPLIER__ ro.dalvik.vm.native.bridge=libndk_translation.so ro.enable.native.bridge.exec=1 ro.product.cpu.abilist=x86_64,arm64-v8a ro.product.cpu.abilist64=x86_64,arm64-v8a
'''

INIT_PREFIX = '''#!/bin/sh
export PATH=/bin
mount -t proc proc /proc
mount -t sysfs sysfs /sys
mount -t devtmpfs devtmpfs /dev
mkdir -p /dev/pts /sys/fs/cgroup
mount -t devpts devpts /dev/pts
mount -t tmpfs tmpfs /sys/fs/cgroup
'''

INIT_SUFFIX = '''
echo ROYALE_ANDROID_LINUX_READY
uname -a
ls -l /dev/binder /dev/hwbinder /dev/vndbinder /dev/vda
# Android vold aborts if it cannot enumerate the kernel FUSE connection tree.
# The fuse module creates this directory; a missing/mis-mounted tree is fatal.
if [ ! -d /sys/fs/fuse/connections ]; then
 echo ROYALE_ANDROID_FUSE_CONNECTIONS_MISSING
 exec sh
fi
mount -t fusectl none /sys/fs/fuse/connections
if [ $? != 0 ]; then echo ROYALE_ANDROID_FUSECTL_MOUNT_FAILED; exec sh; fi
echo ROYALE_ANDROID_FUSECTL_MOUNTED
mount -t ext4 /dev/vda /android
if [ $? != 0 ]; then echo ROYALE_ANDROID_ROOT_MOUNT_FAILED; exec sh; fi
echo ROYALE_ANDROID_ROOT_MOUNTED
sh /restore-ownership.sh
if [ $? != 0 ]; then echo ROYALE_ANDROID_OWNERSHIP_FAILED; exec sh; fi
echo ROYALE_ANDROID_ARCHIVE_OWNERSHIP_RESTORED
mkdir -p /android/dev /android/proc /android/sys /android/data/local/tmp/royale-staging
mount --bind /dev /android/dev
mount --bind /sys /android/sys
mount -t 9p -o trans=virtio,version=9p2000.L,ro royale_staging /android/data/local/tmp/royale-staging
echo ROYALE_ANDROID_STAGING_MOUNTED
ls -l /android/data/local/tmp/royale-staging
ip link set lo up
ip link set eth0 up
ip addr add 10.0.2.15/24 dev eth0
ensure_vm_route() {
 # Android netd replaces routing rules without a main-table fallback. Install
 # the rule before resolving the gateway, including on the first invocation.
 ip rule show | grep -Eq '^100:.*lookup main([[:space:]]|$)' ||
  ip rule add pref 100 lookup main || return 1
 ip route replace default via 10.0.2.2 dev eth0
}
if ! ensure_vm_route; then echo ROYALE_ANDROID_INITIAL_ROUTE_FAILED; exec sh; fi
mkdir -p /sys/fs/cgroup/cpuset
mount -t cgroup -o cpuset,noprefix cgroup /sys/fs/cgroup/cpuset
echo __VM_CPUSET__ > /sys/fs/cgroup/cpuset/cpus
echo 0 > /sys/fs/cgroup/cpuset/mems
for controller in cpu cpuacct memory devices freezer blkio pids; do
 mkdir -p /sys/fs/cgroup/$controller
 mount -t cgroup -o $controller cgroup /sys/fs/cgroup/$controller || true
done
chmod 0666 /dev/binder /dev/hwbinder /dev/vndbinder
mkdir -p /android/.vm-control
cp /bin/busybox /android/.vm-control/busybox
chmod 0700 /android/.vm-control
for applet in cat seq nsenter ps head sleep sh ip grep; do
 ln -sf busybox /android/.vm-control/$applet
done
export PATH=/android/.vm-control
# There is no unshare -n: Android and this Linux monitor share the guest netns.
# Repair only this guest's restricted host-forward route, never a host route.
while :; do
 ensure_vm_route || echo ROYALE_ANDROID_ROUTE_KEEPALIVE_FAILED
 sleep 10
done &
echo ROYALE_ANDROID_ROUTE_KEEPER_PID=$!
echo ROYALE_ANDROID_INIT_LAUNCH
/bin/unshare -f -p -m -u -i --propagation private -- /bin/sh /launch-android.sh &
launcher=$!
echo ANDROID_LAUNCHER_PID=$launcher
sleep 10
android_pid=$(cat /proc/$launcher/task/$launcher/children)
echo ANDROID_INIT_PID=$android_pid
nsenter -t $android_pid -m -p -r -- /system/bin/setprop service.adb.tcp.port 5555
nsenter -t $android_pid -m -p -r -- /system/bin/setprop persist.adb.tcp.port 5555
nsenter -t $android_pid -m -p -r -- /system/bin/setprop ctl.restart adbd
for round in $(seq 1 36); do
 echo ROYALE_ANDROID_BOOT_POLL=$round
 if [ -n "$android_pid" ]; then
  nsenter -t $android_pid -m -p -r -- /system/bin/getprop sys.boot_completed
  nsenter -t $android_pid -m -p -r -- /system/bin/getprop init.svc.adbd
  nsenter -t $android_pid -m -p -r -- /system/bin/getprop service.adb.tcp.port
 fi
 ps | head -n 12
 sleep 10
done
echo ROYALE_ANDROID_POLL_BUDGET_COMPLETE
exec sh
'''


def start(cache, lock, output, vcpus=1, timeout_multiplier=1, resume_attempt=None):
    if vcpus not in (1, 2):
        raise ValueError('Only 1 or 2 experimental guest vCPUs are supported')
    launch_android_script(timeout_multiplier)
    prepare.check_platform()
    cache = unlinked_local(cache)
    with start_lock(cache):
        return _start_locked(cache, lock, output, vcpus, timeout_multiplier, resume_attempt)


def _start_locked(cache, lock, output, vcpus, timeout_multiplier, resume_attempt):
    active = cache / 'active-vm.json'
    unlinked_local(active)
    if active.exists():
        record = json.loads(active.read_text())
        original, _ = checked_record(cache, record['attempt'])
        if original != record:
            raise ValueError('Active VM receipt differs from its saved process record')
        record_stopped(record)
    wait_ports_free()
    image = json.loads(unlinked_local(cache / 'image.json').read_text())
    if (image['lock_sha256'] != prepare.sha256(prepare.LOCK)
            or image['source_ownership_sha256'] != prepare.sha256(cache / 'ownership.tsv')):
        raise ValueError('Image receipt and current lock/ownership differ')
    disk, resumed_from = None, None
    if resume_attempt is None:
        free = prepare.check_space()
    else:
        disk, resumed_from = resume_disk(cache, resume_attempt, image)
        unlinked_local(output)
        free = shutil.disk_usage(ROOT).free
        if free < RESUME_MIN_FREE:
            raise ValueError('At least 1 GiB repository filesystem free space is required for recovery')
    STAGING.mkdir(parents=True, exist_ok=True)
    if STAGING.resolve() != STAGING or any(path.is_symlink() for path in STAGING.rglob('*')):
        raise ValueError('Staging must contain no symlinks; only this input directory is shared')
    attempt = prepare.new_attempt(output, 'vm')
    if len(str(attempt / 'qmp.sock').encode()) >= 104:
        raise ValueError('QMP socket path is too long; use a shorter repository-local --output')
    initrd, modules = make_initramfs(cache, lock, attempt, vcpus, timeout_multiplier)
    if disk is None:
        disk = attempt / 'android.ext4'
        with (attempt / 'copy-image.log').open('w') as log:
            subprocess.run(['cp', '--reflink=auto', '--sparse=always', '--',
                            str(cache / 'android.ext4'), str(disk)], check=True,
                           stdout=log, stderr=subprocess.STDOUT)
    packages = cache / 'packages'
    qmp_path = attempt / 'qmp.sock'
    command = [str(packages / 'usr/bin/qemu-system-x86_64'), '-no-user-config', '-nodefaults',
               '-L', str(packages / 'usr/share/qemu'), '-bios',
               str(packages / 'usr/share/seabios/bios-256k.bin'),
               '-machine', 'pc', '-cpu', 'max', '-accel',
               'tcg,thread=' + ('single' if vcpus == 1 else 'multi'),
               '-smp', str(vcpus), '-m', '1536', '-display', 'none', '-chardev',
               'socket,id=serial0,host=127.0.0.1,port=15556,server=on,wait=off,logfile=' + str(attempt / 'serial.log'),
               '-serial', 'chardev:serial0', '-monitor', 'none', '-qmp',
               'unix:' + str(qmp_path) + ',server=on,wait=off', '-no-reboot',
               '-kernel', str(packages / 'boot' / ('vmlinuz-' + lock['kernel_version'])),
               '-initrd', str(initrd), '-append',
               'console=ttyS0 rdinit=/init panic=-1 lsm=capability systemd.unified_cgroup_hierarchy=0',
               '-drive', 'file=' + str(disk) + ',format=raw,if=virtio,cache=writeback',
               '-netdev', 'user,id=net0,restrict=on,hostfwd=tcp:127.0.0.1:15555-:5555',
               '-device', 'virtio-net-pci,netdev=net0', '-fsdev',
               'local,id=staging,path=' + str(STAGING) + ',security_model=none,readonly=on',
               '-device', 'virtio-9p-pci,fsdev=staging,mount_tag=royale_staging']
    env = dict(os.environ)
    env['LD_LIBRARY_PATH'] = str(packages / 'usr/lib/x86_64-linux-gnu') + ':' + str(packages / 'lib/x86_64-linux-gnu')
    env['TMPDIR'] = str(attempt)
    with (attempt / 'qemu.log').open('w') as log:
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
    identity = process_identity(process.pid)
    record = {'schema': 'experimental-software-android-vm.v1', 'experimental': True,
              'pid': process.pid, 'process_starttime': identity, 'command': command,
              'attempt': str(attempt.relative_to(ROOT)), 'qmp_socket': str(qmp_path),
              'memory_mib': 1536, 'vcpus': vcpus, 'accelerator': 'tcg',
              'timeout_multiplier': timeout_multiplier,
              'mutable_disk': str(disk.relative_to(ROOT)),
              'disk_owner_attempt': str(disk.parent.relative_to(ROOT)),
              'resumed_from': str(resumed_from.relative_to(ROOT)) if resumed_from else None,
              'disk_copied_from_base': resumed_from is None,
              'free_bytes_before_start': free,
              'required_free_bytes': RESUME_MIN_FREE if resumed_from else prepare.MIN_FREE,
              'android_watchdog_timeout_ms_expected': 60000 * timeout_multiplier,
              'android_watchdog_check_interval_ms_expected': 30000 * timeout_multiplier,
              'adb_endpoint': '127.0.0.1:15555', 'serial_endpoint': '127.0.0.1:15556',
              'modules': modules, 'android_boot_verified': False,
              'native_game_execution_verified': False, 'native_rpc_calls': 0}
    prepare.write(attempt / 'process.json', record)
    prepare.write(active, record)
    return record


def console(record, text):
    with socket.create_connection(('127.0.0.1', 15556), timeout=5) as client:
        client.settimeout(.3)
        client.sendall((text + '\n').encode())
        collected = bytearray()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            try:
                block = client.recv(65536)
                if not block:
                    break
                collected.extend(block)
                if len(collected) > 1024 * 1024:
                    break
            except socket.timeout:
                pass
    result = collected.decode(errors='replace')
    attempt = prepare.new_attempt(prepare.local(record['attempt']) / 'console', 'serial')
    (attempt / 'command.txt').write_text(text + '\n')
    (attempt / 'response.txt').write_text(result)
    return {'command_log': str(attempt.relative_to(ROOT)), 'response': result}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['start', 'status', 'stop', 'console'])
    parser.add_argument('--cache', default='.cache/native-tools/software-android')
    parser.add_argument('--output', default='outputs/cloud/android-vm')
    parser.add_argument('--vcpus', type=int, choices=(1, 2), default=1,
                        help='Guest CPUs for start; default 1, choose 2 only after load/Watchdog review')
    parser.add_argument('--timeout-multiplier', type=int, choices=range(1, 21), default=1,
                        help='Next cold boot ro.hw_timeout_multiplier; default 1, slow TCG may use 10')
    parser.add_argument('--resume-attempt',
                        help='Start only: reuse a stopped recorded private disk, mutating it in place; '
                             'new boot logs and evidence are written to a new attempt')
    parser.add_argument('--text', help='Guest serial command only; logged inside the repository')
    args = parser.parse_args()
    if args.resume_attempt is not None and args.command != 'start':
        parser.error('--resume-attempt is valid only for start')
    cache = prepare.local(args.cache)
    try:
        if args.command == 'start':
            result = start(cache, prepare.load_lock(), args.output, args.vcpus,
                           args.timeout_multiplier, args.resume_attempt)
        elif args.command == 'stop':
            result = stop(cache)
        elif args.command == 'status':
            record = require_record(cache)
            result = {'record': record, 'qmp': qmp(record, 'query-status')}
        else:
            if args.text is None:
                parser.error('--text required for console')
            result = console(require_record(cache), args.text)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as error:
        print(json.dumps({'experimental': True, 'success': False,
                          'error': type(error).__name__ + ': ' + str(error),
                          'native_game_execution_verified': False}, ensure_ascii=False, indent=2))
        raise SystemExit(1)


if __name__ == '__main__':
    main()

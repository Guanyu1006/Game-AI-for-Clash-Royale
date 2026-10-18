"""Prepare/start project-local Linux guests without changing host drivers.

The guest is a runtime probe, not an Android or native-battle acceptance result.
"""
import argparse
import io
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
REL=Path('.cache/phase2/secondary_runtime')
BASE=ROOT/REL
OUT=ROOT/'outputs/phase2/secondary'
VMRUN=Path('C:/Program Files (x86)/VMware/VMware Workstation/vmrun.exe')


def execute(args,timeout=30,**kwargs):
    t=time.perf_counter()
    try:
        run=subprocess.run([str(x) for x in args],cwd=ROOT,capture_output=True,timeout=timeout,**kwargs)
        return {'command':[str(x) for x in args],'exit_code':run.returncode,'stdout':run.stdout.decode('utf-8',errors='replace'),
                'stderr':run.stderr.decode('utf-8',errors='replace'),'wall_seconds':time.perf_counter()-t}
    except Exception as exc:return {'command':[str(x) for x in args],'error':repr(exc),'wall_seconds':time.perf_counter()-t}


def seed(folder,network):
    import pycdlib
    key=BASE/'guest-key'
    if not key.exists():
        result=execute(['C:/Windows/System32/OpenSSH/ssh-keygen.exe','-t','ed25519','-f',key,'-N','','-C','phase2-local-guest'])
        assert result.get('exit_code')==0,result
    pub=key.with_suffix('.pub').read_text().strip()
    userdata=f'''#cloud-config
users:
  - name: researcher
    groups: [sudo]
    sudo: ALL=(ALL) NOPASSWD:ALL
    shell: /bin/bash
    lock_passwd: true
    ssh_authorized_keys:
      - {pub}
ssh_pwauth: false
disable_root: true
package_update: false
package_upgrade: false
growpart:
  mode: 'off'
runcmd:
  - [sh, -c, 'echo PHASE2_GUEST_READY; uname -a; getconf GNU_LIBC_VERSION; ls -l /dev/binder* /dev/ashmem 2>/dev/null || true; grep -E "CONFIG_ANDROID_BINDER|CONFIG_ASHMEM" /boot/config-$(uname -r) || true']
'''
    # Increment the local cloud-init identity when the seed contract changes so
    # a previously booted guest applies the offline-safe network declaration.
    metadata='instance-id: phase2-secondary-linux-003\nlocal-hostname: phase2-linux\n'
    if network=='vmware-hostonly':
        net='''version: 2
ethernets:
  eth0:
    match:
      macaddress: "00:50:56:30:15:01"
    set-name: eth0
    addresses: [192.168.221.200/24]
    dhcp4: false
    dhcp6: false
'''
    else:net='''version: 2
ethernets:
  all:
    match:
      name: "e*"
    optional: true
    addresses: [10.0.2.15/24]
    routes:
      - to: default
        via: 10.0.2.2
    nameservers:
      addresses: [10.0.2.3]
    dhcp4: false
    dhcp6: false
'''
    folder.mkdir(parents=True,exist_ok=True)
    iso=pycdlib.PyCdlib();iso.new(interchange_level=3,joliet=3,rock_ridge='1.09',vol_ident='cidata')
    for name,data in [('user-data',userdata),('meta-data',metadata),('network-config',net)]:
        blob=data.encode();(folder/name).write_bytes(blob)
        iso.add_fp(io.BytesIO(blob),len(blob),iso_path='/'+name.replace('-','_').upper()+';1',rr_name=name,joliet_path='/'+name)
    iso.write(str(folder/'seed.iso'));iso.close()


def prepare():
    OUT.mkdir(parents=True,exist_ok=True)
    vmware=BASE/'vmware';seed(vmware,'vmware-hostonly')
    convert=None
    if not (vmware/'disk.vmdk').exists():
        convert=execute([REL/'qemu/qemu-img.exe','convert','-f','qcow2','-O','vmdk','-o','subformat=monolithicSparse,adapter_type=lsilogic',
                         REL/'ubuntu-24.04-minimal-cloudimg-amd64.img',REL/'vmware/disk.vmdk'],timeout=60)
        assert convert.get('exit_code')==0,convert
    config=''' .encoding = "UTF-8"
config.version = "8"
virtualHW.version = "20"
displayName = "Clash Phase2 Linux Runtime"
guestOS = "ubuntu-64"
firmware = "efi"
memsize = "2048"
numvcpus = "2"
cpuid.coresPerSocket = "2"
scsi0.present = "TRUE"
scsi0.virtualDev = "lsilogic"
scsi0:0.present = "TRUE"
scsi0:0.fileName = "disk.vmdk"
ide1:0.present = "TRUE"
ide1:0.deviceType = "cdrom-image"
ide1:0.fileName = "seed.iso"
ethernet0.present = "TRUE"
ethernet0.connectionType = "hostonly"
ethernet0.virtualDev = "e1000e"
ethernet0.addressType = "static"
ethernet0.address = "00:50:56:30:15:01"
serial0.present = "TRUE"
serial0.fileType = "file"
serial0.fileName = "serial.log"
serial0.startConnected = "TRUE"
usb.present = "FALSE"
sound.present = "FALSE"
tools.upgrade.policy = "manual"
msg.autoAnswer = "TRUE"
'''.lstrip()
    # msg.autoAnswer answers only VM copy/move device prompts; never EULA/license UI.
    (vmware/'linux.vmx').write_text(config,encoding='utf-8')
    qemu=BASE/'linux';seed(qemu,'qemu')
    if not (qemu/'disk.qcow2').exists():
        result=execute([REL/'qemu/qemu-img.exe','create','-f','qcow2','-F','qcow2','-b','../ubuntu-24.04-minimal-cloudimg-amd64.img',REL/'linux/disk.qcow2'])
        assert result.get('exit_code')==0,result
    if not (qemu/'uefi-vars.fd').exists():shutil.copyfile(BASE/'qemu/share/edk2-i386-vars.fd',qemu/'uefi-vars.fd')
    report={'prepared':True,'vmware_disk_conversion':convert,'qemu_network':{'preparation':'NAT with localhost-only SSH forwarding','isolated':'restrict=on with localhost-only SSH forwarding'},
            'vmware_network':'existing VMnet1 host-only, guest192.168.221.200, no default route/DNS in seed',
            'key':'local .cache private key excluded from public bundle','guest_is_android':False}
    (OUT/'linux_vm_prepare.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps(report,indent=2))


def vmware_start():
    result=execute([VMRUN,'-T','ws','start',BASE/'vmware/linux.vmx','nogui'],timeout=40)
    (OUT/'vmware_start.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))


def qemu_start(accel,isolated):
    try:
        with socket.create_connection(('127.0.0.1',22226),timeout=1):pass
    except OSError:pass
    else:raise RuntimeError('An existing guest owns QMP port22226; stop it and verify shutdown before starting another guest.')
    folder=BASE/'linux';log=folder/f'qemu-{accel}-stderr.log'
    args=[REL/'qemu/qemu-system-x86_64.exe','-L',REL/'qemu/share','-name','phase2-linux','-machine','q35',
          '-accel',accel,'-cpu','max' if accel=='tcg' else 'qemu64,+ssse3,+sse4.1,+sse4.2,+popcnt,+cx16','-smp','4','-m','4096',
          '-smbios','type=1,serial=ds=nocloud',
          '-drive',f'if=pflash,format=raw,readonly=on,file={REL.as_posix()}/qemu/share/edk2-x86_64-code.fd',
          '-drive',f'if=pflash,format=raw,file={REL.as_posix()}/linux/uefi-vars.fd',
          '-drive',f'file={REL.as_posix()}/linux/disk.qcow2,format=qcow2,if=virtio',
          '-drive',f'file={REL.as_posix()}/linux/seed.iso,media=cdrom,format=raw,readonly=on',
          '-device','virtio-net-pci,netdev=net0','-netdev',f'user,id=net0,restrict={"on" if isolated else "off"},hostfwd=tcp:127.0.0.1:22225-:22,hostfwd=tcp:127.0.0.1:55555-:5555',
          '-display','none','-serial',f'file:{REL.as_posix()}/linux/serial.log','-monitor','none',
          '-qmp','tcp:127.0.0.1:22226,server=on,wait=off','-no-reboot']
    with log.open('wb') as handle:
        proc=subprocess.Popen([str(x) for x in args],cwd=ROOT,stdin=subprocess.DEVNULL,stdout=handle,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    time.sleep(1)
    if proc.poll() is not None:
        result={'pid':proc.pid,'command':[str(x) for x in args],'exit_code':proc.returncode,'log':str(log.relative_to(ROOT))}
        (OUT/'qemu_vm_start_failed.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        raise RuntimeError(result)
    result={'pid':proc.pid,'command':[str(x) for x in args],'accel':accel,'isolated':isolated,'log':str(log.relative_to(ROOT))}
    (OUT/'qemu_vm_process.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))


def qemu_stop():
    with socket.create_connection(('127.0.0.1',22226),timeout=5) as sock:
        file=sock.makefile('rwb',buffering=0);greeting=file.readline()
        file.write(b'{"execute":"qmp_capabilities"}\n');reply=file.readline()
        file.write(b'{"execute":"quit"}\n');last=file.readline()
    print(json.dumps({'greeting':greeting.decode(),'capabilities':reply.decode(),'quit':last.decode()}))


def main():
    p=argparse.ArgumentParser();p.add_argument('action',choices=['prepare','vmware-start','qemu-start','qemu-stop']);p.add_argument('--accel',choices=['whpx','tcg'],default='tcg');p.add_argument('--isolated',action='store_true');args=p.parse_args()
    if args.action=='prepare':prepare()
    elif args.action=='vmware-start':vmware_start()
    elif args.action=='qemu-stop':qemu_stop()
    else:qemu_start(args.accel,args.isolated)


if __name__=='__main__':main()

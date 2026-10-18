"""Collect actual Linux capabilities and isolated-network evidence through local SSH."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
BASE=ROOT/'.cache/phase2/secondary_runtime'
OUT=ROOT/'outputs/phase2/secondary'


def main():
    p=argparse.ArgumentParser();p.add_argument('--label',default='isolated');p.add_argument('--command');args=p.parse_args()
    guest='''import json,os,socket,subprocess,platform,time
def run(cmd):
 p=subprocess.run(cmd,shell=True,capture_output=True,text=True);return {'command':cmd,'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr}
net=[]
for host in ['1.1.1.1','8.8.8.8']:
 t=time.perf_counter()
 try:
  s=socket.create_connection((host,443),timeout=3);s.close();status='connected'
 except Exception as exc:status=type(exc).__name__+': '+str(exc)
 net.append({'host':host,'port':443,'result':status,'seconds':time.perf_counter()-t})
d={'uname':platform.uname()._asdict(),'uid':os.getuid(),'glibc':platform.libc_ver(),'network':net,'checks':[run(c) for c in ['id','ip -4 addr','ip route','ls -l /dev/binder* /dev/ashmem 2>/dev/null','grep -E "CONFIG_ANDROID_BINDER|CONFIG_ASHMEM" /boot/config-$(uname -r)','find /lib/modules/$(uname -r) -iname "*binder*" -o -iname "*ashmem*"','command -v docker; command -v podman; command -v adb; command -v clang; command -v qemu-aarch64','df -B1 /; free -m','cloud-init status --long']]}
print(json.dumps(d))
'''
    command=args.command or "python3 - <<'PY'\n"+guest+"\nPY"
    ssh=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(BASE/'guest-key'),'-p','22225','-o','BatchMode=yes','-o','ConnectTimeout=10',
         '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','researcher@127.0.0.1',command]
    t=time.perf_counter();run=subprocess.run(ssh,cwd=ROOT,capture_output=True,timeout=60)
    report={'label':args.label,'ssh_exit_code':run.returncode,'wall_seconds':time.perf_counter()-t,
            'stdout':run.stdout.decode('utf-8',errors='replace'),'stderr':run.stderr.decode('utf-8',errors='replace')}
    try:report['guest']=json.loads(report['stdout'])
    except ValueError:pass
    (OUT/f'linux_guest_{args.label}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=True,indent=2))


if __name__=='__main__':main()

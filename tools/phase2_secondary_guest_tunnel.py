"""Forward the local Android probe through loopback-only project SSH."""
import argparse,json,socket,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
def main():
 p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=26789);a=p.parse_args()
 if not 1024<=a.port<=65535:raise ValueError('Unprivileged TCP port required')
 args=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),'-p','22225','-o','BatchMode=yes',
 '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','-o','ExitOnForwardFailure=yes',
 '-N','-L',f'127.0.0.1:{a.port}:127.0.0.1:{a.port}','researcher@127.0.0.1']
 with (OUT/f'tunnel_{a.port}.log').open('ab') as log:
  child=subprocess.Popen(args,cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 time.sleep(1)
 report={'pid':child.pid,'port':a.port,'command':args,'running':child.poll() is None,'loopback_only':True}
 (OUT/f'tunnel_{a.port}.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
 if child.poll() is not None:raise SystemExit(1)
if __name__=='__main__':main()

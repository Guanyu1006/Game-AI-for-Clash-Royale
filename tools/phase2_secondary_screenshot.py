"""Capture actual native Android framebuffer, with no redraw or transformation."""
import argparse,hashlib,json,subprocess,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
def main():
 p=argparse.ArgumentParser();p.add_argument('--label',default='native_first_frame');a=p.parse_args()
 if not a.label.replace('_','').replace('-','').isalnum():raise ValueError('ASCII label required')
 args=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),'-p','22225','-o','BatchMode=yes',
 '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','researcher@127.0.0.1',
 'adb -s 127.0.0.1:5555 exec-out screencap -p']
 start=time.perf_counter();run=subprocess.run(args,cwd=ROOT,capture_output=True,timeout=45)
 path=OUT/(a.label+'.png');valid=run.returncode==0 and run.stdout.startswith(b'\x89PNG\r\n\x1a\n')
 if valid:path.write_bytes(run.stdout)
 report={'path':str(path.relative_to(ROOT)),'exit_code':run.returncode,'valid_png_signature':valid,'bytes':len(run.stdout),
 'sha256':hashlib.sha256(run.stdout).hexdigest(),'wall_seconds':time.perf_counter()-start,'stderr':run.stderr.decode(errors='replace'),
 'provenance':'Android adb exec-out screencap -p; unmodified native framebuffer; not a simulator redraw'}
 (OUT/(a.label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
 if not valid:raise SystemExit(1)
if __name__=='__main__':main()

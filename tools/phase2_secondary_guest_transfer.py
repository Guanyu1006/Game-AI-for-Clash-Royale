"""Copy a project file into the project-backed guest, retaining both SHA256s."""
import argparse
import hashlib
import json
from pathlib import Path
import shlex
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'

def main():
    p=argparse.ArgumentParser();p.add_argument('source');p.add_argument('--name',required=True);p.add_argument('--timeout',type=int,default=600);args=p.parse_args()
    source=(ROOT/args.source).resolve();source.relative_to(ROOT)
    if not args.name or '/' in args.name or '\\' in args.name or args.name in ('.','..'):raise ValueError('A basename is required')
    sha=hashlib.file_digest(source.open('rb'),'sha256').hexdigest()
    target='/home/researcher/'+args.name
    ssh=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),'-p','22225','-o','BatchMode=yes',
         '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','researcher@127.0.0.1',
         'cat > '+shlex.quote(target)+' && sha256sum '+shlex.quote(target)]
    t=time.perf_counter()
    with source.open('rb') as handle:run=subprocess.run(ssh,stdin=handle,capture_output=True,cwd=ROOT,timeout=args.timeout)
    result={'source':str(source.relative_to(ROOT)),'guest_path':target,'source_sha256':sha,'source_bytes':source.stat().st_size,
            'exit_code':run.returncode,'wall_seconds':time.perf_counter()-t,'stdout':run.stdout.decode(errors='replace'),'stderr':run.stderr.decode(errors='replace')}
    result['verified']=run.returncode==0 and result['stdout'].split()[0]==sha
    (OUT/('transfer_'+args.name+'.json')).write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    if not result['verified']:raise SystemExit(1)

if __name__=='__main__':main()

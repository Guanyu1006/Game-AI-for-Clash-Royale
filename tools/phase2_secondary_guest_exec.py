"""Run a saved project script in the disposable project-disk Linux guest via SSH."""
import argparse
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    p=argparse.ArgumentParser();p.add_argument('script');p.add_argument('--label',required=True);p.add_argument('--timeout',type=int,default=600);args=p.parse_args()
    script=(ROOT/args.script).resolve();assert script.is_relative_to(ROOT)
    out=ROOT/'outputs/phase2/secondary';out.mkdir(parents=True,exist_ok=True)
    ssh=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),'-p','22225',
         '-o','BatchMode=yes','-o','ConnectTimeout=10','-o','StrictHostKeyChecking=no',
         '-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','researcher@127.0.0.1','bash -s']
    t=time.perf_counter()
    with (out/(args.label+'.stdout.log')).open('wb') as stdout,(out/(args.label+'.stderr.log')).open('wb') as stderr:
        try:
            run=subprocess.run(ssh,input=script.read_bytes(),stdout=stdout,stderr=stderr,cwd=ROOT,timeout=args.timeout)
            report={'exit_code':run.returncode}
        except Exception as exc:report={'error':repr(exc)}
    report.update(script=str(script.relative_to(ROOT)),timeout=args.timeout,wall_seconds=time.perf_counter()-t,
                  stdout='outputs/phase2/secondary/'+args.label+'.stdout.log',stderr='outputs/phase2/secondary/'+args.label+'.stderr.log')
    (out/(args.label+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    if report.get('exit_code')!=0:raise SystemExit(1)


if __name__=='__main__':main()

"""Verify the completed platform in .venv-repro; preserve exact command/exit evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_candidates/repro'
PY=ROOT/'.venv-repro/Scripts/python.exe'

def command(args, label, env):
    start=time.perf_counter()
    result=subprocess.run([str(PY),*args],cwd=ROOT,env=env,capture_output=True,timeout=180)
    out=OUT/(label+'.stdout.log');err=OUT/(label+'.stderr.log')
    out.write_bytes(result.stdout);err.write_bytes(result.stderr)
    return {'label':label,'command':[str(PY),*args],'returncode':result.returncode,
            'wall_seconds':time.perf_counter()-start,'stdout':str(out.relative_to(ROOT)),
            'stderr':str(err.relative_to(ROOT))}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--with-checkpoints',action='store_true');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy()
    env.update(TEMP=str(ROOT/'.tmp'),TMP=str(ROOT/'.tmp'),PIP_CACHE_DIR=str(ROOT/'.cache/pip'),
               PYTHONIOENCODING='utf-8',PYTHONDONTWRITEBYTECODE='1')
    todo=[(['-m','pip','check'],'pip_check'),(['-m','royale_lab','info'],'info')]
    if args.with_checkpoints:
        todo += [(['-m','royale_lab','demo','--policies','learned','ppo','--max-ticks','600'],'learned_vs_ppo'),
                 (['-m','unittest','discover','-s','tests_platform','-v'],'tests_platform')]
    rows=[command(a,label,env) for a,label in todo]
    manifest={'date':'2026-10-14','environment':'.venv-repro','python_base':'system Python 3.14.5; native .pyd reused from current build',
              'all_environment_files_and_caches':'workspace-local',
              'requirements_snapshot':'outputs/audit_candidates/requirements-repro.txt',
              'installation_logs':['outputs/audit_candidates/repro_install.log','outputs/audit_candidates/repro_project_install.log'],
              'checks':rows,'all_passed':all(x['returncode']==0 for x in rows),
              'checkpoint_validation_included':args.with_checkpoints}
    if args.with_checkpoints:
        manifest['checkpoints']=[{'path':str(p.relative_to(ROOT)), 'bytes':p.stat().st_size,
          'sha256':hashlib.sha256(p.read_bytes()).hexdigest()} for p in [ROOT/'models/platform/learned.pt',ROOT/'models/platform/ppo.pt']]
    (OUT/'manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    print(json.dumps(manifest,indent=2))
    if not manifest['all_passed']:raise SystemExit(1)

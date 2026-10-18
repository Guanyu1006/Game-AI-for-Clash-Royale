"""Reproducible acceptance run using the current interpreter, all outputs local."""
from pathlib import Path
import json
import os
import subprocess
import sys
ROOT=Path(__file__).resolve().parent.parent

def main():
    os.chdir(ROOT)
    (ROOT/'outputs').mkdir(exist_ok=True)
    (ROOT/'.tmp').mkdir(exist_ok=True)
    env={**os.environ,'PYTHONUTF8':'1','TEMP':str(ROOT/'.tmp'),'TMP':str(ROOT/'.tmp')}
    commands=[
        [sys.executable,'-m','unittest','discover','-s','tests','-v'],
        [sys.executable,'tools/audit_card_costs.py'],
        [sys.executable,'-m','royale_ai','simulate','--seed','7'],
        [sys.executable,'-m','royale_ai','vision-demo'],
        [sys.executable,'-m','royale_ai','evaluate','--games','12','--seed','10'],
        [sys.executable,'-m','royale_ai','simulate','--seed','8','--log','outputs/teacher_8.jsonl','--html','outputs/teacher_8.html'],
        [sys.executable,'-m','royale_ai','simulate','--seed','9','--log','outputs/teacher_9.jsonl','--html','outputs/teacher_9.html'],
        [sys.executable,'-m','royale_ai','train','outputs/demo.jsonl','outputs/teacher_8.jsonl','outputs/teacher_9.jsonl'],
        ['node','tools/check_viewer.cjs'],
    ]
    report=[]
    for i,command in enumerate(commands):
        result=subprocess.run(command,cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',timeout=120)
        (ROOT/'outputs'/f'verify_{i}.txt').write_text(result.stdout+result.stderr,encoding='utf-8')
        report.append({'command':command,'exit_code':result.returncode,'log':f'outputs/verify_{i}.txt'})
        print(f"{i+1}/{len(commands)} exit={result.returncode}")
        if result.returncode:break
    (ROOT/'outputs'/'verification.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    if any(r['exit_code'] for r in report):sys.exit(1)

if __name__=='__main__':main()

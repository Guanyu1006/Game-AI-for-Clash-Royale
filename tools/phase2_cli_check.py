"""Actually run the documented read-only command entry points without RPC."""
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.versioning import file_hash
from royale_phase2.dataset import write_json


def main():
    configure();folder=local('outputs/phase2/cli');folder.mkdir(parents=True,exist_ok=True)
    shell=shutil.which('pwsh') or shutil.which('powershell')
    if not shell:raise RuntimeError('PowerShell required to verify phase2.ps1')
    commands=[['doctor'],['verify-version'],['verify-data'],['view','plan'],['offline-smoke']]
    commands += [[c,'--help'] for c in ('replay','batch-replay','repeatability','export','train-bc',
                                      'evaluate-model','evaluate','view','cohort','benchmark','offline-smoke','acceptance')]
    records=[]
    for index,args in enumerate(commands):
        command=[shell,'-NoProfile','-File',str(ROOT/'phase2.ps1'),*args]
        start=time.monotonic();result=subprocess.run(command,cwd=ROOT,capture_output=True,encoding='utf-8',errors='replace',timeout=120)
        path=folder/f'{index:02d}-{args[0]}.log';path.write_text(result.stdout+result.stderr,encoding='utf-8')
        records.append({'command':command,'returncode':result.returncode,'wall_seconds':time.monotonic()-start,
                        'log':{'path':path.relative_to(ROOT).as_posix(),'sha256':file_hash(path)}})
        print(json.dumps({'command':args,'returncode':result.returncode}),flush=True)
    report={'schema':'royale-phase2-cli-evidence.v1','checks':records,'native_rpc_operations':0,
            'scope':'Real PowerShell entry routing, source/asset verification, viewer/offline plans and command help. No battle or model success inferred.'}
    write_json(folder/'report.json',report)
    if any(r['returncode'] for r in records):raise SystemExit(1)


if __name__=='__main__':main()

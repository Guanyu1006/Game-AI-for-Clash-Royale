"""Read-only host inventory plus phase-two project-local baseline evidence."""
import hashlib
import json
import platform
from pathlib import Path
import shutil
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,target

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()

def main():
    configure()
    manifest=ROOT/'outputs/platform/acceptance.json'
    previous=json.loads(manifest.read_text(encoding='utf-8-sig'))
    checks=[]
    for row in previous['artifacts']:
        p=ROOT/row['path']
        checks.append({'path':row['path'],'exists':p.is_file(),
                       'matches_delivery_hash':p.is_file() and digest(p)==row['sha256']})
    snapshot=target('outputs/phase2/baseline_snapshot.json')
    if not snapshot.exists():
        snapshot.write_text(json.dumps({'created_unix':time.time(),'source':'outputs/platform/acceptance.json',
            'source_sha256':digest(manifest),'artifacts':previous['artifacts']},ensure_ascii=False,indent=2),encoding='utf-8')
    usage=shutil.disk_usage(ROOT)
    tools={n:shutil.which(n) for n in ('python','git','adb','wsl','docker','qemu-system-x86_64','qemu-system-aarch64','rustc','cargo','cl','java')}
    result={'host':platform.platform(),'machine':platform.machine(),'python':sys.version,'executable':sys.executable,
            'root':str(ROOT),'disk':{'free_bytes':usage.free,'total_bytes':usage.total},'tools':tools,
            'baseline_artifact_count':len(checks),'baseline_all_unchanged':all(c['matches_delivery_hash'] for c in checks),
            'baseline_mismatches':[c for c in checks if not c['matches_delivery_hash']],
            'native_phase2_verified':False,'scope':'Inventory only; no native battle or offline acceptance claim.'}
    target('outputs/phase2/doctor.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

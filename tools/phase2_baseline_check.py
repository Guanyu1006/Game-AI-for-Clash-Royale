"""Run the old regression suites while preserving their original evidence files."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,target

def main():
    configure()
    # Existing tests regenerate these two outputs. Save their bytes explicitly,
    # rather than modifying the original tests or overwriting old delivery data.
    mutable=('data/platform/card_catalog.json','outputs/platform/numeric_integration.json')
    originals={name:(ROOT/name).read_bytes() for name in mutable}
    # Persist before launching tests so interruption cannot lose originals.
    for name,blob in originals.items():
        backup=target('outputs/phase2/baseline_regression/originals/'+name)
        if backup.exists() and backup.read_bytes()!=blob:
            raise RuntimeError(f'Baseline differs from saved original: {name}; inspect before running tests')
        if not backup.exists():
            with backup.open('xb') as stream:
                stream.write(blob)
    archive=target('outputs/phase2/baseline_regression/original-files.json')
    archive.write_text(json.dumps({name:hashlib.sha256(blob).hexdigest() for name,blob in originals.items()},indent=2),encoding='utf-8')
    results=[];restored=[]
    try:
        for suite in ('tests','tests_platform'):
            # Keep first-stage tests in their original project environment.
            baseline_python=ROOT/'.venv/Scripts/python.exe'
            command=[str(baseline_python if baseline_python.is_file() else Path(sys.executable)),'-m','unittest','discover','-s',suite,'-v']
            proc=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONUTF8='1'),capture_output=True,text=True,encoding='utf-8',timeout=180)
            logfile=target(f'outputs/phase2/baseline_regression/{suite}.log')
            logfile.write_text(proc.stdout+proc.stderr,encoding='utf-8')
            results.append({'suite':suite,'command':command,'returncode':proc.returncode,'log':str(logfile.relative_to(ROOT))})
    finally:
        for name,blob in originals.items():
            path=ROOT/name
            if not path.exists() or path.read_bytes()!=blob:
                # The prior file is retained separately for transparent provenance.
                if path.exists():
                    target('outputs/phase2/baseline_regression/regenerated/'+Path(name).name).write_bytes(path.read_bytes())
                path.write_bytes(blob);restored.append(name)
    report={'all_baseline_suites_passed':len(results)==2 and all(r['returncode']==0 for r in results),
            'results':results,'restored_old_evidence_files':restored,
            'original_evidence_preserved':all((ROOT/name).read_bytes()==blob for name,blob in originals.items()),
            'scope':'First-stage regressions only; not phase-two native-game acceptance.'}
    target('outputs/phase2/baseline_regression/report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));raise SystemExit(0 if report['all_baseline_suites_passed'] else 1)

if __name__=='__main__':main()

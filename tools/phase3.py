"""One phase-three entry. Existing platform commands are preserved."""
import argparse
from pathlib import Path
import subprocess
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase3.common import configure,local
configure()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command',choices=['doctor','baseline-audit','data-audit','convert','collect','export','train','evaluate','predict-model','play','acceptance'])
    args,rest=parser.parse_known_args()
    if args.command=='doctor':
        from royale_phase3.doctor import doctor
        p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/phase3/doctor.json');a=p.parse_args(rest)
        print(doctor(a.output));return 0
    scripts={'collect':'phase3_collect.py','export':'phase3_export.py','data-audit':'phase3_export.py',
        'train':'phase3_train.py','evaluate':'phase3_train.py','predict-model':'phase3_world.py',
        'convert':'phase3_sources.py','acceptance':'phase3_acceptance.py'}
    if args.command=='baseline-audit':
        # The preserved Parquet evidence uses the already installed Python3.11/PyArrow environment.
        python=local('.venv-phase2-data/Scripts/python.exe')
        return subprocess.call([str(python),'-B','-m','royale_phase3.evaluation',*rest],cwd=ROOT)
    if args.command=='play':return subprocess.call(['powershell.exe','-NoProfile','-File',str(ROOT/'run_play.ps1'),*rest],cwd=ROOT)
    prefix={'export':['export'],'data-audit':['audit'],'train':['train'],'evaluate':['evaluate']}.get(args.command,[])
    script=ROOT/'tools'/scripts[args.command]
    if not script.exists():raise RuntimeError('Implementation not available: '+str(script))
    return subprocess.call([sys.executable,'-B',str(script),*prefix,*rest],cwd=ROOT)

if __name__=='__main__':raise SystemExit(main())

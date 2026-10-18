"""Phase-two tools expose their evidence boundary instead of simulated success."""
import argparse
import json
import runpy
import subprocess
import sys
from .runtime import ROOT,configure,local
from .versioning import verify_manifest

SCRIPT_DISPATCH = {
    'replay': ('core', 'phase2_replay.py'),
    'batch-replay': ('core', 'phase2_batch.py'),
    'repeatability': ('core', 'phase2_repeatability.py'),
    'export': ('data', 'phase2_export.py'),
    'train-bc': ('data', 'phase2_train_bc.py'),
    'evaluate-model': ('data', 'phase2_evaluate_model.py'),
    'evaluate': ('data', 'phase2_evaluate_native.py'),
    'view': ('core', 'phase2_view.py'),
    'cohort': ('core', 'phase2_cohort.py'),
    'benchmark': ('core', 'phase2_benchmark.py'),
    'offline-smoke': ('audit', 'phase2_offline_smoke.py'),
    'acceptance': ('data', 'phase2_acceptance.py'),
}

def main():
    configure()
    if len(sys.argv) > 1 and sys.argv[1] in SCRIPT_DISPATCH:
        environment, script = SCRIPT_DISPATCH[sys.argv[1]]
        python = local('.venv-audit/Scripts/python.exe' if environment == 'audit' else f'.venv-phase2-{environment}/Scripts/python.exe')
        if not python.is_file():
            raise SystemExit(f'Missing project environment: {python}')
        raise SystemExit(subprocess.run([str(python), str(ROOT / 'tools' / script), *sys.argv[2:]], cwd=ROOT).returncode)
    parser=argparse.ArgumentParser(prog='python -m royale_phase2')
    commands=parser.add_subparsers(dest='command',required=True)
    for name in SCRIPT_DISPATCH:
        commands.add_parser(name, add_help=False, help='Forward arguments to the project-local implementation')
    commands.add_parser('doctor')
    commands.add_parser('status')
    commands.add_parser('baseline-check')
    commands.add_parser('verify-data')
    commands.add_parser('compatibility')
    audit=commands.add_parser('audit-data');audit.add_argument('--audit-only',action='store_true')
    fetch=commands.add_parser('fetch-data');fetch.add_argument('--shard');fetch.add_argument('--max-mib',type=int,default=150)
    version=commands.add_parser('verify-version');version.add_argument('--manifest',default='data/phase2/version_manifest.json')
    probe=commands.add_parser('probe-status');probe.add_argument('--port',type=int,default=26789)
    args=parser.parse_args()
    if args.command=='doctor':runpy.run_path(str(ROOT/'tools/phase2_doctor.py'),run_name='__main__')
    elif args.command=='status':print(local('phase2/STATUS.md').read_text(encoding='utf-8-sig'))
    elif args.command=='baseline-check':runpy.run_path(str(ROOT/'tools/phase2_baseline_check.py'),run_name='__main__')
    elif args.command in ('verify-data','compatibility','audit-data','fetch-data'):
        tools={'verify-data':'verify','compatibility':'compatibility','audit-data':'prepare','fetch-data':'fetch'}
        python=local('.venv-phase2-data/Scripts/python.exe')
        if not python.is_file():raise SystemExit('Missing project data environment; follow phase2/DATA_AUDIT.md')
        command=[str(python),str(ROOT/f'tools/phase2_data_{tools[args.command]}.py')]
        if args.command=='audit-data' and args.audit_only:command.append('--audit-only')
        if args.command=='fetch-data':
            command.extend(['--max-mib',str(args.max_mib)])
            if args.shard:command.extend(['--shard',args.shard])
        raise SystemExit(subprocess.run(command,cwd=ROOT).returncode)
    elif args.command=='probe-status':
        from .probe import inspect_probe
        result=inspect_probe(port=args.port);print(json.dumps(result,ensure_ascii=False,indent=2))
        if not result['reachable']:raise SystemExit(2)
    elif args.command=='verify-version':
        try:result=verify_manifest(args.manifest)
        except (OSError,ValueError,KeyError) as exc:
            print(json.dumps({'verified':False,'error':str(exc),'native_execution_verified':False},ensure_ascii=False,indent=2));raise SystemExit(2)
        print(json.dumps({
            'verified': True,
            'fingerprint_sha256': result['fingerprint'],
            'verified_asset_count': len(result.get('assets', [])),
            'client_version': result['manifest'].get('client_build'),
            'content_version': result['manifest'].get('content_version'),
            'claim': 'Asset identity only; native execution requires separate runtime evidence.'
        },ensure_ascii=False,indent=2))

if __name__=='__main__':main()

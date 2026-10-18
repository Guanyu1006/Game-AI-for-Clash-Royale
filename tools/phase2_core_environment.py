"""Create and verify a clean, stdlib-only project environment for core tools.

This is a component reproduction, not the complete phase-two acceptance.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, target


def main():
    configure()
    env = ROOT / '.venv-phase2-core'
    if not (env / 'Scripts/python.exe').is_file():
        venv.EnvBuilder(with_pip=False).create(env)
    python = env / 'Scripts/python.exe'
    checks = [
        ('environment', [str(python), '-c', 'import json,sys,site; print(json.dumps({"python":sys.version,"executable":sys.executable,"prefix":sys.prefix,"base_prefix":sys.base_prefix,"user_site_enabled":site.ENABLE_USER_SITE}))']),
        ('evidence-contracts', [str(python), '-m', 'unittest', 'discover', '-s', 'tests_phase2', '-p', 'test_evidence_contracts.py', '-v']),
        ('actor-projection', [str(python), '-m', 'unittest', 'discover', '-s', 'tests_phase2', '-p', 'test_actor_projection.py', '-v']),
        ('data-readback', [str(python), '-m', 'royale_phase2', 'verify-data']),
    ]
    records = []
    for name, command in checks:
        process = subprocess.run(command, cwd=ROOT, env=os.environ.copy(), encoding='utf-8', capture_output=True, timeout=180)
        log = target(f'outputs/phase2/core_environment/{name}.log')
        log.write_text(process.stdout + process.stderr, encoding='utf-8')
        records.append({'name': name, 'command': command, 'returncode': process.returncode,
                        'log': log.relative_to(ROOT).as_posix(), 'log_sha256': hashlib.sha256(log.read_bytes()).hexdigest()})
    report = {'schema_version': 'royale-phase2-core-environment.v1',
              'all_checks_passed': all(r['returncode'] == 0 for r in records), 'checks': records,
              'dependencies': 'Python stdlib; verify-data delegates to separately locked project PyArrow environment',
              'scope': 'Clean core/evidence/source-conversion tooling only. Native game, rendering and training not included.'}
    target('outputs/phase2/core_environment/report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))
    if not report['all_checks_passed']: raise SystemExit(1)


if __name__ == '__main__':
    main()

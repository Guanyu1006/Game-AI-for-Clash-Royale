"""Collect evidence and SHA256 for the shipped platform; does not invent test runs."""
import csv
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_lab.runtime import target

def main():
    def load(path):return json.loads((ROOT/path).read_text(encoding='utf-8-sig'))
    interactions=load('data/platform/interaction_tests.json')
    research=load('outputs/research/corpus_acceptance.json')
    http=load('outputs/platform/http_smoke.json')
    evaluation=load('outputs/evaluation/main/report.json')
    numeric=load('outputs/platform/numeric_integration.json')
    equivalence=load('outputs/native_engine_numeric_replay_equivalence.json')
    native_build=load('outputs/native_engine/build_manifest.json')
    native_numeric=load('outputs/native_engine_numeric_stats.json')
    migration=load('outputs/platform/numeric_schema_migration.json')
    native_path=ROOT/native_build['module_path']
    actual_native_hash=hashlib.sha256(native_path.read_bytes()).hexdigest()
    with (ROOT/'outputs/evaluation/main/records.jsonl').open(encoding='utf-8') as f:
        records=list({(r['policy'],r['index']):r for r in (json.loads(line) for line in f if line.strip())}.values())
    with (ROOT/'data/platform/card_coverage.csv').open(encoding='utf-8-sig',newline='') as f:cards=list(csv.DictReader(f))
    with (ROOT/'data/platform/mechanic_coverage.csv').open(encoding='utf-8-sig',newline='') as f:mechanics=list(csv.DictReader(f))
    platform=(ROOT/'outputs/platform-tests-delivery.log').read_text(encoding='utf-8-sig')
    repro=(ROOT/'outputs/repro-tests-delivery.log').read_text(encoding='utf-8-sig')
    checks={'research_minimums':research['status']=='passed','http_closed_loop':http['all_passed'],
            'platform_tests':'Ran 21 tests' in platform and platform.rstrip().endswith('OK'),
            'mvp_26_regressions':'Ran 26 tests' in platform,
            'clean_environment_tests':'Ran 21 tests' in repro and repro.rstrip().endswith('OK'),
            'evaluation_700_games':len(records)==700 and not any(r['crash'] for r in records),
            'no_rejected_actions':sum(r.get('rejected',0) for r in records)==0,
            'each_main_policy_100':all(p['games']==100 for p in evaluation['policies']) and len(evaluation['policies'])==7,
            'card_coverage_173':len(cards)==173,'numeric_real_battle':numeric['returncode']==0,
            'numeric_default_equivalence_100':equivalence['passed'] and equivalence['games']==100,
            'native_740_cases':interactions['runner_reported_cases']==740,
            'native_numeric_checks':len(native_numeric['checks'])==34 and all(c['passed'] for c in native_numeric['checks']),
            'final_native_module_hash':actual_native_hash==native_build['module_sha256']==native_numeric['module_sha256']==migration['module_sha256'],
            'evaluation_balance_hash':evaluation['experiment_config']['balance_hash']==migration['current_hash']==native_numeric['default_balance_hash'],
            'native_no_unexpected_failure':interactions['unexpected_native_failures']==0,
            'known_expected_failure_preserved':len(interactions['known_expected_failures'])==1}
    paths=['SPEC.md','PLAN.md','STATUS.md','README.md','DELIVERY_REPORT.md','requirements.lock','bootstrap.ps1','start_demo.ps1','run.ps1']
    paths += [str(p.relative_to(ROOT)) for pattern in ('royale_lab/*.py','royale_lab/web/*','tests_platform/*.py','docs/*.md','docs/research/*.md','docs/audits/*.md','tools/*.py','tools/*.ps1','outputs/platform/figures/*','outputs/research/real_detection/*/*','outputs/training/*/manifest.json','outputs/training/*/curve.csv') for p in ROOT.glob(pattern)]
    paths += ['data/platform/balance_v2.json','data/platform/balance_v2.schema.json','data/platform/card_coverage.csv',
              'data/platform/mechanic_coverage.csv','data/platform/interaction_tests.json','data/platform/unsupported_numeric_parameters.json',
              'models/platform/learned.pt','models/platform/ppo.pt','references/library/manifest.json',
              'references/library/assets_manifest.json','references/library/bibliography.bib','outputs/native_engine/build_manifest.json',
              'outputs/platform/http_smoke.json','outputs/platform/ui_verified.png','outputs/platform/numeric_integration.json',
              'outputs/native_engine_numeric_replay_equivalence.json','outputs/research/corpus_acceptance.json',
              'outputs/evaluation/main/experiment_config.json','outputs/evaluation/main/report.json','outputs/evaluation/main/summary.csv',
              'outputs/platform-tests-delivery.log','outputs/repro-tests-delivery.log','outputs/bootstrap-repro-final.log',
              'outputs/platform/numeric_schema_migration.json','outputs/native_engine_numeric_stats.json','outputs/native_engine_numeric_tests.xml',
              'outputs/audit_candidates/summary_manifest.json','outputs/platform-http-delivery.log','outputs/research/vision_model_smoke.json',
              str(native_path.relative_to(ROOT))]
    artifacts=[]
    for rel in sorted(set(paths)):
        p=ROOT/rel
        if not p.exists():raise FileNotFoundError(rel)
        content=p.read_bytes();artifacts.append({'path':rel.replace('\\','/'),'bytes':len(content),'sha256':hashlib.sha256(content).hexdigest()})
    report={'all_local_platform_acceptance_passed':all(checks.values()),'checks':checks,'engine':http['engine'],
            'native_cases':interactions['runner_reported_cases'],'native_assertions':interactions['runner_reported_assertions'],
            'native_known_expected_failures':interactions['known_expected_failures'],'card_rows':len(cards),'mechanic_rows':len(mechanics),
            'evaluation_games':len(records),'research':research,'artifacts':artifacts,
            'not_achieved':['Official full-rule parity','Official client compatibility','Real device pixel-to-action closed loop',
                            'Complete tower/level/effect numeric data externalization','Global optimal strategy or real-game win rate'],
            'evidence_boundary':'Passing engineering acceptance is not official gameplay equivalence.'}
    target('outputs/platform/acceptance.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'all_passed':report['all_local_platform_acceptance_passed'],'checks':checks,'artifacts':len(artifacts)},ensure_ascii=False,indent=2))
    sys.exit(0 if report['all_local_platform_acceptance_passed'] else 1)

if __name__=='__main__':main()

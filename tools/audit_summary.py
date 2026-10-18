"""Condense verified artifacts, without upgrading placeholder/probe status."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
def read(path):return json.loads((ROOT/path).read_text(encoding='utf-8-sig'))
repro=read('outputs/audit_candidates/repro/manifest.json')
rows=[
 {'name':'CRForge','repo':'https://github.com/voonhous/crforge','commit':'b41cf9b6276945152ec4c869ce56f98a5fa3f513',
  'license':'Apache-2.0','doc':'docs/audits/CRFORGE.md','status':'compiled_and_complete_bridge_episodes',
  'upstream_tests':{'run':974,'passed':974,'local_patch':'Windows FidelityLedger path separator only'},
  'episodes':read('outputs/audit_candidates/crforge_build/smoke_results.json'),
  'snapshots':'not implemented in public API','privacy':'oracle both-player fields, unsafe for main POMDP policy without filter'},
 {'name':'MSU-AI Gym','repo':'https://github.com/MSU-AI/clash-royale-gym','commit':'1cd23be16e10b25c5a5a0889626a863092f32615',
  'license':'MIT','doc':'docs/audits/MSU_GYM.md','status':'installed_py311_but_placeholder_env_failed',
  'probe':read('outputs/audit_candidates/gym_smoke.json')},
 {'name':'KataCR','repo':'https://github.com/wty-yy/KataCR','commit':'36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8',
  'license':'MIT','doc':'docs/audits/KataCR.md','status':'utility_smoke_passed_full_device_loop_unverified',
  'probe':read('outputs/audit_candidates/KataCR_smoke.json')},
 {'name':'SlashRoyaleLegacy','repo':'https://github.com/avoidr/SlashRoyaleLegacy','commit':'a47d02474efc755427c8416b15e96daa9331d993',
  'license':'GPL-3.0 inner server; wrapper unconfirmed','doc':'docs/audits/PRIVATE_SERVERS.md',
  'status':'source_audited_command_relay_dotnet_build_blocked_no_sdk','source_snapshot':'only critical actual source + primary source views; not complete repository snapshot',
  'build_log':'outputs/audit_candidates/SlashRoyaleLegacy_dotnet_build.log'}]
artifacts=[]
for path in [
 'data/audit/requested_mechanics.json','data/audit/crforge_card_catalog.csv',
 'data/audit/royaleapi_public_card_metadata_web.json','data/audit/catalog_manifest.json',
 'outputs/audit_candidates/source_inventory_manifest.json','outputs/audit_candidates/backend_differential.json',
 'outputs/audit_candidates/backend_differential_before_economy_fix.json',
 'docs/SERVER_SELECTION.md','docs/audits/INDEX.md','docs/audits/CRFORGE.md','docs/audits/KataCR.md',
 'docs/audits/MSU_GYM.md','docs/audits/PRIVATE_SERVERS.md','docs/audits/REPRO.md','docs/audits/PPO_BEHAVIOR.md']:
 p=ROOT/path
 artifacts.append({'path':path,'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()})
result={'verification_date':'2026-10-14','candidate_audits':rows,
        'differential':read('outputs/audit_candidates/backend_differential.json'),
        'clean_environment_repro':repro,'artifacts':artifacts,
        'unverified':['official engine equivalence','full current official card catalog','actual phone/perception game loop',
                      'private server client or database stack','Java complete snapshot/restore'],
        'important_negative_findings':['Java action_failed incorrectly flags executed queued cards',
          'MSU arena/reset/step placeholders','Kata original CUDA pins unavailable on current Windows PyPI',
          'protocol private server cannot compute winners', 'poison damage discrepancy 168 vs160 remains']}
behavior=read('outputs/audit_candidates/ppo_audit/behavior_report.json')
result['ppo_diagnosis']={'report':'outputs/audit_candidates/ppo_audit/behavior_report.json',
  'doc':'docs/audits/PPO_BEHAVIOR.md','summary':behavior['summary'],
  'encoder_probes':behavior['encoder_probes'],'training_evidence':behavior['training_evidence'],
  'resume_edge_before_fix':read('outputs/audit_candidates/ppo_audit/resume_probe_before_fix.json'),
  'resume_edge_after_fix':read('outputs/audit_candidates/ppo_audit/resume_probe_report.json'),
  'core_modified_by_audit_agent':False}
(ROOT/'outputs/audit_candidates/summary_manifest.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
print('audit summary saved')

"""Read-only recomputation of three-source evidence, then write one new receipt."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
import json
import math
from pathlib import Path
import re
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure, digest, local, now, read, reference, rows, sha, write
from royale_phase3.sources import interpret_object_npy_xz, validate_record
from tools.phase3_sources import next_card_examples


def main():
    configure()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--dataset',default='data/phase3/external/converted_v2')
    p.add_argument('--experiment',default='outputs/phase3/sources/aux_next_card_v1')
    p.add_argument('--tests',default='outputs/phase3/sources/source_tests_v2.log')
    p.add_argument('--output',default='outputs/phase3/sources/acceptance_v1.json')
    args=p.parse_args();out=local(args.output)
    if out.exists():raise ValueError('immutable receipt exists; use a new output name')
    checks=[]
    def check(name,passed,detail=None):checks.append({'name':name,'passed':bool(passed),'detail':detail})
    manifest=read(local(args.dataset)/'manifest.json')
    for kind in ('inputs','outputs','code'):
        mismatches=[r['path'] for r in manifest[kind] if sha(r['path'])!=r['sha256']]
        check('conversion_'+kind+'_hashes',not mismatches,mismatches)
    check('conversion_input_fingerprint',digest(manifest['inputs'])==manifest['input_fingerprint'])
    records=[]
    for source in ('cochon','tv','wty'):records.extend(rows(local(args.dataset)/f'{source}.records.jsonl'))
    errors=[{'row':i,'errors':validate_record(r)} for i,r in enumerate(records) if validate_record(r)]
    check('all_converted_records_revalidated',not errors,{'rows':len(records),'errors':errors[:20]})
    check('no_current_native_dynamics_claim',all(r['current_native_compatible'] is False for r in records))
    check('no_external_native_consumption_claim',all(r['action']['consumed'] is None for r in records))
    check('tv_no_invented_timestamp',all(r['time']['seconds'] is None and r['time']['dt_seconds'] is None for r in records if r['source']=='tv'))
    check('wty_true_terminal_unknown',all(r['end']['terminated'] is None and r['end']['truncated'] is None for r in records if r['source']=='wty'))
    audit=read(local(args.dataset)/'schema_audit.json')
    raw_counts={s:sum(r['source']==s for r in records) for s in ('cochon','tv','wty')}
    check('source_counts_recomputed',all(raw_counts[s]==audit['sources'][s]['converted_rows'] for s in raw_counts),raw_counts)
    wty=[]
    for item in audit['sources']['wty']['files']:
        data,receipt=interpret_object_npy_xz(item['file']['path'])
        wty.append({'file':item['file']['path'],'rows':len(data['state']),
                    'globals_executed':receipt['globals_executed'],'reducer_functions_called':receipt['reducer_functions_called']})
    check('wty_safe_interpreter_real_samples_rerun',len(wty)==2 and all(x['globals_executed']==0 and x['reducer_functions_called']==0 for x in wty),wty)
    check('tv_range_budget',audit['sources']['tv']['total_metadata_range_bytes']<=64_000_000,
          audit['sources']['tv']['total_metadata_range_bytes'])
    duplicates=read(local(args.dataset)/'duplicate_audit.json')
    check('cross_source_not_joined_without_evidence',duplicates['cross_source_join_performed'] is False,
          {'pairs':duplicates['cross_source_comparable_pairs'],'near_candidates':len(duplicates['near_candidates']),
           'limitations':duplicates['limitations']})
    report=read(local(args.experiment)/'report.json');config=read(local(args.experiment)/'frozen_config.json')
    ckpt=read(local(args.experiment)/'checkpoint.json')
    check('experiment_frozen_source_hash',sha(config['source']['path'])==config['source']['sha256'])
    check('experiment_config_hash',sha(local(args.experiment)/'frozen_config.json')==report['config']['sha256']==ckpt['config_sha256'])
    check('experiment_checkpoint_hash',sha(local(args.experiment)/'checkpoint.json')==report['checkpoint']['sha256'])
    check('experiment_grouped_split',not set(config['train_groups'])&set(config['validation_groups'])
          and not set(config['train_content_groups'])&set(config['validation_content_groups']))
    check('auxiliary_actor_feature_whitelist',config['actor_features']==['previous_public_card'])
    examples=next_card_examples([r for r in records if r['source']=='cochon'])
    train=[x for x in examples if x['group'] in config['train_groups']]
    val=[x for x in examples if x['group'] in config['validation_groups']]
    vocab=ckpt['vocabulary'];vset=set(vocab)
    label=lambda c:c if c in vset else '<UNK>'
    u=Counter(label(x['target_next_card']) for x in train);b=defaultdict(Counter)
    for x in train:b[label(x['previous_public_card'])][label(x['target_next_card'])]+=1
    check('checkpoint_reproduces_train_counts',dict(u)==ckpt['unigram_counts'] and {k:dict(v) for k,v in b.items()}==ckpt['conditional_counts'],
          {'train_examples':len(train),'validation_examples':len(val)})
    recomputed={}
    for name,conditional in [('baseline_unigram',False),('candidate_public_history_bigram',True)]:
        nll=0.;correct=0;top5=0;bins=defaultdict(list)
        for x in val:
            context=label(x['previous_public_card']);target=label(x['target_next_card'])
            c=ckpt['conditional_counts'].get(context,{}) if conditional else {}
            probability={k:(c.get(k,0)+5.*ckpt['unigram_probability'][k])/(sum(c.values())+5.) for k in vocab}
            order=sorted(vocab,key=lambda k:(-probability[k],k));prediction=order[0]
            nll-=math.log(probability[target]);correct+=prediction==target;top5+=target in order[:5]
            confidence=probability[prediction];bins[min(9,int(confidence*10))].append((confidence,prediction==target))
        n=len(val);ece=sum(len(v)/n*abs(sum(c for c,y in v)/len(v)-sum(y for c,y in v)/len(v)) for v in bins.values())
        metrics={'nll':nll/n,'accuracy':correct/n,'top5_accuracy':top5/n,'ece10':ece}
        recomputed[name]=metrics
        check(name+'_metrics_recomputed',all(abs(metrics[k]-report[name][k])<1e-12 for k in metrics),metrics)
    tests=local(args.tests).read_text(encoding='utf-8-sig')
    count=re.search(r'Ran (\d+) tests?',tests)
    check('source_contract_tests_actual_log',bool(count) and tests.rstrip().endswith('OK'),
          {'tests':int(count.group(1)) if count else None,'log':reference(args.tests)})
    check('real_auxiliary_not_native_strength',report['native_strength_improvement']=='not_tested_not_implied'
          and report['current_native_data_used']==0)
    receipt={'schema':'phase3.three_source_acceptance.v1','created_at':now(),
        'status':'passed_implementation_and_independent_auxiliary_reproduction' if all(x['passed'] for x in checks) else 'failed',
        'checks':checks,'failed_checks':[x['name'] for x in checks if not x['passed']],
        'evidence':[reference(local(args.dataset)/'manifest.json'),reference(local(args.dataset)/'schema_audit.json'),
                    reference(local(args.dataset)/'source_lock.json'),reference(local(args.experiment)/'report.json')],
        'metrics_recomputed':recomputed,
        'implementation_complete':all(x['passed'] for x in checks),
        'auxiliary_validation_nll_improved':recomputed['candidate_public_history_bigram']['nll']<recomputed['baseline_unigram']['nll'],
        'auxiliary_calibration_improved':recomputed['candidate_public_history_bigram']['ece10']<recomputed['baseline_unigram']['ece10'],
        'current_native_data_added':0,'native_strategy_improved':'not_tested',
        'source_scope':'small traced conversions; snapshot inventory only; no full TV or snapshot archive',
        'limitations':['wty_license_unknown','all_three_native_version_compatibility_unproved',
            'TV_all_selected_processed_coordinates_missing_and_exact_dt_unknown',
            'snapshot_ghost_and_visual_tower_hp_unverified_excluded',
            'auxiliary_history_model_calibration_worsened','16_validation_matches_no_test_or_native_winrate']}
    write(out,receipt);print(json.dumps({'status':receipt['status'],'report':reference(out),'failed_checks':receipt['failed_checks']},ensure_ascii=True,indent=2))
    return 0 if not receipt['failed_checks'] else 1


if __name__=='__main__':raise SystemExit(main())

"""Bounded deployed/legacy-path audit; full table denominators, sampled proof.

This does NOT bypass load_export for training. It provides a finite diagnostic
when a full per-row repeated proof audit is too slow. Only sampled rows receive
full validate_row evidence checks, and no sampled accuracy is reported.
"""
import argparse
from collections import Counter, defaultdict
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure,digest,local,now,read,reference,write
from royale_phase3.evaluation import CHECKPOINT,EXPORT,_legacy_predict,random_actions


def run(output):
    configure();folder=local(output)
    if folder.exists():raise FileExistsError('Bounded evidence output is immutable')
    folder.mkdir(parents=True)
    frozen={'schema':'phase3-bounded-baseline-config.v1','created_utc':now(),
        'checkpoint':reference(CHECKPOINT),'export':reference(EXPORT),
        'selection':'For each eligible episode/owner choose first/middle/last WAIT and first/middle/last PLAY, unique rows',
        'purpose':'Exact inference path agreement, NOT model selection or unbiased metrics',
        'test':'May verify path equivalence but never tune parameters or publish sampled test accuracy',
        'source':reference(__file__)}
    write(folder/'configuration.json',frozen)
    import pyarrow.parquet as pq
    from royale_phase2.trajectories import validate_row,verify_reference
    from royale_phase2.learning import NumpyBCPolicy
    export=read(EXPORT);verify_reference(export['parquet'])
    records=pq.read_table(local(export['parquet']['path'])).to_pylist()
    if len(records)!=export['rows']:raise ValueError('Full export denominator mismatch')
    eligible=[];groups=defaultdict(list);counts=Counter();cards=Counter();splits=defaultdict(Counter)
    for record in records:
        row=json.loads(record['row_json'])
        if any(record[k]!=row[k] for k in record if k!='row_json'):raise ValueError('Canonical/typed Parquet mismatch')
        if row['eligible_hard_e']:
            eligible.append(row);counts[row['action']['kind']]+=1;splits[row['learning_split']][row['source_episode_id']]+=1
            if row['action']['kind']=='play_card':cards[str(row['action']['card'])]+=1
            groups[(row['source_episode_id'],row['native_owner'],row['action']['kind'])].append(row)
    chosen={}
    for group in groups.values():
        for index in sorted({0,len(group)//2,len(group)-1}):chosen[group[index]['decision_id']]=group[index]
    sample=list(chosen.values());checkpoint=read(CHECKPOINT)
    model=NumpyBCPolicy.load(CHECKPOINT,version_fingerprint=checkpoint['version_fingerprint_sha256'])
    print(f'Full structural denominator {len(records)} rows, eligible {len(eligible)}; strictly validating {len(sample)} sampled rows',flush=True)
    before=time.perf_counter()
    for row in sample:validate_row(row)
    batch=model.predict_many([r['actor'] for r in sample],legal_masks=[r['legal_action_mask'] for r in sample])
    differences=[];legacy_differences=[]
    for row,pred in zip(sample,batch):
        deployed=model.predict(row['actor'],legal_mask=row['legal_action_mask'])
        old=_legacy_predict(model,row['actor'],row['legal_action_mask'])
        if pred!=deployed:differences.append({'decision_id':row['decision_id'],'batch':pred,'single':deployed})
        if pred!=old:legacy_differences.append({'decision_id':row['decision_id'],'old':old,'new':pred})
    random_a,rng=random_actions(sample,31006);random_b,rng_b=random_actions(sample,31006)
    if random_a!=random_b or rng!=rng_b:raise AssertionError('Random state not reproducible')
    write(folder/'sample_predictions.json',{'sample_ids':[r['decision_id'] for r in sample],'predictions':batch,'random_rng':rng})
    result={'schema':'phase3-bounded-baseline-audit.v1','created_utc':now(),'configuration':reference(folder/'configuration.json'),
        'full_structural_counts':{'export_rows':len(records),'eligible_rows':len(eligible),'episodes':len({r['source_episode_id'] for r in eligible}),
            'actions':dict(counts),'cards':dict(cards),'splits':{s:{'rows':sum(c.values()),'episodes':len(c)} for s,c in splits.items()}},
        'strict_evidence_sample_rows':len(sample),'whole_export_strict_evidence_audit_complete':False,
        'single_batch_mismatches':differences,'historical_decoder_mismatches':legacy_differences,
        'path_agreement_passed':not differences and not legacy_differences,'random_reproducible':True,
        'predictions':reference(folder/'sample_predictions.json'),'elapsed_seconds':time.perf_counter()-before,
        'native_matches':0,'unbiased_accuracy':'not_computed_from_selected_sample','strength_improvement':'not_established',
        'limitations':['Sample diagnostic only; complete canonical/typed counts are not full per-row evidence validation',
            'Inherited phase2 play bias 4.0 has unknown historical selection provenance; not retuned here',
            'Historical WAIT labels sampled every10ticks plus events vs original deployed controller20ticks',
            'No independent official trajectory truth; no native wins/consumption measured']}
    write(folder/'report.json',result);print(json.dumps(result,ensure_ascii=False),flush=True);return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',default='outputs/phase3/baseline/bounded-v1')
    run(parser.parse_args().output)

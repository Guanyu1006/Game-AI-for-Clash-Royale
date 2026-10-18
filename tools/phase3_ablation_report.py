"""Aggregate immutable training reports, without evaluating test or native RPC."""
import argparse
from collections import defaultdict
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import digest,local,now,read,reference,write


def aggregate(reports,output):
    if local(output).exists():raise FileExistsError('Use a new report output')
    groups=defaultdict(list);common=None;seeds=defaultdict(set)
    for path in reports:
        report=read(path);config=read(report['configuration']['path'])
        contract={k:config[k] for k in ('dataset','rows','control_ticks','epochs','sequence_length','burnin','batch_size','learning_rate','train_play_class_weight')}
        if common is None:common=contract
        elif common!=contract:raise ValueError('Main ablation budget/dataset contracts differ')
        a=config['architecture'];name=a['variant']+('-opponent' if a['opponent'] else '')+('-hazard' if a['hazard'] else '')
        if config['seed'] in seeds[name]:raise ValueError('Repeated seed/checkpoint cannot count as independent training')
        seeds[name].add(config['seed']);score=report['best_validation']
        groups[name].append({'seed':config['seed'],'report':reference(path),'checkpoint':report['checkpoint'],
            'parameters':report['parameter_count'],'completed_epochs':report['completed_epochs'],
            'action_nll':score['action_nll'],'event':score['event'],'position_nll_true_slot':score['position_nll_true_slot'],
            'position_when_slot_correct':score['position_when_slot_correct'],'opponent':score['opponent'],
            'inference_seconds':score['inference_seconds'],'native_closed_loop':report['native_closed_loop_status']})
    if not groups:raise ValueError('No reports')
    result={'schema':'phase3-ablation-comparison.v1','created_utc':now(),'common_contract':common,'groups':dict(groups),
        'test_scored_here':False,'comparison_scope':'Frozen natural-time validation only; no native strength claim',
        'independent_seed_counts':{k:len(v) for k,v in seeds.items()},
        'limitations':['A/B fair observation difference is empty uncertified entity slots, not proved added battlefield information',
            'C changes recurrent architecture and explicit history/belief together; this A-E table cannot separately attribute each memory component',
            'D/E have more position-head parameters; same epochs/data/optimizer budget is not equal FLOPs or parameter count',
            'Validation action NLL model selection may prefer WAIT; event metrics expose missed plays',
            'Pairing, card correctness, native consumption, terminal win/loss and CIs require separate closed-loop reports']}
    write(output,result);return result


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--reports',nargs='+',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();aggregate(a.reports,a.output)

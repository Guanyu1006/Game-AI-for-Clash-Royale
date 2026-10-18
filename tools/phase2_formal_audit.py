"""Audit the complete frozen denominator, including rejects and sampling bias."""
import argparse
from collections import Counter,defaultdict
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.dataset import write_json
from royale_phase2.versioning import file_hash


def read(path):return json.loads(local(path).read_text(encoding='utf-8-sig'))


def main():
    configure();p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--queue',default='outputs/phase2/batches/formal100-v1/queue.json')
    p.add_argument('--output',default='outputs/phase2/formal_audit.json');p.add_argument('--require-complete',action='store_true')
    args=p.parse_args();frozen=read('data/phase2/frozen_manifest.json');queue=read(args.queue)
    expected={e['source_episode_id']:e for e in frozen['entries'] if e['split']=='formal'}
    jobs={j['source_episode_id']:j for j in queue['jobs']}
    if len(jobs)!=len(queue['jobs']) or set(jobs)!=set(expected):raise ValueError('Formal denominator changed')
    rows=[];groups=defaultdict(list);card_groups=defaultdict(list)
    for source_id,entry in expected.items():
        source=read(entry['episode']);job=jobs[source_id]
        cards=source['match_config']['required_card_keys']
        attributes={'source_has_ability':any(a['kind']=='activate_ability' for a in source['action_journal']),
                    'source_has_evolution':any('-ev' in card for card in cards),
                    'source_has_hero':any('-hero' in card for card in cards),
                    'source_result_from_team_view':source['reference_terminal']['result_from_team_view']}
        row={'source_episode_id':source_id,'source_episode':entry['episode'],'source_episode_sha256':file_hash(entry['episode']),
             'state':job['state'],'attributes':attributes,'source_actions':len(source['action_journal']),
             'source_action_kind_counts':dict(Counter(a['kind'] for a in source['action_journal']))}
        if job['state']=='recorded':
            ref=job['result']
            if file_hash(ref['path'])!=ref['sha256']:raise ValueError('Recorded result changed')
            result=read(ref['path']);row.update(result=ref,quality=result['quality'],failures=result['failures'],
                native_execution=result['native_execution'],strict_source_levels=result['strict_source_levels'],
                counts=result['counts'],terminal=result.get('terminal'),outcome_compare=result.get('outcome_compare'))
            actions_ref=result['files'].get('actions.json')
            if actions_ref:
                if file_hash(actions_ref['path'])!=actions_ref['sha256']:raise ValueError('Actions changed')
                actions=read(actions_ref['path'])['actions']
                feedback=[a['feedback'] for a in actions if a.get('feedback')]
                row['feedback_counts']={'source_actions':len(actions),'registered':len(feedback),
                    'native_injected':sum(f.get('native_injected') is True for f in feedback),
                    'consumed':sum(f.get('card_consumed') is True for f in feedback),
                    'native_injection_failed':sum(f.get('native_injected') is False for f in feedback),
                    'injected_consumption_unknown':sum(f.get('native_injected') is True and f.get('card_consumed') is not True for f in feedback),
                    'not_submitted':sum(not a.get('feedback') for a in actions)}
                unconfirmed=[a for a in actions if a['execution_status']!='executed']
                row['first_unconfirmed_input_target_tick']=min((a['tick'] for a in unconfirmed),default=None)
                row['unconfirmed_kind_counts']=dict(Counter(a['kind'] for a in unconfirmed))
            else:row['feedback_unavailable_reason']='Rejected or failed before complete native execution; see preserved failures/error.log. No action consumption manufactured.'
            row['first_independent_temporal_divergence_tick']=None
            row['first_independent_temporal_divergence_reason']='No independently acquired temporal trajectory; terminal mismatches do not locate their causal first tick.'
            for key,value in attributes.items():groups[f'{key}={value}'].append(row)
            for card in cards:card_groups[card].append(row)
        rows.append(row)
    def distribution(subset):
        return {'episodes':len(subset),'runtime':dict(Counter(r['quality']['runtime_replayable'] for r in subset)),
                'outcome':dict(Counter(r['quality']['outcome_consistent'] for r in subset)),
                'runtime_and_outcome_pass':sum(r['quality']['runtime_replayable']=='pass' and r['quality']['outcome_consistent']=='pass' for r in subset)}
    recorded=[r for r in rows if r['state']=='recorded']
    report={'schema':'royale-phase2-formal-denominator-audit.v1','formal_denominator':len(rows),'recorded':len(recorded),
            'remaining':len(rows)-len(recorded),'source_manifest_sha256':file_hash('data/phase2/frozen_manifest.json'),
            'queue':{'path':args.queue,'sha256':file_hash(args.queue)},'quality':{k:dict(Counter(r['quality'][k] for r in recorded)) for k in
                ('parse_valid','runtime_replayable','deterministic','outcome_consistent','trajectory_validated')},
            'feedback_totals':dict(sum((Counter(r.get('feedback_counts',{})) for r in recorded),Counter())),
            'by_source_attribute':{k:distribution(v) for k,v in sorted(groups.items())},
            'by_card_presence':{k:distribution(v) for k,v in sorted(card_groups.items())},
            'rows':rows,'official_trajectory_reference_count':0,
            'bias_interpretation':'Runtime filtering penalizes ability-bearing decks while generic ability consumption is unconfirmed; mixed levels and unresolved initial deals are rejected. Outcome filtering further changes deck/result distribution. Card-presence groups overlap; no causal attribution, official100% restoration or whole-game training claim.',
            'first_divergence_semantics':'Unconfirmed input tick is a backend feedback boundary. Independent official temporal divergence remains unknown when no temporal reference exists.'}
    write_json(local(args.output),report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('rows','by_card_presence','by_source_attribute')},ensure_ascii=False,indent=2))
    if args.require_complete and len(recorded)!=len(rows):raise SystemExit(2)


if __name__=='__main__':main()

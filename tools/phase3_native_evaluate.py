"""Frozen serial native comparison. Failed games remain in the denominator."""
import argparse
from collections import Counter
from pathlib import Path
import sys
import time
import traceback
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure,digest,local,native_lock,now,read,reference,write
configure()

def evaluate(args):
    from royale_phase3.native_env import NativePhase3Env
    from royale_phase3.learning import StatefulPolicy
    from royale_phase3.actions import PublicTeacher
    folder=local(args.output);folder.mkdir(parents=True,exist_ok=True)
    configs=[{'seed':args.seed+i,'owner':i%2,'opponent':'cycle' if (i//2)%2==0 else 'random'} for i in range(args.games)]
    config={'schema':'phase3-native-evaluation-config.v1','games':configs,'policy':args.policy,
        'checkpoint':reference(args.checkpoint) if args.checkpoint else None,'max_tick':args.max_tick,
        'control_ticks':10,'profile':'fair','source':reference('royale_phase3/native_env.py')}
    if (folder/'configuration.json').exists():
        if read(folder/'configuration.json')!=config:raise ValueError('Frozen native evaluation differs')
    else:write(folder/'configuration.json',config)
    report={'schema':'phase3-native-evaluation.v1','configuration_sha256':digest(config),'games':[],'started':now()}
    with native_lock():
        for index,cfg in enumerate(configs):
            base=folder/f'game-{index:03d}';base.mkdir(exist_ok=True)
            if (base/'report.json').exists():report['games'].append(read(base/'report.json'));continue
            result={**cfg,'index':index,'status':'running','natural_terminal':False,'truncated':False,'failure':None}
            env=None;started=time.monotonic()
            try:
                env=NativePhase3Env(base/f'attempt-{len(list(base.glob("attempt-*")))+1:03d}',owner=cfg['owner'],opponent=cfg['opponent'],max_tick=args.max_tick)
                row=env.reset(cfg['seed'])
                if args.policy=='checkpoint':policy=StatefulPolicy.load(args.checkpoint,env.backend.version['fingerprint'],'fair')
                elif args.policy in ('cycle','random','wait'):policy=PublicTeacher(args.policy,cfg['seed']*3+cfg['owner'])
                elif args.policy=='aligned_bc':
                    from royale_phase2.learning import NumpyBCPolicy
                    from royale_phase2.trajectories import audit_subspace_proof,subspace_mask
                    policy=NumpyBCPolicy.load('outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json',version_fingerprint=env.backend.version['fingerprint'])
                    proof=audit_subspace_proof('data/phase2/action_subspace_proof.json',env.backend.version['fingerprint'])
                else:raise ValueError('Unknown policy')
                waits=longest=run=decisions=0;inference=[];rejected=0
                while not env.done:
                    t=time.perf_counter()
                    if args.policy=='checkpoint':proposed=policy.predict(row)
                    elif args.policy=='aligned_bc':proposed=policy.predict(env.actor,subspace_mask(env.actor,True,proof))
                    else:proposed=policy.decide(env.actor)
                    inference.append(time.perf_counter()-t)
                    row,reward,terminated,truncated,step=env.step(proposed);decisions+=1
                    wait=step['action']['kind']=='wait';waits+=int(wait);run=run+1 if wait else 0;longest=max(longest,run)
                    rejected+=int(step['boundary_rejection'] is not None)
                    if time.monotonic()-started>300:raise TimeoutError('Native game wall budget300seconds exceeded')
                owner=cfg['owner'];own=[f for f in env.feedback if f['owner']==owner];opponent=[f for f in env.feedback if f['owner']!=owner]
                result.update(status='completed' if terminated else 'truncated',natural_terminal=terminated,truncated=truncated,
                    winner_owner=env.state['result'],crowns=env.state['crowns'],last_tick=env.state['tick'],
                    actor_requests=len(own),actor_consumed=sum(f['card_consumed'] for f in own),
                    actor_injected=sum(f['native_injected'] for f in own),actor_unknown=sum(not f['card_consumed'] for f in own),
                    opponent_requests=len(opponent),opponent_consumed=sum(f['card_consumed'] for f in opponent),
                    boundary_rejections=rejected,decisions=decisions,waits=waits,longest_wait_ticks=longest*10,
                    cycle_lock_diagnostic='long waits observed; intent and tactical necessity unknown',
                    key_missed_defenses='unverified_without_visible_enemy_entity_ground_truth',
                    inference_mean_seconds=sum(inference)/len(inference),
                    artifact_directory=str(env.folder.relative_to(local('.'))))
                result['artifacts']=[reference(env.folder/name) for name in ('journal.json','execution_feedback.json','decisions.jsonl','states.jsonl','native_result.json')]
            except Exception as exc:result.update(status='failed',failure=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
            finally:
                if env:env.close()
            result['wall_seconds']=time.monotonic()-started;write(base/'report.json',result)
            report['games'].append(result);write(folder/'report.json',report)
            print({'game':index,'policy':args.policy,'status':result['status'],'consumed':result.get('actor_consumed'),'wall':round(result['wall_seconds'],2)},flush=True)
            if result['status']=='failed':report['stop_reason']='native_failure_requires_explicit_recovery';break
    statuses=Counter(g['status'] for g in report['games']);finished=[g for g in report['games'] if g['natural_terminal']]
    report.update(finished=now(),requested_games=args.games,statuses=dict(statuses),
        actual_natural_games=len(finished),wins=sum(g.get('winner_owner')==g['owner'] for g in finished),
        losses=sum(g.get('winner_owner') in (0,1) and g['winner_owner']!=g['owner'] for g in finished),
        draws=sum(g.get('winner_owner') not in (0,1) for g in finished),
        actor_consumed=sum(g.get('actor_consumed',0) for g in report['games']),
        strength_improvement_claim=False)
    write(folder/'report.json',report);return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--policy',choices=['checkpoint','aligned_bc','cycle','random','wait'],default='checkpoint')
    p.add_argument('--checkpoint');p.add_argument('--output',required=True);p.add_argument('--games',type=int,default=10);p.add_argument('--seed',type=int,default=410000);p.add_argument('--max-tick',type=int,default=7200)
    a=p.parse_args()
    if a.policy=='checkpoint' and not a.checkpoint:p.error('--checkpoint required')
    evaluate(a)

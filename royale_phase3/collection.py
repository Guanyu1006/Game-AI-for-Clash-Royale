"""Serial, resumable native self-collection; never substitutes a simulated backend."""
from collections import Counter
import time
import traceback
from .common import append, digest, local, native_lock, now, read, reference, rows, write
from .actions import PublicTeacher, STEP_TICKS, match

def configuration(games=50,seed=310000,max_tick=7200):
    return {'schema':'phase3-native-collection-config.v1','games':games,'seed':seed,
        'max_tick':max_tick,'step_ticks':STEP_TICKS,'teacher_kinds':['cycle','random'],
        'source_kind':'native_self_collected_public_rule_teacher','warmup_ticks':90,
        'version':reference('data/phase2/version_manifest.json'),
        'action_code':reference('royale_phase3/actions.py'),
        'collector_code':reference('royale_phase3/collection.py')}

def collect(output,config,stop_after=None):
    from royale_phase2.backend import NativeBattleBackend
    from royale_phase2.versioning import verify_manifest
    folder=local(output); folder.mkdir(parents=True,exist_ok=True)
    if (folder/'configuration.json').exists():
        if read(folder/'configuration.json')!=config: raise ValueError('Immutable collection config differs')
    else: write(folder/'configuration.json',config)
    report={'schema':'phase3-native-collection.v1','configuration_sha256':digest(config),'games':[],'started':now()}
    completed_this_run=0
    with native_lock():
        version=verify_manifest('data/phase2/version_manifest.json')
        for index in range(config['games']):
            base=folder/f'game-{index:03d}'; base.mkdir(exist_ok=True)
            prior=read(base/'result.json') if (base/'result.json').exists() else None
            if prior:
                report['games'].append(prior);continue
            attempts=list(base.glob('attempt-*')); attempt=base/f'attempt-{len(attempts)+1:03d}';attempt.mkdir()
            result={'game_index':index,'seed':config['seed']+index,'attempt':str(attempt.relative_to(local('.'))),
                'status':'running','started':now(),'natural_terminal':False,'truncated':False,
                'source_kind':config['source_kind'],'failure':None,'states':0}
            write(attempt/'result.json',result)
            started=time.monotonic(); backend=None
            try:
                backend=NativeBattleBackend(timeout=30,trace_path=attempt/'wire.jsonl')
                initialization=match(result['seed']);write(attempt/'initialization.json',initialization)
                state=backend.reset(initialization)
                teachers=[PublicTeacher('cycle' if (index+owner)%2==0 else 'random',result['seed']*2+owner) for owner in (0,1)]
                append(attempt/'states.jsonl',state);result['states']+=1
                while not state['finalized'] and state['tick']<config['max_tick']:
                    tick=state['tick'];decision={'tick':tick,'dt_ticks':min(STEP_TICKS,config['max_tick']-tick),'owners':[]}
                    for owner in (0,1):
                        actor=backend.observe_visible(owner);action=teachers[owner].decide(actor)
                        row={'owner':owner,'actor':actor,'action':action,'policy_kind':teachers[owner].kind,
                            'rng_state_before_next_decision':teachers[owner].rng.getstate()}
                        if action['kind']=='play_card':
                            receipt=backend.schedule_card(owner,action['card'],action['x'],action['y'],tick+1,
                                request_id=f'phase3:{index}:{owner}:{tick}')
                            row['request_id']=receipt['request_id'];row['registration']=receipt
                        decision['owners'].append(row)
                    append(attempt/'decisions.jsonl',decision)
                    # Capture the exact first-tick hand/cycle boundary, then finish the shared fixed interval.
                    state=backend.advance_exact_ticks(1);append(attempt/'states.jsonl',state);result['states']+=1
                    remaining=decision['dt_ticks']-1
                    if remaining>0 and not state['finalized']:
                        state=backend.advance_exact_ticks(remaining);append(attempt/'states.jsonl',state);result['states']+=1
                    if time.monotonic()-started>300:raise TimeoutError('Per-game native wall budget exceeded300seconds')
                journal=backend.export_journal();feedback=backend.execution_feedback()
                write(attempt/'journal.json',journal);write(attempt/'execution_feedback.json',feedback)
                write(attempt/'native_result.json',backend.result())
                counts=Counter('consumed' if f['card_consumed'] else 'injected_unknown' if f['native_injected'] else 'rejected_or_unknown' for f in feedback)
                identities=Counter(str(f['card_id']) for f in feedback if f['card_consumed'])
                result.update(status='completed' if state['finalized'] else 'truncated',natural_terminal=state['finalized'],
                    truncated=not state['finalized'],last_tick=state['tick'],winner_owner=state['result'],
                    crowns=state['crowns'],consumptions=dict(counts),cards=dict(identities),requests=len(feedback),
                    version_fingerprint=version['fingerprint'])
                # Same trace with a different seed must not inflate unique-game counts.
                result['semantic_trajectory_hash']=digest([{k:s.get(k) for k in ('tick','finalized','result','crowns','players','entities','plays')} for s in rows(attempt/'states.jsonl')])
                result['artifacts']=[reference(attempt/name) for name in ('initialization.json','states.jsonl','decisions.jsonl','journal.json','execution_feedback.json','native_result.json','wire.jsonl')]
            except Exception as exc:
                result.update(status='failed',failure=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
            finally:
                if backend:backend.close()
            result['wall_seconds']=time.monotonic()-started;result['finished']=now()
            write(attempt/'result.json',result);write(base/'result.json',result)
            report['games'].append(result);write(folder/'report.json',report)
            print({'game':index,'status':result['status'],'consumptions':result.get('consumptions'),'wall':round(result['wall_seconds'],2)},flush=True)
            completed_this_run+=1
            if result['status']=='failed':
                # Invalid native state is not reused; caller must inspect and explicitly restart.
                report['stop_reason']='native_failure_requires_explicit_recovery';break
            if stop_after is not None and completed_this_run>=stop_after:
                report['stop_reason']='intentional_checkpoint_stop';break
    report.update(finished=now(),requested_games=config['games'],attempted=len(report['games']),
        natural_terminal_games=sum(g['natural_terminal'] for g in report['games']))
    write(folder/'report.json',report);return report

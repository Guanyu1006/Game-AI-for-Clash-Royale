"""Audit and export immutable, whole-game-split native self-collection snapshots."""
from collections import Counter
import os
from .common import digest,local,read,reference,rows,sha,write
from .actions import DECK,STEP_TICKS,mask,native_to_grid

def split_for(index): return {3:'validation',4:'test'}.get(index%5,'train')

def world_target(observation):
    towers={(t['side'],t['role']):t for t in observation['towers']}
    values=[observation['own_elixir_raw']/100000]; masks=[True]
    for side in ('own','opponent'):
        for role in ('left','right','king'):
            row=towers.get((side,role),{}); hp=row.get('hp')
            values.append(hp/10000 if hp is not None else 0.);masks.append(hp is not None)
    values.extend([observation['crowns']['own']/3,observation['crowns']['opponent']/3]);masks.extend([True,True])
    return values,masks

def audit_collection(source):
    folder=local(source);config=read(folder/'configuration.json');results=[];seen={}
    for path in sorted(folder.glob('game-*/result.json')):
        result=read(path);reasons=[]
        for item in result.get('artifacts',[]):
            if sha(item['path'])!=item['sha256']:reasons.append('artifact_hash_mismatch:'+item['path'])
        if not result.get('natural_terminal'):reasons.append('not_natural_terminal')
        if result.get('failure'):reasons.append('native_failure')
        if set(result.get('consumptions',{}))-{'consumed'}:reasons.append('unconfirmed_request')
        key=result.get('semantic_trajectory_hash')
        if key in seen:reasons.append('duplicate_native_trace_of:'+str(seen[key]))
        elif key:seen[key]=result['game_index']
        result['qualification']={'qualified':not reasons,'exclusions':reasons}
        results.append(result)
    good=[r for r in results if r['qualification']['qualified']]
    counts=Counter();splits=Counter();cards=Counter()
    for result in good:
        splits[split_for(result['game_index'])]+=1;cards.update(result.get('cards',{}));counts.update(result.get('consumptions',{}))
    report={'schema':'phase3-data-audit.v1','requested_games':config['games'],'sealed_games':len(results),
        'unfinished_games':config['games']-len(results),'qualified_games':len(good),'split_games':dict(splits),
        'confirmed_plays':counts['consumed'],'cards':dict(cards),'results':results,
        'gates':{'games50':len(good)>=50,'plays2000':counts['consumed']>=2000,
            'validation10':splits['validation']>=10,'test10':splits['test']>=10,
            'each_card20':all(cards[str(card)]>=20 for card in DECK)},
        'quality':'Native self-collected public-rule teacher, not expert or independent official ground truth',
        'split_rule':'Game index modulo5:3 validation,4 test,others train; both seats and all derived views stay together',
        'configuration':reference(folder/'configuration.json')}
    return report

def export(source,output,profile='fair',limit=None):
    from royale_phase2.battlefield import build_battlefield_observation
    from royale_phase2.visibility import project_actor,TowerAttestation
    from royale_phase3.observation import ObservationProjector,encode_observation,feature_spec
    from royale_phase3.belief import PublicBelief,encode_belief
    folder=local(output)
    if folder.exists():raise FileExistsError('Immutable export destination already exists')
    folder.mkdir(parents=True)
    audit=audit_collection(source);write(folder/'data_audit.json',audit)
    qualified=[r for r in audit['results'] if r['qualification']['qualified']]
    if limit:qualified=qualified[:limit]
    count=Counter();split_groups={'train':[],'validation':[],'test':[]};sample=[];dim=None
    target=folder/'rows.jsonl';temporary=folder/'rows.jsonl.partial'
    with temporary.open('wb') as destination:
        from .common import canonical
        for result in qualified:
            attempt=local(result['attempt']);episode='native-'+str(result['seed']);split=split_for(result['game_index'])
            split_groups[split].append(episode)
            journal=read(attempt/'journal.json');feedback=read(attempt/'execution_feedback.json')
            by_request={f['request_id']:f for f in feedback}
            states={state['tick']:state for state in rows(attempt/'states.jsonl')}
            decisions=list(rows(attempt/'decisions.jsonl'));attestation=TowerAttestation.from_reset(states[min(states)])
            projectors=[ObservationProjector(profile) for _ in (0,1)]
            beliefs=[PublicBelief(ordinary_resource_only=False) for _ in (0,1)]
            prior=[None,None];previous=[0,0];previous_feedback=[[0.,0.,0.,0.],[0.,0.,0.,0.]]
            cumulative={};prepared=[]
            for decision in decisions:
                tick=decision['tick'];raw=states[tick]
                # state.plays is cursor-incremental, so accumulate only data captured at/before tick.
                for st in sorted((s for t,s in states.items() if t<=tick and (not prepared or t>prepared[-1][0]['tick'])),key=lambda s:s['tick']):
                    for event in st['plays']:cumulative[event[0]]=event
                observations=[]
                for owner in (0,1):
                    actor=project_actor(raw,owner,attestation)
                    bf=build_battlefield_observation(raw,actor,list(cumulative.values()),journal,prior[owner])
                    prior[owner]=bf;observation=projectors[owner].project(bf);observations.append(observation)
                prepared.append((decision,observations))
            for index,(decision,observations) in enumerate(prepared):
                tick=decision['tick'];next_tick=min(t for t in states if t>tick and (t>=tick+STEP_TICKS or states[t]['finalized']))
                next_raw=states[next_tick]
                for owner in (0,1):
                    observation=observations[owner];record=decision['owners'][owner];action=record['action']
                    receipt=by_request.get(record.get('request_id'));confirmed=bool(receipt and receipt['card_consumed'])
                    action_index=action['slot']+1 if action['kind']=='play_card' and confirmed else 0
                    belief=encode_belief(beliefs[owner].update(observation),cards=DECK)
                    future_actor=project_actor(next_raw,owner,attestation)
                    # Supervision labels may use the subsequent public state, never actor inputs.
                    future_public={**observation,**{k:future_actor[k] for k in ('own_elixir_raw','towers','crowns')}}
                    world,world_mask=world_target(future_public)
                    next_opponent=0;opponent_valid=index+1<len(prepared)
                    if opponent_valid:
                        opposite=prepared[index+1][0]['owners'][1-owner]
                        opposite_receipt=by_request.get(opposite.get('request_id'))
                        if opposite['action']['kind']=='play_card':
                            opponent_valid=bool(opposite_receipt and opposite_receipt['card_consumed'])
                            if opponent_valid:next_opponent=DECK.index(opposite['action']['card'])+1
                    row={'episode_id':episode,'native_owner':owner,'split':split,'tick':tick,
                        'source_time':None,'engine_tick':tick,'observation_tick':tick,
                        'planned_tick':tick+1 if action_index else None,
                        'actual_execution_tick':receipt['actual_execution_tick'] if confirmed else None,
                        'action_semantics':'native requested coordinate; final deployment point unknown',
                        'dt_ticks':round(observation['dt_seconds']*20) if observation['dt_seconds'] is not None else 0,
                        'elapsed_dt_known':observation['dt_seconds'] is not None,
                        'transition_dt_ticks':next_tick-tick,'planned_control_ticks':STEP_TICKS,
                        'sampling_probability':1.,'natural_weight':1.,
                        'wait_semantics':'No confirmed native play requested in fixed sampling interval; not a human explicit WAIT',
                        'hud':encode_observation(observation,cards=DECK,include_entities=False,include_history=False),
                        'observation':encode_observation(observation,cards=DECK,include_entities=True,include_history=False),
                        'history':encode_observation(observation,cards=DECK,include_entities=True,include_history=True),
                        'belief':belief,'feedback':previous_feedback[owner], 'previous_action':previous[owner],
                        'mask':mask(record['actor']),'action_index':action_index,
                        'position_index':native_to_grid(action['x'],action['y']) if action_index else 0,
                        'xy':[action['x']/17999,action['y']/31999] if action_index else [0.,0.],
                        'position_mask':bool(action_index),'card_id':action['card'] if action_index else None,
                        'loss_mask': action['kind']=='wait' or confirmed,
                        'terminated':next_raw['finalized'],'truncated':not next_raw['finalized'] and next_tick>=7200,
                        'targets':{'world':world,'world_mask':world_mask,'opponent':next_opponent,'opponent_mask':opponent_valid},
                        'evidence':{'game':episode,'request_id':record.get('request_id'),'source_kind':result['source_kind']}}
                    assert row['mask'][row['action_index']]
                    if dim is None:dim={key:len(row[key]) for key in ('hud','observation','history','belief','feedback')}
                    assert dim=={key:len(row[key]) for key in dim}
                    destination.write(canonical(row)+b'\n');count[(split,'rows')]+=1;count[(split,'play' if action_index else 'wait')]+=1
                    if action_index:count[(split,str(row['card_id']))]+=1
                    previous[owner]=action_index;previous_feedback[owner]=[float(action['kind']=='play_card'),float(confirmed),0.,float(action['kind']=='play_card' and not confirmed)]
                    if len(sample)<8 and action_index:sample.append({'row':row,'public_observation':observation,'receipt':receipt,'native_record':reference(attempt/'states.jsonl')})
        destination.flush();os.fsync(destination.fileno())
    os.replace(temporary,target)
    metadata={'schema':'phase3-learning-dataset.v1','task_id':digest({'source':audit['configuration'],'games':[r['semantic_trajectory_hash'] for r in qualified],'profile':profile})[:16],
        'rows':reference(target),'feature_dims':dim,'observation_profile':profile,
        'version_fingerprint_sha256':qualified[0]['version_fingerprint'] if qualified else None,
        'control_ticks':STEP_TICKS,'ticks_per_second':20,'split_groups':split_groups,'card_vocabulary':list(DECK),
        'source':reference(folder/'data_audit.json'),'counts':{split:{key:value for (s,key),value in count.items() if s==split} for split in split_groups},
        'feature_spec':feature_spec(cards=DECK),'world_target_dimension':9,
        'feature_sources':[reference('royale_phase3/observation.py'),reference('royale_phase3/belief.py'),reference('royale_phase3/actions.py'),reference('royale_phase3/data.py')],
        'targets_schema':{'world':'next own_elixir/6public tower HP/own and opponent crowns, masks explicit; supervision only',
            'opponent':'next interval opposing confirmed card,0 WAIT,1..8 fixed vocabulary; future label never input'},
        'position_convention':'native world coordinates; y-major32rows18columns, grid=y//1000*18+x//1000',
        'fidelity':'Self-collected native teacher; no independent official trajectory truth',
        'fair_entities':'All excluded until visibility independently attested; B adds no proved entities',
        'wait_loss':'Natural time distribution kept; any class weighting must be recorded by training config'}
    write(folder/'metadata.json',metadata);write(folder/'samples.json',sample)
    # Re-read all rows and verify sample counts; never check only file existence.
    reread=Counter(row['split'] for row in rows(target))
    if reread!=Counter({s:metadata['counts'][s].get('rows',0) for s in reread}):raise AssertionError('Export readback mismatch')
    write(folder/'readback.json',{'rows':dict(reread),'sha256':sha(target),'status':'verified'})
    return metadata

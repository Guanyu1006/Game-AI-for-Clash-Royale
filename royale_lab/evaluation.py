"""Frozen paired scenarios, actual execution feedback, journal replay and ablation."""
import csv
import gzip
import hashlib
import json
import time
from concurrent.futures import ProcessPoolExecutor,as_completed
from .runtime import target,local

def scenarios(games=100,max_ticks=3000):
    # Policy-independent seed/deck/opponent/side list. Adjacent games swap sides.
    from .backend import named_decks
    decks=list(named_decks());opponents=['random','greedy','search_no_response']
    rows=[]
    for i in range(games):
        pair=i//2
        rows.append({'index':i,'seed':10000+(pair%5)*101+pair//5,'side':i%2,
                     'own_deck':decks[pair%len(decks)],'enemy_deck':decks[(pair//len(decks)+1)%len(decks)],
                     'opponent':opponents[pair%3],'max_ticks':max_ticks})
    return rows

def configured_policy(name,catalog,seed,budget_ms=15):
    from .policies import SearchPolicy,make_policy
    if name in ('search','search_no_response'):
        return SearchPolicy(catalog,seed,horizon_ticks=40,budget_ms=budget_ms,max_candidates=6,responses=name=='search')
    return make_policy(name,catalog,seed)

def play_game(task):
    from .backend import NativeBackend,named_decks
    policy,row,folder,budget_ms=task
    begin=time.perf_counter();side=row['side'];decks=named_decks()
    pair=[decks[row['own_deck']],decks[row['enemy_deck']]]
    if side:pair.reverse()
    backend=NativeBackend(row['seed'],pair,row['max_ticks'])
    names=[policy,row['opponent']]
    if side:names.reverse()
    controllers=[configured_policy(n,backend.catalog,row['seed']+i,budget_ms) for i,n in enumerate(names)]
    latencies=[];accepted=rejected=waiting=ability=0;trace=[];first_failure=None
    while not backend.done:
        if time.perf_counter()-begin>180:raise TimeoutError('Game wall time exceeded 180 seconds')
        observations=[backend.observe(i) for i in (0,1)]
        decisions=[controllers[i].decide(observations[i]) for i in (0,1)]
        for p,d in enumerate(decisions):
            feedback=backend.apply(p,d.action,command_id=f'{backend.tick}-{p}')
            if p==side:
                latencies.append(d.elapsed_ms);accepted+=feedback['status']=='accepted';rejected+=feedback['status']=='rejected'
                waiting+=d.action.kind=='wait';ability+=d.action.kind=='ability'
                if first_failure is None and feedback['status']=='rejected':first_failure={'obs':observations[p],'decision':d.to_dict(),'feedback':feedback}
            trace.append({'tick':backend.tick,'player':p,'decision':d.to_dict(),'feedback':feedback})
        backend.advance(10)
    state=backend.oracle();wall=time.perf_counter()-begin
    result=backend.result(side)
    prefix=f'{folder}/{policy}/{row["index"]:03}'
    replay=target(prefix+'.replay.json');replay.write_text(json.dumps(backend.export_snapshot()),encoding='utf-8')
    tracepath=target(prefix+'.decisions.json.gz')
    with gzip.open(tracepath,'wt',encoding='utf-8') as f:json.dump(trace,f,ensure_ascii=False)
    if result=='loss' or first_failure:
        failure=target(prefix+'.failure.json')
        failure.write_text(json.dumps({'scenario':row,'result':result,'last_visible_observation':backend.observe(side),
                                      'first_rejection':first_failure,'replay':str(replay)},ensure_ascii=False,indent=2),encoding='utf-8')
    import numpy as np
    towers=[t for t in state['towers'] if t['team']==1-side]
    damage=sum(max(0,t['max_hp']-t['hp']) for t in towers)
    return {**row,'policy':policy,'result':result,'accepted':accepted,'rejected':rejected,'wait_frames':waiting,
            'play_frames':len(latencies)-waiting,'ability_frames':ability,'decisions':len(latencies),
            'latency_p50_ms':float(np.percentile(latencies,50)),'latency_p95_ms':float(np.percentile(latencies,95)),
            'latency_max_ms':max(latencies),'enemy_tower_damage':damage,'wall_seconds':wall,
            'simulated_seconds':backend.tick/10,'replay':str(replay),'trace':str(tracepath),'crash':False,
            '_latencies':latencies}

def evaluate(policies=None,games=100,workers=4,max_ticks=3000,budget_ms=15,output='outputs/evaluation/main',resume=True):
    policies=policies or ['random','greedy','search','learned','ppo','search_no_response']
    scene=scenarios(games,max_ticks);folder=local(output);folder.mkdir(parents=True,exist_ok=True)
    frozen=json.dumps(scene,sort_keys=True).encode();scenario_hash=hashlib.sha256(frozen).hexdigest()
    from .backend import catalog_payload
    backend_meta=catalog_payload()
    configuration={'engine':backend_meta['version'],'balance_hash':backend_meta['balance']['hash'],
                   'search_horizon_ticks':40,'search_max_candidates':6,'search_budget_ms':budget_ms,
                   'decision_ticks':10,'workers':workers,'max_ticks':max_ticks}
    checkpoints={}
    for name in policies:
        if name in ('learned','ppo','ppo_sample'):
            filename='ppo' if name=='ppo_sample' else name
            checkpoints[name]=hashlib.sha256(local(f'models/platform/{filename}.pt').read_bytes()).hexdigest()
    metadata_path=folder/'experiment_config.json'
    if metadata_path.exists():
        previous=json.loads(metadata_path.read_text(encoding='utf-8'))
        if previous['configuration']!=configuration:raise ValueError('Engine/balance/compute config changed; choose a new --output')
        for name,digest in checkpoints.items():
            if name in previous['checkpoints'] and previous['checkpoints'][name]!=digest:
                raise ValueError('Checkpoint changed; choose a new --output')
        checkpoints={**previous['checkpoints'],**checkpoints}
    metadata_path.write_text(json.dumps({'configuration':configuration,'checkpoints':checkpoints},indent=2),encoding='utf-8')
    scene_path=folder/'scenarios.json'
    if scene_path.exists() and scene_path.read_bytes()!=frozen:raise ValueError('Output contains a different frozen scenario list')
    scene_path.write_bytes(frozen)
    records_path=folder/'records.jsonl';existing=[]
    if resume and records_path.exists():
        for line in records_path.read_text(encoding='utf-8').splitlines():
            if line.strip():existing.append(json.loads(line))
    existing=list({(r['policy'],r['index']):r for r in existing}.values())
    completed={(r['policy'],r['index']) for r in existing if not r['crash']}
    expected=games*len(set(policies)|{r['policy'] for r in existing})
    tasks=[(p,s,output,budget_ms) for p in policies for s in scene if (p,s['index']) not in completed]
    started=time.perf_counter();errors=[]
    with records_path.open('a' if resume else 'w',encoding='utf-8') as stream:
        with ProcessPoolExecutor(max_workers=workers) as pool:
            futures={pool.submit(play_game,t):(t[0],t[1]) for t in tasks}
            for future in as_completed(futures):
                name,row=futures[future]
                try:result=future.result()
                except Exception as error:
                    result={**row,'policy':name,'crash':True,'error':repr(error)};errors.append(result)
                stream.write(json.dumps(result,ensure_ascii=False)+'\n');stream.flush();existing.append(result)
                if len(existing)%10==0:print(json.dumps({'evaluation_completed':len(existing),'target':expected,'last':name}),flush=True)
    existing=list({(r['policy'],r['index']):r for r in existing}.values())
    summary=[]
    import numpy as np
    summary_policies=list(dict.fromkeys([*[r['policy'] for r in existing],*policies]))
    for policy in summary_policies:
        allrows=[r for r in existing if r['policy']==policy];rows=[r for r in allrows if not r['crash']]
        latencies=[v for r in rows for v in r['_latencies']]
        summary.append({'policy':policy,'games':len(rows),'wins':sum(r['result']=='win' for r in rows),
            'draws':sum(r['result']=='draw' for r in rows),'losses':sum(r['result']=='loss' for r in rows),
            'crashes':len(allrows)-len(rows),'timeouts':sum('TimeoutError' in r.get('error','') for r in allrows),
            'accepted':sum(r['accepted'] for r in rows),'rejected':sum(r['rejected'] for r in rows),
            'wait_frames':sum(r['wait_frames'] for r in rows),'play_frames':sum(r['play_frames'] for r in rows),
            'latency_p50_ms':float(np.percentile(latencies,50)) if latencies else None,
            'latency_p95_ms':float(np.percentile(latencies,95)) if latencies else None,
            'mean_enemy_tower_damage':float(np.mean([r['enemy_tower_damage'] for r in rows])) if rows else None,
            'games_per_worker_second':len(rows)/sum(r['wall_seconds'] for r in rows) if rows else 0})
    with (folder/'summary.csv').open('w',newline='',encoding='utf-8-sig') as f:
        writer=csv.DictWriter(f,fieldnames=summary[0]);writer.writeheader();writer.writerows(summary)
    paired=[]
    for base,other in [('greedy','search'),('search_no_response','search'),('learned','ppo'),('ppo','ppo_sample')]:
        a={r['index']:r for r in existing if r['policy']==base and not r['crash']}
        b={r['index']:r for r in existing if r['policy']==other and not r['crash']}
        common=sorted(a.keys()&b.keys());score=lambda r:1 if r['result']=='win' else .5 if r['result']=='draw' else 0
        differences=[score(b[i])-score(a[i]) for i in common]
        paired.append({'base':base,'other':other,'paired_games':len(common),'mean_score_difference':float(np.mean(differences)) if differences else None,
                       'interpretation':'Same seed/decks/side/opponent. Wall-clock search budget may vary with concurrent load.'})
    report={'scenario_sha256':scenario_hash,'policies':summary,'paired_comparisons':paired,'workers':workers,
            'max_ticks':max_ticks,'search':{'budget_ms':budget_ms,'horizon_ticks':40,'max_candidates':6,'budget_type':'soft wall clock'},
            'elapsed_this_run_seconds':time.perf_counter()-started,'errors':errors,
            'inference_modes':{'ppo':'argmax diagnostic','ppo_sample':'seed-local categorical sampling','learned':'argmax BC'},
            'experiment_config':configuration,'checkpoint_sha256':checkpoints,
            'claim_scope':'Audited approximate community engine only; not official-game win rate.'}
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

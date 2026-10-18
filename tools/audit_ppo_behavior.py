"""Diagnose stochastic-training / deterministic-deployment behavior without core edits."""
from __future__ import annotations
import argparse
import gzip
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
import numpy as np
import torch
from torch.distributions import Categorical

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_lab.backend import NativeBackend,named_decks,card_catalog
from royale_lab.learning import CandidateNet,encode,BASE_FEATURES
from royale_lab.memory import Belief
from royale_lab.policies import make_policy
from royale_lab.contracts import Action

OUT=ROOT/'outputs/audit_candidates/ppo_audit'

def distribution(net,obs,catalog,memory):
    memory.update(obs,catalog)
    encoded,actions=encode(obs,catalog,memory)
    features=torch.from_numpy(encoded['features'])[None]
    mask=torch.from_numpy(encoded['mask'])[None]
    with torch.no_grad():logits,value=net(features,mask)
    dist=Categorical(logits=logits)
    p=dist.probs[0].numpy()
    return encoded,actions,dist,{'candidate_count':len(actions),'p_wait':float(sum(p[i] for i,a in enumerate(actions) if a.kind=='wait')),
        'p_play_total':float(sum(p[i] for i,a in enumerate(actions) if a.kind=='card')),
        'p_best_play_individual':float(max([p[i] for i,a in enumerate(actions) if a.kind=='card'] or [0])),
        'argmax_kind':actions[int(logits.argmax())].kind,'entropy':float(dist.entropy()),'value':float(value)}

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--games-per-mode',type=int,default=20);args=parser.parse_args()
    checkpoint=OUT/'original_ppo.pt'
    cp=torch.load(checkpoint,map_location='cpu',weights_only=True)
    net=CandidateNet(cp['feature_dim']);net.load_state_dict(cp['state_dict']);net.eval()
    catalog=card_catalog();decks=list(named_decks().values())
    games=[];states=[]
    for mode in ['argmax','sample']:
        for i in range(args.games_per_mode):
            seed=12000+i
            torch.manual_seed(80000+i)
            backend=NativeBackend(seed,[decks[i%len(decks)],decks[(i+1)%len(decks)]],3000)
            memory=Belief();enemy=make_policy('greedy' if i%2 else 'random',catalog,seed+1)
            stats=[];accepted=waits=0;rejected=0;trace=[];beg=time.perf_counter()
            while not backend.done:
                obs=backend.observe(0)
                encoded,actions,dist,row=distribution(net,obs,catalog,memory)
                row.update(mode=mode,seed=seed,tick=backend.tick)
                stats.append(row)
                if len(actions)>1 and len(states)<250:states.append(row)
                choice=int(dist.probs.argmax()) if mode=='argmax' else int(dist.sample())
                action=actions[choice]
                feedback=backend.apply(0,action)
                accepted+=feedback['status']=='accepted';rejected+=feedback['status']=='rejected';waits+=action.kind=='wait'
                backend.apply(1,enemy.decide(backend.observe(1)).action)
                trace.append({'tick':backend.tick,'action':action.to_dict(),'feedback':feedback,'distribution':row})
                backend.advance(10)
            eligible=[r for r in stats if r['candidate_count']>1]
            game={'mode':mode,'seed':seed,'opponent':'greedy' if i%2 else 'random','result':backend.result(0),
                'accepted':accepted,'rejected':rejected,'wait_frames':waits,'frames':len(stats),
                'eligible_frames':len(eligible),'wall_seconds':time.perf_counter()-beg,
                'mean_p_wait_eligible':statistics.mean([r['p_wait'] for r in eligible]) if eligible else None,
                'mean_p_play_total_eligible':statistics.mean([r['p_play_total'] for r in eligible]) if eligible else None,
                'modal_wait_while_total_play_more_likely':sum(r['argmax_kind']=='wait' and r['p_play_total']>r['p_wait'] for r in eligible)}
            games.append(game)
            with gzip.open(OUT/f'{mode}_{i:03}.trace.json.gz','wt',encoding='utf-8') as f:json.dump(trace,f)
            (OUT/f'{mode}_{i:03}.replay.json').write_text(json.dumps(backend.export_snapshot()),encoding='utf-8')
            print(json.dumps(game),flush=True)
    # Inspect encoder blind spots with concrete observation mutations.
    backend=NativeBackend(7,[decks[0],decks[1]],3000);obs=backend.observe();memory=Belief()
    encoded,actions,_,_=distribution(net,obs,catalog,memory)
    import copy
    other=copy.deepcopy(obs)
    other['next_card']=next(cid for cid in catalog if cid!=obs['next_card'])
    altered,_=encode(other,catalog,memory)
    next_blind=bool(np.array_equal(encoded['features'],altered['features']))
    other=copy.deepcopy(obs)
    # Keep legal_actions unchanged to isolate value input: critic reads only WAIT row.
    other['hand']=[cid for cid in catalog if not catalog[cid].get('is_evolution')][:4]
    altered,_=encode(other,catalog,memory)
    wait_hand_blind=bool(np.array_equal(encoded['features'][0],altered['features'][0]))
    episodes=json.loads((OUT/'original_episodes.json').read_text(encoding='utf-8'))
    summary=[]
    for mode in ['argmax','sample']:
        rs=[g for g in games if g['mode']==mode]
        summary.append({'mode':mode,'games':len(rs),'wins':sum(g['result']=='win' for g in rs),
            'draws':sum(g['result']=='draw' for g in rs),'losses':sum(g['result']=='loss' for g in rs),
            'accepted':sum(g['accepted'] for g in rs),'rejected':sum(g['rejected'] for g in rs),
            'wait_frames':sum(g['wait_frames'] for g in rs),'frames':sum(g['frames'] for g in rs),
            'eligible_frames':sum(g['eligible_frames'] for g in rs),
            'modal_wait_while_total_play_more_likely':sum(g['modal_wait_while_total_play_more_likely'] for g in rs)})
    report={'checkpoint_sha256':hashlib.sha256(checkpoint.read_bytes()).hexdigest(),'checkpoint_extra':cp['extra'],
        'paired_scope':'same seeds/decks/enemy and stochastic torch seeds; main100-game frozen scenarios are separate',
        'summary':summary,'games':games,'state_distribution_samples':states,
        'training_evidence':{'complete_episodes':len(episodes),'accepted_plays':sum(e['accepted'] for e in episodes),
                            'wins':sum(e['result']=='win' for e in episodes),'losses':sum(e['result']=='loss' for e in episodes)},
        'encoder_probes':{'next_card_change_has_no_feature_effect':next_blind,
                          'value_wait_row_is_blind_to_hand_ids':wait_hand_blind,
                          'critic_row0_card_identity_slice_nonzero':int(np.count_nonzero(encoded['features'][0,BASE_FEATURES:]))},
        'algorithm_readthrough':'GAE terminal masks and clipped PPO ratio structurally consistent; critic loss effective coefficient .25 due two .5 factors',
        'scope_limit':'No official game inference; demonstrates deployed policy mode mismatch and representation omissions, not training convergence.'}
    (OUT/'behavior_report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'summary':summary,'encoder_probes':report['encoder_probes'],'training':report['training_evidence']},indent=2),flush=True)

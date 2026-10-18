"""Candidate scoring, whole-episode imitation and a small masked PPO implementation.

No oracle state enters this module's encoder. PPO is deliberately short, CPU and
single-process: the checkpoint proves the training path, not policy convergence.
"""
import csv
import json
import math
import time
from pathlib import Path
import numpy as np
import torch
from torch import nn
from torch.distributions import Categorical
import gymnasium as gym
from gymnasium import spaces
from .contracts import Action,Decision,validate_observation
from .memory import Belief
from .policies import candidates,make_policy,SearchPolicy,WIN_CONDITIONS,tactical_score
from .rewards import transition_reward
from .runtime import local,target

torch.set_num_threads(2)
MAX_ACTIONS=128
BASE_FEATURES=34
ENCODER_VERSION='visible-candidates-v1'

def encode(obs,catalog,memory=None):
    validate_observation(obs)
    ids=sorted(catalog);index={c:i for i,c in enumerate(ids)}
    actions=candidates(obs,catalog)[:MAX_ACTIONS]
    matrix=np.zeros((MAX_ACTIONS,BASE_FEATURES+len(ids)),dtype=np.float32)
    mask=np.zeros(MAX_ACTIONS,dtype=np.int8)
    interval=(memory.low,memory.high) if memory else (0,10)
    for i,a in enumerate(actions):
        card=catalog[obs['hand'][a.slot]] if a.kind=='card' else {}
        row=[float(a.kind==k) for k in ('wait','card','ability')]
        row += [obs['time']/360,obs['elixir']/10,interval[0]/10,interval[1]/10,
                a.x/17,a.y/33,card.get('cost',0)/10,card.get('hp',0)/6000,
                float(card.get('air',False)),float(card.get('is_spell',False)),
                float(card.get('is_building',False)),float(any(s in card.get('name','').lower() for s in WIN_CONDITIONS)),
                a.slot/4,obs.get('elixir_multiplier',1)/3]
        local_units=[u for u in obs['units'] if math.hypot(u['x']-a.x,u['y']-a.y)<5]
        for team in (0,1):
            group=[u for u in local_units if u['team']==team]
            row += [min(len(group),30)/30,sum(u['hp'] for u in group)/20000,
                    sum(bool(u.get('air')) for u in group)/15,
                    sum(catalog.get(u['card_id'],{}).get('cost',1) for u in group)/40]
        for team in (0,1):
            for slot in (0,1,2):
                tower=next((t for t in obs['towers'] if t['team']==team and t['slot']==slot),None)
                row.append(tower['hp']/max(1,tower['max_hp']) if tower else 0)
        row += [min(len(obs['units']),100)/100, min(len(obs['events']),150)/150,
                float(any(obs.get('ability_ready',[])))]
        assert len(row)==BASE_FEATURES,len(row)
        matrix[i,:BASE_FEATURES]=np.clip(row,-10,10)
        if a.kind=='card':matrix[i,BASE_FEATURES+index[obs['hand'][a.slot]]]=1
        mask[i]=1
    return {'features':matrix,'mask':mask},actions

class CandidateNet(nn.Module):
    def __init__(self,feature_dim):
        super().__init__()
        self.scorer=nn.Sequential(nn.Linear(feature_dim,96),nn.Tanh(),nn.Linear(96,48),nn.Tanh(),nn.Linear(48,1))
        self.value=nn.Sequential(nn.Linear(feature_dim,64),nn.Tanh(),nn.Linear(64,1))
    def forward(self,features,mask):
        logits=self.scorer(features).squeeze(-1).masked_fill(~mask.bool(),-1e9)
        value=self.value(features[:,0]).squeeze(-1)
        return logits,value

def save_model(net,path,catalog,method,extra=None):
    from .backend import balance_info
    path=target(path)
    torch.save({'state_dict':net.state_dict(),'feature_dim':BASE_FEATURES+len(catalog),
                'card_ids':sorted(catalog),'encoder':ENCODER_VERSION,'method':method,
                'balance_hash':balance_info()['hash'],'extra':extra or {}},path)
    return str(path)

class LearnedPolicy:
    def __init__(self,catalog,path,seed=0,sample=False):
        self.catalog=catalog;self.memory=Belief()
        self.sample=sample;self.generator=torch.Generator().manual_seed(seed)
        checkpoint=torch.load(local(path),map_location='cpu',weights_only=True)
        from .backend import balance_info
        balance=balance_info()
        if checkpoint.get('balance_hash'):
            if checkpoint['balance_hash']!=balance['hash']:raise ValueError('Checkpoint balance mismatch; retrain for this numeric config')
        elif balance['changed']:raise ValueError('Legacy baseline checkpoint cannot run with changed balance; retrain')
        if checkpoint['encoder']!=ENCODER_VERSION or checkpoint['card_ids']!=sorted(catalog):
            raise ValueError('Checkpoint encoder/catalog mismatch; retrain')
        self.net=CandidateNet(checkpoint['feature_dim']);self.net.load_state_dict(checkpoint['state_dict']);self.net.eval()
        self.method=checkpoint['method']
    def decide(self,obs):
        start=time.perf_counter();self.memory.update(obs,self.catalog)
        encoded,actions=encode(obs,self.catalog,self.memory)
        with torch.no_grad():
            logits,_=self.net(torch.from_numpy(encoded['features'])[None],torch.from_numpy(encoded['mask'])[None])
        ranked=sorted(range(len(actions)),key=lambda i:float(logits[0,i]),reverse=True)
        chosen=int(torch.multinomial(logits[0].softmax(-1),1,generator=self.generator)) if self.sample else ranked[0]
        a=actions[chosen]
        rows=[{'action':actions[i].to_dict(),'card_id':obs['hand'][actions[i].slot] if actions[i].kind=='card' else None,
               'score':float(logits[0,i])} for i in ranked[:5]]
        mode='种子控制概率采样' if self.sample else '最大单项候选'
        return Decision(a,self.method+' '+mode+'（短程实验模型）',obs['hand'][a.slot] if a.kind=='card' else None,
                        (time.perf_counter()-start)*1000,rows,self.memory.to_dict())

class CandidateGym(gym.Env):
    metadata={'render_modes':[]}
    def __init__(self,opponent='greedy',max_ticks=1800,decision_ticks=10):
        from .backend import card_catalog
        self.catalog=card_catalog();self.opponent=opponent;self.max_ticks=max_ticks;self.decision_ticks=decision_ticks
        dim=BASE_FEATURES+len(self.catalog)
        self.observation_space=spaces.Dict({'features':spaces.Box(-10,10,(MAX_ACTIONS,dim),np.float32),
                                            'mask':spaces.MultiBinary(MAX_ACTIONS)})
        self.action_space=spaces.Discrete(MAX_ACTIONS);self.backend=None;self.memory=None;self.episode=0
    def _encoded(self):
        self.obs=self.backend.observe(0);self.memory.update(self.obs,self.catalog)
        encoded,self.actions=encode(self.obs,self.catalog,self.memory)
        return encoded
    def reset(self,seed=None,options=None):
        super().reset(seed=seed)
        from .backend import NativeBackend,named_decks
        decks=list(named_decks().values());game_seed=int(self.np_random.integers(0,2**31-1))
        first=int(self.np_random.integers(0,len(decks)));second=int(self.np_random.integers(0,len(decks)))
        pair=[decks[first],decks[second]]
        self.backend=NativeBackend(game_seed,pair,self.max_ticks);self.episode+=1
        self.enemy=make_policy(self.opponent,self.catalog,game_seed+1);self.memory=Belief()
        self.accepted=self.rejected=0
        return self._encoded(),{'seed':game_seed}
    def step(self,action):
        if self.backend.done:raise RuntimeError('Reset a terminal environment')
        before=self.obs;enemy_obs=self.backend.observe(1)
        a=self.actions[int(action)] if 0<=int(action)<len(self.actions) else Action()
        feedback=self.backend.apply(0,a)
        invalid=not 0<=int(action)<len(self.actions) or feedback['status']=='rejected'
        self.accepted+=feedback['status']=='accepted';self.rejected+=invalid
        self.backend.apply(1,self.enemy.decide(enemy_obs).action)
        self.backend.advance(self.decision_ticks)
        encoded=self._encoded();done=self.backend.done
        reward=transition_reward(before,self.obs,self.catalog,self.backend.result(0) if done else None)-float(invalid)*.5
        info={'feedback':feedback,'accepted':self.accepted,'rejected':self.rejected,'tick':self.backend.tick}
        if done:info.update(result=self.backend.result(0))
        return encoded,float(reward),done,False,info

def train_bc(episodes=12,epochs=20,seed=23,max_ticks=1200,resume=False):
    torch.manual_seed(seed);rng=np.random.default_rng(seed)
    folder=target('outputs/training/bc/manifest.json').parent
    dataset=folder/'trajectories.npz'
    catalog=None
    if not resume or not dataset.exists():
        env=CandidateGym(max_ticks=max_ticks);catalog=env.catalog
        teacher=SearchPolicy(catalog,horizon_ticks=40,budget_ms=20,max_candidates=8)
        xs=[];masks=[];ys=[];groups=[];kind=[];evidence=[]
        for ep in range(episodes):
            obs,_=env.reset(seed=seed+ep);teacher.memory=Belief();done=False
            while not done:
                d=teacher.decide(env.obs)
                idx=next((i for i,a in enumerate(env.actions) if a==d.action),0)
                xs.append(obs['features']);masks.append(obs['mask']);ys.append(idx);groups.append(ep);kind.append(d.action.kind!='wait')
                obs,_,done,_,info=env.step(idx)
            replay=target(f'outputs/training/bc/episode-{ep:03}.json')
            replay.write_text(json.dumps(env.backend.export_snapshot()),encoding='utf-8')
            evidence.append({'episode':ep,'seed':seed+ep,'result':env.backend.result(0),'accepted':env.accepted,
                             'rejected':env.rejected,'tick':env.backend.tick,'replay':str(replay)})
            print(json.dumps({'bc_episode':ep,'samples':len(ys),'accepted':env.accepted}),flush=True)
        np.savez_compressed(dataset,x=np.stack(xs),mask=np.stack(masks),y=np.array(ys),episode=np.array(groups),play=np.array(kind))
        (folder/'episodes.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
    from .backend import card_catalog
    catalog=catalog or card_catalog();data=np.load(dataset)
    episodes_present=np.unique(data['episode']);validation_ids=episodes_present[-max(1,len(episodes_present)//4):]
    train=np.flatnonzero(~np.isin(data['episode'],validation_ids));valid=np.flatnonzero(np.isin(data['episode'],validation_ids))
    if not len(train):raise ValueError('BC needs at least two whole episodes')
    x=torch.from_numpy(data['x']);mask=torch.from_numpy(data['mask']);y=torch.tensor(data['y'],dtype=torch.long)
    net=CandidateNet(x.shape[-1]);optimizer=torch.optim.Adam(net.parameters(),lr=8e-4)
    curve=[]
    for epoch in range(epochs):
        losses=[];net.train()
        for ids in np.array_split(rng.permutation(train),max(1,math.ceil(len(train)/64))):
            logits,_=net(x[ids],mask[ids]);loss=nn.functional.cross_entropy(logits,y[ids])
            optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(net.parameters(),1);optimizer.step();losses.append(float(loss.detach()))
        net.eval()
        with torch.no_grad():pred=net(x[valid],mask[valid])[0].argmax(-1).numpy()
        row={'epoch':epoch,'loss':float(np.mean(losses)),'validation_accuracy':float(np.mean(pred==data['y'][valid]))}
        for label,value in [('wait',False),('play',True)]:
            subset=data['play'][valid]==value
            row[label+'_frames']=int(subset.sum());row[label+'_accuracy']=float(np.mean(pred[subset]==data['y'][valid][subset])) if subset.any() else None
        curve.append(row)
    with (folder/'curve.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=curve[0]);writer.writeheader();writer.writerows(curve)
    checkpoint=save_model(net,'models/platform/learned.pt',catalog,'BC-search',{'seed':seed,'samples':len(y),'epochs':epochs})
    report={'method':'BC-search','checkpoint':checkpoint,'samples':len(y),'train_episodes':[int(e) for e in episodes_present if e not in validation_ids],
            'validation_episodes':validation_ids.tolist(),'final':curve[-1],'teacher':{'horizon_ticks':40,'budget_ms':20,'max_candidates':8},
            'note':'Whole-episode split. Candidate match accuracy is not game strength.'}
    (folder/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

def train_ppo(total_steps=4096,seed=71,rollout_steps=256,resume=False,max_ticks=1800):
    torch.manual_seed(seed);rng=np.random.default_rng(seed)
    env=CandidateGym(max_ticks=max_ticks);obs,_=env.reset(seed=seed)
    net=CandidateNet(obs['features'].shape[-1]);optimizer=torch.optim.Adam(net.parameters(),lr=3e-4)
    folder=target('outputs/training/ppo/manifest.json').parent;training_state=folder/'resume.pt'
    start=0;curve=[];episode_rows=[];episode_reward=0
    if resume and training_state.exists():
        previous=torch.load(training_state,weights_only=True);net.load_state_dict(previous['model']);optimizer.load_state_dict(previous['optimizer']);start=previous['steps']
        if start>=total_steps:
            manifest=folder/'manifest.json'
            report=json.loads(manifest.read_text(encoding='utf-8')) if manifest.exists() else {'total_steps':start}
            return {**report,'resume_status':'no-op; saved steps already meet requested target'}
        if (folder/'episodes.json').exists():episode_rows=json.loads((folder/'episodes.json').read_text(encoding='utf-8'))
        if (folder/'curve.csv').exists():
            with (folder/'curve.csv').open(encoding='utf-8',newline='') as f:curve=list(csv.DictReader(f))
    completed=start
    while completed<total_steps:
        features=[];masks=[];actions=[];logprobs=[];values=[];rewards=[];dones=[]
        for _ in range(min(rollout_steps,total_steps-completed)):
            f=torch.from_numpy(obs['features'])[None];m=torch.from_numpy(obs['mask'])[None]
            with torch.no_grad():logits,value=net(f,m);dist=Categorical(logits=logits);a=dist.sample()
            features.append(obs['features']);masks.append(obs['mask']);actions.append(int(a));logprobs.append(float(dist.log_prob(a)));values.append(float(value))
            obs,reward,done,_,info=env.step(int(a));rewards.append(reward);dones.append(done);episode_reward+=reward;completed+=1
            if done:
                path=target(f'outputs/training/ppo/episode-{len(episode_rows):03}.json');path.write_text(json.dumps(env.backend.export_snapshot()),encoding='utf-8')
                episode_rows.append({'steps':completed,'return':episode_reward,'result':info['result'],'accepted':info['accepted'],
                                     'rejected':info['rejected'],'replay':str(path)})
                obs,_=env.reset();episode_reward=0
        with torch.no_grad():bootstrap=float(net(torch.from_numpy(obs['features'])[None],torch.from_numpy(obs['mask'])[None])[1])
        advantages=np.zeros(len(rewards),dtype=np.float32);gae=0
        for i in reversed(range(len(rewards))):
            nonterminal=1-float(dones[i]);next_value=values[i+1] if i+1<len(values) else bootstrap
            delta=rewards[i]+.99*next_value*nonterminal-values[i]
            gae=delta+.99*.95*nonterminal*gae;advantages[i]=gae
        returns=torch.tensor(advantages+np.array(values),dtype=torch.float32)
        advantages=(advantages-advantages.mean())/(advantages.std()+1e-8)
        adv=torch.tensor(advantages);x=torch.tensor(np.stack(features));m=torch.tensor(np.stack(masks));a=torch.tensor(actions);old=torch.tensor(logprobs)
        loss_records=[]
        for _ in range(4):
            for ids in np.array_split(rng.permutation(len(a)),max(1,math.ceil(len(a)/64))):
                logits,val=net(x[ids],m[ids]);dist=Categorical(logits=logits);ratio=(dist.log_prob(a[ids])-old[ids]).exp()
                policy=-torch.minimum(ratio*adv[ids],ratio.clamp(.8,1.2)*adv[ids]).mean()
                value_loss=.5*(val-returns[ids]).square().mean();entropy=dist.entropy().mean()
                loss=policy+.5*value_loss-.01*entropy
                optimizer.zero_grad();loss.backward();nn.utils.clip_grad_norm_(net.parameters(),.5);optimizer.step()
                loss_records.append((float(policy.detach()),float(value_loss.detach()),float(entropy.detach())))
        average=np.mean(loss_records,axis=0)
        row={'steps':completed,'policy_loss':float(average[0]),'value_loss':float(average[1]),'entropy':float(average[2]),
             'completed_episodes':len(episode_rows),'mean_recent_return':float(np.mean([e['return'] for e in episode_rows[-10:]])) if episode_rows else 0}
        curve.append(row);print(json.dumps({'ppo':row}),flush=True)
        torch.save({'model':net.state_dict(),'optimizer':optimizer.state_dict(),'steps':completed,'seed':seed},training_state)
        save_model(net,'models/platform/ppo.pt',env.catalog,'PPO-short',{'steps':completed,'seed':seed})
    with (folder/'curve.csv').open('w',newline='',encoding='utf-8') as f:
        writer=csv.DictWriter(f,fieldnames=curve[0]);writer.writeheader();writer.writerows(curve)
    (folder/'episodes.json').write_text(json.dumps(episode_rows,indent=2),encoding='utf-8')
    report={'checkpoint':str(local('models/platform/ppo.pt')),'total_steps':completed,'episodes':len(episode_rows),
            'seed':seed,'final':curve[-1],'cuda_used':False,'note':'Short CPU PPO sanity run. Not convergence or official-game performance.'}
    (folder/'manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    return report

"""Visible-information candidate policies; search rebuilds hypotheses, not oracle snapshots."""
import math
import random
import time
from .contracts import Action,Decision,validate_observation
from .memory import Belief
from .rewards import utility

WIN_CONDITIONS=('giant','hog rider','balloon','golem','lava hound','royal giant','battle ram','ram rider','goblin giant','goblin drill','miner')
ANTI_AIR=('archer','musketeer','minion','wizard','dragon','executioner','hunter','witch','spear goblin','dart goblin','princess','firecracker','bats','phoenix')

def raw_candidates(obs,catalog):
    actions=[Action()]
    enemies=[u for u in obs['units'] if u['team']==1]
    for slot,cid in enumerate(obs['hand']):
        card=catalog[cid]
        if card['cost']>obs['elixir']+1e-5 and card['name'] not in ('Mirror','Spirit Empress'):continue
        if obs.get('hand_cooldowns',[0]*4)[slot]>0:continue
        if card['is_spell'] or card.get('deploy_anywhere'):
            points=[(u['x'],u['y']) for u in enemies]
            points += [(t['x'],t['y']) for t in obs['towers'] if t['team']==1 and t['hp']>0 and not t.get('king')]
            for u in enemies[:20]:
                nearby=[v for v in enemies if math.hypot(v['x']-u['x'],v['y']-u['y'])<4]
                if len(nearby)>1:points.append((sum(v['x'] for v in nearby)/len(nearby),sum(v['y'] for v in nearby)/len(nearby)))
            if card.get('cast_own_side_only'):points=[(x,min(15,y)) for x,y in points]
        elif card['is_building']:
            points=[(7.5,10),(9.5,10),(8.5,11),(3,9),(14,9)]
        else:
            points=[(2.5,14),(14.5,14),(3,8),(14,8),(3,4),(14,4)]
            for u in enemies[:20]:
                if u['y']<21:
                    offset=3 if any(n in card['name'].lower() for n in ANTI_AIR) else 1
                    points.append((u['x'],min(15,max(3,u['y']-offset))))
                    points.append((u['x'],min(15,max(3,u['y']+1))))
        points=sorted({(round(max(.5,min(16.5,x)),1),round(max(1,min(32,y)),1)) for x,y in points})
        actions += [Action('card',slot,x,y) for x,y in points]
    actions += [Action('ability',i+1) for i,ready in enumerate(obs.get('ability_ready',[])) if ready]
    return actions

def candidates(obs,catalog):
    if 'legal_actions' in obs:return [Action(**a) for a in obs['legal_actions']]
    return raw_candidates(obs,catalog)

def tactical_score(obs,action,catalog):
    if action.kind=='wait':return .25 if obs['elixir']<8 else -.7
    if action.kind=='ability':return .4 if any(u['team']==1 and u['y']<20 for u in obs['units']) else -.2
    card=catalog[obs['hand'][action.slot]];name=card['name'].lower()
    enemies=[u for u in obs['units'] if u['team']==1]
    if card['is_spell']:
        radius=card.get('spell_radius',2.5 if 'fireball' in name else 3.5)
        damage=card.get('spell_damage_per_application',280)*card.get('spell_applications',1)
        hit=sum(catalog.get(u['card_id'],{}).get('cost',2)/max(1,catalog.get(u['card_id'],{}).get('count',1))*min(1,damage/max(1,u['hp']))
                for u in enemies if math.hypot(u['x']-action.x,u['y']-action.y)<=radius)
        for t in obs['towers']:
            if t['team']==1 and not t.get('king') and t['hp']>0 and math.hypot(t['x']-action.x,t['y']-action.y)<=radius:
                crown=card.get('spell_crown_damage_per_application',damage*.3)*card.get('spell_applications',1)
                hit+=18 if t['hp']<crown else .15
        if any(n in name for n in ('graveyard','goblin barrel','goblin curse')):hit+=2
        return hit-card['cost']*.75
    threats=[u for u in enemies if u['y']<21 and abs(u['x']-action.x)<5]
    air=card.get('air',False) or any(n in name for n in ANTI_AIR)
    wincon=any(n in name for n in WIN_CONDITIONS)
    if threats:
        value=sum(catalog.get(u['card_id'],{}).get('cost',2)*max(0,1-math.hypot(u['x']-action.x,u['y']-action.y)/9)
                  *(1 if not u.get('air') or air else .3) for u in threats)
        if wincon:value-=3
        if any(n in name for n in ('pekka','inferno')) and any(u['hp']>1500 for u in threats):value+=1.5
        return value-card['cost']*.22
    if card['is_building']:return -1
    friends=[u for u in obs['units'] if u['team']==0 and abs(u['x']-action.x)<5]
    support=.8 if friends and not wincon else 0
    return (1.7 if wincon else .6)+support-card['cost']*.15-abs(action.y-14)*.09 if obs['elixir']>=7 else -.35

class RandomPolicy:
    def __init__(self,catalog,seed=0):self.catalog=catalog;self.rng=random.Random(seed);self.memory=Belief()
    def decide(self,obs):
        start=time.perf_counter()
        validate_observation(obs);self.memory.update(obs,self.catalog)
        a=self.rng.choice(candidates(obs,self.catalog))
        return Decision(a,'随机合法候选基线',None if a.kind!='card' else obs['hand'][a.slot],
                        elapsed_ms=(time.perf_counter()-start)*1000,belief=self.memory.to_dict())

class GreedyPolicy:
    def __init__(self,catalog,seed=0):self.catalog=catalog;self.memory=Belief()
    def decide(self,obs):
        start=time.perf_counter();validate_observation(obs);self.memory.update(obs,self.catalog)
        ranked=sorted([(tactical_score(obs,a,self.catalog),a) for a in candidates(obs,self.catalog)],key=lambda x:x[0],reverse=True)
        score,a=ranked[0]
        rows=[{'action':b.to_dict(),'card_id':obs['hand'][b.slot] if b.kind=='card' else None,'score':s} for s,b in ranked[:5]]
        return Decision(a,'按可见威胁、费用和落点评分的规则策略',obs['hand'][a.slot] if a.kind=='card' else None,
                        (time.perf_counter()-start)*1000,rows,self.memory.to_dict())

class SearchPolicy(GreedyPolicy):
    def __init__(self,catalog,seed=0,horizon_ticks=60,budget_ms=35,max_candidates=10,responses=True):
        super().__init__(catalog,seed);self.horizon_ticks=horizon_ticks;self.budget_ms=budget_ms
        self.max_candidates=max_candidates;self.responses=responses
    def decide(self,obs):
        from .backend import NativeBackend,named_decks
        start=time.perf_counter();validate_observation(obs);self.memory.update(obs,self.catalog)
        actions=candidates(obs,self.catalog)
        ordered=sorted(actions,key=lambda a:tactical_score(obs,a,self.catalog),reverse=True)[:self.max_candidates]
        if Action() not in ordered:ordered[-1:]=[Action()]
        prior=list(dict.fromkeys([*sorted(self.memory.seen),*list(named_decks().values())[0]]))
        possible=[c for c in prior if self.memory.available(c) and not self.catalog[c].get('is_evolution')]
        # Deck membership and true enemy hand are not read from any live backend.
        hypotheses=[(self.memory.low,False)]
        if self.responses:hypotheses.append((self.memory.high,True))
        bases=[NativeBackend.from_observation(obs,possible,elixir) for elixir,_ in hypotheses]
        ranked=[]
        for a in ordered:
            if ranked and (time.perf_counter()-start)*1000>=self.budget_ms:break
            values=[]
            for base,(_,react) in zip(bases,hypotheses):
                model=NativeBackend.__new__(NativeBackend)
                model.cr=base.cr;model.catalog=base.catalog;model.restore(base.snapshot())
                model.starting_elixir=base.starting_elixir
                feedback=model.apply(0,a)
                if feedback['status']=='rejected':values.append(-1e6);continue
                responder=GreedyPolicy(self.catalog)
                advanced=0
                while advanced<self.horizon_ticks and not model.done:
                    if react:
                        enemy_obs=model.observe(1);response=responder.decide(enemy_obs)
                        model.apply(1,response.action)
                    chunk=min(20,self.horizon_ticks-advanced)
                    model.advance(chunk);advanced+=chunk
                value=utility(model.observe(0),self.catalog)
                if model.done:value+=30 if model.result(0)=='win' else -30 if model.result(0)=='loss' else 0
                values.append(value)
            score=.7*sum(values)/len(values)+.3*min(values)
            if a.kind=='card':score-=.03*self.catalog[obs['hand'][a.slot]]['cost']
            ranked.append({'action':a.to_dict(),'card_id':obs['hand'][a.slot] if a.kind=='card' else None,
                           'score':score,'hypotheses':values})
        ranked.sort(key=lambda r:r['score'],reverse=True)
        chosen=ranked[0];a=Action(**chosen['action'])
        return Decision(a,f"{self.horizon_ticks/10:g}秒原生引擎前瞻；评估{len(ranked)}/{len(actions)}候选，预算{self.budget_ms:g}ms",
                        chosen['card_id'],(time.perf_counter()-start)*1000,ranked[:5],self.memory.to_dict())

def make_policy(name,catalog,seed=0):
    if name=='random':return RandomPolicy(catalog,seed)
    if name=='greedy':return GreedyPolicy(catalog,seed)
    if name=='search':return SearchPolicy(catalog,seed)
    if name=='search_no_response':return SearchPolicy(catalog,seed,responses=False)
    if name in ('learned','ppo','ppo_sample'):
        from .learning import LearnedPolicy
        checkpoint='ppo' if name=='ppo_sample' else name
        return LearnedPolicy(catalog,f'models/platform/{checkpoint}.pt',seed,sample=name=='ppo_sample')
    raise ValueError('Unknown policy: '+name)

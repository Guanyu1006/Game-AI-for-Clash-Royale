"""Audited C++ bridge. Full state is exposed only by oracle(), never by observe()."""
import copy
from functools import lru_cache
import importlib
import json
import hashlib
import math
import os
import re
import sys
import time
import threading
from .contracts import Action,validate_observation
from .runtime import ROOT,local,target

UPSTREAM='2f172925a15b17f973291f41a789b19a29f0fc43'
VERSION='ClashRoyaleAi@'+UPSTREAM[:12]+'+lab-calibrated-v2'
BASE_VERSION=VERSION
BALANCE_INFO={'changed':False,'hash':None,'path':None,'supported':False}
NATIVE_LOCK=threading.RLock()
LOADED_NATIVE=None

def _configure_numbers(module):
    global VERSION,BALANCE_INFO
    path=local(os.environ.get('ROYALE_BALANCE_FILE','data/platform/balance_v2.json'))
    if not hasattr(module,'export_numeric_stats'):
        if path.exists():raise RuntimeError('Numeric config exists but native module lacks loader; rebuild tools/build_native.ps1')
        return
    before=module.export_numeric_stats()
    digest=lambda data:hashlib.sha256(json.dumps(data['cards'],sort_keys=True,separators=(',',':'),allow_nan=False).encode()).hexdigest()
    baseline_hash=digest(before)
    if path.exists():module.apply_numeric_overrides(json.loads(path.read_text(encoding='utf-8-sig')),reset=True)
    elif os.environ.get('ROYALE_BALANCE_FILE'):raise ValueError('Custom balance JSON not found')
    after=module.export_numeric_stats();effective_hash=digest(after)
    changed=effective_hash!=baseline_hash
    VERSION=BASE_VERSION+('+balance'+effective_hash[:12] if changed else '')
    BALANCE_INFO={'supported':True,'hash':effective_hash,'baseline_hash':baseline_hash,'changed':changed,
                  'path':str(path) if path.exists() else None,'version':VERSION}

def balance_info():
    native_module()
    return dict(BALANCE_INFO)

@lru_cache(maxsize=1)
def native_module():
    with NATIVE_LOCK:return _load_native()

def _load_native():
    global LOADED_NATIVE
    if LOADED_NATIVE is not None:return LOADED_NATIVE
    folders=[]
    if os.environ.get('ROYALE_NATIVE_DIR'):folders.append(local(os.environ['ROYALE_NATIVE_DIR']))
    manifest=ROOT/'outputs/native_engine/build_manifest.json'
    if manifest.exists():
        info=json.loads(manifest.read_text(encoding='utf-8-sig'))
        if info.get('module_path'):folders.append(local(info['module_path']).parent)
    source=ROOT/'third_party/ClashRoyaleAi'
    folders += [p.parent for p in source.rglob('clash_royale_env*.pyd')]
    for folder in dict.fromkeys(folders):
        sys.path.insert(0,str(folder))
        try:
            module=importlib.import_module('clash_royale_env')
            if not hasattr(module.ClashRoyaleEnv,'state_dict'):
                raise RuntimeError('Native extension lacks lab bridge patch; rebuild')
            _configure_numbers(module)
            LOADED_NATIVE=module
            return module
        except ImportError:continue
    raise RuntimeError('Audited native engine is not built for this interpreter; run powershell -File tools/build_native.ps1')

@lru_cache(maxsize=1)
def card_catalog():
    cr=native_module()
    ids=cr.get_registry_card_ids() if hasattr(cr,'get_registry_card_ids') else range(cr.ClashRoyaleEnv.NUM_CARD_IDS)
    cards={}
    for cid in ids:
        try:info=dict(cr.get_card_info(cid))
        except (ValueError,RuntimeError):continue
        info.setdefault('hp',0);info.setdefault('count',1)
        if hasattr(cr,'get_card_profile'):
            profile=dict(cr.get_card_profile(cid,info.get('is_evolution',False)))
            info['count']=profile['initial_body_count'] or 1
            if profile['bodies']:
                body=profile['bodies'][0]
                for key in ('damage','range','attack_interval','target_air','speed','spell_radius','spell_damage_per_application','spell_crown_damage_per_application','spell_applications'):
                    if key in body:info[key]=body[key]
        cards[cid]=info
    # Card metadata is a versioned source snapshot, not a latest official balance file.
    output=target(f'data/platform/catalogs/{BALANCE_INFO["hash"]}.json' if BALANCE_INFO['changed'] else 'data/platform/card_catalog.json')
    temporary=output.with_name(f'.{output.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    temporary.write_text(json.dumps({'version':VERSION,'source_commit':UPSTREAM,'balance':balance_info(),
        'source':'third_party/ClashRoyaleAi/include/core/CardRegistry.h','cards':list(cards.values()),
        'fidelity':'upstream reconstructed rules; official equivalence unverified'},ensure_ascii=False,indent=2),encoding='utf-8')
    os.replace(temporary,output)
    return cards

def named_decks():
    catalog=card_catalog()
    norm=lambda s:re.sub('[^a-z0-9]','',s.lower())
    lookup={norm(c['name']):i for i,c in catalog.items() if not c.get('is_evolution',False)}
    definitions={
        'Hog Cycle':['hog rider','musketeer','cannon','ice golem','skeletons','ice spirit','the log','fireball'],
        'Giant Control':['giant','knight','archers','minions','musketeer','mini pekka','arrows','fireball'],
        'Air Control':['balloon','lava hound','mega minion','minions','tombstone','arrows','fireball','guards'],
        'Bridge Pressure':['battle ram','bandit','royal ghost','magic archer','pekka','electro wizard','zap','poison'],
    }
    result={}
    for label,names in definitions.items():
        try:deck=[lookup[norm(n)] for n in names]
        except KeyError:continue
        if not native_module().validate_deck_slots(deck):result[label]=deck
    if not result:result['Registry sample']=list(native_module().sample_random_deck(0))
    return result

def catalog_payload():
    cards=card_catalog()
    return {'version':VERSION,'cards':list(cards.values()),'decks':named_decks(),'balance':balance_info(),
            'geometry':{'width':18,'height':34,'x_max':17,'y_max':33,'bridge_y':16.5},
            'fidelity':'research engine; see data/platform/*coverage.csv'}

class NativeBackend:
    version=VERSION
    def __init__(self,seed=0,decks=None,max_ticks=3000):
        self.cr=native_module();self.catalog=card_catalog()
        if not isinstance(max_ticks,int) or not 1 <= max_ticks <= 100_000:raise ValueError('Invalid max_ticks')
        self.max_ticks=max_ticks
        self.reset(seed,decks)

    def reset(self,seed=0,decks=None):
        self.version=VERSION
        if not isinstance(seed,int) or not 0<=seed<=2**32-1:raise ValueError('Seed must be uint32')
        default=list(named_decks().values())
        self.decks=copy.deepcopy(decks or [default[0],default[min(1,len(default)-1)]])
        if len(self.decks)!=2:raise ValueError('Two decks required')
        for deck in self.decks:
            if len(deck)!=8 or any(not isinstance(c,int) or c not in self.catalog for c in deck):
                raise ValueError('Each deck needs eight registered card IDs')
            if len(set(deck))!=8:raise ValueError('Deck cards must be unique')
            error=self.cr.validate_deck_slots(deck)
            if error:raise ValueError(error)
        self.seed=seed
        self.env=self.cr.ClashRoyaleEnv(self.decks[0],self.decks[1],self.max_ticks)
        self.env.seed(seed)
        self.events=[];self.journal=[];self.command_ids={};self.pending=[]
        self.starting_elixir=self.env.get_elixir_for_team(0)
        return self.observe()

    @property
    def tick(self):return self.env.get_current_tick()
    @property
    def done(self):return self.env.is_game_over()

    def oracle(self):
        raw=dict(self.env.state_dict())
        units=[u for u in raw['entities'] if u.get('alive',True) and u['kind'] in ('troop','building')]
        towers=[{**t,'king':t['slot']==0} for t in raw['towers']]
        return {**raw,'units':units,'towers':towers,'events':copy.deepcopy(self.events),
                'max_ticks':self.max_ticks,'width':18,'height':34}

    def observe(self,player=0):
        if player not in (0,1):raise ValueError('Invalid player')
        state=self.oracle();own=state['players'][player]
        def transform(unit):
            unit=dict(unit)
            unit['team']=0 if unit['team']==player else 1
            if player==1:
                unit['x'],unit['y']=17-unit['x'],33-unit['y']
                if 'slot' in unit and unit['slot'] in (1,2):unit['slot']=3-unit['slot']
            return unit
        events=[transform(e) for e in self.events]
        obs={'tick':state['tick'],'time':state['tick']/10,'width':18,'height':34,
             'max_ticks':self.max_ticks,'elixir':own['elixir'],'hand':list(own['hand']),
             'next_card':own.get('next_card'),'own_deck':list(self.decks[player]),
             'hand_cooldowns':own.get('hand_cooldowns',[0]*4),
             'ability_ready':own.get('ability_ready',[False,False]),
             'units':[transform({k:v for k,v in u.items() if k in
                ('id','card_id','name','team','x','y','hp','max_hp','air','kind','alive','radius','shield_hp','freeze_ticks','slow_ticks')}) for u in state['units']],
             'towers':[transform(t) for t in state['towers']],
             'events':events,'history_complete':True,'starting_elixir':self.starting_elixir,
             'source':'native-visible','done':self.done,'confidence':1,
             'elixir_multiplier':state['elixir_multiplier']}
        validate_observation(obs)
        from .policies import raw_candidates
        obs['legal_actions']=[a.to_dict() for a in raw_candidates(obs,self.catalog) if self.legal(player,a)]
        return obs

    def legal(self,player,action):
        try:action.validate()
        except (TypeError,ValueError):return False
        if player not in (0,1) or self.done:return action.kind=='wait'
        if action.kind=='wait':return True
        if action.kind=='ability':
            return action.slot in (1,2) and self.env.is_champion_ability_ready(player,action.slot)
        hand=self.env.get_hand_for_team(player)
        if not 0<=action.slot<len(hand):return False
        if self.env.state_dict()['players'][player]['hand_cooldowns'][action.slot]>0:return False
        cid=hand[action.slot]
        x,y=(action.x,action.y) if player==0 else (17-action.x,33-action.y)
        if hasattr(self.env,'can_play'):return self.env.can_play(player,action.slot,x,y)
        return self.env.get_elixir_for_team(player)+1e-5>=self.catalog[cid]['cost'] and self.env.is_valid_placement(cid,x,y,player)

    def apply(self,player,action,command_id=None):
        action.validate()
        if player not in (0,1):raise ValueError('Invalid player')
        key=f'{player}:{command_id}' if command_id else None
        if key and key in self.command_ids:
            return {**self.command_ids[key],'duplicate':True}
        if action.delay_ticks:
            delayed=Action(action.kind,action.slot,action.x,action.y,0)
            self.pending.append((self.tick+action.delay_ticks,player,delayed,command_id))
            feedback={'status':'queued','tick':self.tick,'execute_at':self.tick+action.delay_ticks}
            if key:self.command_ids[key]=feedback
            return feedback
        before_hand=list(self.env.get_hand_for_team(player));before_elixir=self.env.get_elixir_for_team(player)
        feedback={'status':'rejected','tick':self.tick,'player':player,'reason':'illegal placement, affordability, cooldown or terminal state'}
        if action.kind=='wait':feedback.update(status='wait',reason='explicit WAIT')
        elif self.legal(player,action):
            if action.kind=='ability':ok=self.env.activate_champion_ability(player,action.slot)
            else:
                x,y=(action.x,action.y) if player==0 else (17-action.x,33-action.y)
                ok=self.env.direct_play(player,action.slot,x,y)
            if ok:
                feedback.update(status='accepted',reason='native engine accepted action')
                self.journal.append({'tick':self.tick,'player':player,'action':action.to_dict()})
                if action.kind=='card':
                    cid=before_hand[action.slot]
                    self.events.append({'id':len(self.events),'tick':self.tick,'time':self.tick/10,'team':player,
                                        'card_id':cid,'cost':before_elixir-self.env.get_elixir_for_team(player),
                                        'x':x,'y':y,'evidence':'confirmed'})
                else:
                    self.events.append({'id':len(self.events),'tick':self.tick,'time':self.tick/10,'team':player,
                        'card_id':-1,'kind':'ability','cost':before_elixir-self.env.get_elixir_for_team(player),
                        'x':0,'y':0,'evidence':'confirmed'})
        feedback['before_hand']=before_hand
        feedback['after_hand']=list(self.env.get_hand_for_team(player))
        feedback['elixir_spent']=round(before_elixir-self.env.get_elixir_for_team(player),6)
        if key:self.command_ids[key]=dict(feedback)
        return feedback

    def _drain_pending(self):
        due=[item for item in self.pending if item[0]<=self.tick]
        self.pending=[item for item in self.pending if item[0]>self.tick]
        for _,player,action,cid in due:
            if cid:self.command_ids.pop(f'{player}:{cid}',None)
            self.apply(player,action,cid)

    def advance(self,ticks=1):
        if not isinstance(ticks,int) or ticks<0 or ticks>100_000:raise ValueError('Invalid tick count')
        remaining=ticks
        while remaining>0 and not self.done:
            self._drain_pending()
            future=min([p[0] for p in self.pending],default=self.tick+remaining)
            chunk=max(1,min(remaining,future-self.tick))
            previous=self.tick;self.env.advance_ticks(chunk)
            advanced=self.tick-previous
            if advanced<=0:break
            remaining-=advanced
        if not self.done:self._drain_pending()

    def result(self,player=None):
        if not self.done:return None
        state=self.env.state_dict();winner=state.get('winner',-1)
        if player is None:return {'winner':winner,'tick':self.tick}
        return 'draw' if winner==-1 else 'win' if winner==player else 'loss'

    def snapshot(self):
        return {'env':self.env.snapshot(),'version':self.version,'seed':self.seed,'decks':copy.deepcopy(self.decks),
                'events':copy.deepcopy(self.events),'journal':copy.deepcopy(self.journal),
                'command_ids':copy.deepcopy(self.command_ids),'pending':copy.deepcopy(self.pending),'max_ticks':self.max_ticks,
                'starting_elixir':self.starting_elixir}

    def restore(self,snapshot):
        self.env=snapshot['env'].snapshot();self.seed=snapshot['seed'];self.decks=copy.deepcopy(snapshot['decks'])
        self.events=copy.deepcopy(snapshot['events']);self.journal=copy.deepcopy(snapshot['journal'])
        self.command_ids=copy.deepcopy(snapshot['command_ids']);self.pending=copy.deepcopy(snapshot['pending'])
        self.max_ticks=snapshot['max_ticks']
        self.version=snapshot.get('version',VERSION)
        self.starting_elixir=snapshot.get('starting_elixir',5)

    def export_snapshot(self):
        return {'format':'royale-lab-journal-v1','engine':self.version,'balance':balance_info(),'seed':self.seed,'decks':self.decks,
                'max_ticks':self.max_ticks,'tick':self.tick,'journal':self.journal,
                'command_ids':self.command_ids,
                'pending':[{'tick':t,'player':p,'action':a.to_dict(),'command_id':c} for t,p,a,c in self.pending]}

    def load_snapshot(self,payload):
        if payload.get('format')!='royale-lab-journal-v1' or payload.get('engine')!=self.version:
            raise ValueError('Snapshot format/engine version mismatch')
        self.max_ticks=int(payload['max_ticks']);self.reset(int(payload['seed']),payload['decks'])
        for item in payload['journal']:
            if item['tick']<self.tick:raise ValueError('Journal time moved backwards')
            self.advance(item['tick']-self.tick)
            feedback=self.apply(item['player'],Action(**item['action']))
            if feedback['status']!='accepted':raise ValueError('Journal action failed to reproduce')
        self.advance(int(payload['tick'])-self.tick)
        self.pending=[(p['tick'],p['player'],Action(**p['action']),p.get('command_id')) for p in payload.get('pending',[])]
        self.command_ids=copy.deepcopy(payload.get('command_ids',{}))

    @classmethod
    def from_observation(cls,obs,enemy_cards,enemy_elixir,catalog=None):
        # Reconstruct using only public observation + explicit hidden-state hypotheses.
        own=list(obs['own_deck'])
        plain=[c for c in obs['own_deck'] if not (card_catalog()[c].get('is_champion') or card_catalog()[c].get('is_hero') or card_catalog()[c].get('is_evolution'))]
        guessed=list(dict.fromkeys([*enemy_cards,*plain,*named_decks()[next(iter(named_decks()))]]))[:8]
        if len(guessed)<8:raise ValueError('Enemy deck hypothesis incomplete')
        if native_module().validate_deck_slots(guessed):
            guessed=list(next(iter(named_decks().values())))
        model=cls(0,[own,guessed],obs['max_ticks'])
        model.env.set_hand_for_team(0,obs['hand'])
        enemy_hand=[c for c in enemy_cards if c in guessed][:4]
        if len(enemy_hand)==4:model.env.set_hand_for_team(1,enemy_hand)
        model.env.set_current_tick(obs['tick'])
        model.env.set_elixir_for_team(0,obs['elixir']);model.env.set_elixir_for_team(1,enemy_elixir)
        for t in obs['towers']:
            if t['hp']<=0:model.env.destroy_tower(t['team'],t['slot'])
            else:model.env.set_tower_hp(t['team'],t['slot'],t['hp'])
        # One card inject can spawn squads: group matching bodies within a small radius.
        inserted=[]
        for u in obs['units']:
            cid=u['card_id']
            if cid not in model.catalog:continue
            if any(v['card_id']==cid and v['team']==u['team'] and math.hypot(v['x']-u['x'],v['y']-u['y'])<2.0 for v in inserted):continue
            model.env.inject(cid,u['x'],u['y'],u['team'],u['hp'],0)
            inserted.append(u)
        model.events=copy.deepcopy(obs['events'])
        return model

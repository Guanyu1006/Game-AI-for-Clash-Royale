"""Native Gym-style agent loop with a narrow, version-proved action space.

The model sees only backend.observe_visible(owner). The opponent receives its
own separate conservative observation. Neither policy receives replay futures.
Gymnasium spaces are supplied when that optional runtime is installed.
"""
from copy import deepcopy
import json
from pathlib import Path
import uuid

import numpy as np
try:
    import gymnasium as gym
    from gymnasium import spaces
    BaseEnv = gym.Env
except ImportError:
    spaces = None
    BaseEnv = object

from .runtime import ROOT, local
from .backend import NativeBattleBackend
from .trajectories import audit_subspace_proof, subspace_mask
from .versioning import verify_manifest

REWARD_VERSION = 'native-terminal-owner-outcome.v1'
OBS_VERSION = 'native-own-hud-gym.v1'
ALLOWED_SPELLS = (28000000, 28000001)


def integer(value, name, low=0, high=2**31-1):
    if isinstance(value, np.integer) and not isinstance(value,np.bool_):value=int(value)
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}')
    return value


def validate_policy_action(actor, mask, action):
    """Pure boundary check; it performs no communication or state advance."""
    if not isinstance(action, dict): raise ValueError('Action must be a dictionary')
    supplied_index=integer(action['action_index'],'action index',0,4) if 'action_index' in action else None
    if action.get('kind') == 'wait' or supplied_index == 0:
        return {'kind': 'wait'}
    if 'action_index' in action:
        index = supplied_index
        slot = index-1
    elif action.get('kind') == 'play_card':
        slot = integer(action.get('slot'), 'hand slot', 0, 3); index = slot+1
    else: raise ValueError('Only WAIT or an ordinary Fireball/Arrows hand slot is supported')
    if not mask.get('full_legality_verified') or mask.get('subspace') != 'wait_or_arena_spell.v1' or not mask['values'][index]:
        raise ValueError('Action is outside the currently proved narrow action subspace')
    cards = [c for c in actor['hand'] if c['slot'] == slot]
    if len(cards) != 1 or cards[0]['card'] not in ALLOWED_SPELLS:
        raise ValueError('Selected current hand card is not an allowed ordinary spell')
    if 'card' in action and action['card'] != cards[0]['card']:
        raise ValueError('Policy card ID does not match its current hand slot')
    return {'kind': 'play_card', 'slot': slot, 'card': cards[0]['card'],
            'x': integer(action.get('x'), 'native requested x', 0, 17999),
            'y': integer(action.get('y'), 'native requested y', 0, 31999)}


def gym_observation(actor, mask):
    hand = np.zeros((4,3), dtype=np.int64)
    for card in actor['hand']: hand[card['slot']] = [card['card'], card['cost'], 1]
    towers = np.zeros((6,3), dtype=np.int64)
    by_role = {(t['side'],t['role']): t for t in actor['towers']}
    for index,key in enumerate((s,r) for s in ('own','opponent') for r in ('king','left','right')):
        tower = by_role.get(key)
        if tower and tower['present']: towers[index] = [tower['hp'],tower['max_hp'],1]
    return {'hand': hand, 'own_elixir_raw': np.asarray([actor['own_elixir_raw']],dtype=np.int64),
            'tick': np.asarray([actor['tick']],dtype=np.int64),
            'crowns': np.asarray([actor['crowns']['own'],actor['crowns']['opponent']],dtype=np.int64),
            'towers': towers, 'action_mask': np.asarray(mask['values'],dtype=np.int8)}


def public_heuristic(actor, decision_index):
    """Declared nonmodel opponent: own budget and public towers, fixed placements."""
    affordable = [c for c in actor['hand'] if c['cost']*10000 <= actor['own_elixir_raw']]
    if not affordable: return {'kind':'wait'}
    card = sorted(affordable,key=lambda c:(-c['cost'],(c['slot']-decision_index)%4))[0]
    owner = actor['native_owner']; x = 3500 if decision_index%2==0 else 14500
    if card['card'] >= 28000000:
        targets = [t for t in actor['towers'] if t['side']=='opponent' and t['role']!='king' and t['present'] and t['hp']>0]
        if targets:
            target = min(targets,key=lambda t:t['hp']); x = 3500 if target['role']=='left' else 14500
        y = 25500 if owner==0 else 6500
    elif card['card'] >= 27000000: x,y = 9000,(11000 if owner==0 else 21000)
    else: y = 12000 if owner==0 else 20000
    return {'kind':'play_card','slot':card['slot'],'card':card['card'],'x':x,'y':y}


class NativeAgentEnv(BaseEnv):
    metadata = {'render_modes': [], 'render_fps':20}

    def __init__(self, *, port=26789, owner=0, step_ticks=20, max_tick=7200,
                 opponent_interval_ticks=20, proof='data/phase2/action_subspace_proof.json',
                 version_manifest='data/phase2/version_manifest.json', trace_path=None, backend=None):
        integer(owner,'native owner',0,1);integer(step_ticks,'step ticks',1,1000)
        integer(max_tick,'native watchdog tick',91,1000000);integer(opponent_interval_ticks,'opponent interval',1,1000000)
        self.owner=owner;self.step_ticks=step_ticks;self.max_tick=max_tick
        self.opponent_interval=opponent_interval_ticks;self.port=port;self.trace_path=trace_path
        self.version=verify_manifest(version_manifest);self.version_manifest=version_manifest
        self.proof=audit_subspace_proof(proof,self.version['fingerprint'])
        if self.proof['status']!='verified_local_rule_evidence':raise ValueError('Current native action-subspace proof is required')
        self.backend=backend;self._active=False;self._done=True;self._actor=None;self._mask=None
        self._opponent_index=0;self._next_opponent_tick=90;self._steps=0;self._episode_id=None
        self.last_audit_step=None
        if spaces is not None:
            self.action_space=spaces.Dict({'action_index':spaces.Discrete(5),'x':spaces.Discrete(18000),'y':spaces.Discrete(32000)})
            self.observation_space=spaces.Dict({'hand':spaces.Box(0,2**31-1,(4,3),dtype=np.int64),
                'own_elixir_raw':spaces.Box(0,100000,(1,),dtype=np.int64),
                'tick':spaces.Box(0,2**31-1,(1,),dtype=np.int64),'crowns':spaces.Box(0,3,(2,),dtype=np.int64),
                'towers':spaces.Box(0,2**31-1,(6,3),dtype=np.int64),'action_mask':spaces.MultiBinary(5)})

    @staticmethod
    def default_match(seed=1, level=16):
        integer(seed,'seed',0);integer(level,'requested level',1,16)
        template=local('outputs/phase2/native/match_probe/runtime86_first/initialization.json')
        match=json.loads(template.read_text(encoding='utf-8'));match['rndSeed']=seed
        match['battle']['lvlcap']=level;match['battle']['cardlvlmin']=level
        for owner in (0,1):
            match['battle'][f'avatar{owner}']['expLevel']=level
            match['battle'][f'avatar{owner}']['name']=f'Local-Agent-{owner}'
        return match

    def _observe(self):
        self._actor=self.backend.observe_visible(self.owner)
        ready=bool(self._active and not self._done and self._actor['tick']>=90 and self._actor['hand_complete'])
        self._mask=subspace_mask(self._actor,ready,self.proof)
        return gym_observation(self._actor,self._mask)

    def actor_observation(self):
        if self._actor is None:raise RuntimeError('Reset before requesting the actor observation')
        return deepcopy(self._actor)

    def legal_mask(self):
        if self._mask is None:raise RuntimeError('Reset before requesting the action mask')
        return deepcopy(self._mask)

    def reset(self, *, seed=None, options=None):
        if spaces is not None:super().reset(seed=seed)
        options=dict(options or {});seed=1 if seed is None else integer(seed,'seed',0)
        match=deepcopy(options.get('match') or self.default_match(seed,options.get('level',16)))
        if match.get('cmd') or match.get('evt'):raise ValueError('Agent evaluation requires a fresh match without replay future commands/events')
        for owner in (0,1):
            deck=match['battle'][f'deck{owner}']['sp'];ids=[c['d'] for c in deck]
            if len(ids)!=8 or len(set(ids))!=8:raise ValueError('Agent fixture requires eight unique native deck cards')
        if self.backend is None:self.backend=NativeBattleBackend(self.version_manifest,port=self.port,trace_path=self.trace_path)
        self.backend.reset(match);self._active=True;self._done=False;self._steps=0
        self._episode_id=str(uuid.uuid4());self._opponent_index=0;self._next_opponent_tick=90
        self.last_audit_step={'reset_match':match,'episode_id':self._episode_id}
        observation=self._observe()
        return observation,{'actor':self.actor_observation(),'legal_mask':self.legal_mask(),
                            'observation_version':OBS_VERSION,'reward_version':REWARD_VERSION,
                            'scope':'Model is WAIT/ordinaryFireball/Arrows only; opponent is the declared public-information heuristic'}

    def step(self, action):
        if not self._active or self._done:raise RuntimeError('Reset is required before step or after terminated/truncated')
        chosen=validate_policy_action(self._actor,self._mask,action);tick=self._actor['tick']
        audit={'episode_id':self._episode_id,'decision_tick':tick,'model_actor':self.actor_observation(),
               'model_legal_mask':self.legal_mask(),'model_action':chosen,'registrations':[]}
        if chosen['kind']=='play_card':
            registration=self.backend.schedule_card(self.owner,chosen['card'],chosen['x'],chosen['y'],tick+1,
                request_id=f'{self._episode_id}:model:{self._steps}')
            audit['registrations'].append({'controller':'model','registration':registration})
        if tick>=self._next_opponent_tick:
            opponent_actor=self.backend.observe_visible(1-self.owner)
            opponent=public_heuristic(opponent_actor,self._opponent_index);audit['opponent_action']=opponent
            self._opponent_index+=1;self._next_opponent_tick=tick+self.opponent_interval
            if opponent['kind']=='play_card':
                registration=self.backend.schedule_card(1-self.owner,opponent['card'],opponent['x'],opponent['y'],tick+1,
                    request_id=f'{self._episode_id}:heuristic:{self._steps}')
                audit['registrations'].append({'controller':'public_heuristic','registration':registration})
        advanced=self.backend.advance_exact_ticks(min(self.step_ticks,self.max_tick-tick))
        result=self.backend.result();terminated=bool(result['finalized']);truncated=bool(not terminated and result['tick']>=self.max_tick)
        reward=0.0;reward_status='nonterminal_zero'
        if terminated:
            if result['result'] in (0,1):reward=1.0 if result['result']==self.owner else -1.0;reward_status='native_winner_owner'
            else:reward_status='unverified_terminal_result_encoding'
        self._steps+=1;self._done=terminated or truncated;observation=self._observe()
        audit.update(result=result,reward=reward,reward_status=reward_status,terminated=terminated,truncated=truncated,
                     actual_card_events=deepcopy(advanced['plays']))
        self.last_audit_step=audit
        return observation,reward,terminated,truncated,{'actor':self.actor_observation(),'legal_mask':self.legal_mask(),
            'public_result':result,'reward_version':REWARD_VERSION,'reward_status':reward_status,
            'truncation_reason':'native_tick_watchdog' if truncated else None}

    def close(self):
        if self.backend is not None:self.backend.close()
        self._active=False;self._done=True

"""Eight ordinary cards, public affordability and explicit coordinate conventions.

Masks below are conservative proposals, NOT a full native legality oracle.
Only actual backend consumption certifies that an individual request was used.
"""
import random
from .common import read

DECK = (26000000,26000001,26000003,26000014,26000018,27000000,28000000,28000001)
GRID_X, GRID_Y = 18, 32
STEP_TICKS=10

def grid_to_native(index):
    if type(index) is not int or not 0<=index<576: raise ValueError('Invalid position grid cell')
    return (index%18)*1000+500,(index//18)*1000+500
def native_to_grid(x,y):
    if type(x) is not int or type(y) is not int or not 0<=x<18000 or not 0<=y<32000:
        raise ValueError('Native position out of bounds')
    return y//1000*18+x//1000
def flip_native(x,y): return 17999-x,31999-y
def normalize_position(x,y,owner): return (x,y) if owner==0 else flip_native(x,y)

def mask(actor):
    slots={c['slot']:c for c in actor['hand']}
    return [True]+[i in slots and slots[i]['card'] in DECK and slots[i]['cost']*10000<=actor['own_elixir_raw'] for i in range(4)]

def position_mask(card,owner):
    # Fixed ordinary arena hypothesis; special/forward deployment is unsupported.
    if card not in DECK: raise ValueError('Unsupported card')
    return [card>=28000000 or ((i//18)<16 if owner==0 else (i//18)>=16) for i in range(576)]

def validate(actor,action):
    if action.get('kind')=='wait': return {'kind':'wait'}
    slot=action.get('slot')
    if type(slot) is not int or not 0<=slot<4 or not mask(actor)[slot+1]: raise ValueError('Slot unavailable or unaffordable')
    card=next(c for c in actor['hand'] if c['slot']==slot)['card']
    x,y=action.get('x'),action.get('y');cell=native_to_grid(x,y)
    if not position_mask(card,actor['native_owner'])[cell]: raise ValueError('Outside supported deployment proposal region')
    return {'kind':'play_card','slot':slot,'card':card,'x':x,'y':y}

def match(seed=310000):
    value=read('outputs/phase2/native/match_probe/runtime86_first/initialization.json')
    value['cmd']=[];value['evt']=[];value['rndSeed']=seed
    # Equal explicit native level. Evolutions/forms/abilities are not activated.
    value['battle']['lvlcap']=16;value['battle']['cardlvlmin']=16
    for owner in (0,1):
        value['battle'][f'deck{owner}']['sp']=[{'d':c} for c in DECK]
        value['battle'][f'avatar{owner}']['expLevel']=16
    return value

class PublicTeacher:
    def __init__(self,kind='cycle',seed=1):
        if kind not in ('cycle','random','wait'): raise ValueError('Unknown public teacher')
        self.kind=kind;self.rng=random.Random(seed);self.last=-100;self.index=0
    def decide(self,actor):
        tick=actor['tick'];allowed=mask(actor)
        if self.kind=='wait' or tick-self.last<20: return {'kind':'wait'}
        cards=[c for c in actor['hand'] if c['card'] in DECK]
        if not cards:return {'kind':'wait'}
        if self.kind=='random':
            if self.rng.random()<0.4:return {'kind':'wait'}
            card=self.rng.choice(cards)
        else: card=min(cards,key=lambda c:(c['slot']-self.index)%4)
        # Saving elixir for the selected card prevents expensive cards from
        # being starved forever by repeatedly spending on cheap alternatives.
        if not allowed[card['slot']+1]: return {'kind':'wait'}
        self.index=(card['slot']+1)%4;self.last=tick
        owner=actor['native_owner'];lane=self.rng.choice((3500,14500))
        y=(25500 if owner==0 else 6500) if card['card']>=28000000 else (11000 if owner==0 else 21000)
        if card['card']==27000000:lane=8500
        return validate(actor,{'kind':'play_card','slot':card['slot'],'x':lane,'y':y})

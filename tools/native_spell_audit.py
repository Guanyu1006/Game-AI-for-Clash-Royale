"""Measure real native spell damage to co-located troop and Crown Tower."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'third_party/ClashRoyaleAi/python_ai'))
import clash_royale_env as native

def measure(card_id):
    deck=[card_id]+[i for i in [0,1,2,3,4,5,6,7] if i!=card_id][:7]
    e=native.ClashRoyaleEnv(deck,[0,1,2,3,4,5,6,7],3000); e.seed(7)
    e.set_hand_for_team(0,[card_id]+[x for x in deck if x!=card_id][:3]); e.set_elixir_for_team(0,10)
    # Stationary Knight fixture; no opponent controller. Deployment lock removes
    # movement and attack damage as confounders. Exact HP is a test fixture.
    e.inject(0,3,27,1,10000,1000); e.advance_ticks(1)
    s=e.state_dict(); unit=next(x for x in s['entities'] if x['card_id']==0)
    before_tower=e.get_tower_hp(1,1); before_unit=unit['hp']
    assert e.direct_play(0,0,3,27)
    e.advance_ticks(100)
    after=next(x for x in e.state_dict()['entities'] if x['id']==unit['id'])
    return {'card_id':card_id,'name':native.get_card_info(card_id)['name'],
            'tower_damage':before_tower-e.get_tower_hp(1,1),'troop_damage':before_unit-after['hp']}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',default='outputs/native_engine_spell_baseline.json'); args=p.parse_args()
    results=[measure(c) for c in [3,7,29,30,31,32]]
    (ROOT/args.output).write_text(json.dumps(results,indent=2),encoding='utf-8'); print(json.dumps(results,indent=2))

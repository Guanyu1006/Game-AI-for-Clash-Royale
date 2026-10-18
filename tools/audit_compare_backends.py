"""Read actual independent-engine measurements and execute native rule probes."""
from __future__ import annotations
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'third_party/ClashRoyaleAi/python_ai'))
sys.path.insert(0, str(ROOT / 'tools'))
import clash_royale_env as native
from native_spell_audit import measure

if __name__ == '__main__':
    cr = json.loads((ROOT / 'outputs/audit_candidates/crforge_build/mechanics_results.json').read_text())
    row = {'comparison_scope':'Both are independent approximations. Matching fixtures do not prove official equivalence.',
           'spells':[], 'native_phase_samples':[], 'crforge_phase_samples':cr['economy'],
           'known_schema_mismatch':{'native_board':[18,34],'crforge_board':[18,32],
                                  'native_princess_initial_hp':2534,'crforge_level11_princess_initial_hp':3052}}
    for card, cid in [('arrows',3),('fireball',7),('zap',29),('rocket',30),('lightning',31),('poison',32)]:
        n = measure(cid)
        other = next(x for x in cr['spells'] if x['card']==card)
        row['spells'].append({'card':card, 'native_tower_damage':n['tower_damage'],
             'crforge_tower_damage':other['tower_damage'], 'difference_native_minus_crforge':n['tower_damage']-other['tower_damage'],
             'fixture_note':'each spell centered on its own backend enemy princess; no troop attacks; native also stationary Knight target fixture'})
    e = native.ClashRoyaleEnv([0,1,2,3,4,5,6,7], [0,1,2,3,4,5,6,7], 12000)
    e.seed(7)
    for tick in [1,10,50,1199,1200,1201,1800,1801,2400,2401]:
        e.advance_ticks(tick-e.get_current_tick())
        s = e.state_dict()
        row['native_phase_samples'].append({'tick':s['tick'], 'seconds':s['tick']/10,
                    'elixir':s['players'][0]['elixir'], 'multiplier':s['elixir_multiplier'], 'done':s['done']})
    extras = []
    for i in range(250):
        try:
            if native.get_card_info(i)['name'] in ['Freeze','Rage']:
                extras.append(i)
        except ValueError:
            continue
    deck = [3,7,29,30,31,32]+extras
    e = native.ClashRoyaleEnv(deck, [0,1,2,3,4,5,6,7], 12000)
    e.seed(7)
    first = e.state_dict()['players'][0]['hand'][0]
    sequence = []
    for turn in range(1,6):
        before = e.state_dict()['players'][0]
        e.set_elixir_for_team(0,10)
        assert e.direct_play(0,0,9,5)
        e.advance_ticks(50)
        after = e.state_dict()['players'][0]
        sequence.append({'turn':turn,'played':before['hand'][0], 'hand_before':before['hand'],
                         'next_before':before['next_card'],'hand_after':after['hand'],'next_after':after['next_card']})
        assert after['hand'][0]==before['next_card'] and after['hand'][1:]==before['hand'][1:]
    assert sequence[3]['next_after']==first and sequence[4]['hand_after'][0]==first
    row['native_cycle']=sequence
    row['crforge_cycle']=cr['card_cycle']
    assert all(x['difference_native_minus_crforge']==0 for x in row['spells'][:5])
    row['five_spell_crown_damage_agreement_test']='passed'
    row['poison_difference_is_regression_fixture']='recorded, not silently accepted as official'
    out=ROOT / 'outputs/audit_candidates/backend_differential.json'
    out.write_text(json.dumps(row,indent=2),encoding='utf-8')
    print(json.dumps(row,indent=2))

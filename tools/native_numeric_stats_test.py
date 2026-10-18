"""Actual configuration-to-factory-to-battle tests in a fresh process."""
import argparse
import copy
import hashlib
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
BASE=list(range(8))

def main():
    p=argparse.ArgumentParser();p.add_argument('--module-dir',default='third_party/ClashRoyaleAi/python_ai')
    p.add_argument('--output',default='outputs/native_engine_numeric_stats.json');a=p.parse_args()
    sys.path.insert(0,str(ROOT/a.module_dir))
    import clash_royale_env as n
    checks=[]
    def check(name,condition,evidence=None):
        checks.append({'name':name,'passed':bool(condition),'evidence':evidence})
        if not condition:raise AssertionError(f'{name}: {evidence}')
    default=n.export_numeric_stats(defaults=True)
    fingerprint=lambda d:hashlib.sha256(json.dumps(d['cards'],sort_keys=True,separators=(',',':')).encode()).hexdigest()
    loaded=json.loads((ROOT/'data/platform/balance_v2.json').read_text(encoding='utf-8'))
    check('173_full_numeric_rows_41_evolutions',len(default['cards'])==173 and sum(x['evolved'] is not None for x in default['cards'].values())==41)
    check('full_json_roundtrip_no_numeric_change',n.apply_numeric_overrides(loaded)['changed_fields']==0 and fingerprint(n.export_numeric_stats())==fingerprint(default))
    rejected=False
    try:n.apply_numeric_overrides({'schema_version':True,'cards':{}})
    except ValueError:rejected=True
    check('bool_cannot_impersonate_integer_schema_version',rejected)
    byname={v['name'].lower():k for k,v in default['card_metadata'].items() if not v['is_evolution']}
    log=byname['the log'];barrel=byname['barbarian barrel'];rage=byname['rage'];clone=byname['clone']
    disc_only={'spell_radius','spell_applications','spell_interval_ticks','spell_knockback','spell_spawn_ring_radius'}
    check('rolling_export_only_settlement_fields',not (set(default['cards'][log]['base']) & disc_only) and not (set(default['cards'][barrel]['base']) & disc_only))
    check('buff_and_clone_export_excludes_ignored_knockback','spell_knockback' not in default['cards'][rage]['base'] and 'spell_knockback' not in default['cards'][clone]['base'])
    invalid=[{'99999':{'base':{'hp':12}}},{'00':{'base':{'hp':12}}},
        {'0':{'base':{'hp':False}}},{'0':{'base':{'hp':2.5}}},
        {'0':{'base':{'speed':float('nan')}}},{'0':{'base':{'damage':-1}}},
        {'0':{'base':{'attack_range':1,'min_attack_range':2}}},
        {'0':{'base':{'abilityEffect':9}}},{'165':{'base':{'hp':3000}}},
        {'164':{'base':{'cost':2}}},{'0':{'evolved':{'hp':3000}}},
        {'0':{'base':{'hp':2200}},'7':{'base':{'spell_applications':0}}},
        {log:{'base':{'spell_applications':2}}},{log:{'base':{'spell_roll_knockback':-1}}},
        {barrel:{'base':{'spell_spawn_ring_radius':3}}},{rage:{'base':{'spell_knockback':2}}},
        {clone:{'base':{'spell_knockback':2}}}]
    for index,patch in enumerate(invalid):
        before=fingerprint(n.export_numeric_stats());rejected=False
        try:n.apply_numeric_overrides({'cards':patch})
        except (ValueError,TypeError,RuntimeError):rejected=True
        check(f'invalid_batch_{index}_atomic',rejected and before==fingerprint(n.export_numeric_stats()))
    evo=next(k for k,v in default['card_metadata'].items() if v['is_evolution'] and 'knight' in v['name'].lower())
    cannon=next(k for k,v in default['card_metadata'].items() if v['name'].lower()=='cannon')
    custom={'cards':{'0':{'base':{'hp':2200,'damage':303,'cost':2,'attack_range':1.5,'speed':0.2,'attack_cooldown':8,'deploy_ticks':3}},
        '7':{'base':{'crown_damage_per_application':123}},'3':{'base':{'spell_applications':2}},
        cannon:{'base':{'building_lifetime_ticks':10}},evo:{'base':{'hp':2100},'evolved':{'hp':3300}}}}
    feedback=n.apply_numeric_overrides(custom)
    active=n.export_numeric_stats()
    profile=n.get_card_profile(0)['bodies'][0]
    check('knight_actual_factory_changed',profile['hp']==2200 and profile['damage']==303 and profile['range']==1.5 and profile['attack_interval']==8,profile)
    check('evolved_factory_separate',n.get_card_profile(int(evo),False)['bodies'][0]['hp']==2100 and n.get_card_profile(int(evo),True)['bodies'][0]['hp']==3300)
    check('full_defaults_reset_actual_factory',n.apply_numeric_overrides(loaded)['changed_fields']>0 and n.get_card_profile(0)['bodies'][0]['hp']==1766)
    n.apply_numeric_overrides(custom)
    def env(card=0):
        deck=[card]+[x for x in BASE if x!=card][:7]
        e=n.ClashRoyaleEnv(deck,BASE,3000);e.seed(7)
        e.set_hand_for_team(0,[card]+[x for x in deck if x!=card][:3]);return e
    e=env();check('knight_accepted_cost_changed',e.direct_play(0,0,3,10) and e.get_elixir_for_team(0)==3)
    e.advance_ticks(1);body=next(x for x in e.state_dict()['entities'] if x['card_id']==0)
    check('spawned_knight_actual_hp_damage_deploy_speed',body['hp']==2200 and body['damage']==303 and body['deploy_ticks']==2 and abs(body['speed']-.2)<1e-6,body)
    fight=env();fight.inject(0,8.5,14,1,10000,1000);fight.advance_ticks(1)
    target=next(x for x in fight.state_dict()['entities'] if x['card_id']==0 and x['team']==1)
    assert fight.direct_play(0,0,8.5,13)
    changes=[];previous=target['hp']
    for _ in range(20):
        fight.advance_ticks(1)
        current=next(x for x in fight.state_dict()['entities'] if x['id']==target['id'])['hp']
        if current!=previous:changes.append(previous-current)
        previous=current
    check('knight_landed_attack_uses_json_damage',bool(changes) and all(x==303 for x in changes),changes)
    def spell(card):
        e=env(card);e.set_elixir_for_team(0,10);e.inject(0,3,27,1,10000,1000);e.advance_ticks(1)
        unit=next(x for x in e.state_dict()['entities'] if x['card_id']==0);hp=unit['hp'];tower=e.get_tower_hp(1,1)
        assert e.direct_play(0,0,3,27);e.advance_ticks(100)
        after=next(x for x in e.state_dict()['entities'] if x['id']==unit['id'])
        return {'tower_damage':tower-e.get_tower_hp(1,1),'troop_damage':hp-after['hp']}
    fireball=spell(7);check('fireball_actual_tower_damage_changed_only',fireball=={'tower_damage':123,'troop_damage':689},fireball)
    arrows=spell(3);check('arrows_actual_wave_count_changed',arrows=={'tower_damage':60,'troop_damage':246},arrows)
    e=env(int(cannon));e.set_elixir_for_team(0,10);assert e.direct_play(0,0,8,10);e.advance_ticks(1)
    check('cannon_exists_before_new_lifetime',any(x['card_id']==int(cannon) for x in e.state_dict()['entities']))
    e.advance_ticks(10);check('cannon_actual_lifetime_changed',not any(x['card_id']==int(cannon) for x in e.state_dict()['entities']))
    before=e.state_dict();check('locked_same_configuration_noop',n.apply_numeric_overrides(active)['changed_fields']==0 and e.state_dict()==before)
    rejected=False
    try:n.apply_numeric_overrides(loaded)
    except RuntimeError:rejected=True
    check('post_battle_different_configuration_rejected',rejected and e.state_dict()==before and fingerprint(n.export_numeric_stats())==fingerprint(active))
    report={'module_sha256':hashlib.sha256(Path(n.__file__).read_bytes()).hexdigest(),'default_balance_hash':fingerprint(default),
        'custom_balance_hash':fingerprint(active),'apply_feedback':feedback,'checks':checks,
        'fidelity':'Tests prove exposed numeric data changes local simulator behavior, not official-game parity.'}
    (ROOT/a.output).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps({'checks':len(checks),'passed':all(x['passed'] for x in checks),'output':a.output}))

if __name__=='__main__':main()

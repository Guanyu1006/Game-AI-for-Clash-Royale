"""Native engine adapter checks and measurements (not official-game parity)."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import random
import statistics
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'third_party' / 'ClashRoyaleAi' / 'python_ai'))
import clash_royale_env as native

BASE = [0, 1, 2, 3, 4, 5, 6, 7]

def make(seed=7, ticks=3000, deck=None):
    env = native.ClashRoyaleEnv(deck or BASE, BASE, ticks)
    env.seed(seed)
    return env

def check(name, condition, records):
    records.append({'name':name, 'passed':bool(condition)})
    if not condition:
        raise AssertionError(name)

def p95(values):
    return sorted(values)[int((len(values)-1)*.95)]

def run(games):
    cards = [native.get_card_info(i) for i in native.get_registry_card_ids()]
    (ROOT/'outputs/native_engine_cards.json').write_text(json.dumps(cards,indent=2),encoding='utf-8')
    checks=[]
    env=make()
    a=env.state_dict()
    check('reset_starts_tick0_six_towers',a['tick']==0 and len(a['towers'])==6,checks)
    check('seed_reproduces_opening',a==make().state_dict(),checks)
    env.set_hand_for_team(0,[0,1,2,3])
    before=env.state_dict()
    check('reject_enemy_half_no_mutation',not env.direct_play(0,0,3,22) and before==env.state_dict(),checks)
    check('reject_nan_no_mutation',not env.direct_play(0,0,float('nan'),10) and before==env.state_dict(),checks)
    check('direct_play_accepts_without_advancing',env.direct_play(0,0,3,10) and env.get_current_tick()==0,checks)
    check('spends_elixir_actual_feedback',env.get_elixir_for_team(0)==2,checks)
    env.advance_ticks(1)
    check('accepted_play_becomes_native_entity',any(e['card_id']==0 for e in env.state_dict()['entities']),checks)
    check('new_hand_slot_cooldown_enforced',not env.direct_play(0,0,3,10),checks)
    saved=env.state_dict()
    fork=env.snapshot()
    fork.advance_ticks(50)
    check('snapshot_independent',env.state_dict()==saved and fork.get_current_tick()==51,checks)
    fork2=env.snapshot()
    env.advance_ticks(50); fork2.advance_ticks(50)
    check('snapshot_deterministic_future',env.state_dict()==fork2.state_dict(),checks)
    done=make(ticks=5); done.advance_ticks(5)
    check('time_limit_returns_terminal',done.is_game_over() and done.state_dict()['done'],checks)
    check('terminal_directplay_rejected',not done.direct_play(0,0,3,10),checks)
    done.advance_ticks(50)
    check('terminal_advance_no_extra_tick',done.get_current_tick()==5,checks)

    card_runs=[]
    for card in cards:
        cid=card['id']; pool=[x for x in BASE if x!=cid][:7]
        deck=[cid]+pool
        if card['is_champion'] or card['is_hero']:
            deck[0],deck[1]=deck[1],deck[0]
        try:
            n=make(deck=deck); hand=[cid]+[x for x in deck if x!=cid][:3]
            assert n.set_hand_for_team(0,hand)
            n.set_elixir_for_team(0,10)
            if cid==164: # Mirror needs a previous non-Mirror play.
                n.direct_play(0,1,3,10); n.advance_ticks(20)
                n.set_hand_for_team(0,hand); n.set_elixir_for_team(0,10)
            accepted=n.direct_play(0,0,3,10)
            n.advance_ticks(30)
            snap=n.snapshot(); snap.advance_ticks(20)
            row={'id':cid,'name':card['name'],'accepted':accepted,'snapshot':True,'entities_after30':len(n.state_dict()['entities']),'error':None}
            # Exercise the actual evolution cycle, not merely its base spawn.
            if card['is_evolution']:
                for _ in range(card['evolution_cycle_threshold']):
                    n.set_hand_for_team(0,hand); n.set_elixir_for_team(0,10)
                    assert n.direct_play(0,0,8.5 + (_ % 2)*3,12 - _*3)
                    n.advance_ticks(1)
                n.advance_ticks(50); n.snapshot().advance_ticks(1)
                row['evolved_cycle_exercised']=True
            if card['is_champion'] or card['is_hero']:
                row['ability_ready']=n.is_champion_ability_ready(0,1)
                row['ability_accepted']=n.activate_champion_ability(0,1)
                n.advance_ticks(30)
        except Exception as exc:
            row={'id':cid,'name':card['name'],'accepted':False,'snapshot':False,'error':f'{type(exc).__name__}: {exc}'}
        card_runs.append(row)
    (ROOT/'outputs/native_engine_card_smoke.json').write_text(json.dumps(card_runs,indent=2),encoding='utf-8')
    check('all_registry_cards_deploy_and_snapshot',all(x['accepted'] and x['snapshot'] for x in card_runs),checks)

    latencies=[]; accepted_total=0; rejected_total=0; results=[]
    started=time.perf_counter()
    for game_index in range(games):
        e=make(seed=game_index); rng=random.Random(game_index)
        while not e.is_game_over():
            for team in [0,1]:
                hand=e.get_hand_for_team(team); slot=rng.randrange(4)
                c=native.get_card_info(hand[slot]); x=rng.choice([2.5,5,8.5,11,14.5]); y=rng.choice([9,11,13,14])
                if c['is_spell'] and not c['cast_own_side_only']: y=rng.choice([10,16,22,26])
                if team: y=33-y
                ok=e.direct_play(team,slot,x,y); accepted_total+=int(ok); rejected_total+=int(not ok)
            s=time.perf_counter_ns(); e.advance_ticks(10); latencies.append((time.perf_counter_ns()-s)/1e6)
        state=e.state_dict()
        results.append({'seed':game_index,'ticks':state['tick'],'winner':state['winner'],'tower_hp':[t['hp'] for t in state['towers']]})
        if game_index==0:
            e.save_log(str(ROOT/'outputs/native_engine_replay.json'))
    elapsed=time.perf_counter()-started
    e=make(); e.direct_play(0,0,3,10); e.advance_ticks(100)
    snapshots=[]; state_calls=[]
    for _ in range(1000):
        s=time.perf_counter_ns(); fork=e.snapshot(); snapshots.append((time.perf_counter_ns()-s)/1e3)
        s=time.perf_counter_ns(); e.state_dict(); state_calls.append((time.perf_counter_ns()-s)/1e3)
    report={'commit':'2f172925a15b17f973291f41a789b19a29f0fc43','python':sys.version,
        'module':str(Path(native.__file__).relative_to(ROOT)), 'module_sha256':hashlib.sha256(Path(native.__file__).read_bytes()).hexdigest(),
        'registry_rows':len(cards),'base_rows':len(native.get_all_card_ids()),'evolution_rows':sum(c['is_evolution'] for c in cards),
        'checks':checks,'card_runs':card_runs,'benchmark':{'games':games,'elapsed_s':elapsed,'games_per_s':games/elapsed,
        'accepted_actions':accepted_total,'rejected_unmasked_random_actions':rejected_total,'advance10_ms_p50':statistics.median(latencies),'advance10_ms_p95':p95(latencies),
        'snapshot_us_p50':statistics.median(snapshots),'snapshot_us_p95':p95(snapshots),'state_dict_us_p50':statistics.median(state_calls)},'results':results,
        'fidelity':'Native source behavior only. These tests do not establish official-game parity.'}
    path=ROOT/'outputs/native_engine_smoke.json'; path.write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'checks':len(checks),'all_passed':all(x['passed'] for x in checks),'registry_rows':len(cards),'benchmark':report['benchmark'],'report':str(path)},indent=2))
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--games',type=int,default=100)
    run(p.parse_args().games)

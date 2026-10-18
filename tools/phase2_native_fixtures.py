"""Actual native mechanism fixtures and a narrow two-spell action-subspace proof.

Default --prepare only reads local source/data. --run requires exclusive control
of the prepared native probe. Missing/failed observations never become passes.
All physics below comes from the Android libg process, not a Python simulator.
"""
import argparse
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json
import math
from pathlib import Path
import sys
import traceback
import csv
import io
import lzma

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.backend import NativeBattleBackend
from royale_phase2.versioning import verify_manifest
from royale_phase2.visibility import project_actor, affordability_mask, TowerAttestation

CATALOG = 'third_party/phase2/firstlight/native_runner/data/competitive/card_specs.json.gz'
ALLOWED = [28000000, 28000001]
POINTS = [(0, 0), (17999, 0), (0, 31999), (17999, 31999), (9000, 16000),
          (3500, 16000), (9000, 3000), (3500, 6500), (9000, 29000), (14500, 25500)]
NAMES = ['reset_six_towers_and_clock', 'single_elixir_regeneration', 'elixir_cap',
         'double_elixir_phase', 'triple_elixir_phase', 'actual_cost_deduction',
         'hand_cycle_after_consumption', 'minions_deployment_count', 'ground_bridge_path',
         'building_target_pull', 'retarget_after_building_disappears', 'attack_damage_and_cooldown',
         'fireball_crown_damage', 'arrows_multiple_waves', 'spell_air_and_ground',
         'shield_absorbs_controlled_hit', 'tombstone_death_spawn', 'golem_death_split',
         'recorded_evolution_deployment', 'hero_ability_readiness_and_activation']


def save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')


def reference(path):
    path = Path(path).resolve()
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def rule_mask(actor, command_ready):
    """Candidate own/public-only rule, independently recomputed for this audit."""
    result = [True, False, False, False, False]
    if command_ready is True and actor['hand_complete']:
        for card in actor['hand']:
            result[card['slot']+1] = bool(card['card'] in ALLOWED and card['cost']*10000 <= actor['own_elixir_raw'])
    return result


def timeline_rates():
    path=ROOT/'third_party/phase2/runtime-assets/full-update/csv_logic/battle_timelines.csv'
    data=path.read_bytes();plain=lzma.decompress(data[:5]+data[5:9]+b'\0'*4+data[9:],format=lzma.FORMAT_ALONE)
    values=[];active=False
    for row in list(csv.DictReader(io.StringIO(plain.decode('utf-8-sig'))))[1:]:
        if row['Name']:
            if active:break
            active=row['Name']=='Default'
        if active and row['ElixirFullBarMS']:values.append(int(row['ElixirFullBarMS']))
    assert values==[28000,14000,9300]
    return values,reference(path)


class Runner:
    def __init__(self, folder, port):
        self.folder = folder; self.case = None; self.states = []; self.serial = 0
        self.native = NativeBattleBackend(port=port)
        self.specs = json.loads(gzip.decompress((ROOT/CATALOG).read_bytes()))['specs']
        self.by_name = {s['name']: s for s in self.specs}; self.by_id = {s['card_id']: s for s in self.specs}
        self.template = json.loads((ROOT/'outputs/phase2/native/match_probe/runtime86_first/initialization.json').read_text())
        self.case_results = []

    def reset(self, name, primary0=(), primary1=(), flags=None):
        self.case = self.folder/name; self.case.mkdir(parents=True, exist_ok=True); self.states = []; self.serial = 0
        self.native.trace_path = self.case/'wire.jsonl'; match = deepcopy(self.template)
        fill = [self.by_name[n]['card_id'] for n in ('Knight','Archer','Skeletons','Minions','Giant','Musketeer') if n in self.by_name]
        fill += [28000000,28000001,26000014,26000001,26000003,26000005,26000009,27000000]
        for owner, primary, indexes in [(0,primary0,[0,3]),(1,primary1,[1,4])]:
            slots = [None]*8
            for index, card in zip(indexes,primary): slots[index]=card
            rest = iter(dict.fromkeys(c for c in fill if c not in primary))
            for index in range(8):
                if slots[index] is None: slots[index]=next(rest)
            match['battle'][f'deck{owner}']['sp']=[{'d':card, **({'el':flags[(owner,card)]} if flags and (owner,card) in flags else {})} for card in slots]
        save(self.case/'initialization.json',match)
        state=self.native.reset(match);self.record(state);return state

    def record(self,state):
        self.states.append(deepcopy(state))
        with (self.case/'states.jsonl').open('a',encoding='utf-8') as f:f.write(json.dumps(state,separators=(',',':'))+'\n')
        return state

    def advance(self,ticks):return self.record(self.native.advance_exact_ticks(ticks))
    def state(self):return self.native.debug_state()
    def player(self,owner,state=None):return next(p for p in (state or self.state())['players'] if p['owner']==owner)
    def card(self,name):return self.by_name[name]['card_id']

    def budget(self,owner,cost):
        deficit=cost*10000-self.player(owner)['elixir']
        if deficit>0:self.advance(math.ceil(deficit/(10000/56))+2)

    def cast(self,card,owner,x,y,steps=1):
        hand=[h for h in self.player(owner)['hand'] if h[1]==card]
        if len(hand)!=1:raise AssertionError(f'Required card {card} not in owner{owner} current native hand')
        self.budget(owner,hand[0][2]);tick=self.state()['tick']+1;self.serial+=1
        receipt=self.native.schedule_card(owner,card,x,y,tick,request_id=f'{self.case.name}:{self.serial}')
        state=self.advance(steps)
        events=[e for e in state['plays'] if e[1]==tick and e[2]==owner]
        if not events:raise AssertionError('Native scheduler registration did not produce card consumption')
        return receipt,state

    def walk(self,ticks,quantum=10):
        result=[]
        for n in range(0,ticks,quantum):
            if self.state()['finalized']:break
            result.append(self.advance(min(quantum,ticks-n)))
        return result

    def finish(self,name,fn):
        result={'fixture':name,'status':'not_run','truth_class':'internal_native_regression','independent_reference':'no_reference'}
        try:
            detail=fn();result.update(status='pass',observations=detail)
        except Exception as exc:
            result.update(status='fail',error=f'{type(exc).__name__}: {exc}')
            if self.case: (self.case/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
        if self.case and self.case.exists():
            result['evidence']=[reference(p) for p in [self.case/'wire.jsonl',self.case/'states.jsonl',self.case/'initialization.json'] if p.exists()]
            if result.get('observations',{}).get('source_evidence'):result['evidence']+=result['observations']['source_evidence']
        self.case_results.append(result);save(self.folder/(name+'.json'),result);return result

    def mechanism(self,index):
        name=NAMES[index]
        if index==0:
            s=self.reset(name);t=TowerAttestation.from_reset(s);assert s['tick']==90 and len(t.identities)==6
            return {'tick':s['tick'],'six_tower_ids':[t.entity_id for t in t.identities]}
        if index==1:
            s=self.reset(name);a=self.player(0,s)['elixir'];e=self.player(0,self.advance(56))['elixir'];rates,source=timeline_rates();per_tick=100000*50//rates[0];assert e-a==56*per_tick
            return {'raw_gain_56_ticks':e-a,'expected_raw_gain':56*per_tick,'native_integer_raw_per_tick':per_tick,'full_bar_ms':rates[0],'source_evidence':[source],'rate_source':'floor(100000 raw full bar *50ms / versioned ElixirFullBarMS)'}
        if index==2:
            self.reset(name);s=self.advance(1000);values=[p['elixir'] for p in s['players']];assert values==[100000,100000]
            return {'raw_elixir':values}
        if index in (3,4):
            boundary=2400 if index==3 else 4800;mult=2 if index==3 else 3
            self.reset(name,ALLOWED);self.advance(boundary-100-self.state()['tick'])
            self.cast(28000000,0,0,0);self.cast(28000001,0,0,0)
            self.advance(boundary-self.state()['tick']);a=self.player(0)['elixir'];b=self.player(0,self.advance(56))['elixir']
            rates,source=timeline_rates();bar_ms=rates[1 if index==3 else 2];per_tick=100000*50//bar_ms;assert b-a==56*per_tick
            return {'boundary':boundary,'display_multiplier':mult,'gain_56_ticks':b-a,'expected_raw_gain':56*per_tick,'native_integer_raw_per_tick':per_tick,'full_bar_ms':bar_ms,'source_evidence':[source],'note':'Displayed3x is a9300ms full bar, not an exact real-number3/2.8-per-second ratio'}
        if index==5:
            self.reset(name,ALLOWED);a=self.player(0)['elixir'];self.advance(1);control=self.player(0)['elixir']
            self.reset(name,ALLOWED);self.cast(28000001,0,0,0);treated=self.player(0)['elixir'];assert control-treated==30000
            return {'same_tick_control_elixir':control,'cast_elixir':treated,'native_cost_raw':control-treated}
        if index==6:
            s=self.reset(name,ALLOWED);before=self.player(0,s);self.cast(28000001,0,0,0);after=self.player(0)
            assert before['hand']!=after['hand'] and before['cycle']!=after['cycle'] and len(after['hand'])==4
            return {'before_hand':before['hand'],'after_hand':after['hand'],'before_cycle':before['cycle'],'after_cycle':after['cycle']}
        if index==7:
            card=self.card('Minions');self.reset(name,[card]);self.cast(card,0,3000,12000,steps=30)
            units=[e for e in self.state()['entities'] if e[1]==0 and e[2]==card and e[9]=='troop'];assert len(units)==3
            return {'native_count':len(units),'source_declared_count':self.by_id[card].get('count')}
        if index==8:
            card=self.card('Giant');self.reset(name,[card]);self.cast(card,0,3500,14500);path=[]
            for s in self.walk(240,5):path.extend([[e[3],e[4]] for e in s['entities'] if e[1]==0 and e[2]==card])
            cross=[p for p in path if 15000<=p[1]<=17000];assert cross and any(p[1]>17500 for p in path) and all(abs(p[0]-3500)<2500 for p in cross)
            return {'path_samples':path,'scope':'left-bridge path internal invariant; no optimal path truth'}
        if index in (9,10):
            giant=self.card('Giant');cannon=self.card('Cannon');self.reset(name,[giant,28000000],[cannon])
            self.cast(cannon,1,6000,19000);self.cast(giant,0,3500,14500);target=[];cannon_ids=set()
            for s in self.walk(360,5):
                cannon_ids.update(e[0] for e in s['entities'] if e[1]==1 and e[2]==cannon)
                target.extend([{'tick':s['tick'],'target':e[7],'id':e[0]} for e in s['entities'] if e[1]==0 and e[2]==giant])
            pull=any(r['target'] in cannon_ids for r in target);assert pull
            if index==10:
                first_seen=min(s['tick'] for s in self.states if any(e[0] in cannon_ids for e in s['entities']))
                gone=[s['tick'] for s in self.states if s['tick']>first_seen and not any(e[0] in cannon_ids for e in s['entities'])]
                assert gone and any(r['tick']>=min(gone) and r['target'] not in cannon_ids and r['target'] is not None for r in target)
            return {'cannon_ids':sorted(cannon_ids),'giant_targets':target,'pull_observed':pull}
        if index==11:
            musk=self.card('Musketeer');giant=self.card('Giant');self.reset(name,[musk],[giant]);self.cast(giant,1,9000,18000);self.cast(musk,0,9000,12000)
            states=self.walk(150,1);hp=[e[5] for s in states for e in s['entities'] if e[1]==1 and e[2]==giant and e[5] is not None];cd=[e[8] for s in states for e in s['entities'] if e[1]==0 and e[2]==musk and e[8] is not None]
            assert hp and min(hp)<max(hp) and any(c and c>0 for c in cd)
            return {'hp_extrema':[min(hp),max(hp)],'cooldown_ms_extrema':[min(cd),max(cd)],'scope':'damage and cooldown transition, not an independently calibrated exact hit period'}
        if index in (12,13):
            card=28000000 if index==12 else 28000001;self.reset(name,[card]);before=next(e[5] for e in self.state()['entities'] if e[1]==1 and e[3]==3500 and e[4]==25500)
            self.cast(card,0,3500,25500);changes=[];last=before
            for s in self.walk(180,1):
                hp=next(e[5] for e in s['entities'] if e[1]==1 and e[3]==3500 and e[4]==25500)
                if hp!=last:changes.append({'tick':s['tick'],'before':last,'after':hp});last=hp
            assert changes and last<before
            if index==13:assert len(changes)>=2
            return {'tower_damage':before-last,'damage_changes':changes,'source_declared':self.by_id[card]['attributes'].get('resolved_projectile'),'official_reference':'none'}
        if index in (14,15):
            ground=self.card('Knight') if index==14 else self.card('DarkPrince');air=self.card('Minions') if index==14 else self.card('Knight')
            self.reset(name,[28000001],[ground,air]);self.budget(1,8);self.cast(ground,1,9000,18500);self.cast(air,1,9500,18500);self.advance(20)
            before={e[0]:deepcopy(e) for e in self.state()['entities'] if e[1]==1 and e[2] in (ground,air)}
            self.cast(28000001,0,9250,18000);self.walk(35,1);after={e[0]:e for e in self.state()['entities']}
            changed={card:any(eid not in after or after[eid][5]<e[5] for eid,e in before.items() if e[2]==card) for card in (ground,air)}
            if index==14:assert all(changed.values())
            else:assert not changed[ground] and changed[air]
            return {'damaged_by_card':changed,'scope':'paired native group control; source air/shield classification, no independent trace'}
        if index==16:
            card=self.card('Tombstone');self.reset(name,[card]);self.cast(card,0,3000,1000);parent=[e[0] for e in self.state()['entities'] if e[1]==0 and e[2]==card]
            if not parent:self.advance(30);parent=[e[0] for e in self.state()['entities'] if e[1]==0 and e[2]==card]
            assert parent;anchor=next(e[3:5] for e in self.state()['entities'] if e[0]==parent[0]);previous={e[0] for e in self.state()['entities']};death=None;children=[]
            for s in self.walk(1000,2):
                ids={e[0] for e in s['entities']}
                if death is None and not any(e[0] in parent for e in s['entities']):death=s['tick']
                if death is not None and s['tick']<=death+4:children.extend(e for e in s['entities'] if e[0] not in previous and e[1]==0 and e[9]=='troop' and abs(e[3]-anchor[0])<2500 and abs(e[4]-anchor[1])<2500)
                previous=ids
            assert death is not None and children
            return {'parent_ids':parent,'requested_position':[3000,1000],'actual_native_parent_position':anchor,'death_tick':death,'new_troops_near_death':children,'scope':'native death-associated spawn observation; exact skeleton cardinality not independently validated'}
        if index==17:
            golem=self.card('Golem');pekka=self.card('MiniPekka') if 'MiniPekka' in self.by_name else 26000018
            self.reset(name,[golem],[pekka]);self.budget(0,8);self.cast(golem,0,3500,14500);self.cast(pekka,1,3500,18000);self.advance(30)
            parents={e[0] for e in self.state()['entities'] if e[1]==0 and e[2]==golem};assert parents;previous={e[0] for e in self.state()['entities']};death=None;children=[]
            for s in self.walk(700,2):
                if death is None and not any(e[0] in parents for e in s['entities']):death=s['tick']
                if death is not None and s['tick']<=death+4:children.extend(e for e in s['entities'] if e[0] not in previous and e[1]==0 and e[9]=='troop')
                previous={e[0] for e in s['entities']}
            assert death is not None and len({e[0] for e in children})>=2
            return {'parent_ids':list(parents),'death_tick':death,'new_same_owner_troops':children,'scope':'death split only, child exact stats not independently calibrated'}
        if index==18:
            # An actual previously executed human replay provides stronger
            # boundary evidence than constructing an untested fake evolution.
            path=ROOT/'outputs/phase2/fixtures/feedback_reanalysis.json';report=json.loads(path.read_text())
            rows=[r for r in report['feedback'] if r.get('matched_by')=='unique_native_event_source_evolution_alias'];assert rows and all(r['own_boundary_consumption_evidence'] for r in rows)
            self.case=self.folder/name;self.case.mkdir(exist_ok=True)
            return {'recorded_native_evolution_consumptions':len(rows),'source_evidence':[reference(path)],'new_execution':False,'scope':'actual form deployment and cycle/hand consumption; evolution combat effect independently unverified'}
        if index==19:
            queen=self.card('ArcherQueen');self.reset(name,[queen]);self.cast(queen,0,3000,1000);self.advance(40)
            abilities=self.player(0)['abilities'];ready=[a for a in abilities if a[0] in (2,4) and a[1]==0 and a[3]!=0 and a[5]];assert ready
            before=deepcopy(abilities);tick=self.state()['tick']+1;registration=self.native.schedule_ability(0,[],tick,request_id='fixture-queen-ability');self.advance(2)
            feedback=self.native.execution_feedback();ability=next(r for r in feedback if r['kind']=='activate_ability');after=self.player(0)['abilities'];assert ability['native_injected'] and after!=before
            return {'before_ability_runtime':before,'after_ability_runtime':after,'registration':registration,'feedback':ability,'scope':'native injection and own runtime transition; invisibility visibility/complete ability effect remains unverified'}

    def subspace(self):
        results={};positive=[];negative=[];raw_examples=[]
        for card in ALLOWED:
            for owner in (0,1):
                for point in POINTS:
                    name=f'subspace_positive_{card}_{owner}_{point[0]}_{point[1]}'
                    self.reset(name,[card] if owner==0 else (),[card] if owner==1 else ())
                    before=self.state();status=self.native._request('status');actor=self.native.observe_visible(owner)
                    own=[h for h in self.player(owner)['hand'] if h[1]==card];assert len(own)==1 and own[0][2]==own[0][3]>>28
                    ready=bool(status['ready'] and status['matchReady'] and status['paused'] and status['contentReady'] and not before['finalized'] and before['tick']>=90)
                    assert ready and rule_mask(actor,ready)[own[0][0]+1]
                    self.cast(card,owner,*point);events=[e for e in self.state()['plays'] if e[1]==91 and e[2]==owner and e[3]==card];assert len(events)==1
                    positive.append({'card':card,'owner':owner,'requested_xy':list(point),'native_event':events[0],'command_ready':ready,'evidence':reference(self.case/'wire.jsonl')})
                    if len(raw_examples)<4:raw_examples.append((deepcopy(before),owner))
        results['native_positive']={'status':'pass','coordinate_points_per_card_owner':len(POINTS),'total_native_casts':len(positive),'tests':positive,'scope':'Finite representative points plus version-locked full-arena card-family rule; not exhaustive coordinates'}
        for x,y in [(-1,0),(18000,0),(0,-1),(0,32000)]:
            self.reset(f'subspace_native_bounds_negative_{x}_{y}',ALLOWED)
            try:self.native._request(f'replay-schedule-card 0 28000000 {x} {y} 91')
            except Exception as exc:negative.append({'requested_xy':[x,y],'native_rejected':True,'error':str(exc),'evidence':reference(self.case/'wire.jsonl')})
            else:raise AssertionError('Out-of-domain native request was accepted')
        self.reset('subspace_missing_card_negative',ALLOWED);self.native.schedule_card(0,26999999,1000,1000,91,request_id='missing');self.advance(1)
        missing=self.native.execution_feedback()[0];assert missing['scheduler_feedback']['error']=='card-unavailable' and not missing['card_consumed'];negative.append({'case':'missing_card','feedback':missing,'evidence':reference(self.case/'wire.jsonl')})
        self.reset('subspace_budget_negative',[self.card('Golem'),28000000]);self.budget(0,10);self.cast(self.card('Golem'),0,3000,1000);actor=self.native.observe_visible(0);spell=next(c for c in actor['hand'] if c['card']==28000000)
        assert actor['own_elixir_raw']<40000 and not rule_mask(actor,True)[spell['slot']+1]
        tick=self.state()['tick']+1;self.native.schedule_card(0,28000000,1000,1000,tick,request_id='insufficient');self.advance(1);fb=next(r for r in self.native.execution_feedback() if r['request_id']=='insufficient');assert not fb['card_consumed']
        negative.append({'case':'insufficient_budget','actor':actor,'mask':rule_mask(actor,True),'feedback':fb,'evidence':reference(self.case/'wire.jsonl')})
        results['native_negative']={'status':'pass','tests':negative}
        privacy=[]
        for raw,owner in raw_examples:
            towers=TowerAttestation.from_reset(raw);base=project_actor(raw,owner,towers);base_mask=rule_mask(base,True)
            for value in range(50):
                changed=deepcopy(raw);enemy=next(p for p in changed['players'] if p['owner']!=owner)
                enemy.update(elixir=value*2000,hand=[[s,28000000+value,99,0,0] for s in range(4)],cycle=[value]*8,abilities=[[0,value,0,0,0,[]]],evolution=[[0,value,True,9,9,False]])
                changed['entities']=[e for e in changed['entities'] if e[2]==-1]
                changed['plays']=[[999,0,1-owner,26000000+value,9]]
                actor=project_actor(changed,owner,towers);assert actor==base and rule_mask(actor,True)==base_mask
                privacy.append({'owner':owner,'perturbation':value,'actor_unchanged':True,'mask_unchanged':True})
        results['enemy_private_perturbation']={'status':'pass','tests':privacy,'method':'Counterfactual perturbation of actual native capture fields; projection/mask test, not simulated native battles'}
        self.reset('subspace_command_ready_negative',ALLOWED);actor=self.native.observe_visible(0);true_mask=rule_mask(actor,True);false_mask=rule_mask(actor,False);assert any(true_mask[1:]) and false_mask==[True,False,False,False,False]
        try:self.native._request('replay-schedule-card 0 28000000 1000 1000 90')
        except Exception as exc:past={'native_rejected_past_boundary':True,'error':str(exc)}
        else:raise AssertionError('Native accepted a command for a nonfuture boundary')
        results['command_ready']={'status':'pass','true_gate_native_casts':len(positive),'false_gate_mask':false_mask,'true_gate_mask':true_mask,'past_boundary':past,'evidence_native':reference(self.case/'wire.jsonl'),'definition':'Current valid capture of initialized paused ready headless manager at tick>=90, nonterminal, complete own native-selection hand; request target strictly next future boundary','scope':'Own/control readiness gate; scheduler success alone is not consumption and no arbitrary-card legality oracle is implied'}
        for name,result in results.items():save(self.folder/(name+'.json'),result)
        return results


def prepare(folder,version):
    specs=json.loads(gzip.decompress((ROOT/CATALOG).read_bytes()))['specs'];rows=[s for s in specs if s['card_id'] in ALLOWED]
    assert len(rows)==2
    for s in rows:
        assert s['target_schema']['placement_mask_key']=='full_arena' and not s['target_schema']['requires_visible_target'] and s['evolution'] is None
        assert all(s['attributes'][key] is True for key in ['CanDeployOnEnemySide','CanPlaceOnWater','CanPlaceOnBuildings'])
    rules={'schema':'phase2-full-arena-spell-rule.v1','version_fingerprint_sha256':version['fingerprint'],
           'catalog_reference':reference(ROOT/CATALOG),'cards':rows,'probe_source':reference(ROOT/'third_party/phase2/jason/vendor/firstlight/probe/cr_replay_probe.cpp'),
           'bounds':{'x':[0,17999],'y':[0,31999]},'native_representative_points':POINTS,
           'coordinate_coverage':'Rule-backed continuous integer request rectangle; ten finite native test points per card/owner, not exhaustive enumeration',
           'ordinary_forms_only':True,'independent_official_reference':'none'}
    save(folder/'rules.json',rules)
    plan={'schema':'phase2-native-fixture-plan.v1','actual_native_execution':False,'fixtures':[{'id':i+1,'mechanism':n,'status':'not_run','independent_reference':'no_reference'} for i,n in enumerate(NAMES)],'subspace':'wait_or_arena_spell.v1','source_rule_reference':reference(folder/'rules.json')}
    save(folder/'plan.json',plan);return rules


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',action='store_true');p.add_argument('--prepare',action='store_true');p.add_argument('--port',type=int,default=26789)
    p.add_argument('--mechanisms',help='Optional comma-separated1-based fixture IDs for explicit repair reruns')
    p.add_argument('--skip-subspace',action='store_true',help='Do not rerun/replace an existing action proof')
    p.add_argument('--merge-runs',nargs='+',help='Read-only merge of completed run reports, oldest to newest; no RPC')
    p.add_argument('--output',default='outputs/phase2/fixtures/native-v1');p.add_argument('--proof',default='data/phase2/action_subspace_proof.json');args=p.parse_args()
    folder=(ROOT/args.output).resolve();proof_path=(ROOT/args.proof).resolve()
    if not folder.is_relative_to(ROOT) or not proof_path.is_relative_to(ROOT):raise ValueError('Paths outside project')
    if args.merge_runs:
        latest={};history={}
        for source in args.merge_runs:
            source=(ROOT/source).resolve()
            if not source.is_relative_to(ROOT):raise ValueError('Run outside project')
            report=json.loads((source/'report.json').read_text(encoding='utf-8'))
            for row in report['mechanisms']:
                name=row['fixture'];history.setdefault(name,[]).append({'status':row['status'],'report':reference(source/(name+'.json'))})
                latest[name]={**deepcopy(row),'selected_report':reference(source/(name+'.json'))}
        merged={'schema':'phase2-native-mechanism-coverage.v1','unique_mechanism_count':len(latest),
                'counts':{status:sum(r['status']==status for r in latest.values()) for status in ['pass','fail','not_run']},
                'independent_official_reference_count':0,'truth_scope':'Internal native regressions only;19fresh setups and one explicitly recorded native evolution boundary slice',
                'mechanisms':[latest[n] for n in NAMES if n in latest],'revision_history':history}
        save(folder/'mechanism_coverage.json',merged);print(json.dumps({'merged':True,'counts':merged['counts'],'output':str(folder/'mechanism_coverage.json')}));return
    folder.mkdir(parents=True,exist_ok=True);version=verify_manifest('data/phase2/version_manifest.json');rules=prepare(folder,version)
    if not args.run:print(json.dumps({'prepared':True,'fixtures':20,'native_execution':False,'plan':str(folder/'plan.json')}));return
    runner=Runner(folder,args.port);summary={'schema':'phase2-native-fixture-results.v1','version_fingerprint_sha256':version['fingerprint'],'actual_native_execution':True,'mechanisms':[]}
    try:
        try:
            checks={} if args.skip_subspace else runner.subspace()
            proof={'schema':'royale-phase2-action-subspace-proof.v1','subspace':'wait_or_arena_spell.v1','version_fingerprint_sha256':version['fingerprint'],
                   'catalog_sha256':rules['catalog_reference']['sha256'],'catalog_reference':rules['catalog_reference'],'allowed_card_ids':ALLOWED,
                   'bounds':{'x':[0,17999],'y':[0,31999]},'actor_dependencies':['own_current_hand','own_current_cost','own_elixir','command_ready','public_tick'],
                   'checks':{name:{'status':value['status'],'evidence':[reference(folder/(name+'.json')),reference(folder/'rules.json')]} for name,value in checks.items()},
                   'coordinate_coverage':rules['coordinate_coverage'],'limitations':['No coordinate exhaustive test','Only two ordinary named spells and WAIT','No ability or arbitrary troop/building placement legality claim','Local native/internal evidence; no official trajectory reference']}
            if not args.skip_subspace:save(proof_path,proof);summary['subspace_proof']=reference(proof_path)
            else:summary['subspace_not_rerun']=True
        except Exception as exc:
            summary['subspace_error']=f'{type(exc).__name__}: {exc}';(folder/'subspace_error.log').write_text(traceback.format_exc(),encoding='utf-8')
        selected=list(range(len(NAMES))) if not args.mechanisms else [int(n)-1 for n in args.mechanisms.split(',')]
        if len(set(selected))!=len(selected) or any(i<0 or i>=len(NAMES) for i in selected):raise ValueError('Invalid fixture IDs')
        for index in selected:
            name=NAMES[index]
            result=runner.finish(name,lambda index=index:runner.mechanism(index));summary['mechanisms'].append(result)
            print(json.dumps({'fixture':name,'status':result['status'],'error':result.get('error')},ensure_ascii=True),flush=True)
        summary['counts']={status:sum(r['status']==status for r in summary['mechanisms']) for status in ['pass','fail','not_run']}
    finally:
        runner.native.close();save(folder/'report.json',summary)
    print(json.dumps({'report':str(folder/'report.json'),'counts':summary.get('counts'),'proof':summary.get('subspace_proof'),'subspace_error':summary.get('subspace_error')}),flush=True)


if __name__=='__main__':main()

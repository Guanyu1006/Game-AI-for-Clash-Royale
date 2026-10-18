"""Independent read-only native numeric API review; never edits engine source.

Freeze the reviewed .pyd under .cache/audit/numeric_review_original before running.
Each battle variant uses a fresh interpreter because configuration is process-wide.
"""
from __future__ import annotations
import argparse
import copy
import gc
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/audit_candidates'


def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()


def child(mode,module_dir):
    sys.path.insert(0,str(ROOT/module_dir))
    import clash_royale_env as n
    original=n.export_numeric_stats(defaults=True)
    def cid(name):return int(next(k for k,v in original['card_metadata'].items() if v['name'].lower()==name.lower()))
    def fingerprint():return hashlib.sha256(json.dumps(n.export_numeric_stats()['cards'],sort_keys=True).encode()).hexdigest()
    evidence={'mode':mode,'module_sha256':sha(Path(n.__file__))}
    if mode=='validation':
        checks=[]
        for label,payload in [
            ('valid_prefix_invalid_tail',{'cards':{'0':{'base':{'hp':2000}},'7':{'base':{'spell_applications':0}}}}),
            ('effect_edit_rejected',{'cards':{'39':{'base':{'death_damage':99}}}}),
            ('evolved_cost_rejected',{'cards':{next(k for k,v in original['card_metadata'].items() if v['is_evolution']):{'evolved':{'cost':1}}}}),
            ('boolean_numeric_rejected',{'cards':{'0':{'base':{'hp':True}}}}),
            ('negative_hp_rejected',{'cards':{'0':{'base':{'hp':-1}}}}),
        ]:
            before=fingerprint();error=None
            try:n.apply_numeric_overrides(payload)
            except Exception as exc:error=type(exc).__name__+': '+str(exc)
            checks.append({'name':label,'rejected':bool(error),'atomic':before==fingerprint(),'error':error})
        metadata=[]
        for key,value in [('schema_version',True),('schema_version',1.0),('ticks_per_second',10.0),('field_schema',{'hp':{'maximum':99999999}})]:
            before=fingerprint();error=None
            try:feedback=n.apply_numeric_overrides({'cards':{},key:value})
            except Exception as exc:error=type(exc).__name__+': '+str(exc);feedback=None
            metadata.append({'field':key,'value':value,'accepted':not bool(error),'unchanged':before==fingerprint(),'error':error,'feedback':feedback})
        n.apply_numeric_overrides({'cards':{'0':{'base':{'hp':2222}}}})
        check_reset=n.apply_numeric_overrides({'cards':{'7':{'base':{'spell_damage':321}}}},reset=False)
        merge_preserves=n.export_numeric_stats()['cards']['0']['base']['hp']==2222
        n.apply_numeric_overrides({'cards':{'7':{'base':{'spell_damage':321}}}},reset=True)
        reset_restores=n.export_numeric_stats()['cards']['0']['base']['hp']==original['cards']['0']['base']['hp']
        gang=cid('Goblin Gang');before=n.get_card_profile(gang)
        n.apply_numeric_overrides({'cards':{str(gang):{'base':{'hp':2222,'damage':999}}}})
        after=n.get_card_profile(gang)
        before_config=fingerprint()
        env=n.ClashRoyaleEnv(list(range(8)),list(range(8)),100)
        state=env.state_dict();error=None
        try:n.apply_numeric_overrides({'cards':{'0':{'base':{'hp':2345}}}})
        except Exception as exc:error=type(exc).__name__+': '+str(exc)
        locked=bool(error) and state==env.state_dict() and before_config==fingerprint()
        del env;gc.collect();error_gc=None
        try:n.apply_numeric_overrides({'cards':{'0':{'base':{'hp':2345}}}})
        except Exception as exc:error_gc=type(exc).__name__+': '+str(exc)
        evidence.update(checks=checks,metadata=metadata,merge_preserves_previous=merge_preserves,
                        reset_restores_omitted_card=reset_restores,lock_rejection_atomic=locked,
                        lock_survives_env_gc=bool(error_gc),lock_error=error,gc_lock_error=error_gc,
                        compound={'card_id':gang,'metadata':original['card_metadata'][str(gang)],'before':before,'after':after},
                        limitations=original['limitations'])
        return evidence

    fixture,variant=mode.split(':',1)
    names={'rolling':'The Log','barrel':'Barbarian Barrel','buff':'Rage','clone':'Clone','death':'Giant Skeleton'}
    spell_id=cid(names[fixture])
    patch={'0':{'base':{'speed':0.0}}}
    if variant=='unused':
        values={'rolling':{'spell_radius':49,'spell_applications':9,'spell_interval_ticks':1,'spell_knockback':30},
                'barrel':{'spell_spawn_ring_radius':20},'buff':{'spell_knockback':30},
                'clone':{'spell_knockback':30},'death':{'damage':999999}}
        patch[str(spell_id)]={'base':values[fixture]}
    elif variant=='negative_roll':patch[str(spell_id)]={'base':{'spell_roll_knockback':-5}}
    elif variant=='zero_roll':patch[str(spell_id)]={'base':{'spell_roll_knockback':0}}
    feedback=n.apply_numeric_overrides({'cards':patch})
    e=n.ClashRoyaleEnv([spell_id]+[x for x in range(8) if x!=spell_id][:7],list(range(8)),200)
    e.seed(19);e.set_elixir_for_team(0,10);e.set_hand_for_team(0,[spell_id]+[x for x in range(8) if x!=spell_id][:3])
    if fixture=='death':
        e.inject(spell_id,8.5,14,0,1,1000)
        e.inject(0,8.5,14.6,1,10000,0)
    else:
        team=0 if fixture in ('buff','clone') else 1
        e.inject(0,8.5,14,team,10000,1000)
        e.advance_ticks(1)
        assert e.direct_play(0,0,8.5,12 if fixture in ('rolling','barrel') else 14)
    trace=[]
    for tick in range(80):
        e.advance_ticks(1)
        state=e.state_dict()
        # Metadata's unused raw parameter also appears in diagnostics; compare actual
        # combat bodies/HP/positions rather than counting a debug-field difference.
        trace.append({'tick':state['tick'],'entities':[{k:u[k] for k in ('id','card_id','name','team','hp','max_hp','x','y') if k in u}
                      for u in state['entities'] if u['card_id']==0 or u.get('card_id',0)<0],
                      'tower_hp':[[e.get_tower_hp(t,s) for s in range(3)] for t in range(2)]})
    evidence.update(card_id=spell_id,patch=patch,feedback=feedback,metadata=original['card_metadata'][str(spell_id)],
                    exported_fields=original['cards'][str(spell_id)]['base'],trace=trace)
    return evidence


def main():
    p=argparse.ArgumentParser();p.add_argument('--child');p.add_argument('--module-dir',default='.cache/audit/numeric_review_original')
    p.add_argument('--output',default='outputs/audit_candidates/numeric_config_review.json');args=p.parse_args()
    if args.child:
        print(json.dumps(child(args.child,args.module_dir)));return
    OUT.mkdir(exist_ok=True,parents=True)
    local_env=os.environ.copy()
    for name in ['TEMP','TMP']:local_env[name]=str(ROOT/'.tmp')
    modes=['validation','rolling:base','rolling:unused','rolling:zero_roll','rolling:negative_roll',
           'barrel:base','barrel:unused','buff:base','buff:unused','clone:base','clone:unused','death:base','death:unused']
    results=[]
    for mode in modes:
        run=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child',mode,'--module-dir',args.module_dir],
                           cwd=ROOT,env=local_env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
        if run.returncode:results.append({'mode':mode,'exit_code':run.returncode,'error':run.stderr});continue
        results.append(json.loads(run.stdout))
    lookup={r['mode']:r for r in results}
    comparisons=[]
    for fixture in ['rolling','barrel','buff','clone','death']:
        a,b=lookup[fixture+':base'],lookup[fixture+':unused']
        comparisons.append({'fixture':fixture,'changed_numeric_fields':b['feedback']['changed_fields'],
                            'combat_trajectory_unchanged':a.get('trace')==b.get('trace'),
                            'base_final':a.get('trace',[]) [-1:],'changed_final':b.get('trace',[]) [-1:]})
    a,b=lookup['rolling:zero_roll'],lookup['rolling:negative_roll']
    comparisons.append({'fixture':'negative_roll_knockback','accepted_negative':b['feedback']['changed_fields']>0,
                        'same_as_zero_roll_knockback':a.get('trace')==b.get('trace')})
    output={'module_dir':args.module_dir,'script_sha256':sha(Path(__file__)),'comparisons':comparisons,'results':results,
            'scope':'Independent API, atomicity/lock and targeted combat trajectory review; no source edits and no official parity claim.'}
    (ROOT/args.output).write_text(json.dumps(output,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'comparisons':comparisons,'output':args.output,'validation':lookup['validation']},ensure_ascii=False,indent=2))


if __name__=='__main__':main()

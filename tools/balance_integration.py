"""Project-local, fresh-process numeric configuration acceptance."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))

def child():
    from royale_lab.backend import NativeBackend,balance_info,named_decks
    from royale_lab.contracts import Action
    from royale_lab.policies import make_policy
    decks=named_decks();b=NativeBackend(7,[decks['Giant Control']]*2,max_ticks=300)
    info=balance_info();assert info['changed']
    b.env.set_hand_for_team(0,[0,1,2,3]);b.env.set_elixir_for_team(0,10)
    first=b.apply(0,Action('card',0,8,13));assert first['status']=='accepted' and first['elixir_spent']==2
    b.advance(1)
    deployed=next(u for u in b.oracle()['units'] if u['team']==0 and u['card_id']==0)
    assert deployed['max_hp']==2200
    b=NativeBackend(7,[decks['Giant Control']]*2,max_ticks=300)
    b.env.inject(0,8,16,0,2200,0);b.env.inject(0,8.5,16,1,2200,0)
    b.advance(1)
    enemy_before=next(u['hp'] for u in b.oracle()['units'] if u['team']==1 and u['card_id']==0)
    b.advance(12)
    own=[u for u in b.oracle()['units'] if u['team']==0 and u['card_id']==0]
    enemy=[u for u in b.oracle()['units'] if u['team']==1 and u['card_id']==0]
    assert own and own[0]['max_hp']==2200
    actual_damage=enemy_before-enemy[0]['hp'];assert actual_damage==303,actual_damage
    before=next(t['hp'] for t in b.oracle()['towers'] if t['team']==1 and t['slot']==1)
    b.env.set_hand_for_team(0,[7,0,1,2]);b.env.set_elixir_for_team(0,10)
    feedback=b.apply(0,Action('card',0,3,27));assert feedback['status']=='accepted'
    b.advance(25)
    after=next(t['hp'] for t in b.oracle()['towers'] if t['team']==1 and t['slot']==1)
    assert before-after==123,before-after
    try:make_policy('learned',b.catalog)
    except ValueError:legacy_rejected=True
    else:raise AssertionError('Legacy checkpoint should reject changed numeric data')
    print(json.dumps({'balance':info,'knight_profile':b.cr.get_card_profile(0,False),'accepted_cost':first['elixir_spent'],
                      'actual_attack_damage':actual_damage,'fireball_tower_damage':before-after,'legacy_checkpoint_rejected':legacy_rejected}))

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--child',action='store_true');parser.add_argument('--native-dir');args=parser.parse_args()
    if args.child:child();return
    from royale_lab.runtime import target,local
    config=target('outputs/platform/numeric_custom_test.json')
    config.write_text(json.dumps({'cards':{'0':{'base':{'hp':2200,'damage':303,'cost':2}},
                                                  '7':{'base':{'crown_damage_per_application':123}}}}),encoding='utf-8')
    env=dict(os.environ,ROYALE_BALANCE_FILE=str(config),PYTHONUTF8='1')
    if args.native_dir:env['ROYALE_NATIVE_DIR']=str(local(args.native_dir))
    proc=subprocess.run([sys.executable,str(Path(__file__).resolve()),'--child'],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8')
    report={'command':proc.args,'returncode':proc.returncode,'stdout':proc.stdout,'stderr':proc.stderr,
            'config':str(config),'config_sha256':hashlib.sha256(config.read_bytes()).hexdigest()}
    if proc.returncode==0:report['result']=json.loads(proc.stdout)
    target('outputs/platform/numeric_integration.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2));sys.exit(proc.returncode)

if __name__=='__main__':main()

"""Hash the actual full native state at every tick of existing accepted journals.

Run baseline and staged DLLs in separate Python processes. This never writes to
existing evaluation/training artifacts. Hashes cover every state_dict field,
not just final tower HP. Same-tick actions are checked separately too.
"""
import argparse
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
canonical=lambda x:json.dumps(x,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
sha=lambda x:hashlib.sha256(x).hexdigest()

def selected(limit):
    folders=sorted(p for p in (ROOT/'outputs/evaluation/main').iterdir() if p.is_dir())
    lists=[sorted(p.glob('*.replay.json')) for p in folders]
    paths=[]
    for i in range(max(map(len,lists))):
        for items in lists:
            if i<len(items):paths.append(items[i])
            if len(paths)==limit:return paths
    return paths

def run(args):
    sys.path.insert(0,str(ROOT/args.module_dir))
    import clash_royale_env as native
    loaded=None
    if args.balance:
        loaded=native.apply_numeric_overrides(json.loads((ROOT/args.balance).read_text(encoding='utf-8')))
    result={'module':str(Path(native.__file__).relative_to(ROOT)),
            'module_sha256':sha(Path(native.__file__).read_bytes()),'loaded_balance':loaded,
            'trace_semantics':'SHA256(canonical JSON of the complete state_dict) after every tick and action',
            'games':[]}
    started=time.perf_counter()
    for path in selected(args.limit):
        replay=json.loads(path.read_text(encoding='utf-8'))
        env=native.ClashRoyaleEnv(replay['decks'][0],replay['decks'][1],replay['max_ticks'])
        env.seed(replay['seed'])
        trace=[['reset',0,sha(canonical(env.state_dict()))]];accepted=0
        def advance_until(target):
            while env.get_current_tick()<target:
                before=env.get_current_tick();env.advance_ticks(1)
                if env.get_current_tick()==before:raise AssertionError(f'{path}: early terminal at {before}, expected {target}')
                trace.append(['tick',env.get_current_tick(),sha(canonical(env.state_dict()))])
        for index,event in enumerate(replay['journal']):
            advance_until(event['tick'])
            a=event['action'];player=event['player'];x=a['x'];y=a['y']
            if player:x,y=17-x,33-y
            if a['kind']=='card':ok=env.direct_play(player,a['slot'],x,y)
            elif a['kind']=='ability':ok=env.activate_champion_ability(player,a['slot'])
            else:ok=True
            if not ok:raise AssertionError(f'{path}: action {index} rejected {event}')
            accepted+=1;trace.append(['action',index,sha(canonical(env.state_dict()))])
        advance_until(replay['tick'])
        result['games'].append({'path':str(path.relative_to(ROOT)),'replay_sha256':sha(path.read_bytes()),
            'seed':replay['seed'],'accepted_actions':accepted,'ticks':env.get_current_tick(),
            'trace':trace,'trace_sha256':sha(canonical(trace))})
    result['elapsed_s']=time.perf_counter()-started
    (ROOT/args.output).write_text(json.dumps(result,separators=(',',':')),encoding='utf-8')
    print(json.dumps({'games':len(result['games']),'accepted':sum(g['accepted_actions'] for g in result['games']),
        'states':sum(len(g['trace']) for g in result['games']),'elapsed_s':result['elapsed_s'],'output':args.output}))

def compare(args):
    docs=[json.loads((ROOT/p).read_text(encoding='utf-8')) for p in args.compare]
    before=docs[0];differences=[]
    for doc in docs[1:]:
        if len(doc['games'])!=len(before['games']):raise AssertionError('Different sample counts')
        for a,b in zip(before['games'],doc['games']):
            for key in ['path','replay_sha256','accepted_actions','ticks','trace']:
                if a[key]!=b[key]:differences.append({'module':doc['module'],'replay':a['path'],'field':key})
    report={'passed':not differences,'games':len(before['games']),
        'accepted_actions':sum(g['accepted_actions'] for g in before['games']),
        'full_state_comparisons_per_variant':sum(len(g['trace']) for g in before['games']),
        'variant_count':len(docs)-1,'module_hashes':[d['module_sha256'] for d in docs],
        'variants':args.compare,'differences':differences,
        'limitation':'Deterministic sample equivalence to local v2, not official-game equivalence.'}
    (ROOT/args.output).write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    if differences:raise AssertionError('Native default trajectory changed')

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--module-dir',default='third_party/ClashRoyaleAi/python_ai')
    p.add_argument('--balance');p.add_argument('--limit',type=int,default=100);p.add_argument('--output',required=True)
    p.add_argument('--compare',nargs='+');a=p.parse_args();compare(a) if a.compare else run(a)

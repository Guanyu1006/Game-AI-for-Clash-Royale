"""Verify real HTTP execution against a running project-local server."""
import argparse
import json
import sys
import time
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_lab.runtime import target

def main():
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=18765);args=parser.parse_args()
    base=f'http://127.0.0.1:{args.port}';opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    calls=[]
    def request(route,data=None):
        begin=time.perf_counter();blob=json.dumps(data).encode() if data is not None else None
        req=urllib.request.Request(base+route,data=blob,headers={'Content-Type':'application/json'})
        with opener.open(req,timeout=60) as response:result=json.load(response)
        calls.append({'route':route,'elapsed_ms':(time.perf_counter()-begin)*1000,'status':200})
        return result
    request('/api/health');catalog=request('/api/catalog');cards={c['id']:c for c in catalog['cards']}
    game=request('/api/new',{'seed':7,'controllers':['human','greedy'],'max_ticks':300});sid=game['session']
    hand=game['state']['players'][0]['hand'];slot=next(i for i,c in enumerate(hand) if cards[c]['cost']<=5 and not cards[c]['is_spell'])
    body={'session':sid,'player':0,'action':{'kind':'card','slot':slot,'x':3,'y':8},'command_id':'http-first'}
    action=request('/api/action',body);duplicate=request('/api/action',body)
    assert action['feedback']['status']=='accepted' and action['feedback']['elixir_spent']>0
    assert action['feedback']['before_hand']!=action['feedback']['after_hand'] and duplicate['feedback']['duplicate']
    state=request('/api/advance',{'session':sid,'ticks':20});assert state['state']['tick']==20
    saved=request('/api/snapshot',{'session':sid,'label':'http-test'})
    request('/api/advance',{'session':sid,'ticks':20})
    restored=request('/api/restore',{'session':sid,'label':'http-test'});assert restored['state']['tick']==20
    branch=request('/api/fork',{'session':sid,'label':'http-test'});branchid=branch['session']
    assert branchid!=sid and branch['state']==restored['state']
    final=request('/api/advance',{'session':sid,'ticks':300});assert final['done'] and final['rejections']==0
    replay=request('/api/export',{'session':sid})
    loaded=request('/api/load',{'session':branchid,'path':replay['path']});assert loaded['done'] and loaded['state']==final['state']
    autonomous=request('/api/new',{'seed':8,'controllers':['search','greedy'],'max_ticks':3000})
    autonomous=request('/api/advance',{'session':autonomous['session'],'ticks':3000})
    assert autonomous['done'] and len(autonomous['state']['events'])>0 and autonomous['rejections']==0
    ai_replay=request('/api/export',{'session':autonomous['session']})
    report={'all_passed':True,'engine':catalog['version'],'card_entries':len(cards),'manual_feedback':action['feedback'],
            'manual_final':final['result'],'autonomous_final':autonomous['result'],'autonomous_plays':len(autonomous['state']['events']),
            'replays':[replay['path'],ai_replay['path']],'snapshot':saved,'calls':calls}
    target('outputs/platform/http_smoke.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    for session in (sid,branchid,autonomous['session']):request('/api/close',{'session':session})
    print(json.dumps(report,ensure_ascii=False,indent=2))

if __name__=='__main__':main()

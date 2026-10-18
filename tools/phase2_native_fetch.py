"""Public, bounded and hashed native-candidate acquisition. No global proxy edits."""
from __future__ import annotations
import concurrent.futures
import hashlib
import json
from pathlib import Path
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/native'
OUT.mkdir(parents=True,exist_ok=True)
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))

def fetch(url,path,limit=40_000_000,timeout=35):
    path=ROOT/path
    path.parent.mkdir(parents=True,exist_ok=True)
    record={'url':url,'path':str(path.relative_to(ROOT)).replace('\\','/'),'started_unix':time.time()}
    try:
        req=urllib.request.Request(url,headers={'User-Agent':'offline-battle-research/phase2','Accept':'application/vnd.github+json' if 'api.github.com' in url else '*/*'})
        with OPENER.open(req,timeout=timeout) as response:
            record.update(status=response.status,final_url=response.url,headers=dict(response.headers))
            data=response.read(limit+1)
            if len(data)>limit:raise ValueError(f'Body exceeds {limit} byte acquisition budget')
            path.write_bytes(data)
            record.update(bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),success=True)
    except Exception as exc:
        record.update(success=False,error=f'{type(exc).__name__}: {exc}')
        if isinstance(exc,urllib.error.HTTPError):
            record.update(status=exc.code,response_body=exc.read(4096).decode('utf-8','replace'))
    record['elapsed_s']=time.time()-record['started_unix']
    return record

def initial():
    requests=[
        ('https://api.github.com/repos/Jason-XII/clash-royale-battle-engine','jason_metadata.json'),
        ('https://gitlab.com/api/v4/projects/firstlight3%2FFirstLight_CR','firstlight_metadata.json'),
        ('https://api.github.com/search/repositories?q=clapha-engine','clapha_search.json'),
        ('https://api.github.com/search/repositories?q=FirstLight_CR','firstlight_search.json')]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results=list(pool.map(lambda pair:fetch(pair[0],OUT.relative_to(ROOT)/pair[1]),requests))
    (OUT/'initial_fetch.json').write_text(json.dumps(results,indent=2),encoding='utf-8')
    for r in results:print(json.dumps({k:r.get(k) for k in ['url','status','success','bytes','error']}))

if __name__=='__main__':initial()

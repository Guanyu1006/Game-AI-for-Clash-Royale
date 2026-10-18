"""Pinned Scroll source/release artifact inventory and bounded public downloads."""
import argparse
import hashlib
import html
import json
from pathlib import Path
import re
import subprocess
import time
import urllib.error
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
SRC=ROOT/'third_party/phase2/scroll'
COMMIT='d897141dd5fb6c09753ff29761ef95c051c4655f'
BASE='https://git.xeondev.com/Supercell/Scroll'


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--download-apks',action='store_true');args=parser.parse_args()
    OUT.mkdir(parents=True,exist_ok=True)
    opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
    records=[]
    def get(url,dest,limit=2*1024*1024):
        t=time.perf_counter();record={'url':url,'destination':str(dest.relative_to(ROOT))}
        try:
            req=urllib.request.Request(url,headers={'User-Agent':'Mozilla/5.0 ClashRoyaleResearch/phase2'})
            with opener.open(req,timeout=25) as response:
                size=int(response.headers.get('Content-Length','0'))
                if size>limit:raise ValueError(f'Download limit: {size}>{limit}')
                record.update(status=response.status,headers=dict(response.headers),final_url=response.url)
                dest.parent.mkdir(parents=True,exist_ok=True);count=0
                with dest.with_suffix(dest.suffix+'.partial').open('wb') as handle:
                    while block:=response.read(1024*512):
                        count+=len(block)
                        if count>limit:raise ValueError('Streaming download exceeds budget')
                        handle.write(block)
                if size and count!=size:raise ValueError(f'Incomplete {count}/{size}')
                dest.with_suffix(dest.suffix+'.partial').replace(dest)
                record.update(bytes=count,sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),success=True)
        except Exception as exc:record.update(success=False,error=repr(exc))
        record['wall_seconds']=time.perf_counter()-t;records.append(record)
        (OUT/'scroll_fetch_progress.json').write_text(json.dumps(records,ensure_ascii=False,indent=2),encoding='utf-8')
        return record
    get('https://git.xeondev.com/api/v1/repos/Supercell/Scroll/releases',OUT/'scroll_releases_api.json')
    release=get(BASE+'/releases',OUT/'scroll_releases.html')
    links=[]
    if release.get('success'):
        text=(OUT/'scroll_releases.html').read_text(encoding='utf-8')
        for link in re.findall(r'href="([^"]+)"',text):
            if '.apk' in link:
                link=html.unescape(link)
                if link.startswith('/'):link='https://git.xeondev.com'+link
                if link not in links:links.append(link)
    for url in links:
        name=url.rsplit('/',1)[-1]
        if args.download_apks:get(url,ROOT/'third_party/phase2/scroll_assets'/name,120*1024*1024)
    actual=subprocess.check_output(['git','-C',str(SRC),'rev-parse','HEAD'],text=True).strip()
    assert actual==COMMIT
    tree=subprocess.check_output(['git','-C',str(SRC),'ls-tree','-r','--name-only',COMMIT],text=True).splitlines()
    inventory=[{'path':p,'bytes':(SRC/p).stat().st_size,'sha256':hashlib.sha256((SRC/p).read_bytes()).hexdigest()} for p in tree]
    report={'repository':BASE,'commit':COMMIT,'source_tree_files':len(tree),'source_inventory':inventory,
            'license':'No LICENSE file in pinned tree; wrapper permission unconfirmed. APK/libg/assets retain separate proprietary rights.',
            'release_links':links,'downloads':records,'apk_download_limit_each_bytes':120*1024*1024,
            'claim_scope':'Artifact availability/source audit only; no installed APK/native battle claim.'}
    (OUT/'scroll_sources_manifest.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'commit':COMMIT,'source_files':len(tree),'release_links':links,'downloads':[{k:v for k,v in r.items() if k!='headers'} for r in records]},ensure_ascii=True,indent=2))


if __name__=='__main__':main()

"""Fetch a bounded, project-local QEMU/Linux preparation set, without installers."""
import concurrent.futures
import hashlib
import json
from pathlib import Path
import shutil
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
CACHE=ROOT/'.cache/phase2/secondary_runtime'
OUT=ROOT/'outputs/phase2/secondary'
PLAN=[
 ('https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.exe','qemu-w64-setup-20260811.exe',230*1024*1024),
 ('https://qemu.weilnetz.de/w64/qemu-w64-setup-20260811.sha512','qemu.sha512',1024*1024),
 ('https://www.7-zip.org/a/7zr.exe','7zr.exe',2*1024*1024),
 ('https://github.com/ip7z/7zip/releases/download/26.04/7z2604-x64.exe','7z2604-x64.exe',4*1024*1024),
 ('https://cloud-images.ubuntu.com/minimal/releases/noble/release-20261008/SHA256SUMS','ubuntu-SHA256SUMS',1024*1024),
 ('https://cloud-images.ubuntu.com/minimal/releases/noble/release-20261008/ubuntu-24.04-minimal-cloudimg-amd64.img','ubuntu-24.04-minimal-cloudimg-amd64.img',400*1024*1024),
]


def fetch(entry):
    url,name,limit=entry;dest=CACHE/name;t=time.perf_counter();record={'url':url,'path':str(dest.relative_to(ROOT)),'limit_bytes':limit}
    if dest.exists():
        record.update(reused=True,bytes=dest.stat().st_size,sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),success=True)
        return record
    for direct in (True,False):
        try:
            opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if direct else urllib.request.build_opener()
            with opener.open(urllib.request.Request(url,headers={'User-Agent':'ClashRoyalePhase2Research/1.0'}),timeout=30) as response:
                size=int(response.headers.get('Content-Length','0'))
                if size>limit:raise ValueError(f'Budget {size}>{limit}')
                count=0;last=0
                with dest.with_suffix(dest.suffix+'.partial').open('wb') as handle:
                    while block:=response.read(512*1024):
                        count+=len(block)
                        if count>limit:raise ValueError('Stream exceeds limit')
                        handle.write(block)
                        if count-last>25*1024*1024:
                            print(json.dumps({'file':name,'bytes':count,'expected':size}),flush=True);last=count
                if size and count!=size:raise ValueError(f'Incomplete {count}/{size}')
                dest.with_suffix(dest.suffix+'.partial').replace(dest)
                record.update(success=True,direct=direct,bytes=count,sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),final_url=response.url)
            break
        except Exception as exc:record.setdefault('attempts',[]).append({'direct':direct,'error':repr(exc)})
    record['wall_seconds']=time.perf_counter()-t
    print(json.dumps({k:v for k,v in record.items() if k not in ('url','limit_bytes')},ensure_ascii=True),flush=True)
    return record


def main():
    CACHE.mkdir(parents=True,exist_ok=True);OUT.mkdir(parents=True,exist_ok=True)
    assert shutil.disk_usage(ROOT).free>10*1024**3,'Preserve >=10GB host free space'
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:records=list(pool.map(fetch,PLAN))
    verification=[]
    for sums_name,target,algorithm in [('qemu.sha512','qemu-w64-setup-20260811.exe','sha512'),('ubuntu-SHA256SUMS','ubuntu-24.04-minimal-cloudimg-amd64.img','sha256')]:
        sums=CACHE/sums_name;path=CACHE/target
        if sums.exists() and path.exists():
            expected=next((line.split()[0] for line in sums.read_text().splitlines() if target in line),'unknown')
            actual=hashlib.new(algorithm,path.read_bytes()).hexdigest()
            verification.append({'file':target,'algorithm':algorithm,'expected':expected,'actual':actual,'matched':expected==actual})
    report={'downloads':records,'upstream_checksum_verification':verification,'purpose':'Portable Linux runtime only; no installer execution, global feature change, license acceptance or GPU stack.'}
    (OUT/'runtime_sources_manifest.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'checksums':verification,'successes':sum(r.get('success',False) for r in records)},indent=2),flush=True)


if __name__=='__main__':main()

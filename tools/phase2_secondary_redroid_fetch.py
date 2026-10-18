"""Download exactly one previously audited OCI layer, verify digest and inventory it."""
import hashlib
import json
from pathlib import Path
import shutil
import tarfile
import time
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
CACHE=ROOT/'.cache/phase2/secondary_runtime'


def main():
    metadata=json.loads((OUT/'redroid_registry_audit.json').read_text())
    assert metadata['platform_descriptor']['digest']=='sha256:'+metadata['manifest']['sha256']
    assert metadata['layer_count']==1
    layer=metadata['layer_descriptors'][0];expected=layer['digest'].split(':')[1]
    assert layer['size']<700*1024*1024
    assert shutil.disk_usage(ROOT).free>15*1024**3
    dest=CACHE/'redroid-amd64-12-layer.tar.gz';partial=dest.with_suffix(dest.suffix+'.partial')
    opener=urllib.request.build_opener();t=time.perf_counter();attempts=[]
    if not dest.exists():
        for attempt in range(2):
            try:
                token=json.load(opener.open('https://auth.docker.io/token?service=registry.docker.io&scope=repository:redroid/redroid:pull',timeout=20))['token']
                headers={'Authorization':'Bearer '+token,'User-Agent':'ClashRoyalePhase2Research/1.0'}
                start=partial.stat().st_size if partial.exists() else 0
                if start:headers['Range']=f'bytes={start}-'
                req=urllib.request.Request('https://registry-1.docker.io/v2/redroid/redroid/blobs/'+layer['digest'],headers=headers)
                with opener.open(req,timeout=30) as response:
                    append=start>0 and response.status==206
                    count=start if append else 0;last=count
                    with partial.open('ab' if append else 'wb') as handle:
                        while block:=response.read(1024*1024):
                            count+=len(block)
                            if count>layer['size']:raise ValueError('Layer exceeds descriptor size')
                            handle.write(block)
                            if count-last>25*1024*1024:
                                print(json.dumps({'downloaded':count,'expected':layer['size']}),flush=True);last=count
                if count!=layer['size']:raise ValueError(f'Incomplete {count}/{layer["size"]}')
                actual=hashlib.sha256(partial.read_bytes()).hexdigest()
                if actual!=expected:raise ValueError('Layer digest mismatch')
                partial.replace(dest);break
            except Exception as exc:attempts.append({'attempt':attempt,'error':repr(exc)});print(json.dumps(attempts[-1]),flush=True)
    report={'path':str(dest.relative_to(ROOT)),'expected_sha256':expected,'expected_bytes':layer['size'],'attempts':attempts,'wall_seconds':time.perf_counter()-t}
    if dest.exists():
        report.update(actual_sha256=hashlib.sha256(dest.read_bytes()).hexdigest(),bytes=dest.stat().st_size)
        files=[];license_files=[];expanded=0
        with tarfile.open(dest,'r:gz') as archive:
            for member in archive:
                expanded+=member.size
                if any(k in member.name for k in ('libndk_translation','libnb.so','system/bin/arm64','system/lib64/arm64')):
                    files.append({'path':member.name,'size':member.size,'link':member.linkname})
                if any(k in member.name.lower() for k in ('license','notice')) and member.isfile():license_files.append({'path':member.name,'size':member.size})
        report.update(expanded_file_bytes=expanded,native_bridge_inventory=files,license_inventory=license_files,
                      verified=report['actual_sha256']==expected,
                      distribution_scope='Local use audit only. Redroid Apache2/GPL components and Android Emulator native-bridge prebuilts have separate rights; no image/binary redistribution in project bundle.')
    else:report['verified']=False
    (OUT/'redroid_layer_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k not in ('native_bridge_inventory','license_inventory')},indent=2),flush=True)


if __name__=='__main__':main()

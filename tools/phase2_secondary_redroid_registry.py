"""Anonymous OCI metadata query; no Docker/global install, account or image pull."""
import hashlib
import json
from pathlib import Path
import urllib.request

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    opener=urllib.request.build_opener()
    headers={'User-Agent':'ClashRoyalePhase2Research/1.0'}
    token=json.load(opener.open(urllib.request.Request('https://auth.docker.io/token?service=registry.docker.io&scope=repository:redroid/redroid:pull',headers=headers),timeout=20))['token']
    headers.update(Authorization='Bearer '+token,Accept='application/vnd.docker.distribution.manifest.list.v2+json, application/vnd.oci.image.index.v1+json, application/vnd.docker.distribution.manifest.v2+json, application/vnd.oci.image.manifest.v1+json')
    report={'repository':'redroid/redroid','tag':'12.0.0_64only-latest','scope':'Anonymous public metadata only; no account/token persisted, no image layers downloaded.'}
    def get(ref,blob=False):
        url='https://registry-1.docker.io/v2/redroid/redroid/'+('blobs/' if blob else 'manifests/')+ref
        with opener.open(urllib.request.Request(url,headers=headers),timeout=25) as response:
            data=response.read();return json.loads(data),{'url':url,'bytes':len(data),'sha256':hashlib.sha256(data).hexdigest(),'docker_content_digest':response.headers.get('Docker-Content-Digest')}
    index,indexmeta=get(report['tag']);(OUT/'redroid_index.json').write_text(json.dumps(index,indent=2),encoding='utf-8');report['index']=indexmeta
    descriptor=next(x for x in index['manifests'] if x.get('platform',{}).get('architecture')=='amd64')
    manifest,meta=get(descriptor['digest']);(OUT/'redroid_amd64_manifest.json').write_text(json.dumps(manifest,indent=2),encoding='utf-8')
    config,cmeta=get(manifest['config']['digest'],blob=True);(OUT/'redroid_amd64_config.json').write_text(json.dumps(config,indent=2),encoding='utf-8')
    report.update(platform_descriptor=descriptor,manifest=meta,compressed_layer_bytes=sum(x['size'] for x in manifest['layers']),layer_count=len(manifest['layers']),
                  layer_descriptors=manifest['layers'],config_metadata=cmeta,config={'created':config.get('created'),'architecture':config.get('architecture'),'os':config.get('os'),'labels':config.get('config',{}).get('Labels'),'entrypoint':config.get('config',{}).get('Entrypoint'),'cmd':config.get('config',{}).get('Cmd')})
    (OUT/'redroid_registry_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':main()

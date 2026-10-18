"""Inspect a publisher APK with bounded HTTP Range; optionally resume download.

No app execution, accounts, or server sessions. ZIP entries are CRC checked;
libg bytes are additionally compared to the exact wrapper-supported SHA256.
"""
import argparse
import hashlib
import io
import json
from pathlib import Path
import struct
import time
import urllib.request
import zipfile
import zlib

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/native'
ASSETS=ROOT/'third_party/phase2/firstlight/user-provided'
URL='https://tempweb.nullsusercontent.com/fpapk/nr_15.535.13_release_ff1c6c29.apk'
OPENER=urllib.request.build_opener(urllib.request.ProxyHandler({}))
HEADERS={'User-Agent':'Mozilla/5.0','Referer':'https://nulls.gg/servers/nulls-royale/download/'}
EXPECTED_LIBG='110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
EXPECTED_APK='685c115ee432d5fd2d7be856a58ea0cb029c9c187aa9061f3cd250994dfbcf2a'

def request_range(start,end):
    req=urllib.request.Request(URL,headers={'User-Agent':'offline-native-research/phase2','Range':f'bytes={start}-{end}'})
    with OPENER.open(req,timeout=45) as response:
        if response.status!=206:raise RuntimeError(f'Range unsupported: HTTP {response.status}')
        data=response.read(end-start+2)
        if len(data)!=end-start+1:raise RuntimeError(f'Range length {len(data)} != {end-start+1}')
        return data

def inspect():
    OUT.mkdir(parents=True,exist_ok=True);ASSETS.mkdir(parents=True,exist_ok=True)
    with OPENER.open(urllib.request.Request(URL,method='HEAD'),timeout=30) as response:
        record={'url':URL,'status':response.status,'headers':dict(response.headers),'final_url':response.url}
        size=int(response.headers['Content-Length']);record['bytes']=size
    (OUT/'apk_head.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
    tail=request_range(max(0,size-65557),size-1)
    marker=tail.rfind(b'PK\x05\x06')
    if marker<0:raise RuntimeError('ZIP EOCD missing')
    endrec=bytearray(tail[marker:]);cdsize,cdoffset=struct.unpack_from('<II',endrec,12)
    if cdsize>16_000_000:raise RuntimeError('Central directory over16MB budget')
    cd=request_range(cdoffset,cdoffset+cdsize-1)
    struct.pack_into('<I',endrec,16,0) # metadata-only fake ZIP, original header offsets retained
    entries=zipfile.ZipFile(io.BytesIO(cd+endrec)).infolist()
    report={'url':URL,'apk_bytes':size,'central_directory_bytes':cdsize,
        'entries':[{'path':e.filename,'size':e.file_size,'compressed_bytes':e.compress_size,'offset':e.header_offset,'crc32':e.CRC} for e in entries],
        'libg':[],'license':'Proprietary game/resource rights are not granted by the wrapper Apache license. Local research copy only; excluded from share bundles.'}
    for info in entries:
        if not info.filename.startswith('lib/') or not info.filename.endswith('/libg.so'):continue
        if info.file_size>200_000_000 or info.compress_size>100_000_000:raise RuntimeError('libg extraction over200MB budget')
        raw=request_range(info.header_offset,info.header_offset+29)
        if raw[:4]!=b'PK\x03\x04':raise RuntimeError('Local ZIP header mismatch')
        namelen,extralen=struct.unpack_from('<HH',raw,26)
        offset=info.header_offset+30+namelen+extralen
        payload=request_range(offset,offset+info.compress_size-1)
        body=zlib.decompress(payload,-15) if info.compress_type==8 else payload
        if len(body)!=info.file_size or zlib.crc32(body)!=info.CRC:raise RuntimeError('libg size/CRC mismatch')
        local=ASSETS/info.filename;local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(body)
        digest=hashlib.sha256(body).hexdigest()
        report['libg'].append({'path':str(local.relative_to(ROOT)).replace('\\','/'),
            'apk_entry':info.filename,'bytes':len(body),'sha256':digest,
            'supported_arm64_hash_matches':digest==EXPECTED_LIBG,
            'elf_class':body[4],'elf_machine':struct.unpack_from('<H',body,18)[0]})
        print(json.dumps(report['libg'][-1]),flush=True)
    (OUT/'apk_range_inspection.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'apk_bytes':size,'entries':len(entries),'native_libraries':len(report['libg'])}),flush=True)

def download():
    ASSETS.mkdir(parents=True,exist_ok=True)
    partial=ASSETS/'nulls-royale.apk.partial';final=ASSETS/'nulls-royale.apk'
    if final.exists() and hashlib.sha256(final.read_bytes()).hexdigest()==EXPECTED_APK:
        print('Already downloaded and expected SHA256 verified');return
    offset=partial.stat().st_size if partial.exists() else 0
    headers=dict(HEADERS)
    if offset:headers['Range']=f'bytes={offset}-'
    started=time.time();last=started
    with OPENER.open(urllib.request.Request(URL,headers=headers),timeout=45) as response:
        resumed=response.status==206
        total=(offset if resumed else 0)+int(response.headers.get('Content-Length','0'))
        if total>1_200_000_000:raise RuntimeError('APK over1.2GB download budget')
        if offset and not resumed:
            # Publisher advertises Accept-Ranges:none. A retry can still
            # continue the file: verify and consume the repeated prefix first.
            with partial.open('rb') as old:
                remaining=offset
                while remaining:
                    block=response.read(min(1024*1024,remaining))
                    if not block or old.read(len(block))!=block:raise RuntimeError('Repeated download prefix mismatch; partial preserved')
                    remaining-=len(block)
            print(json.dumps({'resume_mode':'verified repeated HTTP200 prefix','reused_bytes':offset}),flush=True)
        with partial.open('ab' if offset else 'wb') as output:
            while True:
                block=response.read(1024*1024)
                if not block:break
                output.write(block)
                if time.time()-last>=10:
                    output.flush();last=time.time();print(json.dumps({'downloaded':output.tell(),'total':total,'elapsed_s':last-started}),flush=True)
    with partial.open('rb') as stream:digest=hashlib.file_digest(stream,'sha256').hexdigest()
    report={'url':URL,'bytes':partial.stat().st_size,'sha256':digest,'supported_apk_sha256':EXPECTED_APK,
        'supported_hash_matches':digest==EXPECTED_APK,'elapsed_s':time.time()-started,
        'zip_crc_error':zipfile.ZipFile(partial).testzip()}
    if report['zip_crc_error'] is not None:raise RuntimeError('Downloaded APK ZIP CRC failure')
    partial.replace(final);report['path']=str(final.relative_to(ROOT)).replace('\\','/')
    (OUT/'apk_download.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report),flush=True)

def extract_local():
    apk=ASSETS/'nulls-royale.apk'
    report={'path':str(apk.relative_to(ROOT)).replace('\\','/'),'apk_sha256':hashlib.file_digest(apk.open('rb'),'sha256').hexdigest(),
        'supported_apk_sha256':EXPECTED_APK,'libg':[],'fingerprints':[]}
    with zipfile.ZipFile(apk) as archive:
        report['index']=[{'path':x.filename,'bytes':x.file_size,'compressed_bytes':x.compress_size,'crc32':x.CRC} for x in archive.infolist()]
        for info in archive.infolist():
            if info.filename.startswith('lib/') and info.filename.endswith('/libg.so'):
                body=archive.read(info);local=ASSETS/info.filename
                local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(body)
                digest=hashlib.sha256(body).hexdigest()
                report['libg'].append({'path':str(local.relative_to(ROOT)).replace('\\','/'),'apk_entry':info.filename,
                    'bytes':len(body),'sha256':digest,'supported_arm64_hash_matches':digest==EXPECTED_LIBG,
                    'elf_class':body[4],'elf_machine':struct.unpack_from('<H',body,18)[0]})
            if info.filename.endswith('fingerprint.json') or info.filename=='AndroidManifest.xml':
                body=archive.read(info);local=ASSETS/'metadata'/info.filename
                local.parent.mkdir(parents=True,exist_ok=True);local.write_bytes(body)
                report['fingerprints'].append({'apk_entry':info.filename,'path':str(local.relative_to(ROOT)).replace('\\','/'),
                    'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
    (OUT/'apk_local_inspection.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in report.items() if k!='index'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--download',action='store_true');p.add_argument('--extract-local',action='store_true');args=p.parse_args()
    extract_local() if args.extract_local else download() if args.download else inspect()

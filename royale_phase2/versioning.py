"""Content fingerprints and actual asset verification, independent of any simulator."""
import hashlib
import json
import math
from pathlib import Path
import struct
from .runtime import local

class VersionMismatch(ValueError):
    pass

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode('utf-8')).hexdigest()

def file_hash(path):
    digest=hashlib.sha256()
    with local(path).open('rb') as stream:
        for block in iter(lambda:stream.read(1024*1024),b''):digest.update(block)
    return digest.hexdigest()

def binary_identity(path):
    with local(path).open('rb') as stream:header=stream.read(4096)
    if header[:4]==b'\x7fELF':
        if len(header)<20 or header[4] not in (1,2) or header[5] not in (1,2):
            raise VersionMismatch('Malformed ELF header')
        machine=struct.unpack(('<' if header[5]==1 else '>')+'H',header[18:20])[0]
        return {'format':'ELF','bits':32 if header[4]==1 else 64,'machine':machine,
                'architecture':{3:'x86',40:'arm',62:'x86_64',183:'aarch64'}.get(machine,'unknown')}
    if header[:2]==b'MZ':
        return {'format':'PE','architecture':'inspect_required'}
    return {'format':'unknown','architecture':'unknown'}

def verify_manifest(path):
    path=local(path)
    if not path.is_file():raise FileNotFoundError(f'Version manifest not created: {path}')
    manifest=json.loads(path.read_text(encoding='utf-8-sig'))
    if manifest.get('schema')!='royale-phase2-version.v1':raise VersionMismatch('Unknown version schema')
    if manifest.get('runtime_kind')!='native_game':raise VersionMismatch('A reconstructed simulator is not a native-game backend')
    tick=manifest.get('ticks_per_second')
    if isinstance(tick,bool) or not isinstance(tick,(int,float)) or not math.isfinite(tick) or tick<=0:
        raise VersionMismatch('Native tick rate must be explicitly verified')
    assets=manifest.get('assets',[])
    if not assets or not any(a.get('role')=='native_library' for a in assets):
        raise VersionMismatch('An actual native library is required')
    records=[]
    for asset in assets:
        rel=asset['path'];actual=file_hash(rel)
        if actual!=asset['sha256']:raise VersionMismatch(f'Asset changed: {rel}')
        row={**asset,'verified_sha256':actual}
        if asset.get('role')=='native_library':
            row['binary']=binary_identity(rel)
            if row['binary']['format']=='unknown':raise VersionMismatch('Native library is not an identified executable format')
        records.append(row)
    fingerprint=canonical_hash({k:v for k,v in manifest.items() if k!='fingerprint'})
    if manifest.get('fingerprint') and manifest['fingerprint']!=fingerprint:
        raise VersionMismatch('Version fingerprint mismatch')
    return {'fingerprint':fingerprint,'assets':records,'manifest':manifest,
            'claim':'Asset identity only; does not prove battle execution or official trajectory parity.'}

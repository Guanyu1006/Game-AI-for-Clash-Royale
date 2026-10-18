"""Verify local source snapshots against fixed Git blob hashes; retain missing entries."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
ROWS = [
 ('crforge','crforge_git_sparse','crforge_git_sparse','b41cf9b6276945152ec4c869ce56f98a5fa3f513','Apache-2.0'),
 ('KataCR','KataCR_git_sparse','KataCR','36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8','MIT'),
 ('SlashRoyaleLegacy','SlashRoyaleLegacy_git_sparse','SlashRoyaleLegacy','a47d02474efc755427c8416b15e96daa9331d993','GPL-3.0 at server/SlashRoyale; wrapper license unconfirmed'),
 ('clash-royale-gym','clash-royale-gym','clash-royale-gym','1cd23be16e10b25c5a5a0889626a863092f32615','MIT'),
]
manifest=[]
for name, gitname, sourcename, commit, license_name in ROWS:
    gitdir=ROOT/'third_party/audit_candidates'/gitname
    sourcedir=ROOT/'third_party/audit_candidates'/sourcename
    tree=subprocess.check_output(['git','-C',str(gitdir),'ls-tree','-r',commit],text=True)
    row={'name':name,'commit':commit,'license':license_name,'path':str(sourcedir.relative_to(ROOT)),
         'verification_date':'2026-10-14','snapshot_scope':'source/config snapshot; media omitted where sparse; list records all upstream tree entries',
         'files':[],'present_exact_git_blobs':0,'windows_crlf_equivalent':0,'missing_files':0,'different_files':0}
    for line in tree.splitlines():
        meta,path=line.split('\t',1)
        mode,kind,sha=meta.split()
        if kind!='blob':continue
        local=sourcedir/path
        entry={'path':path,'expected_git_blob_sha1':sha}
        if local.exists():
            data=local.read_bytes()
            got=hashlib.sha1(b'blob '+str(len(data)).encode()+b'\0'+data).hexdigest()
            exact=got==sha
            normalized=data.replace(b'\r\n',b'\n')
            normalized_sha=hashlib.sha1(b'blob '+str(len(normalized)).encode()+b'\0'+normalized).hexdigest()
            equivalent=not exact and normalized_sha==sha
            entry.update(status='verified_exact_source_blob' if exact else 'verified_windows_crlf_checkout' if equivalent else 'differs_from_commit',
                         bytes=len(data),sha256=hashlib.sha256(data).hexdigest(),git_blob_sha1_actual=got)
            row['present_exact_git_blobs']+=int(exact)
            row['windows_crlf_equivalent']+=int(equivalent)
            row['different_files']+=int(not exact and not equivalent)
        else:
            entry['status']='not_downloaded'
            row['missing_files']+=1
        row['files'].append(entry)
    manifest.append(row)
    (ROOT/'outputs/audit_candidates'/f'{name}_snapshot_inventory.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
    print(name,row['present_exact_git_blobs'], 'exact +',row['windows_crlf_equivalent'],'CRLF equivalent /',len(row['files']),'tree blobs;',row['different_files'],'different')
archives=[]
for p in (ROOT/'.cache/audit').glob('*.zip'):
    entry={'path':str(p.relative_to(ROOT)),'bytes':p.stat().st_size,'sha256':hashlib.sha256(p.read_bytes()).hexdigest()}
    try:
        with zipfile.ZipFile(p) as z:
            failure=z.testzip()
            entry['status']='zip_crc_valid' if failure is None else 'zip_crc_failure'
    except Exception as e:
        entry.update(status='partial_or_invalid',error=repr(e))
    archives.append(entry)
(ROOT/'outputs/audit_candidates/source_inventory_manifest.json').write_text(json.dumps({'sources':manifest,'archives':archives},indent=2),encoding='utf-8')

"""Import a verified migration snapshot into an existing checkout, preserving conflicts."""
import argparse
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path,PurePosixPath
import shutil

BLOCK={'.git','.ssh','.aws','.cache','.venv','node_modules'}
def safe(root,name):
    p=PurePosixPath(name)
    if p.is_absolute() or '..' in p.parts or ':' in name or chr(92) in name or set(p.parts)&BLOCK:raise ValueError('Unsafe migration path: '+name)
    out=(root/Path(*p.parts)).resolve();out.relative_to(root)
    if out.is_symlink():raise ValueError('Symlink forbidden')
    return out

def plan(source,target):
    source=source.resolve();target=target.resolve()
    if not (target/'.git').exists():raise ValueError('Target must be an existing Git checkout. Create/switch a migration branch first.')
    manifest=json.loads((source/'MIGRATION_MANIFEST.json').read_text(encoding='utf-8'))
    result=[]
    for row in manifest['files']:
        p=safe(source,row['path']);data=p.read_bytes()
        if len(data)!=row['bytes'] or hashlib.sha256(data).hexdigest()!=row['sha256']:raise ValueError('Source snapshot changed: '+row['path'])
        dest=safe(target,row['path'])
        if dest.is_dir():raise ValueError('File/directory conflict: '+row['path'])
        state='new' if not dest.exists() else 'same' if dest.read_bytes()==data else 'conflict'
        result.append({'path':row['path'],'state':state})
    # Include the manifest itself after its payload was verified.
    result.append({'path':'MIGRATION_MANIFEST.json','state':'new' if not (target/'MIGRATION_MANIFEST.json').exists() else 'conflict'})
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--source',required=True);p.add_argument('--target',default='.')
    p.add_argument('--apply',action='store_true');p.add_argument('--replace',action='store_true');a=p.parse_args()
    source=Path(a.source).resolve();target=Path(a.target).resolve();items=plan(source,target)
    conflicts=[r['path'] for r in items if r['state']=='conflict']
    print(json.dumps({'files':len(items),'conflicts':conflicts,'apply':a.apply},ensure_ascii=False,indent=2))
    if not a.apply:return
    if conflicts and not a.replace:raise SystemExit('Existing differing files preserved. Review conflicts, then add --replace to back up and replace them.')
    backup=target/'.tmp/migration-backups'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
    copied=0
    for row in items:
        if row['state']=='same':continue
        src=safe(source,row['path']);dst=safe(target,row['path'])
        if row['state']=='conflict':
            b=safe(backup.resolve(),row['path']);b.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(dst,b)
        dst.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(src,dst);copied+=1
    print(json.dumps({'copied':copied,'backup':str(backup) if conflicts else None,'git_history_modified':False},ensure_ascii=False))

if __name__=='__main__':main()

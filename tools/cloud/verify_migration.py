"""Exercise a relocated migration package, local Git import/clone, training and HTTP.

No remote repository or GitHub account is contacted. Reports distinguish local
Windows execution from Linux CI/Codespaces execution that still needs a host.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path,PurePosixPath
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
ROOT=Path(__file__).resolve().parents[2]

def safe(path):
    path=(ROOT/path).resolve();path.relative_to(ROOT);return path

def hash_file(path):
    h=hashlib.sha256()
    with path.open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def verify(archive,python,output):
    archive=safe(archive);folder=safe(output);python=safe(python)
    folder.mkdir(parents=True,exist_ok=False)
    env=dict(os.environ,PYTHONPATH='',PYTHONUTF8='1',PYTHONDONTWRITEBYTECODE='1',OPENBLAS_NUM_THREADS='1',OMP_NUM_THREADS='2',TEMP=str(folder/'temp'),TMP=str(folder/'temp'))
    (folder/'temp').mkdir();commands=[]
    def run(argv,cwd,label,expected=0):
        start=time.monotonic();r=subprocess.run([str(a) for a in argv],cwd=cwd,env=env,capture_output=True,encoding='utf-8',errors='replace',timeout=180)
        (folder/(label+'.stdout.log')).write_text(r.stdout,encoding='utf-8');(folder/(label+'.stderr.log')).write_text(r.stderr,encoding='utf-8')
        commands.append({'argv':[str(a) for a in argv],'cwd':str(cwd),'label':label,'exit_code':r.returncode,'expected_exit_code':expected,'wall_seconds':time.monotonic()-start})
        (folder/'commands.json').write_text(json.dumps(commands,ensure_ascii=False,indent=2),encoding='utf-8')
        if r.returncode!=expected:raise RuntimeError(label+' unexpected exit'+str(r.returncode)+': '+(r.stdout+r.stderr)[-2500:])
        return r
    with zipfile.ZipFile(archive) as z:
        if z.testzip() is not None:raise ValueError('ZIP CRC failure')
        names=z.namelist()
        if len(names)!=len(set(names)):raise ValueError('Duplicate ZIP paths')
        for item in z.infolist():
            relative=PurePosixPath(item.filename)
            if relative.is_absolute() or '..' in relative.parts or ':' in item.filename or chr(92) in item.filename:raise ValueError('Unsafe ZIP path')
            target=(folder/'unpacked'/Path(*relative.parts)).resolve();target.relative_to(folder)
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(z.read(item))
    project=folder/'unpacked/RoyaleLab_GitHub'
    manifest=json.loads((project/'MIGRATION_MANIFEST.json').read_text(encoding='utf-8'))
    for row in manifest['files']:
        file=project/row['path']
        if file.stat().st_size!=row['bytes'] or hash_file(file)!=row['sha256']:raise ValueError('Payload integrity mismatch')
    for command,args in [('restore',[]),('test',[]),('infer',[]),('infer-gru',[]),('train',['--epochs','2','--output','outputs/cloud/verification-numpy']),('train-gru',['--epochs','2','--output','outputs/cloud/verification-gru'])]:
        run([python,'-B','tools/cloud/workspace.py',command,*args],project,command)
    run([python,'-B','-m','royale_ai','simulate','--policy','greedy','--opponent','greedy','--seconds','30','--log','outputs/cloud/mvp.jsonl','--html','outputs/cloud/mvp.html'],project,'preserved-mvp')
    # Check the actual web protocol, including live model inference, without a browser.
    log=folder/'web.stdout.log';err=folder/'web.stderr.log'
    with log.open('w',encoding='utf8') as stdout,err.open('w',encoding='utf8') as stderr:
        proc=subprocess.Popen([str(python),'-B','tools/cloud/workspace.py','serve','--port','0'],cwd=project,env=env,stdout=stdout,stderr=stderr,
            creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        try:
            deadline=time.monotonic()+30;server=None
            while time.monotonic()<deadline:
                line=log.read_text(encoding='utf8').strip()
                if line:server=json.loads(line.splitlines()[0]);break
                if proc.poll() is not None:raise RuntimeError('Web process exited')
                time.sleep(.1)
            if not server:raise TimeoutError('Web startup timeout')
            http=[]
            for route in ('/','/api/status','/api/infer','/api/gru'):
                with urllib.request.urlopen(server['url']+route,timeout=30) as response:
                    body=response.read();http.append({'route':route,'status':response.status,'bytes':len(body),'sha256':hashlib.sha256(body).hexdigest()})
                    if route=='/api/status' and json.loads(body)['native_connected'] is not False:raise AssertionError('False native claim')
        finally:
            proc.terminate()
            try:proc.wait(10)
            except subprocess.TimeoutExpired:proc.kill();proc.wait(5)
    git=shutil.which('git')
    if not git:raise RuntimeError('Git required to validate import/clone')
    repo=folder/'friend-existing-repo';repo.mkdir();empty_hooks=folder/'empty-hooks';empty_hooks.mkdir()
    run([git,'init',str(repo)],folder,'git-init')
    (repo/'README.md').write_text('Friends existing README; preserve a backup.\n',encoding='utf-8')
    (repo/'friend-original.txt').write_text('Existing project work\n',encoding='utf-8')
    (repo/'.env').write_text('EXAMPLE_TEST_VALUE=not-a-secret\n',encoding='utf-8')
    importer=project/'tools/cloud/import_repository.py'
    run([python,importer,'--source',project,'--target',repo],folder,'import-plan')
    run([python,importer,'--source',project,'--target',repo,'--apply'],folder,'import-refuse-conflict',expected=1)
    assert (repo/'README.md').read_text(encoding='utf8').startswith('Friends existing')
    run([python,importer,'--source',project,'--target',repo,'--apply','--replace'],folder,'import-apply')
    assert (repo/'friend-original.txt').read_text(encoding='utf8')=='Existing project work\n'
    backups=list((repo/'.tmp/migration-backups').glob('*/README.md'));assert len(backups)==1
    assert backups[0].read_text(encoding='utf8').startswith('Friends existing')
    run([git,'-c','core.autocrlf=false','add','--all'],repo,'git-add')
    tracked=run([git,'ls-files','-z'],repo,'git-tracked').stdout.split(chr(0));tracked=[n for n in tracked if n]
    if '.env' in tracked or any(n.startswith(('.tmp/','.cache/','.venv','data/','models/','outputs/','third_party/')) for n in tracked):raise ValueError('Unsafe or generated Git payload')
    expected={r['path'] for r in manifest['files']}|{'MIGRATION_MANIFEST.json'}
    if not expected.issubset(set(tracked)):raise ValueError('Required payload ignored by Git: '+str(sorted(expected-set(tracked))[:10]))
    run([git,'-c','core.hooksPath='+str(empty_hooks),'-c','user.name=Migration verification','-c','user.email=migration-test@example.invalid','commit','-m','Local migration verification only'],repo,'git-local-test-commit')
    clone=folder/'fresh-clone'
    run([git,'-c','core.autocrlf=false','clone','--no-hardlinks',str(repo),str(clone)],folder,'git-local-clone')
    run([python,'-B','tools/cloud/workspace.py','verify'],clone,'clone-asset-integrity')
    run([python,'-B','tools/cloud/workspace.py','restore'],clone,'clone-restore')
    run([python,'-B','tools/cloud/workspace.py','infer'],clone,'clone-model-inference')
    tests=json.loads((project/'outputs/cloud/tests.json').read_text(encoding='utf8'))
    numeric=json.loads((project/'outputs/cloud/verification-numpy/report.json').read_text(encoding='utf8'))
    gru=json.loads((project/'outputs/cloud/verification-gru/report.json').read_text(encoding='utf8'))
    report={'schema':'github-codespaces-migration-verification.v1','status':'pass','archive':str(archive.relative_to(ROOT)),
      'sha256':hash_file(archive),'zip_bytes':archive.stat().st_size,'payload_files_verified':len(manifest['files']),
      'crc':'pass','relocation':'pass','portable_tests':tests,'numeric_training':{'epochs':numeric['epochs'],'train_rows':numeric['train_rows'],'validation_rows':numeric['validation_rows'],'test_evaluated':False},
      'gru_training':{'epochs':gru['completed_epochs'],'parameters':gru['parameter_count'],'test_evaluated':gru['test_evaluated']},
      'http':http,'existing_repo_conflicts_preserved':True,'git_clone_asset_hashes_verified':True,'tracked_files':len(tracked),
      'windows_launchers_included':all((project/p).exists() for p in ('run_play.ps1','run_platform.ps1','run.ps1','phase2.ps1')),
      'execution_host':sys.platform,'verification_python':str(python),'remote_upload_performed':False,
      'hosted_codespaces_verified':False,'linux_container_build_verified':False,'native_game_runtime_tested':False,
      'limitations':['No authenticated hosted Codespace was available to this task','Linux GitHub Actions is prepared, not yet run','Original native assets and Android/Binder permissions are separate requirements']}
    (folder/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    archive.with_suffix('.verification.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return report

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--archive',required=True);p.add_argument('--python',default='.venv-cloud-verify/Scripts/python.exe');p.add_argument('--output',required=True)
    a=p.parse_args()
    try:verify(a.archive,a.python,a.output)
    except Exception as e:
        folder=safe(a.output);folder.mkdir(parents=True,exist_ok=True)
        (folder/'failure.json').write_text(json.dumps({'status':'failed','error':str(e)},ensure_ascii=False,indent=2),encoding='utf8');raise

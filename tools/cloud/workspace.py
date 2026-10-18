"""Portable CPU workbench. No native RPC, ADB, account login or physics fallback."""
import argparse
from datetime import datetime,timezone
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path,PurePosixPath
import platform
import shutil
import subprocess
import sys
import unittest
from urllib.parse import urlsplit

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ.setdefault('OMP_NUM_THREADS','2');os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
for key,rel in {'TEMP':'.tmp/cloud','TMP':'.tmp/cloud','PIP_CACHE_DIR':'.cache/cloud/pip','TORCH_HOME':'.cache/cloud/torch','XDG_CACHE_HOME':'.cache/cloud/xdg','MPLCONFIGDIR':'.cache/cloud/matplotlib'}.items():
    p=ROOT/rel;p.mkdir(parents=True,exist_ok=True);os.environ[key]=str(p)

def local(path):
    p=(ROOT/path).resolve();p.relative_to(ROOT);return p

def sha(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
    return h.hexdigest()

def read(path):return json.loads(local(path).read_text(encoding='utf-8-sig'))
def write(path,data):
    p=local(path);p.parent.mkdir(parents=True,exist_ok=True)
    tmp=p.with_name(p.name+'.partial');tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8');os.replace(tmp,p)
def output(label):return local('outputs/cloud/runs/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+label)
def manifest():
    p=local('shared_assets/MANIFEST.json')
    if not p.exists():raise FileNotFoundError('Portable assets missing. Use the reviewed GitHub upload package, or run tools/package_github.py locally first.')
    return read(p)

def verify_assets():
    m=manifest()
    for row in m['files']:
        p=local(row['source'])
        if p.is_symlink() or not p.is_file() or p.stat().st_size!=row['bytes'] or sha(p)!=row['sha256']:
            raise ValueError('Portable asset integrity mismatch: '+row['source'])
    return {'status':'pass','files':len(m['files']),'scope':'Reviewed portable payload integrity only; no native or strength claim'}

def restore():
    verified=verify_assets();copied=[];preserved=[]
    for row in manifest()['files']:
        target=row.get('destination')
        if not target:continue
        dest=local(target)
        if dest.is_symlink():raise ValueError('Refusing restored symlink')
        if dest.exists():
            if sha(dest)!=row['sha256']:preserved.append(target)
            continue
        dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(local(row['source']),dest);copied.append(target)
    result={'status':'restored','integrity':verified,'copied':len(copied),'preserved_existing_different':preserved,
      'scope':'Existing local files are never overwritten; history and native state remain separate'}
    write('outputs/cloud/restore.json',result);return result

def doctor():
    from tools.cloud.native_capabilities import inspect
    result={'schema':'royale-cloud-doctor.v1','system':platform.system(),'machine':platform.machine(),
      'python':sys.version,'executable':sys.executable,'root':str(ROOT),'logical_cpus':os.cpu_count(),
      'disk_free_bytes':shutil.disk_usage(ROOT).free,'modules':{k:bool(importlib.util.find_spec(k)) for k in ('numpy','torch','pyarrow')},
      'codespaces_environment':os.environ.get('CODESPACES')=='true','offline_workbench':True,
      'native':inspect(),'assets':verify_assets(),'local_windows_launchers_preserved':True}
    write('outputs/cloud/doctor.json',result);return result

def infer():
    import numpy as np
    from royale_phase2.learning import actor_features,forward,decode_policy_output,action_from_index
    m=manifest();meta=read(m['numeric']['metadata']);sample=read(m['numeric']['samples'])['samples']
    with np.load(local(m['numeric']['weights']),allow_pickle=False) as a:parameters={k:a[k].copy() for k in a.files}
    results=[]
    for s in sample:
        mask=np.asarray([s['recorded_mask']],dtype=bool);x=actor_features(s['actor'],meta['feature_configuration'])[None,:]
        _,probability,xy=forward(parameters,x,mask)
        selected,points=decode_policy_output(probability,xy,mask,play_bias=meta.get('inference_play_bias',0.))
        action=action_from_index(s['actor'],int(selected[0]),points[0])
        results.append({'sample_id':s['sample_id'],'tick':s['actor']['tick'],'action':action,
            'matches_saved_decoding':action==s['expected_deployment_decoding']})
    return {'schema':'cloud-saved-numeric-inference.v1','results':results,'play_bias':meta.get('inference_play_bias',0.),
      'native_rpc_calls':0,'fresh_legality_revalidated':False,
      'scope':'Actual checkpoint inference on saved derived public observations/recorded masks. Not a new native battle.'}

def infer_gru():
    from royale_phase3.learning import StatefulPolicy,load_dataset
    m=manifest()['phase3'];data,meta=load_dataset(m['metadata'])
    model=StatefulPolicy.load(m['checkpoint'],meta['version_fingerprint_sha256'],meta['observation_profile'])
    selected=[r for r in data if r['split']=='train' and r['native_owner']==0][:8]
    return {'schema':'cloud-saved-gru-inference.v1','samples':[{'tick':r['tick'],'prediction':model.predict(r)} for r in selected],
      'native_rpc_calls':0,'scope':'Actual saved GRU on real previously collected training observations; not test evaluation or native rollout'}

def run_tests():
    names=['tests_cloud.test_portable','tests_cloud.test_codex_setup','tests_phase3.test_observation','tests_phase3.test_actions_data']
    if importlib.util.find_spec('torch'):names.append('tests_phase3.test_learning')
    loader=unittest.TestLoader();suite=unittest.TestSuite(loader.loadTestsFromName(n) for n in names)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
      'skipped':len(result.skipped),'modules':names,'native_calls':0,'scope':'Portable implementation contracts; no official-game accuracy claim'}
    write('outputs/cloud/tests.json',report)
    if not result.wasSuccessful():raise SystemExit(1)
    return report

def serve(host='127.0.0.1',port=8770):
    page=local('tools/cloud/workbench.html').read_bytes()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def do_GET(self):
            route=urlsplit(self.path).path
            try:
                if route=='/':body=page;kind='text/html; charset=utf-8'
                elif route=='/api/status':
                    m=manifest();body=json.dumps({'mode':'offline_research','native_connected':False,'description':'保存样例 / 实际模型推理 / CPU训练；原生对局尚未接入',
                      'numeric':m.get('numeric_counts'),'phase3':m.get('phase3_counts'),'python':platform.python_version(),
                      'torch_installed':bool(importlib.util.find_spec('torch'))},ensure_ascii=False).encode();kind='application/json; charset=utf-8'
                elif route=='/api/infer':body=json.dumps(infer(),ensure_ascii=False).encode();kind='application/json; charset=utf-8'
                elif route=='/api/gru':body=json.dumps(infer_gru(),ensure_ascii=False).encode();kind='application/json; charset=utf-8'
                else:self.send_error(404);return
                self.send_response(200);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
                self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
            except Exception as e:
                body=json.dumps({'error':str(e)},ensure_ascii=False).encode();self.send_response(503);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    server=ThreadingHTTPServer((host,port),Handler);server.daemon_threads=True
    print(json.dumps({'url':f'http://{host}:{server.server_port}','mode':'offline_research','native_connected':False}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

def main():
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for c in ('restore','doctor','verify','test','infer','infer-gru','native-check'):sub.add_parser(c)
    a=sub.add_parser('serve');a.add_argument('--host',default='127.0.0.1');a.add_argument('--port',type=int,default=8770)
    a=sub.add_parser('train');a.add_argument('--epochs',type=int,default=2);a.add_argument('--output')
    a=sub.add_parser('train-gru');a.add_argument('--epochs',type=int,default=2);a.add_argument('--output')
    a=p.parse_args()
    if a.command=='restore':result=restore()
    elif a.command=='verify':result=verify_assets()
    elif a.command=='doctor':result=doctor()
    elif a.command=='test':result=run_tests()
    elif a.command=='infer':result=infer()
    elif a.command=='infer-gru':result=infer_gru()
    elif a.command=='native-check':
        from tools.cloud.native_capabilities import inspect
        result=inspect();write('outputs/cloud/native-capabilities.json',result)
    elif a.command=='serve':return serve(a.host,a.port)
    elif a.command=='train':
        from tools.cloud.train_cache import train
        result=train(a.epochs,a.output)
    else:
        from royale_phase3.learning import train
        result=train(manifest()['phase3']['metadata'],a.output or output('gru').relative_to(ROOT).as_posix(),variant='D',epochs=a.epochs,seed=31001,play_weight=8.)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':main()

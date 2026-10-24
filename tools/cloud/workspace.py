"""CPU workbench; explicit native commands retain strict runtime gates."""
import argparse
from datetime import datetime,timezone
import hashlib
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import importlib.util
import json
import os
from pathlib import Path,PurePosixPath,PureWindowsPath
import platform
import shutil
import subprocess
import sys
import threading
import unittest
from urllib.parse import urlsplit,parse_qs

ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT));sys.dont_write_bytecode=True
os.environ.setdefault('OMP_NUM_THREADS','2');os.environ.setdefault('OPENBLAS_NUM_THREADS','2')
for key,rel in {'TEMP':'.tmp/cloud','TMP':'.tmp/cloud','TMPDIR':'.tmp/cloud','PIP_CACHE_DIR':'.cache/cloud/pip','TORCH_HOME':'.cache/cloud/torch','XDG_CACHE_HOME':'.cache/cloud/xdg','MPLCONFIGDIR':'.cache/cloud/matplotlib'}.items():
    p=ROOT/rel;p.mkdir(parents=True,exist_ok=True);os.environ[key]=str(p)

def local(path):
    if os.name!='nt' and PureWindowsPath(str(path)).drive:raise ValueError('Windows paths are not valid in this Linux workspace')
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
    from tools.cloud.memory_resources import inspect_memory
    result={'schema':'royale-cloud-doctor.v1','system':platform.system(),'machine':platform.machine(),
      'python':sys.version,'executable':sys.executable,'root':str(ROOT),'logical_cpus':os.cpu_count(),
      'memory':inspect_memory(),
      'disk_free_bytes':shutil.disk_usage(ROOT).free,'modules':{k:bool(importlib.util.find_spec(k)) for k in ('numpy','torch','pyarrow','gymnasium')},
      'codespaces_environment':os.environ.get('CODESPACES')=='true','offline_workbench':True,
      'native':inspect(),'assets':verify_assets(),'local_windows_launchers_preserved':True}
    write('outputs/cloud/doctor.json',result);return result

def infer(checkpoint=None):
    import numpy as np
    from royale_phase2.learning import actor_features,forward,decode_policy_output,action_from_index
    m=manifest();sample=read(m['numeric']['samples'])['samples']
    if checkpoint:
        from tools.cloud.train_cache import load_checkpoint
        parameters,meta=load_checkpoint(checkpoint)
    else:
        meta=read(m['numeric']['metadata'])
        with np.load(local(m['numeric']['weights']),allow_pickle=False) as a:parameters={k:a[k].copy() for k in a.files}
    results=[]
    for s in sample:
        mask=np.asarray([s['recorded_mask']],dtype=bool);x=actor_features(s['actor'],meta['feature_configuration'])[None,:]
        _,probability,xy=forward(parameters,x,mask)
        selected,points=decode_policy_output(probability,xy,mask,play_bias=meta.get('inference_play_bias',0.))
        action=action_from_index(s['actor'],int(selected[0]),points[0])
        results.append({'sample_id':s['sample_id'],'tick':s['actor']['tick'],'action':action,
            'matches_saved_decoding':action==s['expected_deployment_decoding'] if not checkpoint else None})
    return {'schema':'cloud-saved-numeric-inference.v1','results':results,'play_bias':meta.get('inference_play_bias',0.),
      'native_rpc_calls':0,'fresh_legality_revalidated':False,'checkpoint':checkpoint or m['numeric']['metadata'],
      'scope':'Actual checkpoint inference on saved derived public observations/recorded masks. Not a new native battle.'}

def infer_gru(checkpoint=None,metadata=None):
    from royale_phase3.learning import StatefulPolicy,load_dataset
    m=manifest()['phase3'];data,meta=load_dataset(metadata or m['metadata'])
    model=StatefulPolicy.load(checkpoint or m['checkpoint'],meta['version_fingerprint_sha256'],meta['observation_profile'])
    selected=[r for r in data if r['split']=='train' and r['native_owner']==0][:8]
    return {'schema':'cloud-saved-gru-inference.v1','samples':[{'tick':r['tick'],'prediction':model.predict(r)} for r in selected],
      'native_rpc_calls':0,'checkpoint':checkpoint or m['checkpoint'],
      'scope':'Actual saved GRU on previously collected training observations; not test evaluation or native rollout'}

def run_tests(group='portable'):
    names=['tests_phase3.test_observation','tests_phase3.test_actions_data','tests_phase3.test_evaluation']
    if importlib.util.find_spec('torch'):names.append('tests_phase3.test_learning')
    loader=unittest.TestLoader();cloud_names=['tests_cloud.'+p.stem for p in sorted(local('tests_cloud').glob('test_*.py'))]
    suite=unittest.TestSuite(loader.loadTestsFromName(n) for n in cloud_names+names)
    if group=='extended':
        names+=['tests_phase3.test_belief','tests_phase3.test_sources','tests_phase2 (discovery)']
        suite.addTests(loader.loadTestsFromName(n) for n in ('tests_phase3.test_belief','tests_phase3.test_sources'))
        suite.addTests(loader.loadTestsFromName('tests_phase2.'+p.stem) for p in sorted(local('tests_phase2').glob('test_*.py')))
    elif group!='portable':raise ValueError('Unknown test group')
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    report={'passed':result.wasSuccessful(),'tests':result.testsRun,'failures':len(result.failures),'errors':len(result.errors),
      'skipped':len(result.skipped),'modules':cloud_names+names,'group':group,'native_calls':0,
      'outside_portable_scope':['Historical native fixtures, raw Parquet/source samples, native CSV/assets and rendered archive acceptance'],
      'scope':'Implementation contracts and saved derived data; no official-game accuracy claim'}
    write('outputs/cloud/tests.json',report)
    if not result.wasSuccessful():raise SystemExit(1)
    return report

def allowed_origin(origin,host,port):
    parsed=urlsplit(origin)
    if parsed.scheme not in ('http','https'):return False
    if parsed.netloc==host:return True
    # Codespaces can preserve the public Origin while rewriting the local Host.
    # Accept only this workspace's exact port URL, never arbitrary forwarded headers.
    name=os.environ.get('CODESPACE_NAME');domain=os.environ.get('GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN')
    expected=f'https://{name}-{port}.{domain}' if name and domain else None
    return os.environ.get('CODESPACES')=='true' and origin==expected

def make_server(host='127.0.0.1',port=8770,*,simulation_root=None,replay_metadata=None):
    page=local('tools/cloud/workbench.html').read_bytes();lock=threading.RLock();sessions={}
    if replay_metadata is not None:
        from tools.cloud.replay import ReplaySession
        sessions['replay']=ReplaySession(metadata_path=local(replay_metadata).relative_to(ROOT).as_posix())
    def session(kind):
        if kind not in sessions:
            if kind=='replay':
                from tools.cloud.replay import ReplaySession
                sessions[kind]=ReplaySession()
            else:
                from tools.cloud.simulator import SimulatorSession
                sessions[kind]=SimulatorSession(root=simulation_root or ROOT)
        return sessions[kind]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*a):pass
        def respond(self,body,status=200,kind='application/json; charset=utf-8'):
            if not isinstance(body,bytes):body=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
            self.send_response(status);self.send_header('Content-Type',kind);self.send_header('Content-Length',str(len(body)))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
        def do_GET(self):
            parsed=urlsplit(self.path);route=parsed.path;query=parse_qs(parsed.query)
            try:
                if route=='/':return self.respond(page,kind='text/html; charset=utf-8')
                elif route=='/research':return self.respond(local('tools/cloud/research.html').read_bytes(),kind='text/html; charset=utf-8')
                elif route=='/api/status':
                    m=manifest();body={'mode':'offline_research','native_connected':False,'description':'保存回放 / 实际模型推理 / CPU训练 / 近似模拟器',
                      'numeric':m.get('numeric_counts'),'phase3':m.get('phase3_counts'),'python':platform.python_version(),
                      'torch_installed':bool(importlib.util.find_spec('torch')),
                      'capabilities':{'saved_replay':True,'approximate_simulator':True,'native_game':False}}
                elif route=='/api/infer':body=infer(query.get('checkpoint',[None])[0])
                elif route=='/api/gru':body=infer_gru(query.get('checkpoint',[None])[0])
                elif route=='/api/replay/catalog':
                    with lock:body=session('replay').catalog()
                elif route=='/api/replay/state':
                    with lock:body=session('replay').state()
                elif route=='/api/simulator/state':
                    with lock:body=session('simulator').state()
                elif route=='/api/runs':body={'runs':runs()}
                else:return self.respond({'error':'Unknown route'},404)
                self.respond(body)
            except (ValueError,KeyError,TypeError) as exc:self.respond({'error':str(exc)},400)
            except Exception as exc:self.respond({'error':str(exc)},503)
        def do_POST(self):
            try:
                origin=self.headers.get('Origin')
                if origin and not allowed_origin(origin,self.headers.get('Host'),self.server.server_port):return self.respond({'error':'Cross-origin request rejected'},403)
                if self.headers.get_content_type()!='application/json':return self.respond({'error':'application/json required'},415)
                length=int(self.headers.get('Content-Length','0'))
                if not 0<length<=65536:return self.respond({'error':'JSON body must be 1..65536 bytes'},400)
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError('JSON object required')
                route=urlsplit(self.path).path
                with lock:
                    if route=='/api/replay/reset':body=session('replay').reset(**data)
                    elif route=='/api/replay/seek':body=session('replay').seek(**data)
                    elif route=='/api/replay/step':body=session('replay').step(**data)
                    elif route=='/api/simulator/reset':body=session('simulator').reset(**data)
                    elif route=='/api/simulator/step':body=session('simulator').step(**data)
                    elif route=='/api/simulator/play':body=session('simulator').play(**data)
                    elif route=='/api/simulator/replay':body=session('simulator').replay(**data)
                    elif route=='/api/simulator/save':
                        if data:raise ValueError('Save accepts an empty JSON object')
                        body=session('simulator').save()
                    else:return self.respond({'error':'Unknown route'},404)
                self.respond(body)
            except (ValueError,KeyError,TypeError) as exc:self.respond({'error':str(exc)},400)
            except Exception as exc:self.respond({'error':str(exc)},503)
    server=ThreadingHTTPServer((host,port),Handler);server.daemon_threads=True;return server

def runs():
    folder=local('outputs/cloud/runs');reports=[]
    for path in sorted(folder.glob('*/report.json'),reverse=True)[:30]:
        try:
            value=json.loads(path.read_text(encoding='utf-8'))
            reports.append({'run_id':path.parent.name,'report':str(path.relative_to(ROOT)),
                'schema':value.get('schema'),'checkpoint':value.get('checkpoint'),'completed':value.get('completed'),
                'test_evaluated':value.get('test_evaluated')})
        except (ValueError,OSError):continue
    return reports

def serve(host='127.0.0.1',port=8770,replay_metadata=None):
    server=make_server(host,port,replay_metadata=replay_metadata)
    print(json.dumps({'url':f'http://{host}:{server.server_port}','mode':'offline_research',
        'native_connected':False,'replay_metadata':replay_metadata}),flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

def main():
    native_tools={'native-loop':'tools/native_loop.py','native-connection':'tools/cloud/native_connection.py',
                  'native-browser':'tools/cloud/native_browser.py','native-prepare':'tools/cloud/native_prepare.py',
                  'play-replay':'tools/cloud/replay_app.py'}
    if len(sys.argv)>1 and sys.argv[1] in native_tools:
        return subprocess.call([sys.executable,'-B',str(local(native_tools[sys.argv[1]])),*sys.argv[2:]],cwd=ROOT)
    p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='command',required=True)
    for c in ('restore','doctor','verify','native-check','runs'):sub.add_parser(c)
    a=sub.add_parser('test');a.add_argument('--group',choices=['portable','extended'],default='portable')
    a=sub.add_parser('infer');a.add_argument('--checkpoint')
    a=sub.add_parser('infer-gru');a.add_argument('--checkpoint');a.add_argument('--metadata')
    a=sub.add_parser('data-check');a.add_argument('--output')
    for name in ('evaluate','evaluate-gru'):
        a=sub.add_parser(name);a.add_argument('--checkpoint',required=True);a.add_argument('--split',choices=['train','validation'],default='validation');a.add_argument('--output')
        if name=='evaluate-gru':a.add_argument('--metadata')
    a=sub.add_parser('serve');a.add_argument('--host',default='127.0.0.1');a.add_argument('--port',type=int,default=8770);a.add_argument('--replay-metadata')
    a=sub.add_parser('train');a.add_argument('--epochs',type=int,default=2);a.add_argument('--output')
    a=sub.add_parser('train-gru');a.add_argument('--epochs',type=int,default=2);a.add_argument('--output');a.add_argument('--resume',action='store_true');a.add_argument('--stop-after-epochs',type=int)
    a=sub.add_parser('replay');a.add_argument('--episode-id');a.add_argument('--owner',type=int);a.add_argument('--index',type=int,default=0);a.add_argument('--metadata')
    a=sub.add_parser('native-loop',add_help=False,help='Explicit native collection/training/evaluation; requires verified Android runtime')
    a.add_argument('arguments',nargs=argparse.REMAINDER)
    a=sub.add_parser('native-connection',add_help=False,help='Inspect local native assets; --connect explicitly probes loopback status')
    a.add_argument('arguments',nargs=argparse.REMAINDER)
    for name in ('native-browser','native-prepare'):
        a=sub.add_parser(name,add_help=False,help='Explicit actual Android rendered control/replay and runtime preparation')
        a.add_argument('arguments',nargs=argparse.REMAINDER)
    a=sub.add_parser('simulate');a.add_argument('--seconds',type=int,default=30);a.add_argument('--seed',type=int,default=0)
    a=sub.add_parser('smoke');a.add_argument('--epochs',type=int,default=2);a.add_argument('--output')
    a=p.parse_args()
    if a.command=='restore':result=restore()
    elif a.command=='verify':result=verify_assets()
    elif a.command=='doctor':result=doctor()
    elif a.command=='test':result=run_tests(a.group)
    elif a.command=='infer':result=infer(a.checkpoint)
    elif a.command=='infer-gru':result=infer_gru(a.checkpoint,a.metadata)
    elif a.command=='runs':result=runs()
    elif a.command=='data-check':
        from tools.cloud.evidence import audit
        result=audit(a.output)
    elif a.command=='evaluate':
        from tools.cloud.train_cache import evaluate
        result=evaluate(a.checkpoint,a.split,a.output)
    elif a.command=='evaluate-gru':
        from royale_phase3.learning import StatefulPolicy,evaluate,load_dataset
        data,meta=load_dataset(a.metadata or manifest()['phase3']['metadata'])
        policy=StatefulPolicy.load(a.checkpoint,meta['version_fingerprint_sha256'],meta['observation_profile'])
        result=evaluate(policy.model,data,a.split);result.update(test_evaluated=False,native_rpc_calls=0,checkpoint=a.checkpoint)
        target=local(a.output) if a.output else output('gru-evaluation')/'report.json'
        if target.exists():raise FileExistsError('Use a new evaluation output')
        write(target,result);result['report_path']=str(target.relative_to(ROOT))
    elif a.command=='replay':
        from tools.cloud.replay import ReplaySession
        session=ReplaySession(metadata_path=a.metadata);session.reset(a.episode_id,a.owner);result=session.seek(a.index)
    elif a.command in ('native-loop','native-connection','native-browser','native-prepare'):
        script={'native-loop':'tools/native_loop.py','native-connection':'tools/cloud/native_connection.py',
                'native-browser':'tools/cloud/native_browser.py','native-prepare':'tools/cloud/native_prepare.py'}[a.command]
        return subprocess.call([sys.executable,'-B',str(local(script)),*a.arguments],cwd=ROOT)
    elif a.command=='simulate':
        from tools.cloud.simulator import SimulatorSession
        if not 1<=a.seconds<=600:raise ValueError('seconds must be 1..600')
        session=SimulatorSession(seed=a.seed,max_time=a.seconds)
        while not session.state()['done']:session.step(min(30,a.seconds),autoplay=True)
        result=session.save()
    elif a.command=='smoke':
        from tools.cloud.workflow import smoke
        result=smoke(a.output,a.epochs)
        print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)
        return 0 if result['status']=='passed' else 1
    elif a.command=='native-check':
        from tools.cloud.native_capabilities import inspect
        result=inspect();write('outputs/cloud/native-capabilities.json',result)
    elif a.command=='serve':return serve(a.host,a.port,a.replay_metadata)
    elif a.command=='train':
        from tools.cloud.train_cache import train
        result=train(a.epochs,a.output)
    elif a.command=='train-gru':
        from royale_phase3.learning import train
        if a.resume and not a.output:p.error('--resume requires --output of the original run')
        result=train(manifest()['phase3']['metadata'],a.output or output('gru').relative_to(ROOT).as_posix(),variant='D',epochs=a.epochs,seed=31001,play_weight=8.,resume=a.resume,stop_after_epochs=a.stop_after_epochs)
    print(json.dumps(result,ensure_ascii=False,indent=2),flush=True)

if __name__=='__main__':raise SystemExit(main())

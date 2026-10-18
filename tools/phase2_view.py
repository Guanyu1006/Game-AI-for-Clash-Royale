"""Run the real native viewer, its read-only doctor, and independent anchors."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
from pathlib import Path
import subprocess
import sys
import threading
import time
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.viewer import (AndroidBridge, DEFAULT_EPISODE, ViewerSession, compare_anchors,
                                  digest, doctor, inspect_mp4, load_episode, make_server, reference_run, semantic_packet,
                                  validate_viewer_evidence, write_json)


def prepare_encoder():
    folder=local('.cache/phase2/viewer');folder.mkdir(parents=True,exist_ok=True)
    with urllib.request.urlopen('https://pypi.org/pypi/imageio-ffmpeg/0.6.0/json',timeout=30) as response:metadata=json.load(response)
    item=next(row for row in metadata['urls'] if row['filename'].endswith('win_amd64.whl'))
    wheel=folder/item['filename']
    if not wheel.exists():
        with urllib.request.urlopen(item['url'],timeout=60) as response,wheel.with_suffix('.partial').open('wb') as stream:
            while block:=response.read(1024*1024):stream.write(block)
        wheel.with_suffix('.partial').replace(wheel)
    actual=hashlib.sha256(wheel.read_bytes()).hexdigest()
    if actual!=item['digests']['sha256']:raise ValueError('FFmpeg wheel SHA differs from primary PyPI metadata')
    extracted=[];binary=None
    with zipfile.ZipFile(wheel) as archive:
        if archive.testzip() is not None:raise ValueError('FFmpeg wheel CRC failed')
        for name in archive.namelist():
            if name.endswith('.exe') or 'LICENSE' in name.upper() or 'COPYING' in name.upper():
                target=folder/'encoder'/Path(name).name;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name));extracted.append(str(target.relative_to(ROOT)))
                if name.endswith('.exe'):binary=target
    if binary is None:raise ValueError('Bundled Windows FFmpeg executable missing')
    run=subprocess.run([str(binary),'-version'],cwd=ROOT,capture_output=True,timeout=15)
    report={'source':'Official PyPI imageio-ffmpeg0.6.0 Windows wheel; no global installation',
            'url':item['url'],'wheel_sha256':actual,'expected_sha256':item['digests']['sha256'],'zip_crc_all_passed':True,
            'binary':str(binary.relative_to(ROOT)),'binary_sha256':hashlib.sha256(binary.read_bytes()).hexdigest(),
            'extracted':extracted,'version_exit':run.returncode,'version_output':run.stdout.decode(errors='replace'),
            'license_scope':'imageio wrapper and bundled FFmpeg have separate licenses; preserve bundled notices and exclude binary cache from source-only share'}
    write_json('outputs/phase2/viewer/encoder_provenance.json',report);return report


def api(base, path, token=None, body=None):
    data=None if body is None else json.dumps(body).encode()
    headers={} if data is None else {'Content-Type':'application/json','X-Viewer-Token':token}
    request=urllib.request.Request(base+path,data=data,headers=headers)
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(request,timeout=15) as response:
        raw=response.read()
        return json.loads(raw) if response.headers.get_content_type()=='application/json' else raw


def until(base,predicate,timeout=180):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=api(base,'/api/state')
        if state.get('error'):raise RuntimeError(state['error'])
        if predicate(state):return state
        time.sleep(0.1)
    raise TimeoutError('Viewer operation did not finish within its declared bound')


def command(base,token,name,ticks=None):
    body={'command':name}
    if ticks is not None:body['ticks']=ticks
    ticket=api(base,'/api/control',token,body)['ticket']
    state=until(base,lambda s:s['jobs'][ticket]['status'] in ('done','error'))
    result=state['jobs'][ticket]
    if result['status']!='done':raise RuntimeError(result)
    return result


def exercise(args):
    """Exercise actual HTTP controls and the complete source journal; no mock."""
    folder=local(args.output);folder.mkdir(parents=True,exist_ok=False)
    result={'schema':'phase2-native-viewer-exercise.v1','status':'fail','controls':{},'training_samples':0}
    session=None;server=None
    try:
        result['reference']=reference_run(args.episode,folder/'headless',args.port)
        session=ViewerSession(args.episode,folder/'rendered',args.port,quantum=args.quantum,speed=args.speed,record=True)
        server,token=make_server(session,0);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        base='http://127.0.0.1:'+str(server.server_address[1]);result['http_url']=base
        until(base,lambda s:s['ready'],180)
        html=api(base,'/');result['real_http_page']=b'data-command="play"' in html
        frame=api(base,'/api/frame');result['actual_png_served']=frame.startswith(b'\x89PNG\r\n\x1a\n')
        result['controls']['step']=command(base,token,'step')
        result['controls']['advance']=command(base,token,'advance',9)
        result['controls']['play']=command(base,token,'play')
        until(base,lambda s:s['tick']>=300)
        result['controls']['pause']=command(base,token,'pause')
        result['controls']['pause_check']=command(base,token,'pause_check')
        result['controls']['continue']=command(base,token,'play')
        final=until(base,lambda s:s['terminal'],900);result['final_public_state']=final
        result['controls']['shutdown']=command(base,token,'shutdown');session.closed.wait(60)
        result['anchors']=compare_anchors(folder/'headless/anchors.json',folder/'rendered/anchors.json',folder/'anchor_comparison.json')
        recording=json.loads((folder/'rendered/recording.json').read_text());result['recording']=recording
        parts=recording['parts']
        complete_video=bool(parts and all(p.get('valid_mp4') for p in parts) and parts[0]['start_tick']==90
                            and parts[-1]['end_tick']==session.episode['terminal_tick'])
        if not complete_video and args.encoder:
            encoded=folder/'rendered/native-full-match.mp4'
            code=encode(argparse.Namespace(frames=str((folder/'rendered/frames').relative_to(ROOT)),ffmpeg=args.encoder,output=str(encoded.relative_to(ROOT))))
            result['fallback_encoding']={'exit_code':code,'path':str(encoded.relative_to(ROOT)),
                                         'container':inspect_mp4(encoded.read_bytes()) if encoded.exists() else None}
            complete_video=code==0 and result['fallback_encoding']['container']['valid_mp4']
        result['complete_video_validated']=complete_video
        result['status']='pass' if (result['real_http_page'] and result['actual_png_served']
             and result['controls']['step']['after_tick']-result['controls']['step']['before_tick']==1
             and result['controls']['advance']['after_tick']-result['controls']['advance']['before_tick']==9
             and result['controls']['pause_check'].get('all_minimal_semantics_stable') is True
             and result['anchors']['status']=='pass' and final['terminal'] and complete_video
             and final['actual_native_events']==final['source_action_count']) else 'fail'
    except Exception as exc:
        import traceback
        result.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    finally:
        if session and not session.closed.is_set():
            session.submit('shutdown');session.closed.wait(60)
        if server:server.shutdown();server.server_close()
        if args.restore_headless:
            try:result['cold_restart_after_viewer']=AndroidBridge().restart_headless()
            except Exception as exc:result['cold_restart_error']=str(exc)
        write_json(folder/'report.json',result)
    print(json.dumps({'report':str((folder/'report.json').relative_to(ROOT)),'status':result['status'],
                      'error':result.get('error'),'anchor_status':result.get('anchors',{}).get('status')},indent=2),flush=True)
    return 0 if result['status']=='pass' else 1


def encode(args):
    folder=local(args.frames);rows=[json.loads(line) for line in (folder/'index.jsonl').read_text().splitlines() if line]
    if len(rows)<2:raise ValueError('A real native sequence of at least two frames is required')
    concat=folder.parent/'frames.ffconcat';lines=['ffconcat version 1.0']
    for index,row in enumerate(rows):
        path=local(row['path']);relative=path.relative_to(folder.parent).as_posix()
        if "'" in relative:raise ValueError('Unsupported quote in frame path')
        lines.append("file '"+relative+"'")
        if index+1<len(rows):
            now=datetime.fromisoformat(row['wall_time_utc']);then=datetime.fromisoformat(rows[index+1]['wall_time_utc'])
            lines.append('duration '+str(max(0.04,(then-now).total_seconds())))
    lines.append("file '"+local(rows[-1]['path']).relative_to(folder.parent).as_posix()+"'")
    concat.write_text('\n'.join(lines)+'\n',encoding='utf-8')
    executable=local(args.ffmpeg);output=local(args.output)
    command=[str(executable),'-y','-f','concat','-safe','0','-i',str(concat.relative_to(ROOT)),
             '-vsync','vfr','-c:v','libx264','-pix_fmt','yuv420p',str(output.relative_to(ROOT))]
    with (folder.parent/'encode.stdout.log').open('wb') as out,(folder.parent/'encode.stderr.log').open('wb') as err:
        run=subprocess.run(command,cwd=ROOT,stdout=out,stderr=err,timeout=600)
    write_json(folder.parent/'encoding.json',{'command':command,'exit_code':run.returncode,'real_native_frames':len(rows),
               'timing':'Measured capture wall times; native ticks retained in index','output':str(output.relative_to(ROOT))})
    return run.returncode


def self_check(output):
    """Check lossless semantics and real local codec, without opening native RPC."""
    from copy import deepcopy
    import shutil
    from datetime import timedelta
    folder=local(output);folder.mkdir(parents=True,exist_ok=False)
    episode=load_episode(DEFAULT_EPISODE)
    source=json.loads(local(DEFAULT_EPISODE).read_text())
    first=json.loads(local(source['files']['states.jsonl']['path']).read_text().splitlines()[0])['raw_capture']
    a=deepcopy(first);b=deepcopy(a);b['generation']+=50;b['next']+=100
    for event in b['plays']:event[0]+=100
    pa=semantic_packet(a,a['plays'],a['next']-1);pb=semantic_packet(b,b['plays'],b['next']-1)
    checks={'normalizes_only_generation_and_event_origin':pa==pb}
    for index,name in [(3,'entity_x'),(5,'entity_hp'),(7,'entity_target'),(8,'entity_cooldown'),(10,'entity_extra')]:
        changed=deepcopy(a);changed['entities'][0][index]+=1
        checks['retains_'+name]=digest(semantic_packet(changed,changed['plays'],a['next']-1))!=digest(pa)
    changed=deepcopy(a);changed['players'][0]['hand'][0][3]+=1
    checks['retains_packed_hand_parameter']=digest(semantic_packet(changed,changed['plays'],a['next']-1))!=digest(pa)
    checks['full_journal_source_order_preserved']=len(episode['journal'])==len(source['source_actions'])==43
    encoder=local('.cache/phase2/viewer/encoder/ffmpeg-win-x86_64-v7.1.exe')
    frames=folder/'frames';frames.mkdir();stamp=datetime.now(timezone.utc);rows=[]
    for number in (0,1):
        image=frames/f'{number:02d}.png';shutil.copyfile(local('outputs/phase2/viewer/doctor-v2/readonly_frame.png'),image)
        rows.append({'path':str(image.relative_to(ROOT)),'wall_time_utc':(stamp+timedelta(seconds=number)).isoformat(),
                     'tick':None,'source':'Same real read-only Android screenshot repeated solely to test codec; not a replay video'})
    (frames/'index.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows),encoding='utf-8')
    code=encode(argparse.Namespace(frames=str(frames.relative_to(ROOT)),ffmpeg=str(encoder.relative_to(ROOT)),output=str((folder/'codec-smoke.mp4').relative_to(ROOT))))
    mp4=inspect_mp4((folder/'codec-smoke.mp4').read_bytes())
    checks['actual_ffmpeg_encoded_valid_mp4']=code==0 and mp4['valid_mp4']
    result={'schema':'phase2-viewer-pre-runtime-self-check.v1','checks':checks,'passed':all(checks.values()),
            'rpc_used':False,'native_clock_advanced':False,'codec_smoke':mp4,
            'scope':'Actual source-state semantic retention and real local encoder only; no rendered full-episode acceptance'}
    write_json(folder/'report.json',result);return result


def main():
    configure();p=argparse.ArgumentParser(description=__doc__);sub=p.add_subparsers(dest='mode',required=True)
    d=sub.add_parser('doctor');d.add_argument('--output',default='outputs/phase2/viewer/doctor')
    plan=sub.add_parser('plan');plan.add_argument('--episode',default=DEFAULT_EPISODE)
    sub.add_parser('prepare-encoder')
    check=sub.add_parser('self-check');check.add_argument('--output',default='outputs/phase2/viewer/self-check')
    compare=sub.add_parser('compare');compare.add_argument('reference');compare.add_argument('rendered');compare.add_argument('--output',required=True)
    validate=sub.add_parser('validate');validate.add_argument('folder')
    for name in ('serve','exercise','reference'):
        parser=sub.add_parser(name);parser.add_argument('--episode',default=DEFAULT_EPISODE);parser.add_argument('--port',type=int,default=26789)
        parser.add_argument('--output',default='outputs/phase2/viewer/'+name+'-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S'))
        parser.add_argument('--exclusive-runtime',action='store_true',help='Declare that no other controller owns this native process')
        if name!='reference':
            parser.add_argument('--quantum',type=int,default=10 if name=='serve' else 20);parser.add_argument('--speed',type=float,default=1 if name=='serve' else 0)
            parser.add_argument('--restore-headless',action='store_true')
        if name=='serve':parser.add_argument('--http-port',type=int,default=8766);parser.add_argument('--record',action='store_true')
        if name=='exercise':parser.add_argument('--encoder',default='.cache/phase2/viewer/encoder/ffmpeg-win-x86_64-v7.1.exe')
    restart=sub.add_parser('restart-headless');restart.add_argument('--exclusive-runtime',action='store_true')
    encoder=sub.add_parser('encode-frames');encoder.add_argument('--frames',required=True);encoder.add_argument('--ffmpeg',required=True);encoder.add_argument('--output',required=True)
    args=p.parse_args()
    if args.mode=='doctor':print(json.dumps(doctor(args.output),indent=2));return 0
    if args.mode=='prepare-encoder':print(json.dumps(prepare_encoder(),indent=2));return 0
    if args.mode=='self-check':
        report=self_check(args.output);print(json.dumps(report,indent=2));return 0 if report['passed'] else 1
    if args.mode=='plan':
        episode=load_episode(args.episode);episode.pop('initialization');episode.pop('source_actions');episode.pop('journal')
        print(json.dumps(episode,indent=2));return 0
    if args.mode=='compare':print(json.dumps(compare_anchors(args.reference,args.rendered,args.output),indent=2));return 0
    if args.mode=='validate':
        result=validate_viewer_evidence(args.folder);print(json.dumps(result,ensure_ascii=False,indent=2));return 0
    if args.mode=='encode-frames':return encode(args)
    if not args.exclusive_runtime:raise ValueError('Native RPC is exclusive. Use --exclusive-runtime only after other controllers have released this process.')
    if args.mode=='reference':print(json.dumps(reference_run(args.episode,args.output,args.port),indent=2));return 0
    if args.mode=='restart-headless':print(json.dumps(AndroidBridge().restart_headless(),indent=2));return 0
    if args.mode=='exercise':return exercise(args)
    session=ViewerSession(args.episode,args.output,args.port,args.quantum,args.speed,args.record)
    server,_=make_server(session,args.http_port)
    print(json.dumps({'url':f'http://127.0.0.1:{server.server_address[1]}','output':args.output,
                      'warning':'This real native viewer exclusively owns the rendered process; close before batch headless jobs.'}),flush=True)
    try:server.serve_forever(poll_interval=0.2)
    except KeyboardInterrupt:pass
    finally:
        session.submit('shutdown');session.closed.wait(60);server.server_close()
        if args.restore_headless:print(json.dumps(AndroidBridge().restart_headless(),indent=2))
    return 0


if __name__=='__main__':raise SystemExit(main())

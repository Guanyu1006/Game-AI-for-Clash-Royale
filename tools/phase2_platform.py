"""Serve the project-local research console; no runtime startup is implicit here."""
import argparse
from datetime import datetime, timezone
import json
import hashlib
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure, local
configure()
from royale_phase2.platform import ArchiveSession, LiveSession, make_server, DEFAULT_ARCHIVE, DEFAULT_CHECKPOINT
from royale_phase2.human import HumanSession
from royale_phase2.viewer import DEFAULT_EPISODE, write_json

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode',choices=['archive','replay','agent','human'],default='archive')
    parser.add_argument('--output');parser.add_argument('--archive-folder',default=DEFAULT_ARCHIVE)
    parser.add_argument('--episode',default=DEFAULT_EPISODE);parser.add_argument('--checkpoint',default=DEFAULT_CHECKPOINT)
    parser.add_argument('--policy-file');parser.add_argument('--information-mode',choices=['auto','player','battlefield','oracle'],default='auto')
    parser.add_argument('--seed',type=int,default=20002);parser.add_argument('--quantum',type=int,default=20)
    parser.add_argument('--speed',type=float,default=1);parser.add_argument('--port',type=int,default=8770)
    parser.add_argument('--rpc-port',type=int,default=26789);parser.add_argument('--autoplay',action='store_true')
    parser.add_argument('--record',action='store_true',help='Save actual Android MP4 parts; PNG evidence is always kept')
    parser.add_argument('--max-tick',type=int,default=7200)
    args=parser.parse_args()
    if args.mode=='human' and args.record: parser.error('Human uses live video and saved PNG evidence; simultaneous Android MP4 recording is not supported')
    if not 0<=args.seed<=2**31-1 or not 91<=args.max_tick<=1000000: parser.error('Invalid seed or max tick')
    if args.mode=='agent' and args.information_mode in ('battlefield','oracle') and not args.policy_file:
        parser.error('Expanded observations require --policy-file; saved BC supports player only')
    # Reserve HTTP before initializing a battle: an occupied UI port must never reset native state.
    class Pending:
        def public(self): return {'ready':False}
    pending=Pending();server=make_server(pending,args.port,'royale_phase2/human_ui.html' if args.mode=='human' else 'royale_phase2/platform_ui.html')
    try:
        session_type=HumanSession if args.mode=='human' else LiveSession
        session=ArchiveSession(args.archive_folder) if args.mode=='archive' else session_type(args.mode,
            args.output or 'outputs/platform_next/sessions/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-'+args.mode,
            port=args.rpc_port,quantum=args.quantum,speed=args.speed,seed=args.seed,checkpoint=args.checkpoint,
            policy_file=args.policy_file,information_mode=args.information_mode,episode=args.episode,
            autoplay=args.autoplay,max_tick=args.max_tick,record=args.record)
        server.session = session
        if args.mode!='archive':
            sources=['royale_phase2/platform.py','royale_phase2/observability.py','royale_phase2/battlefield.py','royale_phase2/platform_ui.html',
                     'royale_phase2/backend.py','tools/phase2_platform.py','run_platform.ps1']
            if args.mode=='human': sources+=['royale_phase2/human.py','royale_phase2/platform_stream.py','royale_phase2/human_ui.html','examples/human_ai_policy.py','run_play.ps1','play.cmd']
            write_json(session.folder/'configuration.json',{'arguments':vars(args),'python':sys.executable,
                       'source_sha256':{name:hashlib.sha256(local(name).read_bytes()).hexdigest() for name in sources}})
        print(json.dumps({'url':f'http://127.0.0.1:{server.server_port}','mode':args.mode,
                          'folder':str(session.folder.relative_to(ROOT))},ensure_ascii=False),flush=True)
        import threading
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            while not session.closed.wait(.5): pass
        except KeyboardInterrupt:
            session.submit('shutdown');session.closed.wait(30)
        finally:
            server.shutdown();thread.join(5)
        if args.mode!='archive' and session.public().get('error'):
            print(json.dumps({'error':session.public()['error'],'logs':str(session.folder.relative_to(ROOT))},ensure_ascii=False),flush=True)
            return 1
        return 0
    finally: server.server_close()

if __name__=='__main__':raise SystemExit(main())

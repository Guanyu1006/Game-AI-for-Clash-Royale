"""Loopback-only standard-library local battle service."""
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
import json
import threading
from urllib.parse import urlparse,parse_qs
from .runtime import ROOT,local
from .session import Session

class Service:
    def __init__(self):self.sessions={};self.lock=threading.RLock()
    def get(self,sid):
        if sid not in self.sessions:raise ValueError('Unknown session; create a game first')
        return self.sessions[sid]
    def create(self,data):
        if len(self.sessions)>=12:raise ValueError('Close an old session before creating more')
        s=Session(seed=int(data.get('seed',0)),decks=data.get('decks'),controllers=data.get('controllers'),
                  max_ticks=int(data.get('max_ticks',3000)))
        self.sessions[s.id]=s
        return s.state()
    def request(self,path,data):
        if path=='/api/new':return self.create(data)
        s=self.get(data.get('session'))
        if path=='/api/advance':return s.advance(int(data.get('ticks',1)))
        if path=='/api/action':return s.act(int(data.get('player',0)),data['action'],data.get('command_id'))
        if path=='/api/snapshot':return s.save(data.get('label'))
        if path=='/api/restore':return s.restore(data['label'])
        if path=='/api/load':
            payload=json.loads(local(data['path']).read_text(encoding='utf-8'))
            s.backend.load_snapshot(payload.get('actions',payload))
            s.reset_policies()
            return s.state()
        if path=='/api/fork':
            if len(self.sessions)>=12:raise ValueError('Close an old session before creating more')
            snap=s.snapshots.get(data.get('label')) if data.get('label') else s.backend.snapshot()
            if snap is None:raise ValueError('Unknown snapshot')
            from .backend import NativeBackend
            backend=NativeBackend(seed=0,decks=s.backend.decks,max_ticks=s.backend.max_ticks)
            backend.restore(snap)
            branch=Session(backend=backend,controllers=s.controllers)
            self.sessions[branch.id]=branch
            return branch.state()
        if path=='/api/export':return {'path':s.export()}
        if path=='/api/close':
            del self.sessions[s.id]
            return {'closed':s.id}
        raise ValueError('Unknown API route')

def serve(port=18765):
    service=Service()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,fmt,*args):
            print(f'{self.address_string()} {fmt%args}',flush=True)
        def send(self,status,obj,content_type='application/json; charset=utf-8'):
            blob=json.dumps(obj,ensure_ascii=False).encode() if isinstance(obj,(dict,list)) else obj
            self.send_response(status);self.send_header('Content-Type',content_type)
            self.send_header('Content-Length',str(len(blob)));self.send_header('Cache-Control','no-store')
            self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(blob)
        def do_GET(self):
            parsed=urlparse(self.path)
            try:
                if parsed.path=='/':
                    self.send(200,(ROOT/'royale_lab/web/index.html').read_bytes(),'text/html; charset=utf-8');return
                if parsed.path=='/api/health':
                    self.send(200,{'status':'ok','service':'royale-lab','sessions':len(service.sessions)});return
                if parsed.path=='/api/catalog':
                    from .backend import catalog_payload
                    self.send(200,catalog_payload());return
                if parsed.path=='/api/state':
                    sid=parse_qs(parsed.query).get('session',[''])[0]
                    with service.lock:row=service.get(sid).state()
                    self.send(200,row);return
                self.send(404,{'error':'route not found'})
            except (ValueError,KeyError,RuntimeError,OSError) as e:self.send(400,{'error':str(e)})
        def do_POST(self):
            # Local UI may mutate sessions; no remote origins/cross-site requests.
            origin=self.headers.get('Origin')
            if origin and origin not in (f'http://127.0.0.1:{port}',f'http://localhost:{port}'):
                self.send(403,{'error':'foreign origin'});return
            try:length=int(self.headers.get('Content-Length','0'))
            except ValueError:self.send(400,{'error':'invalid body length'});return
            if not 0<length<=1_000_000:self.send(400,{'error':'invalid body length'});return
            try:
                data=json.loads(self.rfile.read(length))
                if not isinstance(data,dict):raise ValueError('JSON body must be an object')
                with service.lock:row=service.request(urlparse(self.path).path,data)
                self.send(200,row)
            except (ValueError,KeyError,TypeError,RuntimeError,OSError) as e:self.send(400,{'error':str(e)})
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'Royale Lab http://127.0.0.1:{port} (loopback only)',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:server.server_close()

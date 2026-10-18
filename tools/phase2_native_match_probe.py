"""Attempt the complete native match gate with raw command/response evidence.

Requires an already prepared local Android probe; no runtime is substituted.
The diagnostic controller is an explicitly oracle random fixture, not a
privacy-validated actor or a reconstructed human replay.
"""
import argparse
from dataclasses import asdict
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import random
import socket
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
EXPECTED_LIBG='110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783'
EXPECTED_MINIMAL_PROBE='d526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190'

class Wire:
    def __init__(self,host,port,timeout,log):
        if host not in ('127.0.0.1','localhost'):raise ValueError('This probe is restricted to a project-local runtime')
        self.log=log;self.counter=0
        self.socket=socket.create_connection((host,port),timeout=timeout)
        self.stream=self.socket.makefile('rb')
        self.request('session-v1')
    def request(self,command,limit=65537):
        self.counter+=1;started=time.monotonic()
        row={'request_id':self.counter,'command':command,'started_utc':datetime.now(timezone.utc).isoformat()}
        try:
            self.socket.sendall((command+'\n').encode())
            payload=self.stream.readline(-1 if limit<0 else max(limit,8_388_608))
            if not payload.endswith(b'\n'):raise ConnectionError('Missing complete response; action outcome is unknown and will not be retried')
            response=json.loads(payload);row.update(response=response,response_bytes=len(payload),response_sha256=hashlib.sha256(payload).hexdigest())
            return response
        except Exception as exc:
            row['transport_error']=f'{type(exc).__name__}: {exc}';row['outcome']='unknown';raise
        finally:
            row['elapsed_s']=time.monotonic()-started
            self.log.write(json.dumps(row,separators=(',',':'))+'\n');self.log.flush()
    def close(self):self.stream.close();self.socket.close()

def main():
    p=argparse.ArgumentParser();p.add_argument('--port',type=int,default=26789)
    p.add_argument('--timeout',type=float,default=15);p.add_argument('--content-version',default='15.535.86')
    p.add_argument('--seed',type=int,default=1);p.add_argument('--quantum',type=int,default=20)
    p.add_argument('--max-tick',type=int,default=8000);p.add_argument('--output')
    p.add_argument('--location',type=int,help='Explicit existing native location for a diagnostic variant; never changes data tables')
    p.add_argument('--identity',help='Project-local actual ADB identity receipt, only for the pinned minimal probe without attest')
    a=p.parse_args();stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    folder=ROOT/(a.output or f'outputs/phase2/native/match_probe/{stamp}')
    if not folder.resolve().is_relative_to(ROOT.resolve()):raise ValueError('Output outside project')
    folder.mkdir(parents=True,exist_ok=True)
    report={'schema':'phase2-native-match-probe.v1','source':'native standard_match fixture',
        'human_replay':False,'controller':'oracle random diagnostic, not actor/privacy acceptance',
        'runtime_succeeded':False,'terminal':False,'actions_submitted':0,'consumed_card_events':[],
        'requested_content_version':a.content_version,'port':a.port,'trace':'wire.jsonl','states':'states.jsonl'}
    wire=None;started=time.monotonic()
    try:
        sys.path.insert(0,str(ROOT/'third_party/phase2'))
        from jason.engine import Engine,Play,standard_match
        with (folder/'wire.jsonl').open('w',encoding='utf-8') as log,(folder/'states.jsonl').open('w',encoding='utf-8') as states:
            wire=Wire('127.0.0.1',a.port,a.timeout,log)
            report['status_before']=wire.request('status')
            response=wire.request('attest')
            if response.get('ok'):
                attestation=response['attestation'];report['identity_method']='native attest'
            elif a.identity and response.get('error')=='unsupported command in minimal profile':
                identity_path=(ROOT/a.identity).resolve()
                if not identity_path.is_relative_to(ROOT.resolve()):raise ValueError('Identity receipt outside project')
                identity_bytes=identity_path.read_bytes();attestation=json.loads(identity_bytes)
                if attestation.get('schema')!='phase2-native-adb-identity.v1' or not attestation.get('actual_adb_verified'):
                    raise RuntimeError('A live ADB component identity receipt is required')
                if attestation.get('probe_sha256')!=EXPECTED_MINIMAL_PROBE:
                    raise RuntimeError('Only the pinned minimal probe may use the ADB identity path')
                if not attestation.get('loaded_libg_verified') or not attestation.get('content_verified'):
                    raise RuntimeError('Installed files alone do not prove mapped library/content identity')
                report.update(identity_method='live ADB mapped-component identity; native attest unavailable',
                    identity_receipt=str(identity_path.relative_to(ROOT)),identity_receipt_sha256=hashlib.sha256(identity_bytes).hexdigest())
            else:raise RuntimeError('Native attestation unavailable; no verified minimal-profile identity supplied')
            report['attestation']=attestation
            if attestation['libg_sha256']!=EXPECTED_LIBG:raise RuntimeError('Unexpected native library hash')
            if attestation['content_version']!=a.content_version:raise RuntimeError('Content version mismatch; no silent variant fallback')
            engine=Engine(timeout=a.timeout);engine.request=wire.request
            configuration=standard_match(a.seed)
            if a.location is not None:
                configuration['battle']['location']=a.location
                report['explicit_diagnostic_location']=a.location
            (folder/'initialization.json').write_text(json.dumps(configuration,indent=2),encoding='utf-8')
            state=engine.reset(match=configuration);report['reset_generation']=state.generation
            rng=random.Random(a.seed);observed=set();stalled=0
            while True:
                states.write(json.dumps(asdict(state),separators=(',',':'))+'\n');states.flush()
                for event in state.plays:
                    key=(event.sequence,event.tick,event.owner,event.card)
                    if key not in observed:report['consumed_card_events'].append(asdict(event));observed.add(key)
                if state.finalized:break
                if state.tick>=a.max_tick:raise TimeoutError('Native match did not reach terminal within declared tick bound')
                actions=[]
                for player in state.players:
                    affordable=[h for h in player.hand if h.cost*10000<=player.elixir]
                    if not affordable:continue
                    hand=rng.choice(affordable);x=rng.choice([3000,6000,12000,15000])
                    y=(rng.choice([21000,26000]) if player.owner==0 else rng.choice([6000,11000])) if hand.card>=28000000 else (12000 if player.owner==0 else 20000)
                    actions.append(Play(player.owner,hand.slot,x,y))
                report['actions_submitted']+=len(actions);before=state.tick
                transition=engine.step(actions,min(a.quantum,a.max_tick-state.tick));state=transition.state
                stalled=stalled+1 if state.tick==before else 0
                if stalled>=4:raise RuntimeError('Native clock stalled without finalized result')
            report.update(terminal=state.finalized,terminal_state=asdict(state),
                status_after=wire.request('status'),runtime_succeeded=state.finalized and len(observed)>0 and state.tick>90)
            if not report['runtime_succeeded']:raise AssertionError('Terminal alone did not prove actual native card execution')
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}'
        (folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        if wire is not None:wire.close()
        report['elapsed_s']=time.monotonic()-started
        (folder/'report.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
        print(json.dumps({'report':str((folder/'report.json').relative_to(ROOT)),
            'runtime_succeeded':report['runtime_succeeded'],'terminal':report['terminal'],
            'consumed_cards':len(report['consumed_card_events']),'error':report.get('error')}),flush=True)
    if not report['runtime_succeeded']:raise SystemExit(1)

if __name__=='__main__':main()

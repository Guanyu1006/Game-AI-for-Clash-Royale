"""Exercise an already running loopback console, retain real HTTP evidence.

This tool does not start or reset the native engine. --run-to-end asks the
console's single worker to play its current match; --shutdown closes it.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import re
import sys
import time
import traceback
import urllib.request
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.viewer import write_json, append_json
configure()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8770);parser.add_argument('--output',required=True)
    parser.add_argument('--run-to-end',action='store_true');parser.add_argument('--shutdown',action='store_true')
    parser.add_argument('--timeout',type=int,default=600);parser.add_argument('--skip-step',action='store_true')
    parser.add_argument('--advance-ticks',type=int,default=0,help='Bounded native control check; no natural-terminal claim')
    args=parser.parse_args();folder=local(args.output);folder.mkdir(parents=True,exist_ok=False)
    base=f'http://127.0.0.1:{args.port}';start=time.monotonic();report={'schema':'phase2-platform-http-check.v1','status':'failed','checks':[]}
    def get(path): return urllib.request.urlopen(base+path,timeout=30).read()
    def state(): return json.loads(get('/api/state'))
    try:
        page=get('/').decode();(folder/'served.html').write_text(page,encoding='utf-8')
        token=json.loads(re.search(r'const token=("[^"]+")',page).group(1))
        def control(command,**data):
            req=urllib.request.Request(base+'/api/control',data=json.dumps({'command':command,**data}).encode(),
                headers={'Content-Type':'application/json','X-Platform-Token':token})
            answer=json.loads(urllib.request.urlopen(req,timeout=30).read());ticket=answer['ticket']
            if ticket=='archive-synchronous':return
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                value=state();job=value.get('jobs',{}).get(ticket,{})
                if job.get('status')=='done':return
                if job.get('status')=='error':raise RuntimeError(job)
                if value.get('error'):raise RuntimeError(value['error'])
                time.sleep(.2)
            raise TimeoutError('Control ticket did not finish: '+command)
        deadline=time.monotonic()+args.timeout
        while time.monotonic()<deadline:
            initial=state()
            if initial.get('error'):raise RuntimeError(initial['error'])
            if initial.get('ready'):break
            time.sleep(.5)
        else:raise TimeoutError('Console initialization timeout')
        write_json(folder/'initial_state.json',initial)
        report.update(mode=initial['mode'],session_folder=initial['folder'],initial_tick=initial['tick'])
        sha=initial['last_frame']['sha256'];png=get('/api/frame?v='+sha)
        assert hashlib.sha256(png).hexdigest()==sha and png.startswith(b'\x89PNG\r\n\x1a\n')
        (folder/'initial_frame.png').write_bytes(png);report['checks'].append('http_frame_sha_matches_state')
        assert initial['observation']['information_mode']=='researcher_oracle' and len(initial['observation']['players'])==2
        assert initial['observation']['raw']['tick']==initial['tick'];report['checks'].append('both_players_and_raw_state_exposed')
        time.sleep(1);paused=state();assert paused['tick']==initial['tick'];report['checks'].append('paused_tick_stable')
        if not args.skip_step:
            control('step');stepped=state();write_json(folder/'stepped_state.json',stepped)
            if initial['mode']!='archive':assert stepped['tick']==initial['tick']+1
            else:assert stepped['archive_index']==initial['archive_index']+1
            report['checks'].append('exact_native_single_tick' if initial['mode']!='archive' else 'archive_next_saved_frame')
        if args.advance_ticks:
            if initial['mode']=='archive':raise ValueError('Bounded native advance requires live mode')
            control('advance',ticks=args.advance_ticks);control('report');bounded=state()
            write_json(folder/'bounded_state.json',bounded)
            report.update(bounded_tick=bounded['tick'],truncated=bounded.get('truncated'),actual_card_events=bounded['actual_native_events'],
                          consumed_actions=sum(row.get('card_consumed') is True for row in bounded['feedback']))
            report['checks'].append('bounded_native_advance_and_feedback')
        if args.run_to_end:
            if initial['mode']=='archive':
                control('seek',index=initial['archive_count']-1)
            else:
                control('play');last=None
                while time.monotonic()<deadline:
                    value=state()
                    if value.get('error'):raise RuntimeError(value['error'])
                    if value['tick']!=last:
                        append_json(folder/'progress.jsonl',{'tick':value['tick'],'events':value['actual_native_events'],
                                    'decisions':value.get('decision_count'), 'wall_utc':datetime.now(timezone.utc).isoformat()})
                        last=value['tick']
                    if value['terminal']:break
                    if value.get('truncated'):raise RuntimeError('Watchdog truncation, not natural terminal')
                    time.sleep(1)
                else:raise TimeoutError('Natural terminal not reached')
                control('report')
            final=state();assert final['terminal'];write_json(folder/'terminal_state.json',final)
            png=get('/api/frame?v='+final['last_frame']['sha256']);assert hashlib.sha256(png).hexdigest()==final['last_frame']['sha256']
            (folder/'terminal_frame.png').write_bytes(png)
            report.update(terminal_tick=final['tick'],result_raw=final['result'],actual_card_events=final['actual_native_events'],
                          decisions=final.get('decision_count'),consumed_actions=sum(row.get('card_consumed') is True for row in final['feedback']))
            report['checks'].append('natural_terminal_and_bound_png')
        report['status']='pass'
        if args.shutdown:control('shutdown');report['checks'].append('shutdown_submitted')
    except Exception as exc:report.update(error=f'{type(exc).__name__}: {exc}',traceback=traceback.format_exc())
    finally:
        report['wall_seconds']=time.monotonic()-start;write_json(folder/'report.json',report)
    print(json.dumps(report,ensure_ascii=False,indent=2),flush=True)
    return 0 if report['status']=='pass' else 1

if __name__=='__main__':raise SystemExit(main())

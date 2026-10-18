"""Hand over the old project console, then cold-start this project's headless app."""
import argparse
import json
from pathlib import Path
import re
import sys
import time
import urllib.error
import urllib.request
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from royale_phase3.common import configure, native_lock, write, now
configure()

def prepare(output):
    from royale_phase2.viewer import AndroidBridge
    from royale_phase2.probe import ProbeClient
    report = {'started': now(), 'commands': [], 'prior_console': None, 'status': 'running'}
    write(output, report)
    with native_lock():
        try:
            with urllib.request.urlopen('http://127.0.0.1:8770/api/state', timeout=8) as r: state = json.load(r)
        except urllib.error.URLError: state = None
        if state:
            if state.get('mode') not in ('human', 'agent', 'replay'):
                raise RuntimeError('Unknown console on8770; refusing to interfere')
            report['prior_console'] = {k: state.get(k) for k in ('mode','folder','tick','playing')}
            with urllib.request.urlopen('http://127.0.0.1:8770', timeout=8) as r: page = r.read().decode('utf-8')
            token = json.loads(re.search(r'const token\s*=\s*("[^"]+")', page).group(1))
            request = urllib.request.Request('http://127.0.0.1:8770/api/control', data=b'{"command":"shutdown"}',
                headers={'Content-Type':'application/json','X-Platform-Token':token})
            # One mutation only. Never retry unknown outcomes.
            with urllib.request.urlopen(request, timeout=10) as r: report['shutdown_receipt'] = json.load(r)
            for _ in range(45):
                try:
                    with urllib.request.urlopen('http://127.0.0.1:8770/api/state',timeout=2) as r: json.load(r)
                except (urllib.error.URLError, OSError): break
                time.sleep(1)
            else: raise TimeoutError('Previous console did not release control')
        bridge = AndroidBridge()
        bridge.ssh = [s.replace('StrictHostKeyChecking=no','StrictHostKeyChecking=yes') for s in bridge.ssh]
        restart = bridge.restart_headless(); report['commands'].append(restart); write(output, report)
        deadline = time.monotonic()+150
        while time.monotonic()<deadline:
            client = ProbeClient(timeout=3)
            try:
                status = client.status()
                if status.get('contentReady') and status.get('mode')=='headless':
                    report.update(status='ready',native_status=status,finished=now()); write(output,report)
                    print(json.dumps({'status':'ready','output':output}),flush=True); return
            except (OSError,RuntimeError,ValueError) as exc: report['last_error'] = str(exc)
            finally: client.close()
            time.sleep(1)
        raise TimeoutError('Headless native content readiness timeout')

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--output',default='outputs/phase3/runtime/prepare-001.json')
    args=parser.parse_args()
    try: prepare(args.output)
    except Exception as exc:
        write(args.output+'.failure.json', {'status':'failed','error':f'{type(exc).__name__}: {exc}','time':now()});raise

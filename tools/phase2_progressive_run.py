"""Continue the already-authorized local native experiments in exclusive order.

No phase is treated as accepted merely because a process exits. Real evidence
is rechecked, and a failed step remains recorded for root integration.
"""
import argparse
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.versioning import file_hash
from royale_phase2.dataset import write_json

OLD='554282b3b00f256eff58f65c6652ef0a7cab4d8408303c0eb61a9468b28011ff'
NEW='f2ebab84e5c6d0a87c1755ae26fc27dec84c79b95f60cf3c287af30dae8a16d5'


def read(path):return json.loads(local(path).read_text(encoding='utf-8'))


def acquire_run_lock(folder):
    """Prevent two progressive controllers from sharing the native RPC port."""
    lock = folder/'active.lock'
    while True:
        try:
            handle = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(handle, json.dumps({'pid': os.getpid(), 'started_utc': datetime.now(timezone.utc).isoformat()}).encode())
            os.close(handle)
            break
        except FileExistsError:
            try:
                owner = read(lock).get('pid')
                if owner and owner != os.getpid(): os.kill(int(owner), 0)
            except ProcessLookupError:
                lock.unlink(missing_ok=True); continue
            except (OSError, ValueError, KeyError, json.JSONDecodeError):
                # A truncated lock left by a killed launcher is recoverable;
                # a live owner is never removed here.
                try: lock.unlink()
                except OSError: raise RuntimeError('Progressive native run lock is active: '+str(lock))
                continue
            raise RuntimeError('Progressive native run already active: '+str(lock))
    def release():
        try: lock.unlink(missing_ok=True)
        except OSError: pass
    # Do not register release with atexit: the Windows venv launcher can run
    # a wrapper and child process, and the wrapper's exit would unlock a live
    # native controller.  Stale PIDs are reclaimed by the next invocation.


def main():
    configure();p=argparse.ArgumentParser(description=__doc__);p.add_argument('--run',action='store_true')
    args=p.parse_args()
    if not args.run:
        print('Plan: wait formal100 -> archive/apply audited attribution patch -> actual91e replay -> learning16x3 -> formal2x3 -> whole-pipeline benchmark. No concurrent RPC.');return
    folder=local('outputs/phase2/progressive_run');folder.mkdir(parents=True,exist_ok=True);state_path=folder/'state.json'
    acquire_run_lock(folder)
    state=read(state_path) if state_path.exists() else {'schema':'royale-phase2-progressive-native-experiments.v1','commands':[],'stages':{}}
    def publish(stage):
        state.update(current_stage=stage,updated_utc=datetime.now(timezone.utc).isoformat());write_json(state_path,state)
        print(json.dumps({'stage':stage,'updated_utc':state['updated_utc']}),flush=True)
    publish('waiting_for_formal100')
    start=time.monotonic();last=0
    while True:
        queue=read('outputs/phase2/batches/formal100-v1/queue.json')
        remaining=sum(j['state']!='recorded' for j in queue['jobs'])
        if not remaining:break
        if time.monotonic()-start>10000:raise TimeoutError('Formal queue remains incomplete; inspect preserved queue')
        if time.monotonic()-last>30:
            print(json.dumps({'formal_remaining':remaining}),flush=True);last=time.monotonic()
        time.sleep(2)
    assert len(queue['jobs'])==100 and queue['identity']['backend_sha256']==OLD
    for job in queue['jobs']:
        assert file_hash(job['result']['path'])==job['result']['sha256']
    publish('audited_controller_patch')
    candidate=local('outputs/phase2/native/audits/backend.partial-draw.candidate.py')
    assert file_hash(candidate)==NEW
    backend=local('royale_phase2/backend.py');backup=folder/'backend.before-partial.py'
    if file_hash(backend)==OLD:
        if not backup.exists():shutil.copyfile(backend,backup)
        assert file_hash(backup)==OLD
        temporary=backend.with_suffix('.py.partial');shutil.copyfile(candidate,temporary);temporary.replace(backend)
    assert file_hash(backend)==NEW and file_hash(backup)==OLD
    state['stages']['audited_controller_patch']={'original_sha256':OLD,'candidate_sha256':NEW,
        'old_source_backup':backup.relative_to(ROOT).as_posix(),'native_assets_changed':False,
        'old_formal_results_rewritten':False};publish('actual_partial_consumption_validation')
    def execute(name,argv,expected_report,allow_two=False):
        previous=state['stages'].get(name)
        if previous and previous.get('returncode') in ((0,2) if allow_two else (0,)) and previous.get('result'):
            item=previous['result']
            if file_hash(item['path'])==item['sha256']:
                return read(item['path'])
            raise ValueError('Completed progressive result changed: '+name)
        log=folder/(name+'.log');command=[sys.executable,*argv];publish(name)
        started=time.monotonic()
        with log.open('w',encoding='utf-8') as stream:process=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        entry={'command':command,'returncode':process.returncode,'wall_seconds':time.monotonic()-started,
               'log':{'path':log.relative_to(ROOT).as_posix(),'sha256':file_hash(log)}}
        if local(expected_report).exists():entry['result']={'path':expected_report,'sha256':file_hash(expected_report)}
        state['commands'].append(entry);state['stages'][name]=entry;write_json(state_path,state)
        if process.returncode not in ((0,2) if allow_two else (0,)):raise RuntimeError('Native experiment failed: '+name)
        if 'result' not in entry:raise RuntimeError('Expected actual result not published: '+name)
        return read(expected_report)
    actual_path='outputs/phase2/native/backend_partial_live/episode.json'
    result=execute('actual_partial_consumption_validation',['tools/phase2_replay.py','91e21faee1ae4abbb424e759585bf2f0',
        '--output','outputs/phase2/native/backend_partial_live','--grid-ticks','5'],actual_path)
    assert result['quality']['runtime_replayable']=='pass' and result['counts']['executed']==53
    actions=read(result['files']['actions.json']['path'])['actions'];partial=actions[26]['feedback']
    assert partial['card_consumed'] and partial['own_boundary_evidence_kind']=='pending_draw_exact_hand_cycle_cost_parameter_elixir'
    assert partial['card_event'][3]==13000096
    state['actual_partial_validation']={'episode':{'path':actual_path,'sha256':file_hash(actual_path)},
        'actual_event':partial['card_event'],'source_inputs_changed':False};write_json(state_path,state)
    execute('learning16_three_resets',['tools/phase2_cohort.py','--prefix','learning16'],
            'outputs/phase2/repeatability/learning16/report.json',allow_two=True)
    execute('formal2_three_resets',['tools/phase2_cohort.py','--prefix','formal2','--cohort',
            'data/phase2/learning/formal_training_cohort_manifest.json'],
            'outputs/phase2/repeatability/formal2/report.json',allow_two=True)
    execute('whole_pipeline_benchmark',['tools/phase2_benchmark.py'],
            'outputs/phase2/performance/whole-pipeline/report.json')
    publish('native_window_released_after_recorded_experiments')


if __name__=='__main__':main()

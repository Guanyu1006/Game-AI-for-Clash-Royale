"""Execute the predeclared learning cohort with three real independent resets.

This is a single native-worker controller. A completed formal queue can be a
prerequisite, allowing a long local job to advance without concurrent reset.
The first run is the only declared learning acquisition; repeats are evidence.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.versioning import file_hash, canonical_hash
from royale_phase2.dataset import write_json
from royale_phase2.determinism import compare_runs


def acquire_cohort_lock(prefix):
    lock = local('outputs/phase2/repeatability/'+prefix+'.active.lock')
    lock.parent.mkdir(parents=True, exist_ok=True)
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({'pid': os.getpid()}).encode()); os.close(fd)
            break
        except FileExistsError:
            try:
                owner = json.loads(lock.read_text(encoding='utf-8')).get('pid')
                if owner and owner != os.getpid(): os.kill(int(owner), 0)
            except ProcessLookupError:
                lock.unlink(missing_ok=True); continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try: lock.unlink()
                except OSError: raise RuntimeError('Cohort native lock is active: '+str(lock))
                continue
            raise RuntimeError('Cohort native run already active: '+str(lock))
    # Keep the sentinel until the owner is gone.  The next invocation removes
    # a stale PID; an atexit unlink is unsafe with the Windows venv launcher,
    # which briefly has both a wrapper and an executing child process.


def main():
    configure(); parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cohort', default='data/phase2/learning/native_cohort_manifest.json')
    parser.add_argument('--after-queue', help='Wait until this actual queue has all jobs recorded')
    parser.add_argument('--wait-timeout', type=float, default=9000)
    parser.add_argument('--prefix', default='learning16')
    args = parser.parse_args()
    acquire_cohort_lock(args.prefix)
    cohort = json.loads(local(args.cohort).read_text(encoding='utf-8'))
    if cohort.get('cohort_sha256') != canonical_hash({k:v for k,v in cohort.items() if k != 'cohort_sha256'}):
        raise ValueError('Cohort changed')
    for key in ('upstream_manifest', 'combined_manifest'):
        ref = cohort[key]
        if file_hash(ref['path']) != ref['sha256']: raise ValueError('Cohort source manifest changed')
    if cohort['uses_source_final_outcome'] or cohort['uses_native_results']: raise ValueError('Cohort was not source-only predeclared')
    ids = [e['source_episode_id'] for e in cohort['entries']]
    if not ids or len(set(ids)) != len(ids): raise ValueError('Need distinct source episodes; global acceptance requires at least ten')
    if args.after_queue:
        start = time.monotonic(); last_print = 0
        while True:
            if local(args.after_queue).is_file():
                queue = json.loads(local(args.after_queue).read_text(encoding='utf-8'))
                remaining = sum(j['state'] != 'recorded' for j in queue['jobs'])
                if not remaining: break
            else: remaining = 'queue_not_created_yet'
            if time.monotonic()-start > args.wait_timeout: raise TimeoutError('Formal queue prerequisite did not complete')
            if time.monotonic()-last_print > 30:
                print(json.dumps({'stage':'waiting_for_formal_queue','remaining':remaining}), flush=True); last_print=time.monotonic()
            time.sleep(2)
    folder = local('outputs/phase2/repeatability/'+args.prefix); folder.mkdir(parents=True, exist_ok=True)
    acquired = {source_id: [] for source_id in ids}; commands = []
    for repeat in range(1, 4):
        splits = sorted({e['split'] for e in cohort['entries']})
        for split in splits:
            selected = [e['source_episode_id'] for e in cohort['entries'] if e['split'] == split]
            name = f'{args.prefix}-r{repeat}' + (f'-{split}' if len(splits)>1 else '')
            command = [sys.executable, str(ROOT/'tools/phase2_batch.py'), '--name', name,
                       '--split',split,'--grid-ticks','5','--ids', *selected]
            log = folder/f'run-{repeat}-{split}.log'
            with log.open('a', encoding='utf-8') as stream:
                process = subprocess.run(command, cwd=ROOT, stdout=stream, stderr=subprocess.STDOUT)
            commands.append({'command':command,'returncode':process.returncode,
                             'log':{'path':log.relative_to(ROOT).as_posix(),'sha256':file_hash(log)}})
            queue_path = local(f'outputs/phase2/batches/{name}/queue.json')
            queue = json.loads(queue_path.read_text(encoding='utf-8'))
            if process.returncode or any(j['state'] != 'recorded' for j in queue['jobs']):
                write_json(folder/'incomplete.json', {'commands':commands,'queue':queue_path.relative_to(ROOT).as_posix()})
                raise SystemExit(2)
            for job in queue['jobs']: acquired[job['source_episode_id']].append(job['result']['path'])
        print(json.dumps({'stage':'cohort_actual_repeat_completed','repeat':repeat,'sources':len(ids)}),flush=True)
    episodes = []
    for source_id in ids:
        try:
            episodes.append(compare_runs(acquired[source_id]))
        except (KeyError, ValueError, OSError) as error:
            episodes.append({'source_episode_id':source_id,'status':'fail','reason':str(error),
                             'sequence_hashes':[], 'runs':[{'path':p,'sha256':file_hash(p)} for p in acquired[source_id]],
                             'scope':'Uncomparable actual runs retained; no deterministic claim'})
    report = {'schema':'royale-phase2-repeatability.v1','episodes':episodes,
              'episode_denominator':len(episodes),'episodes_passed':sum(e['status']=='pass' for e in episodes),
              'actual_runs':sum(len(e['runs']) for e in episodes),'independent_official_fidelity_claim':False,
              'cohort':{'path':args.cohort,'sha256':file_hash(args.cohort)},'commands':commands,
              'learning_acquisition_rule':'Only predeclared first repeat r1; r2/r3 never duplicate learning samples',
              'learning_run_paths':[acquired[source_id][0] for source_id in ids]}
    write_json(folder/'report.json',report)
    print(json.dumps({k:v for k,v in report.items() if k not in ('episodes','commands','learning_run_paths')},indent=2))
    if any(e['status']!='pass' for e in episodes): raise SystemExit(2)


if __name__ == '__main__': main()

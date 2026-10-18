"""Whole real reconstruction timing for two advancing configurations.

Each configuration runs in a fresh Python process with reset, actor projection,
state/action/wire output and resource measurement included. One native worker.
"""
import argparse
import json
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.versioning import canonical_hash, file_hash
from royale_phase2.dataset import write_json
from royale_phase2.determinism import canonical_run
from phase2_batch import qemu_usage


def main():
    configure(); parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source-id',default='85c0a48b6a1d47cc8057710e05bf7773')
    parser.add_argument('--output',default='outputs/phase2/performance/whole-pipeline')
    parser.add_argument('--grid-ticks',type=int,default=10)
    parser.add_argument('--mode',choices=['atomic','two_rpc'],help=argparse.SUPPRESS)
    args=parser.parse_args(); folder=local(args.output); folder.mkdir(parents=True,exist_ok=True)
    if args.mode:
        from royale_phase2.backend import NativeBattleBackend
        from royale_phase2.reconstruction import reconstruct
        backend=NativeBattleBackend(advance_mode=args.mode,trace_path=folder/'wire.jsonl')
        result=reconstruct(args.source_id,folder,grid_ticks=args.grid_ticks,backend=backend)
        if result['quality']['runtime_replayable']!='pass': raise SystemExit(2)
        return
    if (folder/'report.json').exists(): raise ValueError('Choose a new benchmark output; evidence is immutable')
    results=[]; semantic_hashes=[]
    for mode in ('two_rpc','atomic'):
        command=[sys.executable,str(Path(__file__).resolve()),'--source-id',args.source_id,
                 '--output',str(folder/mode),'--grid-ticks',str(args.grid_ticks),'--mode',mode]
        before=qemu_usage(); started=time.monotonic(); log=folder/(mode+'.log')
        with log.open('w',encoding='utf-8') as stream:
            process=subprocess.run(command,cwd=ROOT,stdout=stream,stderr=subprocess.STDOUT)
        elapsed=time.monotonic()-started; after=qemu_usage()
        episode_path=folder/mode/'episode.json'; episode,packet=canonical_run(episode_path)
        semantic_hashes.append(canonical_hash(packet))
        tick=episode.get('terminal',{}).get('tick',0)
        results.append({'mode':mode,'command':command,'returncode':process.returncode,
                        'log':{'path':log.relative_to(ROOT).as_posix(),'sha256':file_hash(log)},
                        'episode':{'path':episode_path.relative_to(ROOT).as_posix(),'sha256':file_hash(episode_path)},
                        'wall_seconds_including_process_start_reset_actor_io_and_feedback':elapsed,
                        'native_simulated_seconds':tick/20,'simulated_seconds_per_wall_second':tick/20/elapsed,
                        'episodes_per_hour':3600/elapsed,'python_resources':episode.get('resources'),
                        'qemu_before':before,'qemu_after':after,
                        'quality':episode['quality'],'actual_actions':episode['counts']})
    passed=all(r['returncode']==0 and r['quality']['runtime_replayable']=='pass' for r in results) and len(set(semantic_hashes))==1
    report={'schema':'royale-phase2-whole-pipeline-performance.v1','status':'pass' if passed else 'fail',
            'actual_physical_workers':1,'configurations':results,'semantic_sequence_sha256':semantic_hashes,
            'same_native_semantics':len(set(semantic_hashes))==1,
            'atomic_speedup':results[0]['wall_seconds_including_process_start_reset_actor_io_and_feedback']/results[1]['wall_seconds_including_process_start_reset_actor_io_and_feedback'],
            'scope':'One fixed development replay, complete real pipeline; host other workloads may affect timing. No multi-instance scaling or independent game-fidelity claim.'}
    write_json(folder/'report.json',report); print(json.dumps(report,indent=2))
    if not passed:raise SystemExit(2)


if __name__=='__main__':main()

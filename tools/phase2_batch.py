"""Single native-worker queue with immutable attempts and real interruption.

Resume restarts incomplete episodes from reset. It never resends an uncertain
write into an existing battle or counts a partial directory as a result.
"""
import argparse
import ctypes
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.versioning import canonical_hash, file_hash, verify_manifest
from royale_phase2.dataset import write_json


def acquire_queue_lock(folder):
    """Ensure one native worker owns a named queue at a time."""
    lock = folder/'active.lock'
    while True:
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, json.dumps({'pid': os.getpid()}).encode()); os.close(fd); return
        except FileExistsError:
            try:
                owner = json.loads(lock.read_text(encoding='utf-8')).get('pid')
                if owner and owner != os.getpid(): os.kill(int(owner), 0)
            except ProcessLookupError:
                lock.unlink(missing_ok=True); continue
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                try: lock.unlink()
                except OSError: raise RuntimeError('Native queue lock is active: '+str(lock))
                continue
            raise RuntimeError('Native queue already active: '+str(lock))


def qemu_usage():
    try:
        process = subprocess.run(['powershell', '-NoProfile', '-Command',
            'Get-Process -Name qemu-system-x86_64 -ErrorAction SilentlyContinue | Select-Object Id,CPU,WorkingSet64,PeakWorkingSet64 | ConvertTo-Json -Compress'],
            capture_output=True, text=True, timeout=8)
        return json.loads(process.stdout) if process.stdout.strip() else None
    except Exception: return None


def memory_bytes(process):
    if sys.platform != 'win32': return None
    class Counters(ctypes.Structure):
        _fields_ = [('cb', ctypes.c_ulong), ('PageFaultCount', ctypes.c_ulong)] + [(name, ctypes.c_size_t) for name in
            ('PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage', 'QuotaPagedPoolUsage',
             'QuotaPeakNonPagedPoolUsage', 'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]
    counters = Counters(); counters.cb = ctypes.sizeof(counters)
    if ctypes.windll.psapi.GetProcessMemoryInfo(ctypes.c_void_p(int(process._handle)), ctypes.byref(counters), counters.cb):
        return counters.PeakWorkingSetSize
    return None


def valid_result(path, source_id, version):
    if not path.is_file(): return None
    report = json.loads(path.read_text(encoding='utf-8'))
    if report.get('source_episode_id') != source_id: raise ValueError('Result source identity mismatch')
    if report.get('version_fingerprint') not in (None, version): raise ValueError('Result version mismatch')
    for artifact in report.get('files', {}).values():
        if file_hash(artifact['path']) != artifact['sha256']: raise ValueError('Completed result artifact changed')
    return report


def publish_rejection(attempt_folder, source_id, version, reason):
    """Seal a bounded native failure as an explicit denominator result.

    This is deliberately different from accepting a partial replay: no action
    is marked consumed and no terminal is invented.  The partial wire/state
    files remain evidence for the rejection and are hashed into the report.
    """
    report = {'schema_version': 'royale-phase2-native-rejection.v1',
              'source_episode_id': source_id, 'version_fingerprint': version,
              'native_execution': (attempt_folder/'initialization.json').is_file(),
              'completed': False, 'strict_source_levels': 'unknown',
              'quality': {'parse_valid': 'pass', 'runtime_replayable': 'fail',
                          'deterministic': 'not_run', 'outcome_consistent': 'unknown',
                          'trajectory_validated': 'not_available'},
              'counts': {'source_actions': None, 'registered': None, 'executed': 0,
                         'rejected': 0, 'unknown': None, 'not_submitted_native_ended_early': None},
              'failures': [reason], 'rejection': {'kind': 'bounded_native_timeout',
                                                   'partial_trace_accepted': False,
                                                   'training_eligible': False},
              'outcome_compare': {'status': 'unknown', 'reason': 'No natural terminal was observed'},
              'files': {}}
    for path in sorted(attempt_folder.iterdir()):
        if path.is_file() and path.name not in ('episode.json', 'worker.log'):
            report['files'][path.name] = {'path': path.relative_to(ROOT).as_posix(),
                                          'sha256': file_hash(path)}
    write_json(attempt_folder/'episode.json', report)
    return report


def main():
    configure()
    parser = argparse.ArgumentParser(); parser.add_argument('--name', required=True)
    parser.add_argument('--split', choices=['formal', 'development', 'learning_extension'], default='formal')
    parser.add_argument('--ids', nargs='+'); parser.add_argument('--grid-ticks', type=int, default=10)
    parser.add_argument('--port', type=int, default=26789); parser.add_argument('--timeout', type=float, default=240)
    parser.add_argument('--interrupt-once-after-native-play', action='store_true')
    parser.add_argument('--stop-after', type=int)
    parser.add_argument('--record-rejections', action='store_true',
                        help='Seal an existing incomplete attempt as an explicit rejected result')
    args = parser.parse_args()
    if not re.fullmatch('[a-z0-9_-]{1,64}', args.name): raise ValueError('Use a short ASCII queue name')
    if args.grid_ticks < 1 or args.timeout <= 0: raise ValueError('Invalid bounds')
    folder = local('outputs/phase2/batches/' + args.name); folder.mkdir(parents=True, exist_ok=True)
    acquire_queue_lock(folder)
    state_path = folder / 'queue.json'
    version = verify_manifest('data/phase2/version_manifest.json')['fingerprint']
    frozen_path = 'data/phase2/learning/additional_source_manifest.json' if args.split == 'learning_extension' else 'data/phase2/frozen_manifest.json'
    frozen = json.loads(local(frozen_path).read_text(encoding='utf-8'))
    entries = [entry for entry in frozen['entries'] if entry['split'] == args.split]
    if args.ids:
        requested = set(args.ids)
        if not requested <= {e['source_episode_id'] for e in entries}: raise ValueError('ID outside selected frozen split')
        entries = [e for e in entries if e['source_episode_id'] in requested]
    identity = {'schema': 'royale-phase2-single-native-queue.v1', 'source_split': args.split,
                'source_ids': [e['source_episode_id'] for e in entries], 'version_fingerprint': version,
                'frozen_manifest': frozen_path, 'frozen_manifest_sha256': file_hash(frozen_path),
                'reconstruction_sha256': file_hash('royale_phase2/reconstruction.py'),
                'backend_sha256': file_hash('royale_phase2/backend.py'), 'grid_ticks': args.grid_ticks,
                'port': args.port, 'actual_workers': 1}
    if state_path.exists():
        queue = json.loads(state_path.read_text(encoding='utf-8'))
        if queue['identity'] != identity: raise ValueError('Queue inputs/implementation changed; create a new named queue')
    else:
        queue = {'identity': identity, 'identity_sha256': canonical_hash(identity),
                 'jobs': [{'source_episode_id': e['source_episode_id'], 'state': 'queued', 'attempts': []} for e in entries],
                 'invocations': [], 'interrupt_demonstration_done': False}
        write_json(state_path, queue)
    invocation = {'started_utc': datetime.now(timezone.utc).isoformat(), 'command': [sys.executable] + sys.argv,
                  'qemu_before': qemu_usage(), 'new_recorded': 0, 'resumed_existing': 0}
    invocation_start = time.monotonic(); queue['invocations'].append(invocation); exit_code = 0
    try:
        for job in queue['jobs']:
            if job['state'] == 'recorded':
                result_path = local(job['result']['path'])
                if file_hash(result_path) != job['result']['sha256']: raise ValueError('Recorded result changed')
                valid_result(result_path, job['source_episode_id'], version)
                invocation['resumed_existing'] += 1; continue
            if job['attempts']:
                previous = job['attempts'][-1]
                previous_path = local(previous['folder'])/'episode.json'
                if valid_result(previous_path, job['source_episode_id'], version) is not None:
                    job.update(state='recorded', result={'path': previous_path.relative_to(ROOT).as_posix(), 'sha256': file_hash(previous_path)})
                    previous['status'] = 'completed_artifact_recovered_on_resume'
                    invocation['resumed_existing'] += 1; write_json(state_path, queue); continue
                if args.record_rejections and previous['status'] in ('incomplete', 'interrupted'):
                    report = publish_rejection(local(previous['folder']), job['source_episode_id'], version,
                                               previous.get('interruption_reason', 'Native attempt ended before a complete episode was published'))
                    result_path = local(previous['folder'])/'episode.json'
                    job.update(state='recorded', result={'path': result_path.relative_to(ROOT).as_posix(),
                                                        'sha256': file_hash(result_path)})
                    previous['status'] = 'rejected_explicitly'; previous['partial_result_accepted'] = False
                    invocation['resumed_existing'] += 1; write_json(state_path, queue); continue
                if previous['status'] == 'running': previous.update(status='abandoned', abandoned_on_resume=True)
            attempt_number = len(job['attempts'])+1
            if attempt_number > 3:
                job['state'] = 'retry_limit_reached'; continue
            attempt_folder = folder / job['source_episode_id'] / f'attempt-{attempt_number:02d}'
            attempt_folder.mkdir(parents=True, exist_ok=True)
            command = [sys.executable, str(ROOT/'tools/phase2_replay.py'), job['source_episode_id'],
                       '--output', str(attempt_folder), '--port', str(args.port), '--grid-ticks', str(args.grid_ticks)]
            attempt = {'attempt': attempt_number, 'command': command, 'started_utc': datetime.now(timezone.utc).isoformat(),
                       'folder': attempt_folder.relative_to(ROOT).as_posix(), 'status': 'running'}
            job['attempts'].append(attempt); job['state'] = 'running'; write_json(state_path, queue)
            log_path = attempt_folder/'worker.log'; started = time.monotonic(); interrupted = False; peak = 0
            with log_path.open('w', encoding='utf-8') as log:
                process = subprocess.Popen(command, cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
                try:
                    while process.poll() is None:
                        peak = max(peak, memory_bytes(process) or 0)
                        wire = attempt_folder/'wire.jsonl'
                        if args.interrupt_once_after_native_play and not queue['interrupt_demonstration_done'] and wire.is_file():
                            # Observe an actual returned native consumption event
                            # before terminating the worker mid-episode.
                            with wire.open('rb') as trace:
                                trace.seek(max(0, wire.stat().st_size-131072)); tail = trace.read()
                            if b'"plays":[[' in tail:
                                queue['interrupt_demonstration_done'] = True
                                attempt['interruption_reason'] = 'Deliberately terminated worker after actual native CardPlay, before completed episode publication'
                                write_json(state_path, queue); process.terminate(); interrupted = True; break
                        if time.monotonic()-started > args.timeout:
                            attempt['interruption_reason'] = 'Bounded episode wall timeout'; process.terminate(); break
                        time.sleep(0.1)
                    process.wait(timeout=10)
                except BaseException:
                    if process.poll() is None: process.terminate(); process.wait(timeout=10)
                    raise
            attempt.update(returncode=process.returncode, wall_seconds=time.monotonic()-started,
                           launcher_peak_working_set_bytes=peak or None,
                           worker_log={'path': log_path.relative_to(ROOT).as_posix(), 'sha256': file_hash(log_path)})
            result_path = attempt_folder/'episode.json'
            report = valid_result(result_path, job['source_episode_id'], version)
            if report is not None:
                job.update(state='recorded', result={'path': result_path.relative_to(ROOT).as_posix(), 'sha256': file_hash(result_path)})
                attempt['status'] = 'recorded'; invocation['new_recorded'] += 1
                print(json.dumps({'id': job['source_episode_id'], 'quality': report['quality'], 'actions': report['counts'], 'wall_seconds': attempt['wall_seconds']}), flush=True)
            else:
                job['state'] = 'interrupted' if interrupted else 'incomplete'
                attempt['status'] = job['state']; attempt['partial_result_accepted'] = False
            write_json(state_path, queue)
            if interrupted:
                exit_code = 75; break
            if args.stop_after and invocation['new_recorded'] >= args.stop_after:
                exit_code = 75 if any(j['state'] != 'recorded' for j in queue['jobs']) else 0; break
    except BaseException:
        exit_code = 1
        raise
    finally:
        invocation.update(wall_seconds=time.monotonic()-invocation_start, qemu_after=qemu_usage(), exit_code=exit_code)
        write_json(state_path, queue)
        reports = [valid_result(local(job['result']['path']), job['source_episode_id'], version) for job in queue['jobs'] if job['state'] == 'recorded']
        dimensions = {key: dict(__import__('collections').Counter(r['quality'][key] for r in reports)) for key in
                      ('parse_valid', 'runtime_replayable', 'deterministic', 'outcome_consistent', 'trajectory_validated')}
        seconds = sum(v.get('wall_seconds', 0) for v in queue['invocations'])
        ticks = sum(r.get('terminal', {}).get('tick', 0) for r in reports)
        summary = {'schema': 'royale-phase2-native-batch-summary.v1', 'denominator': len(queue['jobs']),
                   'recorded': len(reports), 'remaining': len(queue['jobs'])-len(reports), 'quality_dimensions': dimensions,
                   'full_attempts_in_denominator': True, 'actual_workers': 1, 'grid_ticks': args.grid_ticks,
                   'wall_seconds_including_reset_io_failures': seconds, 'native_simulated_seconds': ticks/20,
                   'simulated_seconds_per_wall_second': ticks/20/seconds if seconds else None,
                   'recorded_episodes_per_hour': len(reports)*3600/seconds if seconds else None,
                   'episode_ids_counted_once': len({r['source_episode_id'] for r in reports}) == len(reports),
                   'interruption_demonstrated': queue['interrupt_demonstration_done'],
                   'queue_path': state_path.relative_to(ROOT).as_posix(), 'queue_sha256': file_hash(state_path),
                   'results': [job['result'] for job in queue['jobs'] if job['state'] == 'recorded']}
        write_json(folder/'summary.json', summary)
        print(json.dumps({k: v for k, v in summary.items() if k != 'results'}), flush=True)
    raise SystemExit(exit_code)


if __name__ == '__main__': main()

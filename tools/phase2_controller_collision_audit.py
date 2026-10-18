"""Record the deliberately preserved learning-controller collision evidence."""
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.versioning import file_hash
from royale_phase2.dataset import write_json


def main():
    configure()
    root = local('outputs/phase2/batches/collision-learning16-r1')
    queue = json.loads((root/'queue.json').read_text(encoding='utf-8'))
    attempts = [attempt for job in queue['jobs'] for attempt in job.get('attempts', [])]
    states = {}
    for job in queue['jobs']: states[job['state']] = states.get(job['state'], 0)+1
    report = {'schema': 'royale-phase2-controller-collision-audit.v1',
              'status': 'preserved_failure', 'reason': 'Two launchers targeted one native RPC queue before the exclusive lock was installed',
              'queue': {'path': root.joinpath('queue.json').relative_to(ROOT).as_posix(), 'sha256': file_hash(root/'queue.json')},
              'job_states': states, 'attempt_count': len(attempts),
              'partial_result_accepted': sum(bool(a.get('partial_result_accepted')) for a in attempts),
              'training_eligible': False, 'used_by_learning_pipeline': False,
              'repair': 'Archived queue excluded; fresh learning16-r1 starts only after the PID sentinel check',
              'evidence_scope': 'Failure/recovery audit only; no native state is promoted to repeatability'}
    write_json('outputs/phase2/repeatability/controller_collision_audit.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__': main()

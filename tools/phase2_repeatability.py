"""Validate actual independently reset run artifacts, grouped by source episode."""
from collections import defaultdict
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from royale_phase2.determinism import compare_runs
from royale_phase2.dataset import write_json
from royale_phase2.runtime import local


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('--run', action='append', required=True)
    parser.add_argument('--output', default='outputs/phase2/repeatability/report.json'); args = parser.parse_args()
    groups = defaultdict(list)
    for path in args.run:
        episode = json.loads(local(path).read_text(encoding='utf-8'))
        groups[episode['source_episode_id']].append(path)
    episodes = []
    from royale_phase2.versioning import file_hash
    for source_id, paths in sorted(groups.items()):
        try:
            episodes.append(compare_runs(paths))
        except (KeyError, ValueError, OSError) as error:
            episodes.append({'source_episode_id':source_id,'status':'fail','reason':str(error),
                             'sequence_hashes':[], 'runs':[{'path':path,'sha256':file_hash(path)} for path in paths],
                             'scope':'Uncomparable actual source retained; no deterministic claim'})
    report = {'schema': 'royale-phase2-repeatability.v1', 'episodes': episodes,
              'episode_denominator': len(episodes), 'episodes_passed': sum(e['status'] == 'pass' for e in episodes),
              'actual_runs': sum(len(e['runs']) for e in episodes), 'independent_official_fidelity_claim': False}
    write_json(local(args.output), report)
    print(json.dumps({k: v for k, v in report.items() if k != 'episodes'}, indent=2))
    if any(e['status'] != 'pass' for e in episodes): raise SystemExit(2)


if __name__ == '__main__': main()

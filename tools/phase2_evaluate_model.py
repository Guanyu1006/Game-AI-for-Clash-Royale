"""New-process checkpoint reload and held-out decision evaluation, not native play."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.learning import evaluate_checkpoint
from royale_phase2.trajectories import write_json
from royale_phase2.versioning import verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--checkpoint', required=True)
    parser.add_argument('--export', default='outputs/phase2/trajectories')
    parser.add_argument('--version-manifest', required=True)
    parser.add_argument('--split', choices=['train', 'validation', 'test'], default='test')
    parser.add_argument('--allow-diagnostic', action='store_true')
    parser.add_argument('--output', default='outputs/phase2/models/bc_evaluation.json')
    args = parser.parse_args()
    try:
        version = verify_manifest(args.version_manifest)['fingerprint']
        result = evaluate_checkpoint(args.checkpoint, args.export, version_fingerprint=version,
                                     split=args.split, allow_diagnostic=args.allow_diagnostic)
        write_json(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, TypeError) as error:
        write_json(args.output, {'status': 'refused', 'reason': str(error), 'native_matches_run': 0})
        print(json.dumps({'status': 'refused', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

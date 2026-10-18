"""Export evidence-gated native trajectories; an empty export is not training."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.trajectories import (TrajectoryError, export_episodes, freeze_learning_split,
                                       project_path, write_json)
from royale_phase2.versioning import verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', action='append', default=[], help='Native episode JSON inside this project; repeatable')
    parser.add_argument('--input-dir', help='Directory recursively containing episode.json records')
    parser.add_argument('--frozen-manifest', default='data/phase2/frozen_manifest.json')
    parser.add_argument('--split-manifest', default='data/phase2/learning/split_manifest.json')
    parser.add_argument('--output', default='outputs/phase2/trajectories')
    parser.add_argument('--subspace-proof')
    parser.add_argument('--quality-supplement', action='append', default=[], help='Repeatability manifest; repeatable')
    parser.add_argument('--version-manifest', help='Actual asset-verified native version manifest, required for nonempty export')
    parser.add_argument('--freeze-split-only', action='store_true')
    args = parser.parse_args()
    try:
        split = freeze_learning_split(args.frozen_manifest, args.split_manifest)
        if args.freeze_split_only:
            print(json.dumps({'status': 'learning_groups_frozen', 'counts': split['counts'],
                              'split_sha256': split['split_sha256'], 'uses_native_results': False}, ensure_ascii=False, indent=2))
            return 0
        paths = [project_path(path) for path in args.input]
        if args.input_dir:
            paths.extend(sorted(project_path(args.input_dir).rglob('episode.json')))
        if len(paths) != len(set(paths)):
            raise TrajectoryError('Duplicate input episode paths')
        version = None
        if paths:
            if not args.version_manifest:
                raise TrajectoryError('A nonempty native trajectory export requires --version-manifest')
            version = verify_manifest(args.version_manifest)['fingerprint']
        report = export_episodes(paths, args.split_manifest, args.output, proof_path=args.subspace_proof,
                                 expected_version=version, quality_supplements=args.quality_supplement)
        print(json.dumps({key: report[key] for key in ['status', 'attempted_episode_files', 'converted_episodes', 'rows',
                                                      'eligible_hard_e', 'eligible_native_label_bc', 'frozen_episode_denominator', 'parquet']}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, TypeError) as error:
        write_json(Path(args.output)/'export_failure.json', {'status': 'refused', 'reason': str(error), 'training_samples_created': 0})
        print(json.dumps({'status': 'refused', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

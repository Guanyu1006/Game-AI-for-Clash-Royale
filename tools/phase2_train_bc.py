"""Train minimal native BC, refusing zero or unqualified samples by default."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.learning import train_bc
from royale_phase2.trajectories import write_json
from royale_phase2.versioning import verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--export', default='outputs/phase2/trajectories')
    parser.add_argument('--output', default='outputs/phase2/models/bc_hard_e')
    parser.add_argument('--version-manifest', required=True)
    parser.add_argument('--profile', choices=['hard_e', 'native_label_bc'], default='hard_e')
    parser.add_argument('--minimum-samples', type=int, default=10000)
    parser.add_argument('--epochs', type=int, default=20)
    parser.add_argument('--hidden', type=int, default=64)
    parser.add_argument('--batch-size', type=int, default=128)
    parser.add_argument('--learning-rate', type=float, default=0.001)
    parser.add_argument('--play-class-weight-cap', type=float, default=20.0)
    parser.add_argument('--seed', type=int, default=19)
    args = parser.parse_args()
    try:
        version = verify_manifest(args.version_manifest)['fingerprint']
        result = train_bc(args.export, args.output, version_fingerprint=version, profile=args.profile,
                          minimum_samples=args.minimum_samples, epochs=args.epochs, hidden=args.hidden,
                          batch_size=args.batch_size, learning_rate=args.learning_rate, seed=args.seed,
                          play_class_weight_cap=args.play_class_weight_cap)
        print(json.dumps({'status': 'trained_and_reloaded', 'profile': result['profile'],
                          'eligible_decisions': result['training_data']['eligible_decisions'],
                          'checkpoint_sha256': result['checkpoint_sha256'], 'metrics': result['metrics'],
                          'native_agent_evaluation_completed': False}, ensure_ascii=False, indent=2))
        return 0
    except (OSError, KeyError, ValueError, TypeError) as error:
        write_json(Path(args.output)/'training_refused.json', {'status': 'refused', 'reason': str(error),
                                                             'profile': args.profile, 'model_trained': False})
        print(json.dumps({'status': 'refused', 'reason': str(error)}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

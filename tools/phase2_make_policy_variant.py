"""Create an auditable inference-prior variant without changing tensors."""
import argparse
import json
from pathlib import Path
import shutil
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.learning import MODEL_SCHEMA, NumpyBCPolicy
from royale_phase2.trajectories import digest, file_sha, project_path, read_json, write_json
from royale_phase2.versioning import verify_manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--bias', type=float, default=4.0)
    parser.add_argument('--version-manifest', default='data/phase2/version_manifest.json')
    args = parser.parse_args()
    if args.bias <= 0 or args.bias > 20:
        raise ValueError('bias must be in (0,20]')
    version = verify_manifest(args.version_manifest)
    source = project_path(args.source)
    metadata = read_json(source)
    policy = NumpyBCPolicy.load(source, version_fingerprint=version['fingerprint'])
    output = project_path(args.output); output.mkdir(parents=True, exist_ok=True)
    weights_source = project_path(metadata['weights']['path'])
    weights_target = output / 'weights.npz'
    shutil.copyfile(weights_source, weights_target)
    variant = dict(metadata)
    variant['weights'] = {'path': weights_target.relative_to(ROOT).as_posix(), 'sha256': file_sha(weights_target)}
    variant['inference_play_bias'] = float(args.bias)
    variant['variant_of'] = {'checkpoint_path': source.relative_to(ROOT).as_posix(),
                             'checkpoint_sha256': file_sha(source),
                             'scope': 'Inference ranking prior only; tensors and frozen export unchanged'}
    variant['checkpoint_sha256'] = digest({key: value for key, value in variant.items() if key != 'checkpoint_sha256'})
    write_json(output / 'checkpoint.json', variant)
    # Load the exact saved variant in a fresh object before publishing its report.
    NumpyBCPolicy.load(output / 'checkpoint.json', version_fingerprint=version['fingerprint'])
    report = {'schema': 'royale-phase2-policy-variant.v1', 'status': 'pass',
              'source': {'path': source.relative_to(ROOT).as_posix(), 'sha256': file_sha(source)},
              'variant': {'path': (output / 'checkpoint.json').relative_to(ROOT).as_posix(),
                          'sha256': file_sha(output / 'checkpoint.json'),
                          'weights': variant['weights'], 'inference_play_bias': args.bias},
              'scope': 'Same hard-E native-reconstructed export and tensors; legal-mask-constrained inference prior'}
    write_json(output / 'variant_report.json', report)
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

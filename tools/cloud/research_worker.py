"""Small independent-process validation of the exact saved GRU C checkpoint."""
import argparse
import os
from pathlib import Path
import sys

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def validate(metadata, checkpoint, output):
    from royale_phase3.common import configure, local, now, reference, write
    from royale_phase3.native_loop import train_validation_view
    from royale_phase3.research import _checkpoint
    configure()
    folder = local(output)
    if folder.exists():
        raise FileExistsError('Use a new validation output; prior evidence is immutable')
    # A separate process and physical row file keep held-out test out of policy
    # loading, model selection and validation. This uses the deployment decoder.
    training_metadata = train_validation_view(metadata, folder.with_suffix('').with_name(folder.stem + '-input'))
    from royale_phase3.data_contract import load_dataset
    data, meta = load_dataset(training_metadata)
    from royale_phase3.learning import StatefulPolicy, evaluate
    import torch
    torch.set_num_threads(2)
    identity = _checkpoint(checkpoint, meta['version_fingerprint_sha256'])
    policy = StatefulPolicy.load(checkpoint, meta['version_fingerprint_sha256'], 'fair')
    metrics = evaluate(policy.model, data, 'validation')
    if metrics.get('status') == 'no_data' or metrics.get('online_offline_exact_match') is not True:
        raise ValueError('Independent validation requires deployment-decoder agreement and eligible validation data')
    report = {'schema': 'phase3-research-offline-validation.v1', 'status': 'completed',
              'created_at': now(), 'pid': os.getpid(), 'checkpoint': identity,
              'origin_metadata': reference(metadata), 'metadata': reference(training_metadata),
              'metrics': metrics, 'test_evaluated': False, 'test_rows_supplied_to_policy': 0,
              'native_rpc_calls': 0, 'native_execution_verified': False,
              'scope': 'Fresh process checkpoint load and saved validation observations, not native rollout'}
    write(output, report)
    return report


def freeze_policy(metadata, checkpoint, output, reference_path=None):
    """Record deployment inputs/actions/hidden states before and after a refactor."""
    from royale_phase3.common import configure, digest, local, now, read, reference, write
    from royale_phase3.data_contract import load_dataset
    from royale_phase3.learning import StatefulPolicy, actor_vector
    import torch
    configure()
    torch.set_num_threads(2)
    if local(output).exists():
        raise FileExistsError('Use a new frozen prediction output')
    data, meta = load_dataset(metadata)
    policy = StatefulPolicy.load(checkpoint, meta['version_fingerprint_sha256'], 'fair')
    groups = {}
    for row in sorted((r for r in data if r['split'] == 'validation'),
                      key=lambda r: (r['episode_id'], r['native_owner'], r['tick'])):
        values = groups.setdefault((row['episode_id'], row['native_owner']), [])
        if len(values) < 32:
            values.append(row)
    if not groups:
        raise ValueError('No validation observations to freeze')
    selected = [row for group in groups.values() for row in group]

    def traverse():
        predictions = []
        for row in selected:
            action = policy.predict(row)
            hidden = policy.hidden.tolist() if policy.hidden is not None else None
            # Re-reading the same tick must not advance the recurrent state.
            if policy.predict(row) != action or (policy.hidden.tolist() if policy.hidden is not None else None) != hidden:
                raise AssertionError('Repeated tick changed prediction/hidden state')
            predictions.append({'episode_id': row['episode_id'], 'owner': row['native_owner'],
                'tick': row['tick'], 'actor_input_sha256': digest({'vector': actor_vector(row, 'C'), 'mask': row['mask']}),
                'action': action, 'hidden_sha256': digest(hidden)})
        return predictions

    predictions = traverse()
    policy.reset()
    if traverse() != predictions:
        raise AssertionError('Explicit reset failed to reproduce the deployment traversal')
    report = {'schema': 'phase3-research-frozen-policy.v1', 'created_at': now(),
              'checkpoint': reference(checkpoint), 'metadata': reference(metadata),
              'predictions': predictions, 'predictions_sha256': digest(predictions),
              'same_tick_cached': True, 'explicit_reset_reproduced': True,
              'native_rpc_calls': 0, 'test_rows_used': 0, 'status': 'passed'}
    if reference_path:
        before = read(reference_path)
        if before['checkpoint'] != report['checkpoint'] or before['metadata'] != report['metadata'] or before['predictions_sha256'] != report['predictions_sha256']:
            raise AssertionError('Frozen deployment behavior changed')
        report['reference'] = reference(reference_path)
        report['reference_exact_match'] = True
    write(output, report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    command = sub.add_parser('validate')
    command.add_argument('--metadata', required=True)
    command.add_argument('--checkpoint', required=True)
    command.add_argument('--output', required=True)
    command = sub.add_parser('freeze-policy')
    command.add_argument('--metadata', required=True)
    command.add_argument('--checkpoint', required=True)
    command.add_argument('--output', required=True)
    command.add_argument('--reference', dest='reference_path')
    args = vars(parser.parse_args(argv)); name = args.pop('command')
    (validate if name == 'validate' else freeze_policy)(**args)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""Immutable phase3 baseline audit; entirely offline, never connects to native RPC."""
from collections import Counter, defaultdict
from copy import deepcopy
import argparse
import json
import time

from .common import configure, digest, local, now, read, reference, write

CHECKPOINT = 'outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json'
EXPORT = 'outputs/phase2/learning_pipeline/export/export_manifest.json'


def freeze_configuration(output):
    """Freeze before loading predictions; inherited bias is not retuned here."""
    from royale_phase2.learning import inference_configuration
    config = {'schema': 'phase3-baseline-evaluation-config.v1', 'created_utc': now(),
        'checkpoint': reference(CHECKPOINT), 'export': reference(EXPORT),
        'inference': inference_configuration(read(CHECKPOINT)),
        'historical_bias_provenance': 'Inherited 4.0 from phase2 model_g_variant; no trustworthy train/validation selection record found; not tuned by this phase3 audit',
        'historical_control_ticks': 20, 'historical_export_wait_grid_ticks': 10,
        'historical_export_extra_rows': 'Action-pre-submit boundaries may be off-grid',
        'phase3_main_control_ticks': 10, 'ticks_per_second': 20,
        'event_matching': {'tolerance_ticks': 10, 'tolerance_seconds': 0.5,
            'prediction_time': 'observation tick+1 scheduled request; not confirmed execution',
            'label_time': 'actual_execution_tick', 'group': ['source_episode_id', 'native_owner'],
            'algorithm': 'one-to-one maximum cardinality matching within absolute tolerance; no card requirement for event PR'},
        'random_baseline': {'seed': 31006, 'selection': 'uniform over legal indices including WAIT; uniform integer arena coordinates'},
        'scope': 'Historical input-distribution correction only; no comparison attribution to new 10-tick A-E cohort',
        'selection': 'No thresholds, weights, bias, temperature or tolerance optimized on test; test reported once after this freeze'}
    config['config_sha256'] = digest(config)
    path = local(output)/'configuration.json'
    if path.exists(): raise FileExistsError('Refusing to replace an evaluation configuration')
    write(path, config)
    return config


def event_metrics(rows, actions, tolerance_ticks):
    """Maximum-cardinality time pairing, with each prediction/label used once."""
    if type(tolerance_ticks) is not int or tolerance_ticks < 0: raise ValueError('Invalid event tolerance')
    if len(rows) != len(actions): raise ValueError('Prediction and label count mismatch')
    grouped = defaultdict(lambda: {'predicted': [], 'actual': []})
    for row, action in zip(rows, actions):
        group = grouped[(row['source_episode_id'], row['native_owner'])]
        if action['kind'] == 'play_card': group['predicted'].append((row['engine_tick']+1, action['card']))
        if row['action']['kind'] == 'play_card': group['actual'].append((row['actual_execution_tick'], row['action']['card']))
    total_pred = total_actual = matched = correct_card = 0; timing = []
    for group in grouped.values():
        predicted = sorted(group['predicted']); actual = sorted(group['actual'])
        total_pred += len(predicted); total_actual += len(actual)
        # Sorted earliest feasible pairing maximizes event cardinality in a
        # symmetric interval graph. It does not pick pairings by test card ID.
        p = a = 0
        while p < len(predicted) and a < len(actual):
            dt = predicted[p][0]-actual[a][0]
            if dt < -tolerance_ticks: p += 1
            elif dt > tolerance_ticks: a += 1
            else:
                matched += 1; correct_card += int(predicted[p][1] == actual[a][1]); timing.append(abs(dt)); p += 1; a += 1
    return {'predicted_events': total_pred, 'actual_events': total_actual, 'matched_events': matched,
        'precision': matched/total_pred if total_pred else None, 'recall': matched/total_actual if total_actual else None,
        'card_accuracy_on_time_matched': correct_card/matched if matched else None,
        'mean_absolute_timing_error_ticks': sum(timing)/len(timing) if timing else None,
        'tolerance_ticks': tolerance_ticks, 'native_consumption_status': 'not_run_offline',
        'scope': 'Teacher-forced historical rows, not counterfactual closed-loop actions'}


def random_actions(rows, seed):
    import numpy as np
    from royale_phase2.learning import action_from_index
    rng = np.random.default_rng(seed); before = deepcopy(rng.bit_generator.state); actions = []
    for row in rows:
        choices = np.flatnonzero(row['legal_action_mask']['values'])
        index = int(rng.choice(choices)); point = [int(rng.integers(18000)), int(rng.integers(32000))]
        actions.append(action_from_index(row['actor'], index, point))
    return actions, {'before': before, 'after': deepcopy(rng.bit_generator.state), 'actions_sha256': digest(actions)}


def _legacy_predict(model, actor, mask):
    """Frozen reproduction of pre-fix predict decoder, for regression evidence."""
    import numpy as np
    from royale_phase2.learning import actor_features, forward
    x = actor_features(actor, model.metadata['feature_configuration'])[None, :]
    _, p, xy = forward(model.parameters, x, np.asarray([mask['values']], dtype=bool))
    scores = np.log(np.clip(p[0], 1e-30, 1.)); bias = float(model.metadata.get('inference_play_bias', 0.))
    for i in range(1, 5):
        if mask['values'][i]: scores[i] += bias
    index = int(scores.argmax())
    if index == 0: return {'kind': 'wait'}
    card = next(c for c in actor['hand'] if c['slot'] == index-1)
    return {'kind': 'play_card', 'slot': card['slot'], 'card': card['card'],
        'x': int(np.clip(np.rint(xy[0, 0]*17999), 0, 17999)),
        'y': int(np.clip(np.rint(xy[0, 1]*31999), 0, 31999))}


def baseline_audit(output='outputs/phase3/baseline/aligned-v1'):
    configure(); started = time.monotonic(); folder = local(output)
    config = freeze_configuration(output)
    from royale_phase2.learning import (NumpyBCPolicy, _group_guard, dataset_arrays,
        deployment_metrics, metrics)
    from royale_phase2.trajectories import load_export
    from royale_phase2.agent_env import public_heuristic
    version = read(CHECKPOINT)['version_fingerprint_sha256']
    model = NumpyBCPolicy.load(CHECKPOINT, version_fingerprint=version)
    print('Loading and verifying native-reconstruction rows and evidence hashes...', flush=True)
    rows, manifest = load_export(EXPORT, profile='hard_e')
    assert _group_guard(rows) == version
    assert manifest['split_sha256'] == model.metadata['training_data']['split_sha256']
    print(f'Verified {len(rows)} hard-E rows; predicting with deployed path...', flush=True)
    t = time.perf_counter(); actions = model.predict_many([r['actor'] for r in rows], legal_masks=[r['legal_action_mask'] for r in rows])
    batched_seconds = time.perf_counter()-t
    mismatches = []; legacy_mismatches = []; per_row_times = []
    for i, (row, action) in enumerate(zip(rows, actions)):
        t = time.perf_counter(); online = model.predict(row['actor'], legal_mask=row['legal_action_mask'])
        per_row_times.append(time.perf_counter()-t)
        if online != action: mismatches.append({'decision_id': row['decision_id'], 'batch': action, 'single': online})
        old = _legacy_predict(model, row['actor'], row['legal_action_mask'])
        if old != action: legacy_mismatches.append({'decision_id': row['decision_id'], 'old': old, 'new': action})
        if i and i % 2000 == 0: print(f'Compared {i}/{len(rows)} rows', flush=True)
    write(folder/'prediction_agreement.json', {'schema': 'phase3-bc-path-agreement.v1',
        'rows': len(rows), 'action_digest': digest(actions), 'single_batch_mismatches': mismatches,
        'historical_decoder_mismatches': legacy_mismatches, 'passed': not mismatches and not legacy_mismatches})
    waits = [{'kind': 'wait'} for _ in rows]
    random, rng = random_actions(rows, config['random_baseline']['seed'])
    repeated, repeated_rng = random_actions(rows, config['random_baseline']['seed'])
    assert random == repeated and rng == repeated_rng
    write(folder/'random_rng.json', rng)
    rule = [public_heuristic(row['actor'], i) for i, row in enumerate(rows)]
    rule_unsupported = sum(a['kind'] == 'play_card' and not row['legal_action_mask']['values'][a['slot']+1] for row, a in zip(rows, rule))
    projected_rule = [a if a['kind'] == 'wait' or r['legal_action_mask']['values'][a['slot']+1] else {'kind': 'wait'} for r, a in zip(rows, rule)]
    policies = {'all_wait': waits, 'random_legal': random, 'existing_rule_projected_to_strict_subspace': projected_rule,
                'current_bc_aligned': actions}
    report = {'schema': 'phase3-baseline-audit.v1', 'created_utc': now(), 'config': reference(folder/'configuration.json'),
        'version_fingerprint_sha256': version, 'source': {name: reference(path) for name, path in {
            'learning': 'royale_phase2/learning.py', 'agent_env': 'royale_phase2/agent_env.py',
            'evaluation': 'royale_phase3/evaluation.py', 'legacy_variant': 'outputs/phase2/learning_pipeline/model_g_variant/variant_report.json',
            'old_heldout_metrics_untouched': 'outputs/phase2/learning_pipeline/heldout_evaluation.json'}.items()},
        'data': {'raw_export_rows': manifest['rows'], 'eligible_rows': len(rows), 'whole_games': len({r['source_episode_id'] for r in rows}),
            'action_counts': dict(Counter(r['action']['kind'] for r in rows)),
            'card_counts': dict(Counter(str(r['action'].get('card')) for r in rows if r['action']['kind'] == 'play_card'))},
        'agreement': reference(folder/'prediction_agreement.json'), 'random_reproducible': True,
        'rule_scope': {'existing_unmodified_rule_out_of_strict_subspace_requests': rule_unsupported,
            'projection': 'Replace unsupported requests by WAIT; projected-rule baseline is explicitly different from unmodified full-card rule',
            'unmodified_rule_native_legality': 'not_verified'},
        'split_results': {}, 'native_matches_run': 0,
        'unknown_metrics': ['native actual consumed/rejected/unknown rates', 'whole-game victory', 'cycle lock', 'key missed defense', 'end-to-end latency'],
        'limitations': ['Only 12 reconstructed hard-E games; not independently measured official trajectory truth',
            'Historical prior selection provenance unknown; retained, not selected anew',
            'Historical teacher-forced rows are 0.5-second grid plus event boundaries; deployed old controller uses one second',
            'Prediction differences do not establish strategy improvement; new A-E 10-tick cohort is separate'],
        'inference_timing': {'rows': len(rows), 'batch_seconds_including_mask_verification': batched_seconds,
            'mean_single_seconds_including_proof_verification': sum(per_row_times)/len(per_row_times)},
        'passed_path_alignment': not mismatches and not legacy_mismatches}
    for split in ('train', 'validation', 'test'):
        indices = [i for i, r in enumerate(rows) if r['learning_split'] == split]
        subset = [rows[i] for i in indices]; arrays = dataset_arrays(subset, model.metadata['feature_configuration'], 'hard_e')
        result = {'rows': len(subset), 'whole_games': len({r['source_episode_id'] for r in subset}),
            'unbiased_training_reference': metrics(model.parameters, arrays, subset), 'policies': {}}
        for name, predictions in policies.items():
            chosen = [predictions[i] for i in indices]
            result['policies'][name] = {'metrics': deployment_metrics(model, arrays, subset, actions=chosen),
                'event_precision_recall': event_metrics(subset, chosen, config['event_matching']['tolerance_ticks']),
                'forced_wait_only_rows': sum(sum(r['legal_action_mask']['values']) == 1 for r in subset),
                'native_consumption': 'not_run'}
        report['split_results'][split] = result
    report['elapsed_seconds'] = time.monotonic()-started
    write(folder/'report.json', report)
    print(json.dumps({'report': str(folder/'report.json'), 'aligned': report['passed_path_alignment'],
        'rows': len(rows), 'elapsed_seconds': report['elapsed_seconds']}, ensure_ascii=False), flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default='outputs/phase3/baseline/aligned-v1')
    args = parser.parse_args(); baseline_audit(args.output)


if __name__ == '__main__': main()

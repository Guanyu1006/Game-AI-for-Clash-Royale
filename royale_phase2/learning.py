"""Small NumPy behavior cloning on versioned native trajectories only.

No game backend is implemented here. Empty/unqualified inputs are errors, and
diagnostic native-label training is kept separate from hard-E training.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import sys

from .trajectories import (TrajectoryError, audit_subspace_proof, digest, file_sha, load_export, project_path,
                           read_json, strict_int, subspace_mask, validate_actor, verify_reference, write_json)
from .visibility import affordability_mask

MODEL_SCHEMA = 'royale-phase2-numpy-bc.v1'
ROOT = Path(__file__).resolve().parents[1]


def numpy():
    import numpy as np
    return np


def feature_configuration(training_rows):
    if not training_rows:
        raise TrajectoryError('No training rows: vocabulary cannot be inferred')
    cards = sorted({card['card'] for row in training_rows for card in row['actor']['hand']})
    return {'schema': 'royale-phase2-public-actor-features.v1', 'card_vocabulary': cards,
            'vocabulary_source': 'training whole-source groups only; unseen validation/test cards use UNK',
            'time_scale_ticks': 12000, 'hp_scale': 100000,
            'tower_order': [[side, role] for side in ('own', 'opponent') for role in ('king', 'left', 'right')],
            'excludes': ['opponent_hand', 'opponent_exact_elixir', 'full_cycle', 'seed', 'future_events',
                         'packed_parameters', 'effective_form_ids', 'non_tower_entities', 'source_results', 'quality_metadata']}


def actor_features(actor, config):
    validate_actor(actor)
    np = numpy()
    if config.get('schema') != 'royale-phase2-public-actor-features.v1':
        raise TrajectoryError('Unsupported feature schema')
    vocabulary = {card: index+1 for index, card in enumerate(config['card_vocabulary'])}
    width = len(vocabulary)+1
    values = [actor['tick']/config['time_scale_ticks'], actor['native_owner'],
              actor['own_elixir_raw']/100000, actor['crowns']['own']/3,
              actor['crowns']['opponent']/3, float(actor['hand_complete'])]
    by_slot = {card['slot']: card for card in actor['hand']}
    for slot in range(4):
        card = by_slot.get(slot); vector = [0.0]*width
        if card is not None: vector[vocabulary.get(card['card'], 0)] = 1.0
        values.extend(vector)
        values.extend([float(card is not None), card['cost']/10 if card is not None else 0.0])
    towers = {(tower['side'], tower['role']): tower for tower in actor['towers']}
    for side, role in config['tower_order']:
        tower = towers.get((side, role))
        known = bool(tower and tower['present'])
        values.extend([float(known), tower['hp']/config['hp_scale'] if known else 0.0,
                       tower['max_hp']/config['hp_scale'] if known else 0.0])
    result = np.asarray(values, dtype=np.float32)
    if not np.isfinite(result).all():
        raise TrajectoryError('Non-finite actor features')
    return result


def dataset_arrays(rows, config, profile):
    np = numpy()
    if not rows:
        raise TrajectoryError('Zero eligible decisions; refusing to create training arrays')
    x = np.stack([actor_features(row['actor'], config) for row in rows])
    target = np.asarray([0 if row['action']['kind'] == 'wait' else row['action']['slot']+1 for row in rows], dtype=np.int64)
    xy = np.asarray([[0.0, 0.0] if row['action']['kind'] == 'wait' else
                     [row['action']['x']/17999, row['action']['y']/31999] for row in rows], dtype=np.float32)
    masks = np.asarray([row['legal_action_mask']['values'] if profile == 'hard_e' else row['affordability_only_mask']['values'] for row in rows], dtype=bool)
    if masks.shape != (len(rows), 5) or not masks[:, 0].all() or not masks[np.arange(len(rows)), target].all():
        raise TrajectoryError('A selected label is outside its declared action mask')
    if profile == 'hard_e' and any(row['legal_action_mask']['full_legality_verified'] is not True for row in rows):
        raise TrajectoryError('Hard-E cannot use affordability-only or unverified placement legality')
    return x, target, xy, masks


def init_parameters(input_width, hidden=64, seed=19):
    np = numpy(); rng = np.random.default_rng(seed)
    def matrix(a, b):
        return (rng.standard_normal((a, b))*((2/(a+b))**0.5)).astype(np.float32)
    return {'w1': matrix(input_width, hidden), 'b1': np.zeros(hidden, dtype=np.float32),
            'ws': matrix(hidden, 5), 'bs': np.zeros(5, dtype=np.float32),
            'wx': matrix(hidden, 2), 'bx': np.zeros(2, dtype=np.float32)}


def forward(parameters, x, masks):
    np = numpy()
    h = np.tanh(x@parameters['w1']+parameters['b1'])
    logits = h@parameters['ws']+parameters['bs']
    logits = np.where(masks, logits, -1e9)
    logits -= logits.max(axis=1, keepdims=True)
    scores = np.exp(logits); probabilities = scores/scores.sum(axis=1, keepdims=True)
    z = np.clip(h@parameters['wx']+parameters['bx'], -30, 30)
    coordinates = 1/(1+np.exp(-z))
    return h, probabilities, coordinates


def loss_and_gradients(parameters, x, target, xy, masks, *, class_weights, coordinate_weight=2.0, l2=1e-5):
    np = numpy()
    h, probability, coordinates = forward(parameters, x, masks)
    sample_weight = class_weights[target]
    denominator = max(float(sample_weight.sum()), 1e-12)
    ce = float((-np.log(np.maximum(probability[np.arange(len(target)), target], 1e-12))*sample_weight).sum()/denominator)
    ds = probability.copy(); ds[np.arange(len(target)), target] -= 1
    ds *= (sample_weight/denominator)[:, None]
    playing = target != 0; count = int(playing.sum())
    dc = np.zeros_like(coordinates)
    coordinate_loss = 0.0
    if count:
        errors = coordinates[playing]-xy[playing]
        coordinate_loss = float(np.square(errors).mean())
        dc[playing] = coordinate_weight*errors/count
    dz = dc*coordinates*(1-coordinates)
    dh = (ds@parameters['ws'].T+dz@parameters['wx'].T)*(1-h*h)
    gradients = {'ws': h.T@ds, 'bs': ds.sum(axis=0), 'wx': h.T@dz, 'bx': dz.sum(axis=0),
                 'w1': x.T@dh, 'b1': dh.sum(axis=0)}
    penalty = 0.0
    for key in ('w1', 'ws', 'wx'):
        penalty += 0.5*l2*float(np.square(parameters[key]).sum())
        gradients[key] += l2*parameters[key]
    if not all(np.isfinite(value).all() for value in gradients.values()):
        raise TrajectoryError('Non-finite BC gradients')
    return ce+coordinate_weight*coordinate_loss+penalty, gradients


def metrics(parameters, arrays, rows, *, predictions=None, coordinates=None):
    np = numpy(); x, target, xy, masks = arrays
    if predictions is None:
        _, probabilities, coordinates = forward(parameters, x, masks)
        predictions = probabilities.argmax(axis=1)
    elif coordinates is None:
        raise TrajectoryError('Explicit predictions require explicit normalized coordinates')
    predicted_play = predictions != 0; actual_play = target != 0
    true_play = predicted_play & actual_play
    predicted_wait = ~predicted_play; actual_wait = ~actual_play
    def ratio(a, b): return float(a/b) if b else None
    card_correct = []; matched_position = []
    for index, row in enumerate(rows):
        if not actual_play[index]: continue
        hand = {card['slot']+1: card['card'] for card in row['actor']['hand']}
        correct = hand.get(int(predictions[index])) == row['action']['card']
        card_correct.append(correct)
        if correct: matched_position.append(index)
    result = {'samples': len(rows), 'whole_source_episodes': len({row['source_episode_id'] for row in rows}),
              'wait_labels': int(actual_wait.sum()), 'play_labels': int(actual_play.sum()),
              'slot_accuracy': float((predictions == target).mean()),
              'wait_precision': ratio((predicted_wait & actual_wait).sum(), predicted_wait.sum()),
              'wait_recall': ratio((predicted_wait & actual_wait).sum(), actual_wait.sum()),
              'play_precision': ratio(true_play.sum(), predicted_play.sum()),
              'play_recall': ratio(true_play.sum(), actual_play.sum()),
              'card_accuracy_on_actual_play': ratio(sum(card_correct), len(card_correct)),
              'mask_label_coverage': float(masks[np.arange(len(target)), target].mean()),
              'action_choice_counts': {'forced_wait_only_mask': int((masks.sum(axis=1) == 1).sum()),
                                       'at_least_one_play_available': int((masks[:, 1:].any(axis=1)).sum())},
              'coordinate_units': 'native_requested_coordinate; not impact/entity position',
              'timing': 'No separate reaction-time head; decision-grid timing must be supplied by the native controller.',
              'position_on_all_actual_play': None, 'position_when_card_correct': None}
    for name, indices in [('position_on_all_actual_play', np.flatnonzero(actual_play)), ('position_when_card_correct', matched_position)]:
        if len(indices):
            errors = (coordinates[indices]-xy[indices])*np.array([17999, 31999])
            result[name] = {'count': len(indices), 'mae_x': float(np.abs(errors[:, 0]).mean()),
                            'mae_y': float(np.abs(errors[:, 1]).mean()),
                            'mean_euclidean': float(np.sqrt(np.square(errors).sum(axis=1)).mean())}
    return result


def _group_guard(rows):
    group_splits = {}; source_splits = {}; seen = set(); versions = set()
    for row in rows:
        identity = (row['source_episode_id'], row['decision_id'])
        if identity in seen: raise TrajectoryError('Duplicate training decision')
        seen.add(identity); versions.add(row['version_fingerprint_sha256'])
        for name, mapping in [('leakage_group', group_splits), ('source_episode_id', source_splits)]:
            value = row[name]
            if value in mapping and mapping[value] != row['learning_split']:
                raise TrajectoryError('Whole-source/seat group leakage across train-validation-test')
            mapping[value] = row['learning_split']
    if len(versions) != 1:
        raise TrajectoryError('Train one exact version fingerprint at a time')
    return versions.pop()


def train_bc(export, output, *, version_fingerprint, profile='hard_e', epochs=20,
             hidden=64, batch_size=128, learning_rate=0.001, seed=19, minimum_samples=10000,
             play_class_weight_cap=20.0):
    np = numpy()
    if profile not in {'hard_e', 'native_label_bc'}:
        raise TrajectoryError('Unsupported quality profile')
    rows, export_manifest = load_export(export, profile=profile)
    if not rows:
        raise TrajectoryError(f'Zero eligible {profile} decisions; no model will be trained or saved')
    strict_int(minimum_samples, 'minimum samples', 1)
    if len(rows) < minimum_samples:
        raise TrajectoryError(f'Only {len(rows)} eligible decisions; requested minimum {minimum_samples}')
    actual_version = _group_guard(rows)
    if actual_version != version_fingerprint:
        raise TrajectoryError('Training data and actual version manifest fingerprints differ')
    strict_int(epochs, 'epochs', 1, 10000); strict_int(hidden, 'hidden width', 2, 4096)
    strict_int(batch_size, 'batch size', 1, 100000)
    if not 0 < learning_rate < 1:
        raise TrajectoryError('Invalid learning rate')
    split_rows = {split: [row for row in rows if row['learning_split'] == split] for split in ('train', 'validation', 'test')}
    if not split_rows['train'] or not split_rows['validation']:
        raise TrajectoryError('Independent whole-source train and validation subsets are required')
    for split in ('train', 'validation'):
        if not any(row['action']['kind'] == 'play_card' for row in split_rows[split]):
            raise TrajectoryError(f'{split} has no eligible real card labels; refusing a WAIT-only BC demonstration')
    config = feature_configuration(split_rows['train'])
    arrays = {split: dataset_arrays(subset, config, profile) for split, subset in split_rows.items() if subset}
    parameters = init_parameters(arrays['train'][0].shape[1], hidden, seed)
    counts = np.bincount(arrays['train'][1], minlength=5).astype(np.float32)
    nonzero = max(int((counts > 0).sum()), 1)
    if not 0 < play_class_weight_cap <= 10000:
        raise TrajectoryError('Invalid play class weight cap')
    # WAIT is an observed sampling-grid absence and dominates the native
    # demonstrations. Keep its weight at one while giving rare card slots a
    # bounded, explicit cap so a candidate can be evaluated for action recall.
    weights = np.ones(5, dtype=np.float32)
    for action_index in range(1, 5):
        if counts[action_index] > 0:
            weights[action_index] = min(len(split_rows['train'])/(nonzero*counts[action_index]), play_class_weight_cap)
    rng = np.random.default_rng(seed)
    moment = {key: np.zeros_like(value) for key, value in parameters.items()}
    variance = {key: np.zeros_like(value) for key, value in parameters.items()}
    curve = []; step = 0; best = None; best_loss = float('inf'); best_epoch = None
    x, target, xy, masks = arrays['train']
    for epoch in range(1, epochs+1):
        order = rng.permutation(len(x)); total_loss = 0.0
        for start in range(0, len(order), batch_size):
            selected = order[start:start+batch_size]
            loss, gradients = loss_and_gradients(parameters, x[selected], target[selected], xy[selected], masks[selected], class_weights=weights)
            total_loss += loss*len(selected); step += 1
            norm = sum(float(np.square(value).sum()) for value in gradients.values())**0.5
            scale = min(1.0, 5/max(norm, 1e-12))
            for key in parameters:
                gradient = gradients[key]*scale
                moment[key] = 0.9*moment[key]+0.1*gradient
                variance[key] = 0.999*variance[key]+0.001*gradient*gradient
                parameters[key] -= learning_rate*(moment[key]/(1-0.9**step))/(np.sqrt(variance[key]/(1-0.999**step))+1e-8)
        validation = metrics(parameters, arrays['validation'], split_rows['validation'])
        validation_loss, _ = loss_and_gradients(parameters, *arrays['validation'], class_weights=weights)
        curve.append({'epoch': epoch, 'train_loss': total_loss/len(x),
                      'validation_loss': validation_loss,
                      'train': metrics(parameters, arrays['train'], split_rows['train']), 'validation': validation})
        # Validation-only selection. Test performance never chooses parameters.
        if validation_loss < best_loss:
            best_loss = validation_loss; best_epoch = epoch; best = {key: value.copy() for key, value in parameters.items()}
    output = project_path(output); output.mkdir(parents=True, exist_ok=True)
    weights_file = output / 'weights.npz'; partial = output / 'weights.npz.partial'
    with partial.open('wb') as handle: np.savez_compressed(handle, **best)
    partial.replace(weights_file)
    final_metrics = {split: metrics(best, arrays[split], split_rows[split]) for split in arrays}
    export_path = project_path(export)
    if export_path.is_dir(): export_path /= 'export_manifest.json'
    metadata = {'schema': MODEL_SCHEMA, 'profile': profile,
                'scope': 'Minimal native replay BC; no strength or official trajectory fidelity claim',
                'environment': {'python': sys.version, 'numpy': np.__version__},
                'hard_e_training_count_met': profile == 'hard_e' and len(rows) >= 10000,
                'counts_toward_phase2_G': profile == 'hard_e' and len(rows) >= 10000,
                'native_agent_evaluation_completed': False,
                'version_fingerprint_sha256': actual_version, 'feature_configuration': config,
                'weights': {'path': weights_file.relative_to(ROOT).as_posix(), 'sha256': file_sha(weights_file)},
                'architecture': {'hidden': hidden, 'input_width': int(x.shape[1]), 'slot_classes': 5,
                                 'coordinate_head': 'sigmoid2 native requested x/17999,y/31999; play-only MSE'},
                'training_configuration': {'epochs': epochs, 'batch_size': batch_size, 'learning_rate': learning_rate,
                                           'seed': seed, 'play_class_weight_cap': play_class_weight_cap,
                                           'class_weights': weights.tolist(), 'coordinate_loss_weight': 2.0,
                                           'minimum_samples': minimum_samples, 'selected_epoch': best_epoch,
                                           'selection': 'minimum whole-source validation weighted CE+2MSE+L2; test never used for selection'},
                'training_data': {'export_manifest': export_path.relative_to(ROOT).as_posix(), 'export_manifest_sha256': file_sha(export_path),
                                  'parquet_sha256': export_manifest['parquet']['sha256'], 'split_sha256': export_manifest['split_sha256'],
                                  'quality_grade_counts': dict(Counter(row['quality_grade'] for row in rows)),
                                  'reconstruction_source_role_decisions': dict(Counter(row['reconstruction_split'] for row in rows)),
                                  'formal_source_ids_by_learning_split': {split: sorted({row['source_episode_id'] for row in subset if row['reconstruction_split'] == 'formal'}) for split, subset in split_rows.items()},
                                  'whole_source_groups': {split: sorted({row['leakage_group'] for row in subset}) for split, subset in split_rows.items()},
                                  'eligible_decisions': len(rows), 'source_episodes': len({row['source_episode_id'] for row in rows})},
                'metrics': final_metrics, 'learning_source_sha256': file_sha(Path(__file__))}
    metadata['checkpoint_sha256'] = digest(metadata)
    write_json(output / 'checkpoint.json', metadata)
    write_json(output / 'learning_curve.json', {'schema': 'royale-phase2-bc-learning-curve.v1', 'profile': profile, 'epochs': curve})
    # Reload without object arrays; numerical serialization must be exact.
    reloaded = NumpyBCPolicy.load(output / 'checkpoint.json', version_fingerprint=actual_version, allow_diagnostic=profile != 'hard_e')
    if not all(np.array_equal(best[key], reloaded.parameters[key]) for key in best):
        raise TrajectoryError('BC checkpoint roundtrip mismatch')
    return metadata


class NumpyBCPolicy:
    def __init__(self, parameters, metadata):
        self.parameters = parameters; self.metadata = metadata

    @classmethod
    def load(cls, checkpoint, *, version_fingerprint, allow_diagnostic=False):
        np = numpy(); metadata = read_json(checkpoint)
        if metadata.get('schema') != MODEL_SCHEMA or metadata.get('checkpoint_sha256') != digest({key: value for key, value in metadata.items() if key != 'checkpoint_sha256'}):
            raise TrajectoryError('BC checkpoint metadata integrity/schema mismatch')
        if metadata['version_fingerprint_sha256'] != version_fingerprint:
            raise TrajectoryError('Checkpoint belongs to another engine/resource fingerprint')
        if metadata['profile'] != 'hard_e' and not allow_diagnostic:
            raise TrajectoryError('Diagnostic native-label checkpoint requires explicit allow_diagnostic')
        weights = project_path(metadata['weights']['path'])
        if weights.stat().st_size > 100*1024*1024 or file_sha(weights) != metadata['weights']['sha256']:
            raise TrajectoryError('BC weights changed or exceed supported size')
        with np.load(weights, allow_pickle=False) as archive:
            if set(archive.files) != {'w1', 'b1', 'ws', 'bs', 'wx', 'bx'}:
                raise TrajectoryError('Unexpected BC weight tensors')
            parameters = {key: archive[key].copy() for key in archive.files}
        width = metadata['architecture']['input_width']; hidden = metadata['architecture']['hidden']
        shapes = {'w1': (width, hidden), 'b1': (hidden,), 'ws': (hidden, 5), 'bs': (5,), 'wx': (hidden, 2), 'bx': (2,)}
        for key, value in parameters.items():
            if value.shape != shapes[key] or value.dtype != np.float32 or not np.isfinite(value).all():
                raise TrajectoryError(f'Invalid checkpoint tensor: {key}')
        return cls(parameters, metadata)

    def _inference_mask(self, actor, legal_mask, proof_cache):
        np = numpy(); validate_actor(actor)
        if self.metadata['profile'] == 'hard_e':
            if legal_mask is None or legal_mask.get('full_legality_verified') is not True or legal_mask.get('subspace') != 'wait_or_arena_spell.v1':
                raise TrajectoryError('Hard-E policy requires a currently verified public action-subspace mask')
            # Cache only within one batch, never across observations/calls: a
            # modified proof or underlying evidence must be noticed next call.
            key = digest(legal_mask.get('proof'))
            if key not in proof_cache:
                reference = verify_reference(legal_mask.get('proof'))
                proof_cache[key] = audit_subspace_proof(reference['path'], self.metadata['version_fingerprint_sha256'])
            audit = proof_cache[key]
            if legal_mask != subspace_mask(actor, legal_mask.get('command_ready'), audit):
                raise TrajectoryError('Inference mask differs from recomputed public/own-only subspace mask')
            mask = legal_mask['values']
        else:
            mask = affordability_mask(actor)['values']
            if not actor['hand_complete']:
                mask = [True, False, False, False, False]
        if len(mask) != 5 or any(type(value) is not bool for value in mask) or not mask[0]:
            raise TrajectoryError('Invalid inference action mask')
        if not actor['hand_complete'] and any(mask[1:]):
            raise TrajectoryError('Incomplete current hand permits only WAIT at inference')
        return mask

    def predict_many(self, actors, *, legal_masks=None):
        """The same deployable path for offline batches and online decisions."""
        np = numpy(); actors = list(actors)
        legal_masks = [None]*len(actors) if legal_masks is None else list(legal_masks)
        if len(legal_masks) != len(actors):
            raise TrajectoryError('Actors and masks must have the same length')
        if not actors: return []
        cache = {}
        masks = np.asarray([self._inference_mask(a, m, cache) for a, m in zip(actors, legal_masks)], dtype=bool)
        # Keep each forward at batch size one. Some BLAS kernels accumulate
        # different sizes differently; close argmax ties or .5 rounding must
        # not make offline actions differ from deployed single-step actions.
        actions = []
        for actor, mask in zip(actors, masks):
            x = actor_features(actor, self.metadata['feature_configuration'])[None, :]
            _, probabilities, coordinates = forward(self.parameters, x, mask[None, :])
            index, points = decode_policy_output(probabilities, coordinates, mask[None, :],
                play_bias=self.metadata.get('inference_play_bias', 0.0))
            actions.append(action_from_index(actor, int(index[0]), points[0]))
        return actions

    def predict(self, actor, *, legal_mask=None):
        return self.predict_many([actor], legal_masks=[legal_mask])[0]


def inference_configuration(metadata):
    bias = float(metadata.get('inference_play_bias', 0.0))
    if not numpy().isfinite(bias): raise TrajectoryError('Non-finite inference play bias')
    return {'schema': 'royale-phase2-deployment-inference.v1', 'play_log_score_bias': bias,
            'selection': 'deterministic argmax; first index wins ties; no sampling',
            'scores': 'log(clip(masked_softmax_probability,1e-30,1))+legal_play_bias; illegal=-inf',
            'temperature': 1.0, 'mask': 'recomputed own/public wait_or_arena_spell.v1 proof',
            'coordinates': 'numpy rint ties-to-even, clip x to [0,17999], y to [0,31999]',
            'forward_batch_size': 1}


def decode_policy_output(probabilities, coordinates, masks, *, play_bias=0.0):
    """Pure action postprocessing, shared by every BC deployment/evaluation."""
    np = numpy(); p = np.asarray(probabilities); xy = np.asarray(coordinates); mask = np.asarray(masks)
    if (p.ndim != 2 or p.shape[1] != 5 or xy.shape != (len(p), 2)
            or mask.shape != p.shape or mask.dtype != np.bool_ or not mask[:, 0].all()
            or not np.isfinite(p).all() or not np.isfinite(xy).all()
            or (p < 0).any() or (p > 1).any() or (xy < 0).any() or (xy > 1).any()
            or not np.isfinite(float(play_bias))):
        raise TrajectoryError('Invalid inference tensors, mask or bias')
    scores = np.log(np.clip(p, 1e-30, 1.0)); scores[:, 1:] += float(play_bias)
    scores = np.where(mask, scores, -np.inf)
    # Preserve the historical scalar float32 multiplication/rounding dtype.
    points = np.column_stack([np.clip(np.rint(xy[:, 0]*17999), 0, 17999),
                              np.clip(np.rint(xy[:, 1]*31999), 0, 31999)]).astype(np.int64)
    return scores.argmax(axis=1), points


def action_from_index(actor, index, point):
    if index == 0: return {'kind': 'wait'}
    card = next((c for c in actor['hand'] if c['slot'] == index-1), None)
    if card is None: raise TrajectoryError('Predicted slot is absent')
    return {'kind': 'play_card', 'slot': card['slot'], 'card': card['card'],
            'x': int(point[0]), 'y': int(point[1])}


def deployment_metrics(model, arrays, rows, *, actions=None):
    np = numpy()
    if actions is None:
        actions = model.predict_many([r['actor'] for r in rows],
                                    legal_masks=[r['legal_action_mask'] for r in rows])
    if len(actions) != len(rows): raise TrajectoryError('Prediction count differs from labels')
    predicted = np.asarray([0 if a['kind'] == 'wait' else a['slot']+1 for a in actions])
    # There is no deployed location for WAIT. Exclude it from position metrics
    # rather than claiming the unused coordinate head was an executed action.
    xy = np.asarray([[0., 0.] if a['kind'] == 'wait' else [a['x']/17999, a['y']/31999] for a in actions])
    result = metrics(model.parameters, arrays, rows, predictions=predicted, coordinates=xy)
    for field in ('position_on_all_actual_play', 'position_when_card_correct'):
        indices = [i for i, row in enumerate(rows) if row['action']['kind'] == 'play_card'
                   and actions[i]['kind'] == 'play_card'
                   and (field == 'position_on_all_actual_play' or actions[i]['card'] == row['action']['card'])]
        result[field] = None
        if indices:
            errors = (xy[indices]-arrays[2][indices])*np.asarray([17999, 31999])
            result[field] = {'count': len(indices), 'mae_x': float(np.abs(errors[:, 0]).mean()),
                'mae_y': float(np.abs(errors[:, 1]).mean()), 'mean_euclidean': float(np.linalg.norm(errors, axis=1).mean())}
    result['position_semantics'] = 'Returned integer position, conditional on predicting PLAY; abstentions are misses, not positions'
    result['predicted_play_count'] = int((predicted != 0).sum())
    result['predicted_slot_mask_valid_rate'] = float(arrays[3][np.arange(len(rows)), predicted].mean())
    return result


def evaluate_checkpoint(checkpoint, export, *, version_fingerprint, split='test', allow_diagnostic=False):
    if split not in {'train', 'validation', 'test'}:
        raise TrajectoryError('Unknown evaluation split')
    model = NumpyBCPolicy.load(checkpoint, version_fingerprint=version_fingerprint, allow_diagnostic=allow_diagnostic)
    rows, export_manifest = load_export(export, profile=model.metadata['profile'])
    if not rows: raise TrajectoryError('No eligible native evaluation decisions')
    if _group_guard(rows) != version_fingerprint: raise TrajectoryError('Evaluation version mismatch')
    if export_manifest['split_sha256'] != model.metadata['training_data']['split_sha256']:
        raise TrajectoryError('Evaluation split manifest differs from training declaration')
    selected = [row for row in rows if row['learning_split'] == split]
    if not selected: raise TrajectoryError(f'No eligible whole-source {split} decisions')
    arrays = dataset_arrays(selected, model.metadata['feature_configuration'], model.metadata['profile'])
    return {'schema': 'royale-phase2-bc-offline-evaluation.v2', 'checkpoint_sha256': model.metadata['checkpoint_sha256'],
            'split': split, 'profile': model.metadata['profile'], 'metrics': deployment_metrics(model, arrays, selected),
            'inference_configuration': inference_configuration(model.metadata),
            'unbiased_training_reference': metrics(model.parameters, arrays, selected),
            'native_matches_run': 0, 'scope': 'Offline held-out native-reconstructed decision prediction only. This is not the required ten complete native agent matches.'}

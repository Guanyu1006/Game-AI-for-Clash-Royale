"""Torch-independent validation of saved phase-three learning rows."""
import math
from .common import read, rows, sha


def load_dataset(metadata):
    meta = read(metadata)
    if meta.get('schema') != 'phase3-learning-dataset.v1':
        raise ValueError('Unsupported learning dataset')
    if meta.get('control_ticks') != 10:
        raise ValueError('The frozen comparison requires 10 ticks (0.5 seconds)')
    if sha(meta['rows']['path']) != meta['rows']['sha256']:
        raise ValueError('Dataset rows SHA256 mismatch')
    data = list(rows(meta['rows']['path']))
    groups = {}; previous = {}
    for row in data:
        key = (row['episode_id'], row['native_owner'])
        if row['split'] not in ('train', 'validation', 'test'):
            raise ValueError('Unknown split')
        # Both perspectives of the same episode must be in one split.
        if row['episode_id'] in groups and groups[row['episode_id']] != row['split']:
            raise ValueError('Episode leakage across splits')
        groups[row['episode_id']] = row['split']
        if type(row['native_owner']) is not int or row['native_owner'] not in (0, 1):
            raise ValueError('Invalid native owner')
        if type(row['tick']) is not int or row['tick'] < 0 or row['tick'] <= previous.get(key, -1):
            raise ValueError('Sequence ticks must strictly increase per episode/owner')
        previous[key] = row['tick']
        for name in ('hud', 'observation', 'history', 'belief', 'feedback'):
            vector = row[name]
            if len(vector) != meta['feature_dims'][name] or any(type(x) not in (int, float) or not math.isfinite(x) for x in vector):
                raise ValueError('Invalid feature vector: '+name)
        mask = row['mask']
        if len(mask) != 5 or any(type(x) is not bool for x in mask) or not mask[0]:
            raise ValueError('Mask must contain WAIT and four slots')
        if type(row['action_index']) is not int or not 0 <= row['action_index'] <= 4:
            raise ValueError('Invalid action label')
        if row['loss_mask'] and not mask[row['action_index']]:
            raise ValueError('Label violates supplied public affordability mask')
        if type(row['previous_action']) is not int or not 0 <= row['previous_action'] <= 4:
            raise ValueError('Invalid previous action')
        if type(row['dt_ticks']) not in (int, float) or not math.isfinite(row['dt_ticks']) or row['dt_ticks'] < 0:
            raise ValueError('Invalid dt')
        if type(row['position_index']) is not int or not 0 <= row['position_index'] < 576:
            raise ValueError('Invalid 18x32 y-major position')
        if len(row['xy']) != 2 or any(not math.isfinite(x) or not 0 <= x <= 1 for x in row['xy']):
            raise ValueError('Invalid native normalized coordinate')
        if len(row['targets']['world']) != 9 or len(row['targets']['world_mask']) != 9:
            raise ValueError('World target shape must be nine')
    return data, meta


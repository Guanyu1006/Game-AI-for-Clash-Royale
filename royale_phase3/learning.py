"""Small CPU sequence BC, with one inference path and pickle-free checkpoints.

No native connection is made here. Targets may contain privileged supervision;
only explicitly named feature arrays enter the actor. Test is never evaluated by
train(). Models are research artifacts, not evidence of stronger native play.
"""
from collections import Counter, defaultdict
from copy import deepcopy
import json
import math
import os
import time
import zipfile

import numpy as np
import torch
from torch import nn
from torch.nn import functional as F

from .common import configure, digest, local, now, read, reference, rows, sha, write
from .actions import DECK, position_mask as deployment_position_mask

VARIANTS = ('A', 'B', 'C', 'D', 'E')
SCHEMA = 'phase3-small-policy.v1'


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


def actor_vector(row, variant):
    if variant == 'A': return row['hud']
    if variant == 'B': return row['observation']
    # `history` is the complete current+history vector, not an actor identity.
    return row['history'] + row['belief'] + [float(row['previous_action'] == i) for i in range(5)] + row['feedback'] + [row['dt_ticks']/20.]


def public_hand_cards(row):
    """Decode the fixed public HUD, never the supervised label card_id.

    The schema's eight-card one-hot starts after scalar8 + slot-present1.
    Explicit hand_cards is reserved for contract fixtures/adapters with the
    same public observation provenance; main native rows use their HUD.
    """
    if 'hand_cards' in row:
        cards=row['hand_cards']
        if len(cards)!=4 or any(c is not None and c not in DECK for c in cards):raise ValueError('Invalid public hand')
        return cards
    hud=row['hud']
    if len(hud)!=96:raise ValueError('Position proposal requires the fixed eight-card public HUD')
    result=[]
    for slot in range(4):
        start=8+slot*13;onehot=hud[start+1:start+9]
        matches=[i for i,value in enumerate(onehot) if value==1.]
        result.append(DECK[matches[0]] if hud[start]==1. and len(matches)==1 and sum(onehot)==1. else None)
    return result


def public_position_masks(row):
    return [deployment_position_mask(card,row['native_owner']) if card is not None else [False]*576 for card in public_hand_cards(row)]


def _project_regression(xy, cards, owners):
    """Differentiable public deployment-region transform, used by loss/decoder."""
    spell=cards>=28000000; y=xy[...,1]
    native_y=torch.where(spell,y*31999,torch.where(owners==0,y*15999,16000+y*15999))
    return torch.stack([xy[...,0],native_y/31999],-1)


def architecture(meta, variant, hidden=32, world=False, opponent=False, hazard=False):
    if variant not in VARIANTS or not 4 <= hidden <= 512: raise ValueError('Invalid model architecture')
    dims = meta['feature_dims']
    width = dims['hud'] if variant == 'A' else dims['observation'] if variant == 'B' else dims['history']+dims['belief']+5+dims['feedback']+1
    return {'variant': variant, 'input_width': width, 'hidden': hidden,
        'recurrent': variant in ('C','D','E'), 'grid': variant in ('D','E'),
        'world': bool(world), 'opponent': bool(opponent), 'hazard': bool(hazard)}


class SmallPolicy(nn.Module):
    def __init__(self, config):
        super().__init__(); self.config = config; h = config['hidden']
        self.encoder = nn.Linear(config['input_width'], h)
        self.gru = nn.GRU(h, h, batch_first=True) if config['recurrent'] else None
        self.action_head = nn.Linear(h, 5)
        self.position_head = nn.Linear(h, 4*576 if config['grid'] else 2)
        self.world_head = nn.Linear(h, 9) if config['world'] else None
        self.opponent_head = nn.Linear(h, 9) if config['opponent'] else None
        self.hazard_head = nn.Linear(h, 2) if config['hazard'] else None

    def forward(self, x, hidden=None):
        z = torch.tanh(self.encoder(x))
        if self.gru is not None: z, hidden = self.gru(z, hidden)
        else: hidden = None
        position = self.position_head(z)
        if self.config['grid']: position = position.reshape(*z.shape[:-1], 4, 576)
        else: position = position.sigmoid()
        return {'action': self.action_head(z), 'position': position,
            'world': self.world_head(z) if self.world_head else None,
            'opponent': self.opponent_head(z) if self.opponent_head else None,
            'hazard': self.hazard_head(z) if self.hazard_head else None}, hidden


def sequence_windows(data, length=32, burnin=8):
    """Every eligible row is learned exactly once; overlap is burn-in only.

    Context cannot cross an episode/owner. Right padding is excluded by an
    explicit valid mask. Detach hidden state after context to truncate BPTT.
    """
    if length < 1 or burnin < 0: raise ValueError('Invalid sequence dimensions')
    grouped = defaultdict(list)
    for row in data: grouped[(row['episode_id'], row['native_owner'])].append(row)
    windows = []
    for key, group in sorted(grouped.items()):
        for start in range(0, len(group), length):
            context = group[max(0, start-burnin):start]
            learned = group[start:start+length]
            windows.append({'key': key, 'context': context, 'rows': learned,
                'burnin_count': len(context), 'valid': [True]*len(learned)+[False]*(length-len(learned))})
    return windows


def _tensor_batch(windows, variant, length):
    width = len(actor_vector(windows[0]['rows'][0], variant)); batch = len(windows)
    x = torch.zeros(batch, length, width); valid = torch.zeros(batch, length, dtype=torch.bool)
    mask = torch.ones(batch, length, 5, dtype=torch.bool)
    labels = torch.zeros(batch, length, dtype=torch.long); position = labels.clone()
    xy = torch.zeros(batch, length, 2); position_valid = valid.clone()
    position_masks=torch.zeros(batch,length,4,576,dtype=torch.bool)
    hand_cards=torch.zeros(batch,length,4,dtype=torch.long);owners=torch.zeros(batch,length,dtype=torch.long)
    target = torch.zeros(batch, length, 9); target_valid = torch.zeros(batch, length, 9, dtype=torch.bool)
    opponent = labels.clone(); opponent_valid = valid.clone(); eligible = valid.clone()
    for b, window in enumerate(windows):
        for t, row in enumerate(window['rows']):
            x[b,t] = torch.tensor(actor_vector(row, variant)); valid[b,t] = True
            eligible[b,t] = bool(row['loss_mask']); labels[b,t] = row['action_index']
            mask[b,t] = torch.tensor(row['mask']); position[b,t] = row['position_index']
            xy[b,t] = torch.tensor(row['xy']); position_valid[b,t] = bool(row['position_mask']) and row['action_index'] > 0
            cards=public_hand_cards(row);hand_cards[b,t]=torch.tensor([c or 0 for c in cards]);owners[b,t]=row['native_owner']
            position_masks[b,t]=torch.tensor(public_position_masks(row))
            target[b,t] = torch.tensor(row['targets']['world']); target_valid[b,t] = torch.tensor(row['targets']['world_mask'])
            opponent[b,t] = row['targets']['opponent']; opponent_valid[b,t] = bool(row['targets']['opponent_mask'])
    return {'x':x, 'valid':valid & eligible, 'labels':labels, 'mask':mask, 'position':position,
        'xy':xy, 'position_valid':position_valid & valid & eligible, 'world':target,
        'position_masks':position_masks,'hand_cards':hand_cards,'owners':owners,
        'world_valid':target_valid & valid[:,:,None] & eligible[:,:,None],
        'opponent':opponent, 'opponent_valid':opponent_valid & valid & eligible}


def _initial_hidden(model, windows, variant):
    if model.gru is None: return None
    states = []
    with torch.no_grad():
        for window in windows:
            if window['context']:
                x = torch.tensor([[actor_vector(row, variant) for row in window['context']]], dtype=torch.float32)
                _, h = model(x); states.append(h)
            else: states.append(torch.zeros(1,1,model.config['hidden']))
    return torch.cat(states, 1).detach()


def loss(model, out, b, world_weight=.1, opponent_weight=.1, hazard_weight=.1, play_weight=1.):
    active = b['valid']; scores = out['action'].masked_fill(~b['mask'], -1e9)
    zero = scores.sum()*0
    # Fixed declared train weighting only. Validation is always natural-time.
    weights=torch.tensor([1.]+[float(play_weight)]*4)
    action = F.cross_entropy(scores[active], b['labels'][active],weight=weights) if active.any() else zero
    pos_active = b['position_valid']
    position = zero
    if pos_active.any():
        if model.config['grid']:
            slots = (b['labels']-1).clamp(0,3)
            masked_position=out['position'].masked_fill(~b['position_masks'],-1e9)
            selected = masked_position.gather(2, slots[:,:,None,None].expand(-1,-1,1,576)).squeeze(2)
            position = F.cross_entropy(selected[pos_active], b['position'][pos_active])
        else:
            cards=b['hand_cards'].gather(2,(b['labels']-1).clamp(0,3)[:,:,None]).squeeze(2)
            projected=_project_regression(out['position'],cards,b['owners'])
            position = F.mse_loss(projected[pos_active], b['xy'][pos_active])
    world = F.mse_loss(out['world'][b['world_valid']], b['world'][b['world_valid']]) if out['world'] is not None and b['world_valid'].any() else zero
    op = F.cross_entropy(out['opponent'][b['opponent_valid']], b['opponent'][b['opponent_valid']]) if out['opponent'] is not None and b['opponent_valid'].any() else zero
    hz = F.cross_entropy(out['hazard'][active], (b['labels'][active]>0).long()) if out['hazard'] is not None and active.any() else zero
    total = action+position+world_weight*world+opponent_weight*op+hazard_weight*hz
    return total, {'total':float(total.detach()), 'action':float(action.detach()), 'position':float(position.detach()),
        'world':float(world.detach()), 'opponent':float(op.detach()), 'hazard':float(hz.detach())}


def decode(out, mask, config, row):
    """One pure deterministic decoder shared by validation and online inference."""
    if len(mask) != 5 or any(type(x) is not bool for x in mask) or not mask[0]: raise ValueError('Invalid public mask')
    scores = out['action'].reshape(5).detach().cpu().numpy().astype(np.float64)
    if not np.isfinite(scores).all(): raise ValueError('Nonfinite action logits')
    scores[~np.asarray(mask)] = -np.inf
    cards=public_hand_cards(row)
    for slot,card in enumerate(cards):
        if card is None:scores[slot+1]=-np.inf
    index = int(np.argmax(scores)); point = None; position_log_probability = None
    if index:
        if config['grid']:
            p = out['position'].reshape(4,576)[index-1].detach().cpu().clone()
            p=p.masked_fill(~torch.tensor(deployment_position_mask(cards[index-1],row['native_owner'])),-1e9)
            cell = int(p.argmax()); point = [cell%18*1000+500, cell//18*1000+500]
            position_log_probability = float(p.log_softmax(-1)[cell])
        else:
            xy = _project_regression(out['position'].reshape(2),torch.tensor(cards[index-1]),torch.tensor(row['native_owner'])).detach().cpu().numpy()
            point = [int(np.clip(np.rint(float(xy[0])*17999),0,17999)), int(np.clip(np.rint(float(xy[1])*31999),0,31999))]
    return {'action_index':index, 'xy_native':point, 'position_log_probability':position_log_probability}


def _save_arrays(path, arrays):
    path = local(path); path.parent.mkdir(parents=True, exist_ok=True); temporary = path.with_name(path.name+'.tmp')
    with temporary.open('wb') as f: np.savez_compressed(f, **arrays); f.flush(); os.fsync(f.fileno())
    os.replace(temporary,path)


def _pack(value, tensors):
    if torch.is_tensor(value):
        key = 'tensor_'+str(len(tensors)); tensors[key] = value.detach().cpu().numpy(); return {'tensor':key}
    if isinstance(value, dict): return {'dict': [[_pack(k,tensors), _pack(v,tensors)] for k,v in value.items()]}
    if isinstance(value, tuple): return {'tuple': [_pack(v,tensors) for v in value]}
    if isinstance(value, list): return [_pack(v,tensors) for v in value]
    if value is None or type(value) in (str,int,float,bool): return value
    raise TypeError('Unsupported checkpoint value')


def _unpack(value, arrays):
    if isinstance(value,dict):
        if set(value)=={'tensor'}: return torch.from_numpy(arrays[value['tensor']].copy())
        if set(value)=={'dict'}: return {_unpack(k,arrays):_unpack(v,arrays) for k,v in value['dict']}
        if set(value)=={'tuple'}: return tuple(_unpack(v,arrays) for v in value['tuple'])
        raise ValueError('Unknown checkpoint value')
    if isinstance(value,list): return [_unpack(v,arrays) for v in value]
    return value


def save_checkpoint(folder, model, optimizer, config, epoch, curve, best_score, best_weights):
    """Write one epoch snapshot, then atomically point latest.json at it."""
    folder = local(folder); arrays = {}; snapshot = {
        'model':_pack(model.state_dict(), arrays), 'optimizer':_pack(optimizer.state_dict(), arrays),
        'rng':_pack(torch.get_rng_state(),arrays), 'best_model':_pack(best_weights, arrays)}
    name = f'epoch-{epoch:04d}'; weights = folder/(name+'.npz'); _save_arrays(weights,arrays)
    payload = {'schema':SCHEMA, 'created_utc':now(), 'config':config, 'epoch':epoch, 'curve':curve,
        'best_validation_action_nll':best_score, 'weights':reference(weights), 'snapshot':snapshot,
        'format':'NumPy npz allow_pickle=False; JSON tensor tree; no torch.load/pickle'}
    payload['checkpoint_sha256'] = digest(payload); write(folder/(name+'.json'),payload)
    write(folder/'latest.json',reference(folder/(name+'.json')))
    return folder/(name+'.json')


def read_checkpoint(path):
    metadata = read(path)
    if metadata.get('schema') != SCHEMA or metadata.get('checkpoint_sha256') != digest({k:v for k,v in metadata.items() if k!='checkpoint_sha256'}):
        raise ValueError('Checkpoint metadata integrity failed')
    file = local(metadata['weights']['path'])
    if file.stat().st_size > 128*1024*1024 or sha(file) != metadata['weights']['sha256']:
        raise ValueError('Checkpoint weights changed or too large')
    with zipfile.ZipFile(file) as archive:
        if sum(item.file_size for item in archive.infolist())>256*1024*1024:raise ValueError('Checkpoint decompressed arrays too large')
    with np.load(file, allow_pickle=False) as archive:
        arrays = {k:archive[k].copy() for k in archive.files}
    if any(a.dtype.hasobject or a.size>10_000_000 or not np.isfinite(a).all() for a in arrays.values()):
        raise ValueError('Invalid checkpoint array')
    return metadata, {k:_unpack(v,arrays) for k,v in metadata['snapshot'].items()}


class StatefulPolicy:
    def __init__(self, model, metadata=None):
        self.model = model.eval(); self.metadata = metadata or {}; self.reset()

    @classmethod
    def load(cls, path, version=None, profile=None):
        meta, state = read_checkpoint(path); config = meta['config']
        if version is not None and config['version_fingerprint_sha256'] != version: raise ValueError('Wrong native version')
        if profile is not None and config['observation_profile'] != profile: raise ValueError('Wrong observation profile')
        model = SmallPolicy(config['architecture']); model.load_state_dict(state['best_model'], strict=True)
        return cls(model,meta)

    def reset(self): self.hidden = None; self.key = None; self.tick = None; self.cached = None; self.last_input = None

    def predict(self, row):
        key = (row['episode_id'],row['native_owner'])
        if key != self.key: self.reset(); self.key = key
        actor_input = {'vector':actor_vector(row,self.model.config['variant']), 'mask':row['mask']}
        fingerprint = digest(actor_input)
        if self.tick is not None and row['tick'] < self.tick: raise ValueError('Tick regressed; explicit reset required')
        if self.tick == row['tick']:
            if fingerprint != self.last_input: raise ValueError('Observation changed at same tick')
            return deepcopy(self.cached)
        with torch.no_grad():
            x = torch.tensor([[actor_input['vector']]],dtype=torch.float32)
            output,self.hidden = self.model(x,self.hidden)
            if self.hidden is not None: self.hidden=self.hidden.detach()
            action = decode(output,row['mask'],self.model.config,row)
        self.tick=row['tick']; self.last_input=fingerprint; self.cached=deepcopy(action)
        return action


def evaluate(model, data, split, *, event_tolerance_ticks=10):
    """Natural-time, full-sequence inference. No oversampling or hidden restart."""
    selected = [r for r in data if r['split']==split]; selected.sort(key=lambda r:(r['episode_id'],r['native_owner'],r['tick']))
    if not selected: return {'split':split,'rows':0,'status':'no_data'}
    policy = StatefulPolicy(model); predictions=[]; elapsed=[]; nll=[]; correct=0; mask_valid=0
    positions=[]; correct_positions=[]; card_positions=defaultdict(list); pos_nll=[]; op_nll=[]; op_probs=[]; op_labels=[]
    # Compute action probabilities using the same recurrent states as deployment;
    # a separate exact traversal checks each decoded output against predict().
    hidden=None; previous_key=None
    for row in selected:
        key=(row['episode_id'],row['native_owner'])
        if key!=previous_key: hidden=None; previous_key=key
        t=time.perf_counter(); action=policy.predict(row); elapsed.append(time.perf_counter()-t); predictions.append(action)
        with torch.no_grad():
            out,hidden=model(torch.tensor([[actor_vector(row,model.config['variant'])]],dtype=torch.float32),hidden)
            direct=decode(out,row['mask'],model.config,row)
            if action!=direct: raise AssertionError('Online/offline inference disagreement')
            scores=out['action'].reshape(5).masked_fill(~torch.tensor(row['mask']),-1e9)
            if row['loss_mask']: nll.append(float(-scores.log_softmax(-1)[row['action_index']])); correct+=int(action['action_index']==row['action_index'])
            mask_valid+=int(row['mask'][action['action_index']])
            if row['position_mask'] and row['action_index']>0:
                if model.config['grid']:
                    truth=out['position'].reshape(4,576)[row['action_index']-1].clone()
                    truth=truth.masked_fill(~torch.tensor(public_position_masks(row)[row['action_index']-1]),-1e9)
                    pos_nll.append(float(-truth.log_softmax(-1)[row['position_index']]))
                if action['action_index']:
                    error=math.hypot(action['xy_native'][0]-row['xy'][0]*17999,action['xy_native'][1]-row['xy'][1]*31999)
                    positions.append(error); card_positions[str(row['card_id'])].append(error)
                    if action['action_index']==row['action_index']: correct_positions.append(error)
            if out['opponent'] is not None and row['targets']['opponent_mask']:
                p=out['opponent'].reshape(9).softmax(-1); label=row['targets']['opponent']; op_nll.append(float(-p[label].clamp_min(1e-30).log()))
                op_probs.append(p.tolist()); op_labels.append(label)
    # One-to-one time-only event matching, predeclared +/-0.5 seconds.
    groups=defaultdict(lambda:{'pred':[],'label':[]}); predicted_plays=actual_plays=matched=card_correct=0; time_errors=[]
    for row,action in zip(selected,predictions):
        group=groups[(row['episode_id'],row['native_owner'])]
        if action['action_index']:
            group['pred'].append((row['tick']+1,action['action_index'])); predicted_plays+=1
        if row['loss_mask'] and row['action_index']:
            group['label'].append((row.get('actual_execution_tick',row.get('confirmed_tick',row['tick']+1)),row['action_index'])); actual_plays+=1
    for group in groups.values():
        p=a=0
        while p<len(group['pred']) and a<len(group['label']):
            dt=group['pred'][p][0]-group['label'][a][0]
            if dt < -event_tolerance_ticks:p+=1
            elif dt > event_tolerance_ticks:a+=1
            else:
                matched+=1;card_correct+=int(group['pred'][p][1]==group['label'][a][1]);time_errors.append(abs(dt));p+=1;a+=1
    def mean(values): return float(np.mean(values)) if values else None
    eligible=sum(bool(r['loss_mask']) for r in selected)
    contexts={}
    for name,predicate in [('only_wait_legal',lambda r:sum(r['mask'])==1),('can_play',lambda r:sum(r['mask'])>1)]:
        subset=[(r,p) for r,p in zip(selected,predictions) if predicate(r)]
        contexts[name]={'rows':len(subset),'predicted_play':sum(p['action_index']>0 for r,p in subset),'actual_play':sum(r['loss_mask'] and r['action_index']>0 for r,p in subset)}
    opponent=None
    if op_probs:
        p=np.asarray(op_probs);labels=np.asarray(op_labels);confidence=p.max(1);correct_op=p.argmax(1)==labels;ece=0.
        for lo in np.linspace(0,.9,10):
            chosen=(confidence>=lo)&(confidence<lo+.1+1e-8)
            if chosen.any():ece+=float(chosen.mean()*abs(correct_op[chosen].mean()-confidence[chosen].mean()))
        opponent={'nll':mean(op_nll),'top1':float(correct_op.mean()),'ece_10_bins':ece,'class_counts':dict(Counter(map(str,op_labels))),'rows':len(op_labels)}
    return {'split':split,'rows':len(selected),'eligible_rows':eligible,'episodes':len({r['episode_id'] for r in selected}),
        'action_nll':mean(nll),'action_accuracy':correct/eligible if eligible else None,'position_nll_true_slot':mean(pos_nll),
        'position_on_all_actual_plays':{'predicted_play_denominator':len(positions),'actual_play_denominator':actual_plays,'mean_native_distance':mean(positions)},
        'position_when_slot_correct':{'count':len(correct_positions),'mean_native_distance':mean(correct_positions)},
        'position_by_true_card':{k:{'count':len(v),'mean_native_distance':mean(v)} for k,v in card_positions.items()},
        'event':{'tolerance_ticks':event_tolerance_ticks,'predicted':predicted_plays,'actual':actual_plays,'matched':matched,
            'precision':matched/predicted_plays if predicted_plays else None,'recall':matched/actual_plays if actual_plays else None,
            'slot_correct_on_time_match':card_correct/matched if matched else None,'mean_abs_error_ticks':mean(time_errors)},
        'contexts':contexts,'opponent':opponent,'predicted_slot_affordable_rate':mask_valid/len(selected),
        'prediction_sha256':digest(predictions),'inference_seconds':{'mean':mean(elapsed),'p95':float(np.percentile(elapsed,95))},
        'online_offline_exact_match':True,'native_consumed_rejected_unknown':'not_run_offline',
        'whole_game_win_cycle_lock_key_defense':'not_run_offline','position_legality':'not_proven_by_affordability_mask',
        'scope':'teacher-forced observations; recurrent hidden follows actual history; no counterfactual native rollout'}


def train(metadata, output, variant='C', epochs=5, seed=31001, hidden=32, sequence_length=32,
          burnin=8, batch_size=8, learning_rate=.001, world_weight=.1, opponent=False,
          hazard=False, resume=False, stop_after_epochs=None, play_weight=1.):
    configure(); torch.set_num_threads(2); torch.use_deterministic_algorithms(True)
    data,meta=load_dataset(metadata); folder=local(output)
    config={'schema':'phase3-small-training-config.v1','dataset':reference(metadata),'rows':meta['rows'],
        'version_fingerprint_sha256':meta['version_fingerprint_sha256'],'observation_profile':meta['observation_profile'],
        'control_ticks':10,'seed':seed,'epochs':epochs,'sequence_length':sequence_length,'burnin':burnin,
        'sequence_seconds':sequence_length*.5,'burnin_seconds':burnin*.5,'batch_size':batch_size,
        'learning_rate':learning_rate,'world_weight':world_weight,'opponent_weight':.1,'hazard_weight':.1,
        'train_play_class_weight':float(play_weight),
        'architecture':architecture(meta,variant,hidden,variant=='E',opponent,hazard),
        'sampling':'each natural-time row once per epoch; no oversampling or class weighting',
        'inference':'masked deterministic argmax; zero play bias; public predicted-slot position proposal mask; 18x32 cell centers or public-region-transformed 2D ties-to-even',
        'actor_features':'hud / observation / full history vector + belief + own previous action + feedback + actual dt',
        'model_selection':'lowest validation action NLL, never test','event_tolerance_ticks':10,
        'training_source':reference('royale_phase3/learning.py'),'threads':2}
    if epochs<1 or batch_size<1 or sequence_length<1 or burnin<0 or not math.isfinite(play_weight) or play_weight<=0: raise ValueError('Invalid training budget')
    torch.manual_seed(seed); model=SmallPolicy(config['architecture']);optimizer=torch.optim.Adam(model.parameters(),lr=learning_rate)
    curve=[];best_score=float('inf');best_weights=deepcopy(model.state_dict());start_epoch=0
    if resume:
        if read(folder/'configuration.json')!=config:raise ValueError('Resume configuration mismatch')
        pointer=read(folder/'latest.json')
        if sha(pointer['path'])!=pointer['sha256']:raise ValueError('Resume checkpoint hash mismatch')
        saved,state=read_checkpoint(pointer['path'])
        if saved['config']!=config:raise ValueError('Resume snapshot configuration mismatch')
        model.load_state_dict(state['model']);optimizer.load_state_dict(state['optimizer']);torch.set_rng_state(state['rng'])
        best_weights=state['best_model'];best_score=saved['best_validation_action_nll'];curve=saved['curve'];start_epoch=saved['epoch']
    else:
        if folder.exists():raise FileExistsError('Use a new output directory, or explicit resume')
        folder.mkdir(parents=True);write(folder/'configuration.json',config)
    selected=[r for r in data if r['split']=='train'];validation=[r for r in data if r['split']=='validation']
    if not any(r['loss_mask'] for r in selected) or not any(r['loss_mask'] for r in validation):raise ValueError('Training and independent validation eligible rows required')
    windows=sequence_windows(selected,sequence_length,burnin);started=time.monotonic();checkpoint=None;ran=0
    for epoch in range(start_epoch,epochs):
        model.train();order=np.random.default_rng(seed+epoch).permutation(len(windows));losses=[]
        for offset in range(0,len(order),batch_size):
            chunk=[windows[int(i)] for i in order[offset:offset+batch_size]];batch=_tensor_batch(chunk,variant,sequence_length)
            initial=_initial_hidden(model,chunk,variant);out,_=model(batch['x'],initial)
            total,parts=loss(model,out,batch,world_weight,.1,.1,play_weight)
            optimizer.zero_grad(set_to_none=True);total.backward();nn.utils.clip_grad_norm_(model.parameters(),1.);optimizer.step();losses.append(parts)
        model.eval();score=evaluate(model,validation,'validation');value=score['action_nll']
        if value is not None and value<best_score:best_score=value;best_weights=deepcopy(model.state_dict())
        record={'epoch':epoch+1,'training_batches':len(losses),'training_loss':{k:float(np.mean([x[k] for x in losses])) for k in losses[0]},
            'validation':score};curve.append(record);checkpoint=save_checkpoint(folder,model,optimizer,config,epoch+1,curve,best_score,best_weights);ran+=1
        print(json.dumps({'variant':variant,'seed':seed,'epoch':epoch+1,'train_loss':record['training_loss'],'validation_action_nll':value},ensure_ascii=False),flush=True)
        if stop_after_epochs is not None and ran>=stop_after_epochs:break
    if checkpoint is None:checkpoint=local(read(folder/'latest.json')['path'])
    policy=StatefulPolicy.load(checkpoint,meta['version_fingerprint_sha256'],meta['observation_profile'])
    report={'schema':'phase3-small-training-report.v1','created_utc':now(),'configuration':reference(folder/'configuration.json'),
        'checkpoint':reference(checkpoint),'parameter_count':sum(p.numel() for p in model.parameters()),'completed_epochs':len(curve),
        'planned_epochs':epochs,'completed':len(curve)==epochs,'resumed':resume,'elapsed_this_invocation_seconds':time.monotonic()-started,
        'dataset_counts':dict(Counter(r['split'] for r in data)),'training_windows':len(windows),
        'training_rows_per_epoch':len(selected),'test_evaluated':False,'best_validation':evaluate(policy.model,data,'validation'),
        'real_environment_steps_generated':0,'imagined_steps':0,'native_closed_loop_status':'not_run_here',
        'limitations':['No evidence of policy strength from BC loss','Fair observation excludes all non-tower entities until visibility is proven',
            'World auxiliary head is one-step supervision, not validated open-loop dynamics or a planner',
            'Grid affordability mask does not prove every position legal','No test-based selection; recurrent burn-in truncates earlier context during training']}
    write(folder/'report.json',report);return report


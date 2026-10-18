"""Small deterministic short-horizon model with genuinely open-loop validation.

Public aggregate targets only in this first experiment. Object-level dynamics
are unavailable in the fair profile, so passing aggregate MSE cannot authorize
formal imagined policy optimization. No pixel decoder or native fork is claimed.
"""
from collections import defaultdict
import time
import torch
from torch import nn
from .common import digest,local,read,reference,rows,write
from .actions import DECK

HORIZONS=(1,2,4)  # 0.5,1,2 seconds at the frozen 10tick control interval.

def current_target(row):
    hud=row['hud'];return [hud[1]]+[hud[60+6*i+2] for i in range(6)]+[hud[3],hud[4]]

def simple_step(state,row,cost_by_card,action=None):
    state=list(state);action=row['action_index'] if action is None else action
    dt=row['transition_dt_ticks']
    # Validated first Default timeline rate only; phase transitions are masked
    # by the caller instead of importing an old simulator's timeline.
    gain=178*dt/100000
    # Condition on the declared executed own action; never inspect future HUD.
    cost=cost_by_card.get(row.get('card_id'),0.) if action else 0.
    state[0]=max(0.,min(1.,state[0]-cost+gain));return state

class ShortModel(nn.Module):
    def __init__(self):
        super().__init__();self.gru=nn.GRUCell(9+5+1,32);self.head=nn.Linear(32,9)
    def forward(self,state,action,dt,hidden=None):
        onehot=torch.nn.functional.one_hot(action,5).float()
        h=self.gru(torch.cat((state,onehot,dt.unsqueeze(-1)),dim=-1),hidden)
        return torch.clamp(state+torch.tanh(self.head(h))*.15,0,1),h

def load_groups(metadata):
    meta=read(metadata)
    if reference(meta['rows']['path'])!=meta['rows']:raise ValueError('Dataset rows hash changed')
    groups=defaultdict(list)
    for row in rows(meta['rows']['path']):groups[(row['split'],row['episode_id'],row['native_owner'])].append(row)
    return meta,{key:sorted(value,key=lambda r:r['tick']) for key,value in groups.items()}

def measure(model,groups,split,cost_by_card,read_future=False):
    accum=defaultdict(lambda:[0.,0,0.,0.,0.,0]);failures=[]
    model.eval()
    with torch.no_grad():
        for (s,episode,owner),sequence in groups.items():
            if s!=split:continue
            for index,start in enumerate(sequence):
                if index+max(HORIZONS)>len(sequence):continue
                if not all(start['hud'][60+6*i+3] for i in range(6)):continue
                state=torch.tensor([current_target(start)],dtype=torch.float32);h=None
                persistence=state[0].tolist();linear=list(persistence)
                for offset in range(max(HORIZONS)):
                    row=sequence[index+offset]
                    # Only strictly inside the first source-verified Default
                    # timeline interval, not its unproved switch boundary.
                    if row['transition_dt_ticks']!=10 or row['tick']+10>=2400:break
                    action=torch.tensor([row['action_index']]);dt=torch.tensor([row['transition_dt_ticks']/20])
                    # Actions are declared actual jointly interacting teacher actions.
                    # No future observations enter this open-loop transition.
                    if read_future:state=torch.tensor([current_target(row)])
                    state,h=model(state,action,dt,h);linear=simple_step(linear,row,cost_by_card)
                    horizon=offset+1
                    if horizon not in HORIZONS:continue
                    actual=row['targets']['world'];mask=row['targets']['world_mask'];pred=state[0].tolist()
                    valid=[i for i,v in enumerate(mask) if v]
                    model_error=sum((pred[i]-actual[i])**2 for i in valid)/len(valid)
                    persist_error=sum((persistence[i]-actual[i])**2 for i in valid)/len(valid)
                    linear_error=sum((linear[i]-actual[i])**2 for i in valid)/len(valid)
                    tower_valid=[i for i in range(1,7) if mask[i]]
                    if not tower_valid:continue
                    tower_error=sum((pred[i]-actual[i])**2 for i in tower_valid)/len(tower_valid)
                    baseline_tower=sum((persistence[i]-actual[i])**2 for i in tower_valid)/len(tower_valid)
                    bucket=accum[horizon];bucket[0]+=model_error;bucket[1]+=1;bucket[2]+=persist_error;bucket[3]+=linear_error;bucket[4]+=tower_error;bucket[5]+=baseline_tower
                    if len(failures)<10 and model_error>linear_error*2+.001:
                        failures.append({'episode':episode,'owner':owner,'tick':start['tick'],'seconds':horizon*.5,'predicted':pred,'actual':actual,'linear_baseline':linear})
    return {str(h*.5):{'mse':v[0]/v[1] if v[1] else None,'samples':v[1],
        'persistence_mse':v[2]/v[1] if v[1] else None,'resource_rule_mse':v[3]/v[1] if v[1] else None,
        'tower_mse':v[4]/v[1] if v[1] else None,'baseline_tower_mse':v[5]/v[1] if v[1] else None} for h,v in accum.items()},failures

def train(metadata,output,seed=1,epochs=20):
    folder=local(output)
    if folder.exists():raise FileExistsError('Immutable world-model experiment already exists')
    folder.mkdir(parents=True);torch.set_num_threads(2);torch.manual_seed(seed)
    meta,groups=load_groups(metadata)
    config={'seed':seed,'epochs':epochs,'dataset':reference(metadata),'horizons_seconds':[.5,1.,2.],
        'gate_predeclared':'At least2 horizons below both baselines; tower errors must not degrade; object/death/action-ranking coverage also required',
        'source':reference('royale_phase3/world.py'),'scope':'Fair public aggregate state; no entity visibility proof, no object death/creation supervision',
        'opponent_assumption':'Fixed public teacher pool; actual own actions conditioned for offline prediction; future opposing commands never input',
        'training':'teacher-forced contiguous8-step sequences with recurrent state, deterministic GRUCell32; open-loop validation1/2/4steps',
        'timing_scope':'Only strictly before native tick2400, source-verified first Default interval178raw/tick; switch boundary excluded',
        'selection':'Last frozen epoch; no test-driven tuning'}
    write(folder/'configuration.json',config)
    training=[r for (s,_,_),seq in groups.items() if s=='train' for r in seq if r['loss_mask'] and r['tick']+r['transition_dt_ticks']<2400]
    if not training:raise ValueError('No actual training rows')
    model=ShortModel();optimizer=torch.optim.Adam(model.parameters(),lr=.003);curve=[];began=time.monotonic()
    generator=torch.Generator().manual_seed(seed)
    # Learn from real contiguous eight-step teacher-forced sequences, with a
    # recurrent hidden state and no sequences crossing independent games/seats.
    windows=[];cost_by_card={}
    for (split,_,_),sequence in groups.items():
        if split!='train':continue
        for row in sequence:
            if row['action_index']:
                cost_by_card[row['card_id']]=row['hud'][8+13*(row['action_index']-1)+10]
        for start in range(0,len(sequence)-7,8):
            window=sequence[start:start+8]
            if all(r['loss_mask'] and r['transition_dt_ticks']==10 and r['tick']+10<2400
                   and all(r['hud'][60+6*i+3] for i in range(6)) for r in window):windows.append(window)
    if not windows:raise ValueError('No contiguous eight-step native sequences')
    state=torch.tensor([[current_target(r) for r in window] for window in windows])
    action=torch.tensor([[r['action_index'] for r in window] for window in windows])
    dt=torch.tensor([[r['transition_dt_ticks']/20 for r in window] for window in windows])
    target=torch.tensor([[r['targets']['world'] for r in window] for window in windows])
    mask=torch.tensor([[r['targets']['world_mask'] for r in window] for window in windows])
    for epoch in range(epochs):
        model.train();total=0.;count=0
        for ids in torch.randperm(len(windows),generator=generator).split(16):
            hidden=None;predictions=[]
            for step in range(8):
                predicted,hidden=model(state[ids,step],action[ids,step],dt[ids,step],hidden)
                predictions.append(predicted)
            predicted=torch.stack(predictions,1)
            loss=((predicted-target[ids]).square()*mask[ids]).sum()/mask[ids].sum().clamp_min(1)
            optimizer.zero_grad();loss.backward();optimizer.step();total+=loss.item()*len(ids);count+=len(ids)
        curve.append({'epoch':epoch+1,'train_mse':total/count})
    torch.save(model.state_dict(),folder/'weights.pt');write(folder/'curve.json',curve)
    validation,failures=measure(model,groups,'validation',cost_by_card,False);teacher_forced,_=measure(model,groups,'validation',cost_by_card,True)
    metric_pass=sum(v['mse']<min(v['persistence_mse'],v['resource_rule_mse']) and v['tower_mse']<=v['baseline_tower_mse'] for v in validation.values() if v['samples'])>=2
    report={'schema':'phase3-world-prediction.v1','status':'experiment_run','checkpoint':reference(folder/'weights.pt'),
        'configuration':reference(folder/'configuration.json'),'training_rows':len(training),'wall_seconds':time.monotonic()-began,
        'validation_open_loop':validation,'validation_teacher_forced':teacher_forced,'aggregate_prediction_gate_pass':metric_pass,
        'formal_imagination_authorized':False,'formal_gate_reason':'Entity visibility/dynamics, death/creation and native action-ranking validation are missing; aggregate metrics alone cannot authorize imagination',
        'test_evaluated':False,'native_fork_used':False,'real_native_steps_added':0,'imagined_actor_critic_steps':0,
        'training_windows':len(windows),'training_sequence_seconds':4.,'resource_rule_costs_from_train_only':cost_by_card,
        'open_loop_conditioning':'Actual future OWN action indices/card identities and fixed dt; no future observations or opponent commands',
        'prediction_scope':'Public aggregate9 fields only; entity/death/creation modeling not implemented'}
    write(folder/'report.json',report);write(folder/'failures.json',failures);return report

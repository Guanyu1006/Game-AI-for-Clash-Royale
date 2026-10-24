"""Real NumPy optimization from reviewed arrays; original model/history stay unchanged."""
import time
import sys
import hashlib
import json
from tools.cloud.workspace import ROOT,local,read,write,manifest,output,sha,verify_assets

SCHEMA='cloud-numeric-checkpoint.v1'

def canonical_hash(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False,allow_nan=False).encode()).hexdigest()

def load_checkpoint(path):
    """Read a cloud-only checkpoint; this does not certify native legality."""
    import numpy as np
    meta=read(path)
    if meta.get('schema')!=SCHEMA or meta.get('checkpoint_sha256')!=canonical_hash({k:v for k,v in meta.items() if k!='checkpoint_sha256'}):
        raise ValueError('Cloud checkpoint metadata integrity failed')
    weights=local(meta['weights']['path'])
    if weights.stat().st_size>100*1024*1024 or sha(weights)!=meta['weights']['sha256']:
        raise ValueError('Cloud checkpoint weights hash/size mismatch')
    with np.load(weights,allow_pickle=False) as saved:
        parameters={key:saved[key].copy() for key in saved.files}
    width=meta['architecture']['input_width'];hidden=meta['architecture']['hidden']
    shapes={'w1':(width,hidden),'b1':(hidden,),'ws':(hidden,5),'bs':(5,),'wx':(hidden,2),'bx':(2,)}
    if set(parameters)!=set(shapes):raise ValueError('Unexpected cloud checkpoint arrays')
    for key,value in parameters.items():
        if value.shape!=shapes[key] or value.dtype!=np.float32 or not np.isfinite(value).all():
            raise ValueError('Invalid cloud checkpoint tensor: '+key)
    return parameters,meta

def _metrics(parameters,arrays,bias=0.):
    import numpy as np
    from royale_phase2.learning import forward,decode_policy_output
    x,y,xy,masks=arrays;_,p,pos=forward(parameters,x,masks)
    selected,points=decode_policy_output(p,pos,masks,play_bias=bias)
    play=y!=0;pred=selected!=0;matched=play&pred
    correct_card=matched&(selected==y)
    distance=np.linalg.norm(points-np.rint(xy*np.asarray([17999,31999])),axis=1)
    return {'rows':len(y),'accuracy':float((selected==y).mean()),'actual_play':int(play.sum()),
        'predicted_play':int(pred.sum()),'same_row_play_precision':float(matched.sum()/pred.sum()) if pred.any() else None,
        'same_row_play_recall':float(matched.sum()/play.sum()) if play.any() else None,
        'slot_accuracy_on_predicted_and_actual_play':float((selected[matched]==y[matched]).mean()) if matched.any() else None,
        'position_on_predicted_and_actual_play':{'count':int(matched.sum()),'mean_native_distance':float(distance[matched].mean()) if matched.any() else None},
        'position_when_slot_correct':{'count':int(correct_card.sum()),'mean_native_distance':float(distance[correct_card].mean()) if correct_card.any() else None},
        'recorded_mask_valid_rate':float(masks[np.arange(len(y)),selected].mean()),
        'prediction_sha256':canonical_hash({'selected':selected.tolist(),'points':points.tolist()}),
        'scope':'Saved natural-time numeric rows; same-row metrics, no native action consumption or game strength claim'}

def evaluate(checkpoint,split='validation',destination=None):
    import numpy as np
    if split not in ('train','validation'):raise ValueError('Cloud cache evaluation supports train/validation only')
    parameters,meta=load_checkpoint(checkpoint)
    source=meta['training_data']['arrays']
    if sha(local(source['path']))!=source['sha256']:raise ValueError('Training cache changed')
    with np.load(local(source['path']),allow_pickle=False) as saved:
        arrays=tuple(saved[split+'_'+key].copy() for key in ('x','target','xy','masks'))
    report={'schema':'cloud-numeric-evaluation.v1','split':split,'checkpoint':{'path':str(local(checkpoint).relative_to(ROOT)),'sha256':sha(local(checkpoint))},
        'metrics':_metrics(parameters,arrays,meta['inference_play_bias']),'test_evaluated':False,
        'native_rpc_calls':0,'fresh_legality_revalidated':False}
    target=local(destination) if destination else output('numeric-evaluation')/'report.json'
    if target.exists():raise FileExistsError('Use a new evaluation output')
    write(target,report);report['report_path']=str(target.relative_to(ROOT));return report

def train(epochs=2,destination=None):
    if type(epochs) is not int or not 1<=epochs<=1000:raise ValueError('epochs must be1..1000')
    import numpy as np
    from royale_phase2.learning import init_parameters,loss_and_gradients,forward,decode_policy_output
    verify_assets();m=manifest()['numeric'];meta=read(m['metadata']);cfg=meta['training_configuration']
    folder=local(destination) if destination else output('numpy-bc')
    if folder.exists():raise FileExistsError('Use a new run directory')
    folder.mkdir(parents=True);started=time.monotonic()
    with np.load(local(m['arrays']),allow_pickle=False) as a:
        arrays={s:tuple(a[s+'_'+n].copy() for n in ('x','target','xy','masks')) for s in ('train','validation')}
    x,y,xy,masks=arrays['train'];parameters=init_parameters(x.shape[1],meta['architecture']['hidden'],cfg['seed'])
    rng=np.random.default_rng(cfg['seed']);moment={k:np.zeros_like(v) for k,v in parameters.items()};variance={k:np.zeros_like(v) for k,v in parameters.items()}
    weights=np.asarray(cfg['class_weights'],dtype=np.float32);curve=[];step=0;best=None;best_loss=float('inf')
    for epoch in range(1,epochs+1):
        permutation=rng.permutation(len(x));total=0.
        for start in range(0,len(x),cfg['batch_size']):
            chosen=permutation[start:start+cfg['batch_size']]
            loss,grad=loss_and_gradients(parameters,x[chosen],y[chosen],xy[chosen],masks[chosen],class_weights=weights)
            total+=loss*len(chosen);step+=1;norm=sum(float(np.square(v).sum()) for v in grad.values())**.5;scale=min(1.,5/max(norm,1e-12))
            for key in parameters:
                g=grad[key]*scale;moment[key]=.9*moment[key]+.1*g;variance[key]=.999*variance[key]+.001*g*g
                parameters[key]-=cfg['learning_rate']*(moment[key]/(1-.9**step))/(np.sqrt(variance[key]/(1-.999**step))+1e-8)
        validation_loss,_=loss_and_gradients(parameters,*arrays['validation'],class_weights=weights)
        curve.append({'epoch':epoch,'train_loss':total/len(x),'validation_loss':validation_loss})
        if validation_loss<best_loss:best_loss=validation_loss;best={k:v.copy() for k,v in parameters.items()}
        write(folder/'progress.json',{'epochs_done':epoch,'planned':epochs,'curve':curve})
        print(curve[-1],flush=True)
    np.savez_compressed(folder/'weights.npz',**best)
    vx,vy,vxy,vmask=arrays['validation'];_,p,pos=forward(best,vx,vmask)
    selected,_=decode_policy_output(p,pos,vmask,play_bias=0.)
    play=vy!=0;pred=selected!=0;matched=(play&pred)
    report={'schema':'cloud-numeric-training.v1','epochs':epochs,'train_rows':len(x),'validation_rows':len(vy),
      'training_plays':int((y!=0).sum()),'validation_plays':int(play.sum()),'test_evaluated':False,
      'validation':{'unbiased_accuracy':float((selected==vy).mean()),'predicted_play':int(pred.sum()),
        'same_row_play_precision':float(matched.sum()/pred.sum()) if pred.any() else None,
        'same_row_play_recall':float(matched.sum()/play.sum()) if play.any() else None},
      'curve':curve,'wall_seconds':time.monotonic()-started,'python':sys.version,'numpy':np.__version__,
      'weights':{'path':str((folder/'weights.npz').relative_to(ROOT)),'sha256':sha(folder/'weights.npz')},
      'scope':'Actual optimization from saved public numeric features/recorded masks. No new battle, legality re-audit, full native checkpoint or strength claim.'}
    checkpoint={'schema':SCHEMA,'architecture':meta['architecture'],'feature_configuration':meta['feature_configuration'],
        'inference_play_bias':0.,'weights':report['weights'],'epochs':epochs,
        'training_data':{'arrays':{'path':m['arrays'],'sha256':sha(local(m['arrays']))},
            'historical_metadata':{'path':m['metadata'],'sha256':sha(local(m['metadata']))},
            'split':'Preserved train/validation arrays from whole-source groups; test unused'},
        'model_selection':'Lowest validation loss','native_deployment_certified':False,
        'scope':'Cloud saved numeric cache; inference requires recorded public masks, no fresh native legality proof'}
    checkpoint['checkpoint_sha256']=canonical_hash(checkpoint);write(folder/'checkpoint.json',checkpoint)
    restored,restored_meta=load_checkpoint(folder/'checkpoint.json')
    if not all(np.array_equal(best[key],restored[key]) for key in best):raise ValueError('Numeric checkpoint roundtrip mismatch')
    report['checkpoint']={'path':str((folder/'checkpoint.json').relative_to(ROOT)),'sha256':sha(folder/'checkpoint.json')}
    report['checkpoint_roundtrip_exact']=True
    report['validation_decoded']=_metrics(restored,arrays['validation'],restored_meta['inference_play_bias'])
    write(folder/'report.json',report);return report

"""Real NumPy optimization from reviewed arrays; original model/history stay unchanged."""
import time
import sys
from tools.cloud.workspace import ROOT,local,read,write,manifest,output,sha,verify_assets

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
    write(folder/'report.json',report);return report

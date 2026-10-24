"""Small real training-to-evaluation smoke, with immutable logs for every step."""
import sys
from tools.cloud.workspace import ROOT,local,output,write
from tools.phase3_acceptance import dependency_inventory,run_check


def smoke(destination=None,epochs=2):
    if type(epochs) is not int or not 1<=epochs<=5:raise ValueError('Smoke epochs must be 1..5')
    folder=local(destination) if destination else output('development-smoke')
    if folder.exists():raise FileExistsError('Use a new smoke output directory')
    folder.mkdir(parents=True)
    relative=folder.relative_to(ROOT).as_posix();numpy_checkpoint=relative+'/numpy/checkpoint.json'
    gru_checkpoint=relative+f'/gru/epoch-{epochs:04d}.json'
    dependencies=dependency_inventory('research')
    report={'schema':'cloud-development-smoke.v1','status':'running','python':sys.executable,'epochs':epochs,
        'dependencies':dependencies,'checks':[],'test_evaluated':False,'native_rpc_calls':0,
        'native_runtime_verified':False,'scope':'Codespaces offline development only; actual small optimization, no policy strength claim'}
    plan=[
        ('doctor',['doctor'],lambda v:v['assets']['status']=='pass'),
        ('tests',['test'],lambda v:v['passed'] and not v['errors'] and not v['failures']),
        ('data-check',['data-check','--output',relative+'/data-check.json'],lambda v:v['saved_cache_usable']),
        ('numeric-train',['train','--epochs',str(epochs),'--output',relative+'/numpy'],lambda v:v['checkpoint_roundtrip_exact'] and not v['test_evaluated']),
        ('numeric-reload',['infer','--checkpoint',numpy_checkpoint],lambda v:len(v['results'])==3 and v['native_rpc_calls']==0),
        ('numeric-validation',['evaluate','--checkpoint',numpy_checkpoint,'--output',relative+'/numeric-validation.json'],lambda v:v['split']=='validation' and not v['test_evaluated']),
        ('gru-train',['train-gru','--epochs',str(epochs),'--output',relative+'/gru'],lambda v:v['completed'] and not v['test_evaluated']),
        ('gru-reload',['infer-gru','--checkpoint',gru_checkpoint],lambda v:len(v['samples'])==8 and v['native_rpc_calls']==0),
        ('gru-validation',['evaluate-gru','--checkpoint',gru_checkpoint,'--output',relative+'/gru-validation.json'],lambda v:v['online_offline_exact_match'] and not v['test_evaluated']),
        ('saved-replay',['replay','--index','0'],lambda v:v['index']==0 and v['native_rpc_calls']==0),
        ('sandbox',['simulate','--seconds','30','--seed','20261020'],lambda v:v['done'] and v['native_rpc_calls']==0 and not v['official_fidelity_claim']),
    ]
    missing=[name for name,value in dependencies.items() if not value['available']]
    report['missing_dependencies']=missing
    if missing:
        report.update(status='failed',reason='Run bash tools/cloud/bootstrap.sh --ml --research before smoke')
    else:
        for label,arguments,predicate in plan:
            def validate(value):
                if not predicate(value):raise ValueError('Unexpected result for '+label)
            check=run_check(folder,label,['tools/cloud/workspace.py',*arguments],timeout=180,validate=validate)
            report['checks'].append(check);write(folder/'report.json',report)
            print({'step':label,'status':check['status']},flush=True)
            if check['status']!='passed':
                report.update(status='failed',failed_step=label);break
        else:report.update(status='passed',numpy_checkpoint=numpy_checkpoint,gru_checkpoint=gru_checkpoint)
    report['not_run_steps']=[name for name,_,_ in plan if name not in {r['id'] for r in report['checks']}]
    report['report_path']=relative+'/report.json'
    report['recovery']='Keep this directory; fix the failed condition and rerun smoke with a new output. GRU training can resume explicitly using its original configuration.'
    write(folder/'report.json',report);return report

"""Recompute phase-two A-I gates from actual immutable artifacts; no RPC.

Missing experiments remain not_run. Existing reports are inputs to validation,
never sufficient evidence merely because they contain a passed flag.
"""
import argparse
from collections import Counter
from datetime import datetime, timezone
import json
from pathlib import Path
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from royale_phase2.runtime import configure,local
from royale_phase2.versioning import file_hash,canonical_hash,verify_manifest
from royale_phase2.dataset import write_json
from royale_phase2.determinism import compare_runs


class Incomplete(RuntimeError):pass


def read(path):return json.loads(local(path).read_text(encoding='utf-8-sig'))
def ref(path):return {'path':local(path).relative_to(ROOT).as_posix(),'sha256':file_hash(path)}
def checked(reference):
    if file_hash(reference['path'])!=reference['sha256']:raise AssertionError('Artifact changed: '+reference['path'])
    return local(reference['path'])
def jsonl(path):return [json.loads(line) for line in local(path).read_text(encoding='utf-8').splitlines() if line]


def episode(path):
    result=read(path)
    for artifact in result['files'].values():checked(artifact)
    return result


def native_trace(folder,rendered=False):
    from phase2_offline_smoke import validate_native_trace
    return validate_native_trace(local(folder),rendered=rendered)


def gate_a(args,version):
    identity=read(version['manifest']['runtime_identity_receipt'])
    for item in identity['source_evidence']:checked(item)
    assert identity['libg_sha256'] in {a['sha256'] for a in version['assets'] if a['role']=='native_library'}
    assert identity['content_version']==version['manifest']['content_version']
    source='outputs/phase2/replays/development/ordinary-feedback-fixed/episode.json'
    result=episode(source);assert result['native_execution'] and result['version_fingerprint']==version['fingerprint']
    trace=native_trace(local(source).parent)
    assert trace['actual_consumed_events']>0 and trace['terminal_tick']>90
    backend=local('royale_phase2/backend.py').read_text(encoding='utf-8')
    assert 'from royale_lab' not in backend and 'from royale_ai' not in backend
    return {'evidence':[ref(source),ref(version['manifest']['runtime_identity_receipt']),ref('royale_phase2/backend.py')],
            'actual_native_trace':trace,'claim':'Fixed native components and actual battle; no official trajectory identity claim'}


def gate_b(args,version):
    folder=local(args.offline);report=read(folder/'report.json')
    for artifact in report['artifacts']:checked(artifact)
    assert report['new_boot_id']!=report['old_boot_id']
    process=report['actual_qemu_process'];assert 'restrict=on' in json.dumps(process)
    positive=report['preparation_positive_control'];checked(positive)
    assert positive['record']['result']['status']==200
    for network in (report['isolated_network'],report['network_after_games']):
        assert network['checks']
        for item in network['checks']:
            result=item.get('result')
            assert result!='connected' and not (isinstance(result,dict) and 'status' in result)
    for name in ('replay_controller_guard','agent_controller_guard'):
        guard=read(checked(report[name]));assert guard['exit_code']==0 and guard['external_canary']['denied']
        assert any(e['outcome']=='denied_before_DNS_or_socket_connect' for e in guard['denied_attempts'])
    from phase2_offline_smoke import validate_replay,validate_agent
    replay=validate_replay(folder/'replay');agent=validate_agent(folder/'agent',report['checkpoint']['sha256'])
    return {'evidence':[ref(folder/'report.json')],'cold_boot_ids':[report['old_boot_id'],report['new_boot_id']],
            'actual_replay':replay,'actual_model_game':agent,'network_scope':'QEMU restricted guest + audited Python loopback socket guard; no host-wide firewall changes'}


def gate_c(args,version):
    frozen=read('data/phase2/frozen_manifest.json');formal={e['source_episode_id'] for e in frozen['entries'] if e['split']=='formal'}
    queue=read(args.formal_queue);jobs=queue['jobs'];assert len(formal)>=100 and {j['source_episode_id'] for j in jobs}==formal
    remaining=[j['source_episode_id'] for j in jobs if j['state']!='recorded']
    if remaining:raise Incomplete(f'{len(jobs)-len(remaining)}/{len(jobs)} formal episode records completed')
    records=[]
    for job in jobs:
        result=episode(checked(job['result']));assert result['source_episode_id']==job['source_episode_id']
        assert len(result['quality'])==5
        if result['quality']['runtime_replayable']=='pass':
            trace=native_trace(local(job['result']['path']).parent)
            assert result['native_execution'] and result['version_fingerprint']==version['fingerprint']
            assert trace['actual_consumed_events']==result['counts']['source_actions']==result['counts']['executed']
        else:assert result['failures']
        records.append(result)
    from royale_phase2.trajectories import load_export
    rows,_=load_export(args.export,profile='hard_e')
    strict_formal_ids=sorted({r['source_episode_id'] for r in rows}&formal)
    assert strict_formal_ids,'No evidence-qualified training subset from the formal denominator yet'
    action_counts=Counter();unknown_counts=Counter()
    for record in records:
        for key,value in record.get('counts',{}).items():
            if type(value) is int: action_counts[key]+=value
            elif value is None: unknown_counts[key]+=1
    summary={'formal_denominator':len(formal),'quality_dimensions':{k:dict(Counter(r['quality'][k] for r in records)) for k in records[0]['quality']},
             'action_counts':dict(action_counts),'unknown_action_counts':dict(unknown_counts),
             'strict_trainable_formal_source_ids':strict_formal_ids,
             'failure_reasons':dict(Counter(reason for r in records for reason in r['failures'])),
             'evidence':[ref(args.formal_queue),ref('data/phase2/frozen_manifest.json'),ref(local(args.export)/'export_manifest.json')]}
    return summary


def gate_d(args,version):
    results=[];references=[];seen_sources=set()
    for path in args.repeatability:
        report=read(path);references.append(ref(path))
        for item in report['episodes']:
            source_id=item.get('source_episode_id')
            if source_id in seen_sources:
                results.append({'source_episode_id':source_id,'status':'fail',
                                'runs':item.get('runs',[]),
                                'reason':'duplicate source_episode_id across repeatability reports'})
                continue
            seen_sources.add(source_id)
            try:
                run_refs=item.get('runs')
                if not isinstance(run_refs,list) or len(run_refs)<3:
                    raise ValueError('fewer than three independent reset runs')
                paths=[]
                for run_ref in run_refs:
                    run_path=str(checked(run_ref))
                    episode(run_path)
                    paths.append(run_path)
                actual=compare_runs(paths)
            except (AssertionError,KeyError,ValueError,OSError,IndexError,TypeError) as error:
                actual={'source_episode_id':source_id,'status':'fail','runs':item.get('runs',[]),'reason':str(error)}
            results.append(actual)
    passed_ids={r.get('source_episode_id') for r in results if r.get('status')=='pass'}
    assert len(passed_ids)>=10,f'Only {len(passed_ids)} sources passed independent three-reset comparison'
    index=read('outputs/phase2/fixtures/mechanism_coverage.json');names=set()
    for item in index['mechanisms']:
        assert item['fixture'] not in names;names.add(item['fixture'])
        record=read(checked(item['selected_report']))
        for artifact in item['evidence']:assert checked(artifact).stat().st_size>0
        assert item['status']=='pass' and record.get('status')=='pass'
        assert item['truth_class']=='internal_native_regression' and item['independent_reference']=='no_reference'
    assert len(names)>=20
    references.append(ref('outputs/phase2/fixtures/mechanism_coverage.json'))
    return {'evidence':references,'independent_reset_sources_passed':len(passed_ids),'actual_repeat_runs':sum(len(r.get('runs',[])) for r in results),
            'internal_native_mechanisms':len(names),'independent_official_trajectory_references':0,
            'scope':'Every exposed semantic state/event/queue field retained except documented nonsemantic counter origins'}


def gate_e(args,version):
    from royale_phase2.trajectories import load_export,audit_subspace_proof
    rows,manifest=load_export(args.export,profile='hard_e');assert len(rows)>=10000
    groups={split:{r['leakage_group'] for r in rows if r['learning_split']==split} for split in ('train','validation','test')}
    assert not groups['train']&groups['validation'] and not groups['train']&groups['test'] and not groups['validation']&groups['test']
    for split in ('train','validation','test'):
        assert any(r['learning_split']==split and r['action']['kind']=='play_card' for r in rows),f'No genuine spell label in {split}'
    assert len({r['decision_id'] for r in rows})==len(rows),'Repeated acquisition duplicated decisions'
    assert {r['version_fingerprint_sha256'] for r in rows}=={version['fingerprint']}
    proof=audit_subspace_proof('data/phase2/action_subspace_proof.json',version['fingerprint']);assert proof['status']=='verified_local_rule_evidence'
    return {'evidence':[ref(local(args.export)/'export_manifest.json'),manifest['parquet'],proof['proof']],
            'actual_re_read_valid_decisions':len(rows),'by_split':manifest['by_split'],
            'distributions':manifest['before_after_distributions'],'official_gold_rows':0,
            'scope':'Strict operational reconstruction; WAIT plus two ordinary all-arena spells. Sampled WAIT is not explicit human waiting.'}


def gate_f(args,version):
    from royale_phase2.viewer import compare_anchors,inspect_mp4,validate_viewer_evidence
    folder=local(args.viewer);report=read(folder/'report.json')
    evidence_validation=validate_viewer_evidence(folder)
    comparison=compare_anchors(folder/'headless/anchors.json',folder/'rendered/anchors.json','outputs/phase2/acceptance_checks/viewer_anchors.json')
    assert len(comparison['anchors'])==5 and all(r['equal'] for r in comparison['anchors'])
    controls=jsonl(folder/'rendered/controls.jsonl');by={c['command']:c for c in controls}
    assert by['step']['after_tick']-by['step']['before_tick']==1 and by['advance']['after_tick']-by['advance']['before_tick']==9
    assert sum(c['command']=='play' for c in controls)>=2 and 'pause' in by
    wire=jsonl(folder/'rendered/wire.jsonl');pause_tick=by['pause_check']['after_tick']
    captures=[r for r in wire if r.get('command','').startswith('minimal ') and r.get('response',{}).get('tick')==pause_tick]
    assert len(captures)>=2 and captures[-2]['response']==captures[-1]['response'],'Pause raw semantics differ'
    frames=jsonl(folder/'rendered/frames/index.jsonl');assert len(frames)>5
    for frame in frames:
        assert checked(frame).read_bytes().startswith(b'\x89PNG\r\n\x1a\n')
    assert frames[0]['tick']==90 and frames[-1]['tick']==report['final_public_state']['tick']
    videos=list((folder/'rendered').glob('*.mp4'));assert any(inspect_mp4(p.read_bytes())['valid_mp4'] for p in videos)
    trace=native_trace(folder/'rendered',rendered=True);assert trace['actual_consumed_events']==report['final_public_state']['source_action_count']
    return {'evidence':[ref(folder/'report.json'),ref(folder/'rendered/anchors.json'),ref(folder/'headless/anchors.json')],
            'raw_binding_validation':evidence_validation,
            'semantic_anchors':comparison['anchors'],'actual_native_frames':len(frames),'complete_source_actions':trace['actual_consumed_events'],
            'controls_recomputed_from_records':True,'independent_official_fidelity_claim':False}


def gate_g(args,version):
    from royale_phase2.learning import NumpyBCPolicy
    policy=NumpyBCPolicy.load(args.checkpoint,version_fingerprint=version['fingerprint']);assert policy.metadata['counts_toward_phase2_G']
    report=read(local(args.agent)/'report.json');assert report['checkpoint']['file_sha256']==file_hash(args.checkpoint)
    assert len(report['games'])>=10 and len({g['seed'] for g in report['games']})==len(report['games'])
    total=0
    for index,game in enumerate(report['games']):
        folder=local(args.agent)/f'game-{index:03d}';trace=native_trace(folder)
        raw=jsonl(folder/'states.jsonl');assert raw[-1]['state']['finalized'] and trace['terminal_tick']==raw[-1]['state']['tick']
        assert not game.get('truncated') and not game.get('error')
        decisions=jsonl(folder/'decisions.jsonl');plays=[r for r in decisions if r['model_prediction']['kind']=='play_card']
        feedback=read(folder/'execution_feedback.json')['actions'];model=[r for r in feedback if ':model:' in r['request_id']]
        assert plays and len(plays)==len(model) and all(r['card_consumed'] and r['card_event'] in trace['consumed_events'] for r in model)
        total+=len(model)
    return {'evidence':[ref(args.checkpoint),policy.metadata['weights'],ref(local(args.agent)/'report.json')],
            'complete_native_model_games':len(report['games']),'actual_model_card_consumptions':total,
            'metrics':policy.metadata['metrics'],'scope':'Saved restricted-action BC; heuristic opponent; no strong-agent claim'}


def gate_h(args,version):
    queue=read('outputs/phase2/batches/recovery-verified/queue.json');assert len(queue['jobs'])==1
    job=queue['jobs'][0];attempts=job['attempts'];assert job['state']=='recorded' and len(attempts)==2
    assert not (local(attempts[0]['folder'])/'episode.json').exists() and attempts[0]['partial_result_accepted'] is False
    partial=native_trace_partial(local(attempts[0]['folder'])/'wire.jsonl');assert partial>0
    complete=episode(checked(job['result']));assert complete['quality']['runtime_replayable']=='pass'
    first=jsonl(local(attempts[0]['folder'])/'states.jsonl')[0]['raw_capture']['generation']
    second=jsonl(complete['files']['states.jsonl']['path'])[0]['raw_capture']['generation'];assert first!=second
    benchmark=read(args.benchmark);assert len(benchmark['configurations'])>=2
    hashes=[]
    from royale_phase2.determinism import canonical_run
    for config in benchmark['configurations']:
        result,packet=canonical_run(checked(config['episode']));hashes.append(canonical_hash(packet))
        assert config['wall_seconds_including_process_start_reset_actor_io_and_feedback']>result['wall_seconds']>0
        assert result['resources']['after']['available'] and config['qemu_after']['WorkingSet64']>0
    assert len(set(hashes))==1
    core=read('outputs/phase2/core_environment/report.json')
    for item in core['checks']:assert item['returncode']==0 and file_hash(item['log'])==item['log_sha256']
    return {'evidence':[ref('outputs/phase2/batches/recovery-verified/queue.json'),ref(args.benchmark),ref('outputs/phase2/core_environment/report.json')],
            'actual_partial_native_events':partial,'resume_distinct_generations':[first,second],
            'actual_workers':1,'configurations':benchmark['configurations']}


def native_trace_partial(path):
    return len({tuple(p) for r in jsonl(path) for p in r.get('response',{}).get('plays',[])})


def gate_i(args,version):
    baseline=read('outputs/phase2/baseline_regression/report.json')
    assert all(r['returncode']==0 for r in baseline['results'])
    snapshot=read('outputs/phase2/baseline_snapshot.json')
    for artifact in snapshot['artifacts']:checked(artifact)
    docs=['QUICKSTART.md','TUTORIAL.md','NATIVE_AUDIT.md','NATIVE_ENGINE_GUIDE.md','SECONDARY_AUDIT.md','DATA_AUDIT.md','TRAJECTORIES_AND_BC.md','VIEWER.md','OFFLINE.md','STATUS.md','BLOCKERS.md']
    for name in docs:assert len(local('phase2/'+name).read_text(encoding='utf-8-sig'))>100
    cli=read('outputs/phase2/cli/report.json')
    assert cli['checks'] and all(r['returncode']==0 for r in cli['checks'])
    for item in cli['checks']:checked(item['log'])
    return {'evidence':[ref('outputs/phase2/baseline_regression/report.json'),ref('outputs/phase2/cli/report.json')]+[ref('phase2/'+n) for n in docs],
            'baseline_unchanged_artifact_count':len(snapshot['artifacts']),'actual_cli_checks':len(cli['checks'])}


def main():
    configure();p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--formal-queue',default='outputs/phase2/batches/formal100-v1/queue.json')
    p.add_argument('--repeatability',action='append')
    p.add_argument('--export',default='outputs/phase2/learning_pipeline/export')
    p.add_argument('--checkpoint',default='outputs/phase2/learning_pipeline/model/checkpoint.json')
    p.add_argument('--agent',default='outputs/phase2/native/agent_eval/acceptance-001')
    p.add_argument('--offline',default='outputs/phase2/offline/acceptance-001')
    p.add_argument('--viewer',default='outputs/phase2/viewer/acceptance-001')
    p.add_argument('--benchmark',default='outputs/phase2/performance/whole-pipeline/report.json')
    p.add_argument('--output',default='outputs/phase2/acceptance.json');args=p.parse_args()
    args.repeatability=args.repeatability or ['outputs/phase2/repeatability/learning16/report.json','outputs/phase2/repeatability/formal2/report.json']
    report={'schema':'royale-phase2-acceptance.v1','generated_utc':datetime.now(timezone.utc).isoformat(),
            'command':[sys.executable,*sys.argv],'checks':{},'all_required_passed':False,
            'independent_official_trajectory_fidelity_claim':False}
    version=verify_manifest('data/phase2/version_manifest.json');report['version_fingerprint']=version['fingerprint']
    for letter in 'abcdefghi':
        try:item={'status':'pass',**globals()['gate_'+letter](args,version)}
        except (FileNotFoundError,Incomplete) as error:item={'status':'not_run','reason':str(error)}
        except Exception as error:item={'status':'fail','reason':f'{type(error).__name__}: {error}','traceback':traceback.format_exc()}
        report['checks'][letter.upper()]=item;write_json(local(args.output),report)
        print(json.dumps({'gate':letter.upper(),'status':item['status'],'reason':item.get('reason')},ensure_ascii=False),flush=True)
    report['all_required_passed']=all(c['status']=='pass' for c in report['checks'].values())
    write_json(local(args.output),report)
    if not report['all_required_passed']:raise SystemExit(2)


if __name__=='__main__':main()

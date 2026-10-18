"""Load a saved NumPy BC checkpoint in a new process and play actual native games.

--check-boundaries never connects to RPC. Real evaluation requires the exclusive
local probe window; every failure/watchdog/WAIT-only game remains a failure.
"""
import argparse
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from royale_phase2.agent_env import NativeAgentEnv, validate_policy_action, gym_observation, public_heuristic, REWARD_VERSION
from royale_phase2.learning import NumpyBCPolicy
from royale_phase2.trajectories import audit_subspace_proof, subspace_mask, file_sha
from royale_phase2.visibility import TowerAttestation,project_actor
from royale_phase2.versioning import verify_manifest


def write(path,value):
    path.parent.mkdir(parents=True,exist_ok=True);path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')


def boundary_checks(folder):
    version=verify_manifest('data/phase2/version_manifest.json')
    proof=audit_subspace_proof('data/phase2/action_subspace_proof.json',version['fingerprint'])
    source=ROOT/'outputs/phase2/fixtures/native-v1/subspace_positive_28000000_0_0_0/states.jsonl'
    raw=json.loads(source.read_text(encoding='utf-8').splitlines()[0]);actor=project_actor(raw,0,TowerAttestation.from_reset(raw))
    mask=subspace_mask(actor,True,proof);spell=next(c for c in actor['hand'] if c['card']==28000000)
    checks=[]
    for x,y in [(0,0),(17999,31999)]:
        selected=validate_policy_action(actor,mask,{'kind':'play_card','slot':spell['slot'],'card':spell['card'],'x':x,'y':y})
        assert selected['x']==x and selected['y']==y;checks.append(f'local valid boundary{x},{y}')
    invalid=[{'kind':'play_card','slot':spell['slot'],'x':-1,'y':0},
             {'kind':'play_card','slot':spell['slot'],'x':18000,'y':0},
             {'kind':'play_card','slot':spell['slot'],'x':0,'y':32000},
             {'kind':'play_card','slot':spell['slot'],'card':26000000,'x':0,'y':0},
             {'action_index':False,'x':0,'y':0}, {'kind':'activate_ability'}]
    for action in invalid:
        try:validate_policy_action(actor,mask,action)
        except ValueError:checks.append('local invalid action rejected')
        else:raise AssertionError(f'Invalid action passed: {action}')
    stopped=subspace_mask(actor,False,proof)
    try:validate_policy_action(actor,stopped,{'kind':'play_card','slot':spell['slot'],'x':0,'y':0})
    except ValueError:checks.append('command-not-ready action rejected')
    else:raise AssertionError('Command-ready gate bypassed')
    assert validate_policy_action(actor,mask,{'kind':'wait'})=={'kind':'wait'};checks.append('WAIT accepted')
    env=NativeAgentEnv();assert env.backend is None
    observation=gym_observation(actor,mask)
    if hasattr(env,'observation_space'):assert env.observation_space.contains(observation);checks.append('actual captured actor fits declared Gym observation space')
    try:env.step({'kind':'wait'})
    except RuntimeError:checks.append('step before reset rejected without communication')
    else:raise AssertionError('Step before reset accepted')
    match=env.default_match(12);assert match['cmd']==[] and match['evt']==[]
    opponent=public_heuristic(actor,0);assert opponent['kind'] in ('wait','play_card');checks.append('opponent uses supplied own/public actor only')
    env.close()
    report={'schema':'phase2-native-agent-boundaries.v1','passed':True,'native_rpc_connections':0,
            'source_native_capture':{'path':source.relative_to(ROOT).as_posix(),'sha256':file_sha(source)},
            'checks':checks,'scope':'Local validation and Gym spaces only; no new native rollout or model game'}
    write(folder/'boundary_report.json',report);print(json.dumps({'passed':True,'boundary_checks':len(checks),'rpc_connections':0}));return report


def evaluate(args,folder):
    started=time.monotonic();report={'schema':'phase2-native-agent-evaluation.v1','created_utc':datetime.now(timezone.utc).isoformat(),
        'games':[],'counts_toward_G':False,'all_games_passed':False,'fallback_used':False,
        'controller':'Saved NumPy BC argmax; no forced play, sampling override or source future events',
        'opponent':'Nonmodel public_heuristic: its own conservative HUD and public tower HP, highest-affordable fixed lane placements at declared interval',
        'reward_version':REWARD_VERSION,'action_subspace':'wait_or_arena_spell.v1',
        'limitations':['Two ordinary spells can leave the hand without other allowed cards to cycle them back; WAIT lockout is recorded',
                       'Opponent placement is a fixed heuristic; native refusals are logged, not labeled universally legal',
                       'No model-strength, official-rule fidelity or full-deck agent claim']}
    try:
        if args.offline_smoke:
            if args.episodes!=1:raise ValueError('--offline-smoke requires exactly one explicit episode')
        elif args.episodes<10:raise ValueError('Complete evaluation requires at least10 games; one cold-start smoke must explicitly use --offline-smoke')
        report.update(offline_smoke=args.offline_smoke,games_required_for_G=10)
        version=verify_manifest(args.manifest);checkpoint=(ROOT/args.checkpoint).resolve()
        if not checkpoint.is_relative_to(ROOT):raise ValueError('Checkpoint outside project')
        policy=NumpyBCPolicy.load(checkpoint,version_fingerprint=version['fingerprint'],allow_diagnostic=args.allow_diagnostic)
        report.update(checkpoint={'path':checkpoint.relative_to(ROOT).as_posix(),'file_sha256':file_sha(checkpoint),
                                  'metadata_sha256':policy.metadata['checkpoint_sha256'],'weights':policy.metadata['weights'],
                                  'profile':policy.metadata['profile']},version_fingerprint_sha256=version['fingerprint'],
                      backend_source_sha256=file_sha(ROOT/'royale_phase2/backend.py'),
                      agent_env_source_sha256=file_sha(ROOT/'royale_phase2/agent_env.py'),
                      evaluation_source_sha256=file_sha(Path(__file__)),configuration=vars(args))
        proof=audit_subspace_proof(args.proof,version['fingerprint'])
        if proof['status']!='verified_local_rule_evidence':raise ValueError('Valid current action-subspace proof required')
        report['action_proof']=proof
        seed_values = ([int(value) for value in args.seed_list.split(',') if value.strip()]
                       if args.seed_list else [args.seed + index for index in range(args.episodes)])
        if len(seed_values) != args.episodes or len(set(seed_values)) != len(seed_values):
            raise ValueError('--seed-list must contain exactly one unique integer per episode')
        report['selected_seeds'] = seed_values
        for index in range(args.episodes):
            game_folder=folder/f'game-{index:03d}';game_folder.mkdir(parents=True,exist_ok=True)
            game={'game_index':index,'owner':index%2,'seed':seed_values[index],'natural_terminal':False,
                  'truncated':False,'model_play_requests':0,'model_card_consumptions':0,'passed':False,
                  'model_decisions':0,'decisions_with_legal_spell':0,'rewards':[]}
            env=None;state_stream=None;wall=time.monotonic()
            try:
                env=NativeAgentEnv(port=args.port,owner=game['owner'],step_ticks=args.step_ticks,max_tick=args.max_tick,
                    opponent_interval_ticks=args.opponent_interval,proof=args.proof,version_manifest=args.manifest,
                    trace_path=game_folder/'wire.jsonl')
                reset_options={'level':args.level}
                if args.spell_first_deck:
                    # Legal evaluation fixture: keep the frozen eight-card deck
                    # but put the two verified ordinary spells first so the
                    # restricted actor can demonstrate a real native receipt.
                    match=env.default_match(game['seed'],args.level)
                    for owner in (0,1):
                        deck=match['battle'][f'deck{owner}']['sp']
                        spells=[card for card in deck if card['d'] in (28000000,28000001)]
                        remainder=[card for card in deck if card['d'] not in (28000000,28000001)]
                        match['battle'][f'deck{owner}']['sp']=spells+remainder
                    reset_options['match']=match
                observation,info=env.reset(seed=game['seed'],options=reset_options)
                state_stream=(game_folder/'states.jsonl').open('w',encoding='utf-8')
                state_stream.write(json.dumps({'oracle_debug_only':True,'state':env.backend.debug_state()},separators=(',',':'))+'\n');state_stream.flush()
                write(game_folder/'initialization.json',env.last_audit_step['reset_match'])
                game['gym_reset_observation_shapes']={k:list(v.shape) for k,v in observation.items()}
                with (game_folder/'decisions.jsonl').open('w',encoding='utf-8') as stream:
                    while True:
                        if time.monotonic()-wall>args.max_wall_seconds:
                            game.update(truncated=True,truncation_reason='wall_clock_watchdog');break
                        actor=env.actor_observation();mask=env.legal_mask()
                        if any(mask['values'][1:]):game['decisions_with_legal_spell']+=1
                        predicted=policy.predict(actor,legal_mask=mask)
                        game['model_decisions']+=1;game['model_play_requests']+=int(predicted['kind']=='play_card')
                        observation,reward,terminated,truncated,info=env.step(predicted)
                        state_stream.write(json.dumps({'oracle_debug_only':True,'state':env.backend.debug_state()},separators=(',',':'))+'\n');state_stream.flush()
                        row=deepcopy(env.last_audit_step)
                        row['model_prediction']=predicted;row['gym_step_contract']={'reward':reward,'terminated':terminated,'truncated':truncated,
                            'observation_shapes':{k:list(v.shape) for k,v in observation.items()},'reward_version':REWARD_VERSION}
                        stream.write(json.dumps(row,separators=(',',':'),allow_nan=False)+'\n');stream.flush()
                        game['rewards'].append(reward)
                        if terminated or truncated:
                            game.update(natural_terminal=terminated,truncated=truncated,truncation_reason=info.get('truncation_reason'),result=info['public_result']);break
                state_stream.close()
                feedback=env.backend.execution_feedback();write(game_folder/'execution_feedback.json',{'actions':feedback})
                model_rows=[r for r in feedback if ':model:' in r['request_id']]
                opponent_rows=[r for r in feedback if ':heuristic:' in r['request_id']]
                game['model_card_consumptions']=sum(r['card_consumed'] for r in model_rows)
                game['model_native_injected']=sum(r['native_injected'] for r in model_rows)
                game['opponent_card_consumptions']=sum(r['card_consumed'] for r in opponent_rows)
                game['opponent_native_failures']=sum(not r['native_injected'] or not r['card_consumed'] for r in opponent_rows)
                game['model_unconfirmed_plays']=sum(not r['card_consumed'] for r in model_rows)
                game['wait_lockout_fraction']=1-game['decisions_with_legal_spell']/max(1,game['model_decisions'])
                game['all_wait_model_failure']=game['model_play_requests']==0
                game['passed']=bool(game['natural_terminal'] and not game['truncated'] and game['model_card_consumptions']>0
                                    and game['model_unconfirmed_plays']==0
                                    and game['model_native_injected']==game['model_play_requests'])
                if not game['passed']:game['failure_reason']='No natural terminal or no actual model card consumption; actions were never forced'
                write(game_folder/'journal.json',env.backend.export_journal())
            except Exception as exc:
                game['error']=f'{type(exc).__name__}: {exc}';(game_folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
            finally:
                if state_stream is not None:state_stream.close()
                if env is not None:env.close()
                game['wall_seconds']=time.monotonic()-wall;write(game_folder/'report.json',game)
                report['games'].append(game);write(folder/'report.json',report)
                print(json.dumps({'game':index,'natural_terminal':game['natural_terminal'],'model_consumptions':game['model_card_consumptions'],
                                  'passed':game['passed'],'error':game.get('error')}),flush=True)
        report['counts']={'games':len(report['games']),'natural_terminal':sum(g['natural_terminal'] for g in report['games']),
                          'with_model_consumption':sum(g['model_card_consumptions']>0 for g in report['games']),
                          'passed':sum(g['passed'] for g in report['games']),
                          'truncated':sum(g['truncated'] for g in report['games'])}
        report['all_games_passed']=len(report['games'])==args.episodes and all(g['passed'] for g in report['games'])
        report['counts_toward_G']=bool(report['all_games_passed'] and len(report['games'])>=10 and not args.offline_smoke
                                     and policy.metadata['profile']=='hard_e' and policy.metadata.get('counts_toward_phase2_G'))
    except Exception as exc:
        report['error']=f'{type(exc).__name__}: {exc}';(folder/'error.log').write_text(traceback.format_exc(),encoding='utf-8')
    finally:
        report['elapsed_seconds']=time.monotonic()-started;write(folder/'report.json',report)
    print(json.dumps({'report':str((folder/'report.json').relative_to(ROOT)),'all_games_passed':report['all_games_passed'],
                      'counts_toward_G':report['counts_toward_G'],'error':report.get('error')}),flush=True)
    if not report['all_games_passed']:raise SystemExit(1)


def main():
    p=argparse.ArgumentParser();p.add_argument('--checkpoint',default='outputs/phase2/learning_pipeline/model/checkpoint.json')
    p.add_argument('--episodes',type=int,default=10);p.add_argument('--port',type=int,default=26789)
    p.add_argument('--manifest',default='data/phase2/version_manifest.json');p.add_argument('--proof',default='data/phase2/action_subspace_proof.json')
    p.add_argument('--seed',type=int,default=9100);p.add_argument('--seed-list',default=None,
                   help='Comma-separated explicit unique seeds, one per episode; owner remains index%%2')
    p.add_argument('--level',type=int,default=16)
    p.add_argument('--step-ticks',type=int,default=20);p.add_argument('--opponent-interval',type=int,default=20)
    p.add_argument('--max-tick',type=int,default=7200);p.add_argument('--max-wall-seconds',type=float,default=240)
    p.add_argument('--allow-diagnostic',action='store_true');p.add_argument('--check-boundaries',action='store_true')
    p.add_argument('--spell-first-deck',action='store_true',help='Use a legal frozen-card-order fixture with the two verified spells first; records the fixture in the report')
    p.add_argument('--offline-smoke',action='store_true',help='Exactly one actual cold-start game; never counts toward10-gameG')
    p.add_argument('--output');args=p.parse_args()
    folder=(ROOT/(args.output or ('outputs/phase2/native/agent_boundaries' if args.check_boundaries else
            'outputs/phase2/native/agent_eval/'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')))).resolve()
    if not folder.is_relative_to(ROOT):raise ValueError('Output outside project')
    folder.mkdir(parents=True,exist_ok=True)
    if args.check_boundaries:boundary_checks(folder)
    else:evaluate(args,folder)


if __name__=='__main__':main()

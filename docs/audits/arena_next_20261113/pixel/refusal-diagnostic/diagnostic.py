"""Six NEW development games: inspect refusals without changing the frozen actor."""
from pathlib import Path
import collections
import hashlib
import json
import random
import sys
import time
sys.path.insert(0, str(Path.cwd()))
import numpy as np
import torch
from arena_lab.environment import ArenaConfig
from arena_lab.evaluation import baseline_action, validate_checkpoint_environment
from arena_lab.league import canonical, digest, write_json
from arena_lab.model import PolicyAgent, load_policy
from arena_lab.pixel_policy import MaskedArenaEnv
from royalegym.protocol import DeployStatus, EntityKind

torch.set_num_threads(1)
output = Path('outputs/arena-next-20261113/pixel-refusal-diagnostic-v1')
output.mkdir(exist_ok=False)
checkpoint = Path('arena_lab/artifacts/pixel-masked-v1.pt')
model, metadata = load_policy(checkpoint)
env = MaskedArenaEnv(ArenaConfig(**metadata['environment_identity']['config']))
validate_checkpoint_environment(model, metadata, env)
actor = PolicyAgent(model, deterministic=True)
write_json(output/'plan.json', {'schema':'pixel-refusal-development-diagnostic-v1',
    'seeds':[930001,930002,930003], 'seats':[0,1], 'checkpoint_sha256':digest(checkpoint),
    'partition':'development_diagnostic_after_frozen_evaluation_not_strength_test',
    'weights_and_threshold_unchanged':True,'torch_threads':1,'opponent':'baseline-scripted',
    'audit_boundary':'Only projected observation enters actor; tick/resources/public dynamic bodies below are separate audit-only data',
    'implementation_sha256':{name:digest(Path('arena_lab')/name)for name in
        ('pixel_policy.py','model.py','timing.py','environment.py','evaluation.py','baselines.py')}})
totals=collections.Counter();games=[];started=time.perf_counter()
try:
    with (output/'actions.jsonl').open('w')as actions,(output/'refusals.jsonl').open('w')as refusals:
        for seed in (930001,930002,930003):
            for side in (0,1):
                observations,info=env.reset(seed=seed);actor.reset();rng=random.Random(seed*101+1-side)
                history=collections.deque(maxlen=8);counts=collections.Counter();status='truncated'
                for step in range(1500):
                    observation=observations[side]
                    action=actor.act(observation)
                    before=env.env.state;own=before.players[side]
                    selected=int(action['card']);position=int(action['position'])
                    card=(env.projection.cards[int(observation['hand_ids'][selected-1])-1]if 1<=selected<=4 else None)
                    record={'seed':seed,'side':side,'tick':before.tick,'card_action':selected,'card':card,'position':position,
                            'card_probability':action.get('card_probability')}
                    # Audit state is read ONLY after the policy has already selected its action.
                    enemy=baseline_action('scripted',observations[1-side],rng,environment=env,player=1-side)
                    observations,_,ended,truncated,info=env.step({side:action,1-side:enemy})
                    own_results=[r for r in info['deploy_results']if r['team']==side]
                    record['deploy_statuses']=[DeployStatus(r['status']).name for r in own_results]
                    actions.write(canonical(record)+'\n');history.append(record)
                    counts['attempts']+=int(selected!=0)
                    counts['executed']+=sum(r['status']==0 for r in own_results)
                    for result in own_results:
                        if result['status']==0:continue
                        reason=DeployStatus(result['status']).name
                        category=('initial_deploy_lockout'if reason=='TOO_EARLY'else'cost'if reason=='NOT_ENOUGH_ELIXIR'
                                  else'dynamic_occupancy'if reason=='OCCUPIED'else'geometry'if reason in
                                  ('NO_DEPLOY','WATER','OUT_OF_TERRITORY','OUT_OF_ARENA')else'other')
                        counts['refused']+=1;totals[reason]+=1
                        gdim,edim=env.projection.gdim,env.projection.edim
                        global_values=observation['global_features'];known=np.flatnonzero(global_values[gdim:]>.5)
                        fields=env.env.identity()['global_base_fields']
                        actor_known={'global_features':[{ 'index':int(i),'name':fields[i]if i<len(fields)else str(i),
                                       'value':float(global_values[i])}for i in known],
                            'hand_names':[env.projection.cards[int(cid)-1]if cid else None for cid in observation['hand_ids']],
                            'visible_entities':[{'x':float(row[0]),'y':float(row[1]),'hp_known':bool(row[edim+2]),
                                                'team_known':bool(row[edim+3]),'troop':bool(row[4]),'building':bool(row[5])}
                                for row in observation['entities'][observation['entity_mask']]],
                            'selected_card_mask':bool(observation['legal_cards'][selected]),
                            'selected_position_mask':bool(observation['legal_positions'][selected-1,position])}
                        audit={'tick':before.tick,'initial_deploy_lockout_ticks':env.env.oracle.rules.deploy_lockout_ticks,
                            'own_true_elixir_milli':own.elixir_milli,'own_pending_cost':own.pending_cost,
                            'own_hand_costs':own.hand_costs,'public_dynamic_buildings':[
                                {'team':e.team,'x':e.x,'y':e.y,'radius':e.radius}
                                for e in env.env._public_state(before,side).entities if e.kind==EntityKind.BUILDING]}
                        item={**record,'reason':reason,'category':category,'actor_known_observation':actor_known,
                            'audit_only_never_actor':audit,'recent_actions_including_refusal':list(history)}
                        refusals.write(canonical(item)+'\n');refusals.flush()
                    counts['decisions']+=1
                    if ended or truncated:
                        status='natural_end'if ended else'truncated';break
                games.append({'seed':seed,'side':side,'status':status,'final_tick':info['tick'],
                              'final_state_hash':info['state_hash'],**dict(counts)})
                actions.flush();write_json(output/'games.json',games)
    result={'schema':'pixel-refusal-development-report-v1','games':games,'refusal_reasons':dict(totals),
        'counts':{key:sum(row.get(key,0)for row in games)for key in ('attempts','executed','refused','decisions')},
        'all_natural':len(games)==6 and all(row['status']=='natural_end'for row in games),
        'checkpoint_sha256':digest(checkpoint),'wall_seconds':time.perf_counter()-started,
        'actions_sha256':digest(output/'actions.jsonl'),'refusals_sha256':digest(output/'refusals.jsonl'),
        'scope':'Six new development games to diagnose refusal causes; not a replacement or modification of the frozen40 results'}
    write_json(output/'report.json',result);print(canonical(result),flush=True)
finally:
    env.close()

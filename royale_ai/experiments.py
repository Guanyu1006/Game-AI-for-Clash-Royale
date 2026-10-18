import json
import statistics
import time
from .belief import OpponentBelief
from .cards import CARDS, DEFAULT_DECK, HOG_DECK
from .paths import output_path
from .policy import LookaheadPolicy,GreedyPolicy,RandomPolicy,Decision
from .replay import render_replay
from .simulator import Arena
from .state import Action,Entity,Observation

def make_policy(name,seed=0,horizon=8):
    if name=="lookahead":
        policy=LookaheadPolicy(horizon=horizon)
        policy.belief=OpponentBelief(low=5,high=5)
        return policy
    if name=="greedy": return GreedyPolicy()
    if name=="random": return RandomPolicy(seed)
    from .learning import LinearPolicy
    return LinearPolicy(name)

def play_game(seed=0,policy_name="lookahead",opponent_name="greedy",seconds=180,
              interval=2,horizon=8,log=None,seat=0,enemy_deck="giant"):
    if interval <= 0 or seconds <= 0:
        raise ValueError("Positive match duration and decision interval required")
    decks=[DEFAULT_DECK, HOG_DECK if enemy_deck=="hog" else DEFAULT_DECK]
    if seat==1: decks.reverse()
    arena=Arena(seed=seed,decks=decks,max_time=seconds)
    policy=make_policy(policy_name,seed,horizon)
    opponent=make_policy(opponent_name,seed+100,horizon)
    cursors=[0,0]
    rows=[]
    elapsed=[]
    invalid=0
    plays=0
    while not arena.done:
        # Observe both players before either commits the simultaneous decision tick.
        observations=[arena.observation(team,cursors[team]) for team in (0,1)]
        cursors=[len(arena.events)]*2
        obs=observations[seat]
        started=time.perf_counter()
        if hasattr(policy,"decide"):
            decision=policy.decide(obs)
        else:
            action=policy.choose(obs)
            decision=Decision(action,None if action.is_wait else obs.hand[action.slot],policy_name,0)
        elapsed.append((time.perf_counter()-started)*1000)
        enemy_action=opponent.choose(observations[1-seat])
        rows.append({"seed":seed,"seat":seat,"observation":obs.to_dict(),"decision":decision.to_dict(),
                     "opponent_action":enemy_action.to_dict()})
        for team,action in ((seat,decision.action),(1-seat,enemy_action)):
            ok=arena.play(team,action)
            invalid+=not ok
            if team==seat and not action.is_wait and ok: plays+=1
        arena.advance(interval)
    winner=arena.winner()
    metrics={"seed":seed,"seat":seat,"policy":policy_name,"opponent":opponent_name,
        "enemy_deck":enemy_deck,"duration":arena.time,"winner":winner,
        "result":"draw" if winner==-1 else "win" if winner==seat else "loss",
        "invalid_actions":invalid,"our_plays":plays,"decisions":len(elapsed),
        "latency_median_ms":statistics.median(elapsed),"latency_p95_ms":sorted(elapsed)[max(0,int(.95*len(elapsed))-1)],
        "tower_hp":[{"id":t.id,"hp":round(t.hp,1)} for t in arena.towers],
        "scope":"approximate simulator; not real-game performance"}
    rows.append({"observation":arena.observation(seat).to_dict(),"metrics":metrics})
    if log:
        dest=output_path(log)
        dest.write_text("\n".join(json.dumps(r,ensure_ascii=False) for r in rows)+"\n",encoding="utf-8")
    return metrics

def evaluate(games=8,seed=10,seconds=180,horizon=8,output="outputs/evaluation.json"):
    if games < 1: raise ValueError("At least one game required")
    matches=[]
    # Same seed, deck, seat and opponent for each policy: paired comparison.
    for policy in ("random","greedy","lookahead"):
        for i in range(games):
            matches.append(play_game(seed+i,policy,"greedy",seconds,horizon=horizon,
                seat=i%2,enemy_deck="hog" if (i//2)%2 else "giant"))
    summary={}
    for policy in ("random","greedy","lookahead"):
        rows=[r for r in matches if r["policy"]==policy]
        wins=sum(r["result"]=="win" for r in rows)
        summary[policy]={"wins":wins,"draws":sum(r["result"]=="draw" for r in rows),
            "losses":sum(r["result"]=="loss" for r in rows),"win_rate":wins/len(rows),
            "invalid_actions":sum(r["invalid_actions"] for r in rows),
            "median_latency_ms":statistics.median(r["latency_median_ms"] for r in rows)}
    report={"protocol":{"games_per_policy":games,"seed_start":seed,"duration":seconds,
        "horizon":horizon,"seat":"alternating","decks":"giant/hog alternation in pairs",
        "paired":True,"opponent":"greedy","scope":"synthetic simulator only; small sample, no general strength claim"},
        "summary":summary,"matches":matches}
    output_path(output).write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
    return report

def scenario(name):
    arena=Arena(shuffle=False)
    arena.time=30
    arena.players[0].elixir=7
    arena.players[0].hand=["mini_pekka","musketeer","arrows","knight"]
    if name=="giant":
        arena.units=[Entity(1,"giant",1,4,23,2900)]
    elif name=="air":
        arena.units=[Entity(i+1,"minions",1,4+i*.4,23,210) for i in range(3)]
    elif name=="finish":
        arena.towers[3].hp=70
        arena.players[0].hand=["fireball","musketeer","arrows","knight"]
    elif name=="save":
        arena.players[0].elixir=1
    else:
        raise ValueError("Unknown scenario")
    obs=arena.observation()
    obs.events=[{"id":"scenario_seen","kind":"seen","team":1,"time":30,"card":u.card}
                for u in arena.units[:1]]
    return obs

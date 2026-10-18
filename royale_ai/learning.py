"""Dependency-free candidate-ranking imitation baseline; not deep RL."""
import json
import math
import random
from .cards import CARDS
from .paths import inside, output_path
from .policy import Decision, candidates
from .state import Action, Observation

def features(obs, action):
    one_hot = [float(not action.is_wait and obs.hand[action.slot] == cid) for cid in CARDS]
    c = None if action.is_wait else CARDS[obs.hand[action.slot]]
    x,y = action.x,action.y
    enemy = [u for u in obs.units if u.team == 1]
    radius = 0 if c is None else (c.radius if c.kind == "spell" else max(3,c.range))
    nearby = [u for u in enemy if math.hypot(u.x-x,u.y-y) <= radius]
    hits = sum(CARDS[u.card].cost/CARDS[u.card].count * min(1,c.damage/max(1,u.hp))
               for u in nearby) if c and c.kind == "spell" else 0
    ground = sum(CARDS[u.card].cost/CARDS[u.card].count for u in nearby if not CARDS[u.card].flying)
    air = sum(CARDS[u.card].cost/CARDS[u.card].count for u in nearby if CARDS[u.card].flying)
    urgent = sum(CARDS[u.card].cost/CARDS[u.card].count for u in enemy if u.y > 22)
    return [1, float(action.is_wait), obs.elixir/10, (obs.elixir-(c.cost if c else 0))/10,
            x/18,y/32, (c.cost if c else 0)/10, (c.damage if c else 0)/620,
            (c.hp if c else 0)/2900, float(bool(c and c.target_air)),float(bool(c and c.building_only)),
            hits/10,ground/10,air/10,urgent/20, float(action.is_wait)*urgent/20,
            float(action.is_wait)*obs.elixir/10, float(bool(c and c.target_air))*air/10,
            *one_hot]

def softmax(scores):
    m = max(scores)
    exps = [math.exp(v-m) for v in scores]
    return [e/sum(exps) for e in exps]

class LinearPolicy:
    def __init__(self, path):
        data=json.loads(inside(path).read_text(encoding="utf-8"))
        if data["cards"] != list(CARDS) or data.get("feature_version") != 1:
            raise ValueError("Model feature schema mismatch")
        self.weights=data["weights"]
        if len(self.weights) != 18+len(CARDS) or not all(math.isfinite(w) for w in self.weights):
            raise ValueError("Invalid model weights")

    def decide(self, obs):
        obs.validate()
        if obs.confidence < .75 or not obs.in_battle:
            return Decision(Action(),None,"观测不足，等待",0)
        actions=candidates(obs)
        scored=[(sum(w*f for w,f in zip(self.weights,features(obs,a))),i) for i,a in enumerate(actions)]
        score,index=max(scored)
        a=actions[index]
        return Decision(a,None if a.is_wait else obs.hand[a.slot],"线性模仿模型的合法候选排名",0)

    def choose(self, obs):
        return self.decide(obs).action

def train(paths, output="models/linear_policy.json", epochs=80, seed=0):
    if len({inside(p) for p in paths}) != len(paths):
        raise ValueError("Duplicate episode paths would leak across train/validation")
    if len(paths) < 2 or epochs <= 0:
        raise ValueError("Need at least two different game logs for episode-level train/validation split")
    episodes=[]
    for path in paths:
        episode=[]
        for line in inside(path).read_text(encoding="utf-8").splitlines():
            row=json.loads(line)
            if "decision" not in row:
                continue
            obs=Observation.from_dict(row["observation"])
            actions=candidates(obs)
            label=Action(**row["decision"]["action"])
            if label not in actions:
                raise ValueError("Teacher action absent from candidate set")
            episode.append(([features(obs,a) for a in actions], actions.index(label)))
        if not episode:
            raise ValueError("Empty episode")
        episodes.append(episode)
    rng=random.Random(seed)
    rng.shuffle(episodes)
    n=max(1,len(episodes)//5)
    valid=[row for ep in episodes[:n] for row in ep]
    training=[row for ep in episodes[n:] for row in ep]
    weights=[0.0]*len(training[0][0][0])
    for epoch in range(epochs):
        rng.shuffle(training)
        rate=.1/(1+epoch*.04)
        for xs,label in training:
            ps=softmax([sum(w*f for w,f in zip(weights,x)) for x in xs])
            for j in range(len(weights)):
                gradient=sum((p-float(i==label))*x[j] for i,(p,x) in enumerate(zip(ps,xs)))
                weights[j]-=rate*(gradient+.001*weights[j])
    def accuracy(rows):
        return sum(max(range(len(xs)),key=lambda i:sum(w*f for w,f in zip(weights,xs[i])))==label
                   for xs,label in rows)/len(rows) if rows else None
    valid_plays=[(xs,label) for xs,label in valid if label != 0]
    report={"train_samples":len(training),"validation_samples":len(valid),
            "train_top1_accuracy":accuracy(training),"validation_top1_accuracy":accuracy(valid),
            "validation_play_samples":len(valid_plays),"validation_play_top1_accuracy":accuracy(valid_plays),
            "validation_wait_fraction":sum(label==0 for xs,label in valid)/len(valid),
            "split":"whole episodes", "epochs":epochs,"seed":seed,
            "note":"Teacher action agreement; not game strength or win rate"}
    output_path(output).write_text(json.dumps({"feature_version":1,"cards":list(CARDS),
        "weights":weights,"report":report},indent=2),encoding="utf-8")
    return report

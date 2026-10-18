"""Legality-masked tactical candidates and observation-only lookahead search."""
from dataclasses import dataclass, field
import math
import random
import time
from .belief import OpponentBelief
from .cards import CARDS
from .simulator import Arena
from .state import Action, Observation, legal_action

def candidates(obs: Observation) -> list[Action]:
    actions = [Action()]
    enemies = [u for u in obs.units if u.team == 1]
    for slot, cid in enumerate(obs.hand):
        card = CARDS[cid]
        if card.cost > obs.elixir:
            continue
        if card.kind == "spell":
            points = [(u.x, u.y) for u in enemies]
            points += [(t.x, t.y) for t in obs.towers if t.team == 1 and t.hp > 0 and not t.king]
            # Cluster centers can hit groups that a single-unit center would miss.
            for u in enemies:
                group = [v for v in enemies if math.hypot(u.x-v.x, u.y-v.y) <= card.radius*1.5]
                if len(group) > 1:
                    points.append((sum(v.x for v in group)/len(group), sum(v.y for v in group)/len(group)))
        elif card.kind == "building":
            points = [(7, 21), (11, 21), (9, 22)]
        else:
            points = [(4, 18), (14, 18), (4, 25), (14, 25)]
            for u in enemies:
                if u.y >= 13:
                    points.append((u.x, max(17, min(26, u.y + (2.5 if card.range > 2 else .8)))))
            if enemies:
                points.append((9, 21))
        unique = sorted({(round(max(.5, min(17.5, x)), 1), round(max(.5, min(31.5, y)), 1)) for x, y in points})
        for x, y in unique:
            action = Action(slot, x, y)
            if legal_action(obs, action):
                actions.append(action)
    return actions

def threat(obs: Observation, team=1) -> float:
    result = 0
    for u in obs.units:
        if u.team != team:
            continue
        card = CARDS[u.card]
        towers = [t for t in obs.towers if t.team != team and t.hp > 0]
        if towers:
            d = min(math.hypot(u.x-t.x, u.y-t.y) for t in towers)
            urgency = max(0, 1-d/14)
            result += card.cost/card.count * min(1, u.hp/max(1, card.hp)) * urgency * (1.5 if card.building_only else 1)
    return result

def evaluate(obs: Observation, enemy_elixir: float) -> float:
    score = 0.0
    for t in obs.towers:
        sign = 1 if t.team == 0 else -1
        score += sign * (12 if t.king else 7) * t.hp/t.max_hp
        if t.hp <= 0:
            score -= sign * (100 if t.king else 16)
    for u in obs.units:
        c = CARDS[u.card]
        value = c.cost/c.count * min(1, u.hp/max(1, c.hp))
        score += (1 if u.team == 0 else -1) * .42 * value
    score += .22 * (obs.elixir-enemy_elixir)
    score += .38 * threat(obs, 0) - .8 * threat(obs, 1)
    return score

@dataclass
class Decision:
    action: Action
    card: str | None
    reason: str
    elapsed_ms: float
    rankings: list[dict] = field(default_factory=list)
    belief: dict = field(default_factory=dict)

    def to_dict(self):
        return {"action": self.action.to_dict(), "card": self.card, "reason": self.reason,
                "elapsed_ms": round(self.elapsed_ms, 2), "rankings": self.rankings, "belief": self.belief}

class RandomPolicy:
    def __init__(self, seed=0):
        self.rng = random.Random(seed)

    def choose(self, obs):
        return self.rng.choice(candidates(obs))

class GreedyPolicy:
    def choose(self, obs):
        enemies = [u for u in obs.units if u.team == 1]
        def score(a):
            if a.is_wait:
                return .25 if obs.elixir < 8 else -.3
            card = CARDS[obs.hand[a.slot]]
            if card.kind == "spell":
                value = 0
                for u in enemies:
                    if math.hypot(u.x-a.x, u.y-a.y) <= card.radius:
                        c = CARDS[u.card]
                        value += c.cost/c.count * min(1, card.damage/max(1,u.hp))
                for t in obs.towers:
                    if t.team == 1 and t.hp > 0 and math.hypot(t.x-a.x,t.y-a.y) <= card.radius:
                        value += 8 if t.hp <= card.damage*card.tower_scale else .15
                return value-card.cost*.7
            local = [u for u in enemies if u.y > 13 and abs(u.x-a.x) < 6]
            if local:
                value = 0
                for u in local:
                    enemy = CARDS[u.card]
                    can_hit = not enemy.flying or card.target_air
                    value += (enemy.cost/ enemy.count) * (1 if can_hit else -.6) * max(0,1-math.hypot(u.x-a.x,u.y-a.y)/9)
                if card.building_only:
                    value -= 3
                value += .8 if card.damage >= 400 and any(CARDS[u.card].hp > 1200 for u in local) else 0
                return value-card.cost*.18
            if card.kind == "building":
                return -1
            enemy_towers = [t for t in obs.towers if t.team == 1 and not t.king and t.hp > 0]
            lane_hp = min((t.hp for t in enemy_towers if (t.x<9)==(a.x<9)), default=2500)
            return (1.5 if card.building_only else .6) + (2500-lane_hp)/2500 - card.cost*.15 - abs(a.y-18)*.07 if obs.elixir >= 7 else -.2
        return max(candidates(obs), key=lambda a: (score(a), - (0 if a.is_wait else CARDS[obs.hand[a.slot]].cost)))

class LookaheadPolicy:
    def __init__(self, horizon=8.0, responses=2, min_confidence=.75):
        if horizon <= 0 or responses not in (1, 2):
            raise ValueError("Positive horizon and 1 or 2 response samples required")
        self.horizon = horizon
        self.responses = responses
        self.min_confidence = min_confidence
        self.belief = OpponentBelief()
        self.rollout_policy = GreedyPolicy()
        self.started = False

    def decide(self, obs: Observation) -> Decision:
        start = time.perf_counter()
        obs.validate()
        if not self.started and obs.time > self.belief.time:
            # Attaching mid-match cannot assume the opponent never spent elixir.
            confirmed = [e.get("time",obs.time) for e in obs.events if e.get("kind")=="play" and e.get("team")==1]
            self.belief.time = min([obs.time,*confirmed])
        self.started = True
        if obs.source == "screen":
            # Inferred tracks cannot account for off-screen troops or invisible spells.
            self.belief.low,self.belief.high = 0,10
            self.belief.time = obs.time
        self.belief.update(obs.time, obs.events)
        belief = self.belief.to_dict()
        if not obs.in_battle or obs.confidence < self.min_confidence:
            return Decision(Action(), None, "观测置信度不足或不在对局中，等待新观测", 0, belief=belief)
        enemy_cards = self.belief.candidates()
        ranked = []
        for action in candidates(obs):
            values = []
            for sample in range(self.responses):
                # Contrasting hypotheses. Only public evidence is used here.
                elixir = self.belief.low if sample == 0 else self.belief.high
                rotated = enemy_cards[sample*3:] + enemy_cards[:sample*3]
                sim = Arena.from_observation(obs, rotated, elixir)
                sim.play(0, action)
                elapsed = 0
                while elapsed < self.horizon and not sim.done:
                    if sample > 0 and len(sim.players[1].hand) == 4:
                        sim.play(1, self.rollout_policy.choose(sim.observation(1)))
                    # One initial action; subsequent own plays are deliberately excluded.
                    chunk = min(2, self.horizon-elapsed)
                    sim.advance(chunk, dt=.5)
                    elapsed += chunk
                values.append(evaluate(sim.observation(), sim.players[1].elixir))
            value = .7 * sum(values)/len(values) + .3 * min(values)
            if not action.is_wait:
                value -= .04 * CARDS[obs.hand[action.slot]].cost
            ranked.append({"action": action.to_dict(), "card": None if action.is_wait else obs.hand[action.slot],
                           "score": round(value, 5), "hypotheses": [round(v,5) for v in values]})
        ranked.sort(key=lambda r: r["score"], reverse=True)
        best = ranked[0]
        a = Action(**best["action"])
        reason = "保留圣水，当前候选出牌收益未超过等待" if a.is_wait else f"{CARDS[best['card']].name}：{self.horizon:g}秒前瞻，在{len(ranked)}个合法候选中评分最高"
        return Decision(a, best["card"], reason, (time.perf_counter()-start)*1000, ranked[:5], belief)

    def choose(self, obs):
        return self.decide(obs).action

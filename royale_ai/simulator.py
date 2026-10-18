"""Small deterministic combat sandbox, not an implementation of Supercell's engine."""
from dataclasses import dataclass, replace
import copy
import math
import random
from .belief import regen_between
from .cards import CARDS, DEFAULT_DECK
from .state import Action, Entity, Observation, Tower, WIDTH, HEIGHT, legal_action

@dataclass
class Player:
    hand: list[str]
    cycle: list[str]
    elixir: float = 5

def distance(a, b):
    return math.hypot(a.x - b.x, a.y - b.y)

class Arena:
    def __init__(self, seed=0, decks=None, shuffle=True, max_time=180):
        self.time = 0.0
        self.max_time = max_time
        self.players = []
        rng = random.Random(seed)
        for deck in decks or [DEFAULT_DECK, DEFAULT_DECK]:
            deck = list(deck)
            if len(deck) != 8 or len(set(deck)) != 8 or any(c not in CARDS for c in deck):
                raise ValueError("Deck must contain eight unique supported cards")
            if shuffle:
                rng.shuffle(deck)
            self.players.append(Player(deck[:4], deck[4:]))
        self.units: list[Entity] = []
        self.towers: list[Tower] = []
        self.events: list[dict] = []
        self.next_id = 1
        for team in (0, 1):
            for label, x, y in (("left", 3.5, 26.5), ("right", 14.5, 26.5), ("king", 9, 30)):
                king = label == "king"
                self.towers.append(Tower(f"{team}_{label}", team, x,
                    y if team == 0 else HEIGHT-y, 4000 if king else 2500,
                    4000 if king else 2500, king, active=not king))

    def clone(self):
        return copy.deepcopy(self)

    @classmethod
    def from_observation(cls, obs: Observation, enemy_cards: list[str], enemy_elixir: float):
        arena = cls(shuffle=False)
        arena.time = obs.time
        arena.max_time = 180 if obs.time < 180 else obs.time + 20
        unseen = [c for c in DEFAULT_DECK if c not in obs.hand]
        if obs.next_card:
            unseen = [obs.next_card] + [c for c in unseen if c != obs.next_card]
        # Only the next own card is visible; remaining cycle is a prior, not hidden truth.
        arena.players[0] = Player(obs.hand.copy(), (unseen + DEFAULT_DECK)[:4], obs.elixir)
        enemy = list(dict.fromkeys(enemy_cards))[:4]
        cycle_prior = [c for c in dict.fromkeys([*enemy_cards,*DEFAULT_DECK]) if c not in enemy][:4]
        arena.players[1] = Player(enemy, cycle_prior, enemy_elixir)
        arena.units = copy.deepcopy(obs.units)
        arena.towers = copy.deepcopy(obs.towers)
        arena.next_id = max([0, *(u.id for u in arena.units)]) + 1
        arena.events = []
        return arena

    def observation(self, team=0, event_since=0):
        def transform(u):
            if team == 0:
                return replace(u)
            return replace(u, team=1-u.team, x=WIDTH-u.x, y=HEIGHT-u.y)
        units = [transform(u) for u in self.units]
        towers = [transform(t) for t in self.towers]
        events = []
        for event in self.events[event_since:]:
            event = dict(event)
            if team == 1:
                event.update(team=1-event["team"], x=WIDTH-event["x"], y=HEIGHT-event["y"])
            events.append(event)
        p = self.players[team]
        return Observation(self.time, p.elixir, p.hand.copy(), p.cycle[0] if p.cycle else None,
                           units, towers, events, in_battle=not self.done)

    def play(self, team: int, action: Action) -> bool:
        if action.is_wait:
            return True
        if not legal_action(self.observation(team), action):
            return False
        p = self.players[team]
        card_id = p.hand[action.slot]
        card = CARDS[card_id]
        x, y = (action.x, action.y) if team == 0 else (WIDTH-action.x, HEIGHT-action.y)
        p.elixir -= card.cost
        p.hand[action.slot] = p.cycle.pop(0)
        p.cycle.append(card_id)
        self.events.append({"id": len(self.events), "kind": "play", "time": self.time,
                            "team": team, "card": card_id, "x": x, "y": y})
        if card.kind == "spell":
            for unit in self.units:
                if unit.team != team and math.hypot(unit.x-x, unit.y-y) <= card.radius:
                    unit.hp = max(0, unit.hp-card.damage)
            for tower in self.towers:
                if tower.team != team and tower.hp > 0 and math.hypot(tower.x-x, tower.y-y) <= card.radius:
                    tower.hp = max(0, tower.hp-card.damage*card.tower_scale)
                    if tower.king:
                        tower.active = True
            self.units = [u for u in self.units if u.hp > 0]
        else:
            for i in range(card.count):
                dx = (i-(card.count-1)/2) * 0.5
                self.units.append(Entity(self.next_id, card_id, team,
                    min(WIDTH-.5, max(.5, x+dx)), y, card.hp, ready_at=self.time+1,
                    expires_at=self.time+card.lifetime if card.lifetime else 0))
                self.next_id += 1
        return True

    def _target(self, unit):
        card = CARDS[unit.card]
        enemy_units = [u for u in self.units if u.team != unit.team and u.hp > 0
                       and (not CARDS[u.card].flying or card.target_air)
                       and (not card.building_only or CARDS[u.card].kind == "building")]
        # Buildings can pull building-targeting troops; ordinary units acquire locally.
        acquisition = 8 if card.building_only else max(5.5, card.range + .5)
        targets = [u for u in enemy_units if distance(unit, u) <= acquisition]
        towers = [t for t in self.towers if t.team != unit.team and t.hp > 0]
        if targets:
            return min(targets, key=lambda t: (distance(unit, t), t.id))
        princess = [t for t in towers if not t.king]
        # Stable lane preference outside aggro range; kings become a target on open lanes.
        lane_towers = [t for t in princess if (t.x < WIDTH/2) == (unit.x < WIDTH/2)]
        options = lane_towers or towers
        return min(options, key=lambda t: distance(unit, t)) if options else None

    def _move(self, unit, target, dt):
        card = CARDS[unit.card]
        tx, ty = target.x, target.y
        # Ground troops cross the river at two bridges. No collision/pathfinding grid.
        if not card.flying and (unit.y-16) * (target.y-16) < 0:
            bx = 4 if unit.x < 9 else 14
            if abs(unit.x-bx) > .6 or abs(unit.y-16) > .4:
                tx, ty = bx, 16
        dx, dy = tx-unit.x, ty-unit.y
        dist = math.hypot(dx, dy)
        if dist:
            move = min(card.speed*dt, dist)
            unit.x += move*dx/dist
            unit.y += move*dy/dist

    def advance(self, seconds: float, dt=.25):
        if seconds < 0 or dt <= 0:
            raise ValueError("Invalid simulation step")
        end = min(self.time+seconds, self.max_time)
        while self.time + 1e-8 < end and not self.done:
            step = min(dt, end-self.time)
            self._tick(step)

    def _tick(self, dt):
        start = self.time
        self.time += dt
        for p in self.players:
            p.elixir = min(10, p.elixir + regen_between(start, self.time))
        damage: dict[tuple[str, object], float] = {}
        def add(target, amount):
            key = ("tower" if isinstance(target, Tower) else "unit", target.id)
            damage[key] = damage.get(key, 0) + amount
        for unit in self.units:
            card = CARDS[unit.card]
            unit.cooldown = max(0, unit.cooldown-dt)
            if unit.hp <= 0 or unit.ready_at > self.time or (unit.expires_at and self.time >= unit.expires_at):
                continue
            target = self._target(unit)
            if target is None:
                continue
            if distance(unit, target) <= card.range + (0.6 if isinstance(target, Tower) else 0):
                if unit.cooldown <= 1e-8:
                    add(target, card.damage)
                    if card.splash and isinstance(target, Entity):
                        for other in self.units:
                            if other.id != target.id and other.team != unit.team and distance(other, target) <= card.splash and (not CARDS[other.card].flying or card.target_air):
                                add(other, card.damage)
                    unit.cooldown = card.interval
            elif card.kind != "building":
                self._move(unit, target, dt)
        for tower in self.towers:
            tower.cooldown = max(0, tower.cooldown-dt)
            if tower.hp <= 0 or not tower.active:
                continue
            targets = [u for u in self.units if u.team != tower.team and u.hp > 0 and u.ready_at <= self.time and distance(u, tower) <= 7]
            if targets and tower.cooldown <= 1e-8:
                add(min(targets, key=lambda u: (distance(u, tower), u.id)), 90)
                tower.cooldown = 1.0
        for u in self.units:
            u.hp = max(0, u.hp-damage.get(("unit", u.id), 0))
        for t in self.towers:
            old = t.hp
            t.hp = max(0, t.hp-damage.get(("tower", t.id), 0))
            if t.king and t.hp < old:
                t.active = True
        for team in (0, 1):
            if any(t.hp <= 0 and not t.king and t.team == team for t in self.towers):
                for t in self.towers:
                    if t.king and t.team == team:
                        t.active = True
        self.units = [u for u in self.units if u.hp > 0 and not (u.expires_at and self.time >= u.expires_at)]

    @property
    def done(self):
        return self.time >= self.max_time or any(t.king and t.hp <= 0 for t in self.towers)

    def winner(self):
        if not self.done:
            return None
        dead_kings = {t.team for t in self.towers if t.king and t.hp <= 0}
        if len(dead_kings) == 1:
            return 1-next(iter(dead_kings))
        if len(dead_kings) == 2:
            return -1
        crowns = [sum(t.hp <= 0 and t.team != team for t in self.towers) for team in (0, 1)]
        if crowns[0] != crowns[1]:
            return 0 if crowns[0] > crowns[1] else 1
        # Explicit MVP tiebreaker: sum of normalized surviving tower HP, not official rules.
        health = [sum(t.hp/t.max_hp for t in self.towers if t.team == team) for team in (0, 1)]
        return -1 if abs(health[0]-health[1]) < 1e-8 else (0 if health[0] > health[1] else 1)

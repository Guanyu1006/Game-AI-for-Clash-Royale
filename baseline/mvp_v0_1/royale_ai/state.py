from dataclasses import asdict, dataclass, field
import math
from typing import Any
from .cards import CARDS

WIDTH, HEIGHT, RIVER = 18.0, 32.0, 16.0

@dataclass(frozen=True)
class Action:
    slot: int = -1
    x: float = 0
    y: float = 0

    @property
    def is_wait(self) -> bool:
        return self.slot == -1

    def to_dict(self) -> dict:
        return asdict(self)

@dataclass
class Entity:
    id: int
    card: str
    team: int
    x: float
    y: float
    hp: float
    cooldown: float = 0
    ready_at: float = 0
    expires_at: float = 0

@dataclass
class Tower:
    id: str
    team: int
    x: float
    y: float
    hp: float
    max_hp: float
    king: bool = False
    cooldown: float = 0
    active: bool = True

@dataclass
class Observation:
    time: float
    elixir: float
    hand: list[str]
    next_card: str | None
    units: list[Entity]
    towers: list[Tower]
    events: list[dict] = field(default_factory=list)
    confidence: float = 1.0
    in_battle: bool = True
    source: str = "simulator"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Observation":
        obs = cls(**{**data, "units": [Entity(**x) for x in data["units"]],
                     "towers": [Tower(**x) for x in data["towers"]]})
        obs.validate()
        return obs

    def validate(self) -> None:
        if not math.isfinite(self.time) or self.time < 0:
            raise ValueError("Invalid observation time")
        if not math.isfinite(self.elixir) or not 0 <= self.elixir <= 10:
            raise ValueError("Invalid elixir")
        if len(self.hand) != 4 or any(c not in CARDS for c in self.hand):
            raise ValueError("Exactly four recognized supported hand cards required")
        if self.next_card is not None and self.next_card not in CARDS:
            raise ValueError("Unsupported next card")
        if not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("Invalid confidence")
        if len(self.towers) != 6 or len({t.id for t in self.towers}) != 6:
            raise ValueError("Exactly six unique towers required")
        if len({u.id for u in self.units}) != len(self.units):
            raise ValueError("Duplicate unit track IDs")
        for u in [*self.units, *self.towers]:
            if u.team not in (0, 1) or not all(math.isfinite(v) for v in (u.x, u.y, u.hp)):
                raise ValueError("Invalid entity values")
            if not 0 <= u.x <= WIDTH or not 0 <= u.y <= HEIGHT or u.hp < 0:
                raise ValueError("Entity outside arena or invalid HP")
        if any(u.card not in CARDS or CARDS[u.card].kind == "spell" for u in self.units):
            raise ValueError("Unsupported unit card")
        if any(t.max_hp <= 0 or t.hp > t.max_hp for t in self.towers):
            raise ValueError("Invalid tower health")
        for e in self.events:
            if e.get("kind") == "play" and (e.get("card") not in CARDS or e.get("team") not in (0, 1)):
                raise ValueError("Unsupported card play event")

def legal_action(obs: Observation, action: Action) -> bool:
    if action.is_wait:
        return True
    if not obs.in_battle or not 0 <= action.slot < 4:
        return False
    if not all(math.isfinite(v) for v in (action.x, action.y)):
        return False
    card = CARDS[obs.hand[action.slot]]
    return (obs.elixir + 1e-6 >= card.cost and 0.5 <= action.x <= WIDTH - 0.5
            and 0.5 <= action.y <= HEIGHT - 0.5
            and (card.kind == "spell" or action.y >= RIVER + 1))

from dataclasses import asdict,dataclass,field
import math
from typing import Protocol

@dataclass(frozen=True)
class Action:
    kind: str = "wait"
    slot: int = -1
    x: float = 0.0
    y: float = 0.0
    delay_ticks: int = 0

    def validate(self):
        if self.kind not in ("wait","card","ability"):
            raise ValueError("Unknown action kind")
        if not isinstance(self.slot,int) or not isinstance(self.delay_ticks,int) or not 0 <= self.delay_ticks <= 300:
            raise ValueError("Invalid slot/delay")
        if not all(math.isfinite(v) for v in (self.x,self.y)):
            raise ValueError("Coordinates must be finite")
        if self.kind == "card" and not 0 <= self.slot < 4:
            raise ValueError("Card slot must be 0..3")

    def to_dict(self): return asdict(self)

@dataclass
class Decision:
    action: Action
    reason: str
    card_id: int | None = None
    elapsed_ms: float = 0
    rankings: list[dict] = field(default_factory=list)
    belief: dict = field(default_factory=dict)

    def to_dict(self):
        return {**asdict(self),"elapsed_ms":round(self.elapsed_ms,3)}

class BattleBackend(Protocol):
    def reset(self,seed:int=0,decks=None): ...
    def observe(self,player:int=0) -> dict: ...
    def oracle(self) -> dict: ...
    def legal(self,player:int,action:Action) -> bool: ...
    def apply(self,player:int,action:Action,command_id:str|None=None) -> dict: ...
    def advance(self,ticks:int=1): ...
    def snapshot(self): ...
    def restore(self,snapshot): ...
    @property
    def done(self) -> bool: ...

class Policy(Protocol):
    def decide(self,observation:dict) -> Decision: ...

def validate_observation(obs):
    if len(obs["hand"]) != 4 or not 0 <= obs["elixir"] <= 10.0001:
        raise ValueError("Invalid visible hand/elixir")
    forbidden={"opponent_hand","enemy_hand","opponent_elixir","enemy_elixir","rng_state","future_events","opponent_cycle"}
    if forbidden.intersection(obs):
        raise ValueError("Hidden-state fields in policy observation")
    for unit in obs["units"]:
        if unit["team"] not in (0,1) or not all(math.isfinite(unit[k]) for k in ("x","y","hp")):
            raise ValueError("Invalid unit")

from dataclasses import dataclass
from pathlib import Path
import json

@dataclass(frozen=True)
class Card:
    id: str
    name: str
    cost: int
    kind: str = "troop"
    hp: float = 0
    damage: float = 0
    interval: float = 1.0
    speed: float = 0
    range: float = 0
    count: int = 1
    flying: bool = False
    target_air: bool = False
    building_only: bool = False
    radius: float = 0
    splash: float = 0
    tower_scale: float = 0.3
    lifetime: float = 0

def load_cards(path: Path | None = None) -> dict[str, Card]:
    path = path or Path(__file__).parent / "data" / "cards.json"
    return {row["id"]: Card(**row) for row in json.loads(path.read_text(encoding="utf-8"))["cards"]}

CARDS = load_cards()
DEFAULT_DECK = ["giant", "knight", "archers", "minions", "musketeer", "mini_pekka", "arrows", "fireball"]
HOG_DECK = ["hog_rider", "cannon", "skeletons", "valkyrie", "musketeer", "archers", "arrows", "fireball"]

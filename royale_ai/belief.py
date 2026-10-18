"""Observable event driven elixir interval and approximate four-card cycle."""
from dataclasses import dataclass, field
from .cards import CARDS, DEFAULT_DECK

def regen_between(start: float, end: float) -> float:
    # MVP schedule: 1x before 120 s, 2x until 180 s, 3x thereafter.
    total = 0.0
    for lo, hi, mult in ((0, 120, 1), (120, 180, 2), (180, float("inf"), 3)):
        total += max(0.0, min(end, hi) - max(start, lo)) * mult / 2.8
    return total

@dataclass
class OpponentBelief:
    low: float = 0
    high: float = 10
    time: float = 0
    played_count: int = 0
    last_play: dict[str, int] = field(default_factory=dict)
    seen_events: set[str] = field(default_factory=set)
    seen_cards: set[str] = field(default_factory=set)
    inconsistencies: int = 0

    def advance(self, time: float) -> None:
        if time < self.time:
            raise ValueError("Observation/event time moved backwards")
        amount = regen_between(self.time, time)
        self.low = min(10, self.low + amount)
        self.high = min(10, self.high + amount)
        self.time = time

    def update(self, time: float, events: list[dict]) -> None:
        # Missing visible deployments/spells means inference cannot prove exact elixir.
        for event in sorted(events, key=lambda e: e.get("time", time)):
            if event.get("team") == 1 and event.get("card") in CARDS:
                self.seen_cards.add(event["card"])
            if event.get("kind") != "play" or event.get("team") != 1:
                continue
            key = str(event.get("id", f"{event.get('time')}:{event.get('card')}"))
            if key in self.seen_events:
                continue
            self.seen_events.add(key)
            event_time = float(event.get("time", time))
            if event_time < self.time or event_time > time:
                self.inconsistencies += 1
                self.low, self.high = 0, 10
                continue
            self.advance(event_time)
            cost = CARDS[event["card"]].cost
            if self.high + 1e-6 < cost:
                self.inconsistencies += 1
                self.low, self.high = 0, 10 - cost
            else:
                self.low, self.high = max(0, self.low - cost), max(0, self.high - cost)
            self.played_count += 1
            self.last_play[event["card"]] = self.played_count
        self.advance(time)

    def candidates(self) -> list[str]:
        # Four intervening cards before a base card returns. No champion/evolution model.
        prior = list(dict.fromkeys([*self.last_play, *sorted(self.seen_cards), *DEFAULT_DECK]))
        return [c for c in prior if c not in self.last_play or
                self.played_count - self.last_play[c] >= 4]

    def to_dict(self) -> dict:
        return {"elixir_interval": [round(self.low, 2), round(self.high, 2)],
                "observed_cards": sorted(self.seen_cards), "possible_cards": self.candidates(),
                "event_count": self.played_count, "inconsistencies": self.inconsistencies}

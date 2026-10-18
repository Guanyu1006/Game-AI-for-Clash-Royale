"""Causal, conservative public-history beliefs for the pinned native version.

These are intervals and observed-frequency summaries, not the enemy's true
hand/deck/elixir. Unknown mode, missing history, unknown costs or unvalidated
resource mechanics widen the interval instead of importing phase1 rates.
"""
from collections import Counter
from copy import deepcopy
import json

from .common import local, sha
from .observation import SCHEMA, DEFAULT_CARDS, actor_hash

BELIEF_SCHEMA = 'royale-phase3-public-belief.v1'


class PublicBelief:
    """One tracker per projector episode. Explicit reset before new backend.

    Narrow resource propagation is opt-in via ``ordinary_resource_only``. It
    must be justified by the frozen experiment's supported ordinary deck and
    match configuration. This option does not reveal which enemy cards are in
    the deck. Without it, unseen resource-changing mechanics remain possible,
    and the returned opponent interval conservatively remains [0, 10].
    """
    def __init__(self, version_manifest='data/phase2/version_manifest.json',
                 game_mode=72000006, ordinary_resource_only=False,
                 standard_cycle_evidence=None):
        from royale_phase2.backend import _read_locked_elixir_rates
        manifest = json.loads(local(version_manifest).read_text(encoding='utf-8-sig'))
        if manifest.get('ticks_per_second') != 20:
            raise ValueError('Belief supports only the pinned 20 Hz version')
        if type(game_mode) is not int or game_mode < 0:
            raise ValueError('Native game mode ID must be a nonnegative integer')
        if type(ordinary_resource_only) is not bool:
            raise ValueError('Resource scope must be an explicit boolean')
        schedules = _read_locked_elixir_rates(manifest)
        self.schedule = tuple(schedules.get(game_mode, ()))
        self.ordinary_resource_only = bool(ordinary_resource_only)
        # Standard four-other-card cycling is NOT assumed from an old engine.
        if standard_cycle_evidence is not None:
            raise ValueError('Standard-cycle evidence adapter is not certified in v1')
        self.metadata = {'schema': BELIEF_SCHEMA,
            'version_manifest_sha256': sha(version_manifest),
            'version_fingerprint': manifest.get('fingerprint'), 'game_mode': game_mode,
            'rate_source': 'current_manifest_hash_verified_game_modes_and_battle_timelines_csv',
            'rate_supported': bool(self.schedule), 'ordinary_resource_only': self.ordinary_resource_only,
            'cycle_inference': 'unknown_without_current_native_cycle_evidence',
            'interval_calibrated': False,
            'normal_resource_scope': 'declared_fixed_ordinary_deck_assumption_requires_experiment_evidence' if ordinary_resource_only else 'unknown_resource_modifiers_wide_interval'}
        self.reset()

    def reset(self):
        self._tick = self._owner = None
        self._events = []
        self._low, self._high = 0, 100000
        self._previous_key = self._previous_output = None
        self.diagnostics = {}

    def _regeneration(self, previous_tick, tick):
        """Bounds on raw gain; a timeline boundary uses both adjacent rates."""
        total_low = total_high = 0
        for current in range(previous_tick+1, tick+1):
            rates = [rate for start,end,rate in self.schedule if start < current < end]
            if not rates:
                # CSV timing boundaries lack exact before/after-phase proof.
                rates = [rate for start,end,rate in self.schedule if current in (start,end)]
            if not rates:
                return None
            total_low += min(rates); total_high += max(rates)
        return total_low, total_high

    def update(self, observation):
        if observation.get('schema') != SCHEMA:
            raise ValueError('Phase3 observation required')
        tick, owner = observation['tick'], observation['native_owner']
        if self._tick is not None and (tick < self._tick or owner != self._owner):
            raise ValueError('Belief requires explicit reset before new episode/perspective')
        key = actor_hash(observation)
        if key == self._previous_key:
            return deepcopy(self._previous_output)
        history = deepcopy(observation['history'])
        if any(type(e.get('tick')) is not int or e['tick'] > tick for e in history):
            raise ValueError('Future/invalid belief event')
        if history[:len(self._events)] != self._events:
            raise ValueError('Public history changed or regressed without reset')
        # Recompute from the public history. This permits sparse online calls
        # and exactly matches batched/offline calls at the same tick.
        low, high = 0, 100000
        cursor = 0
        consistent = True
        enemy = []
        for event in history:
            if event.get('side') != 'opponent':
                continue
            enemy.append(event)
            gain = self._regeneration(cursor, event['tick']) if self.schedule and self.ordinary_resource_only else None
            if gain is None:
                low, high = 0, 100000
            else:
                low, high = min(100000, low+gain[0]), min(100000, high+gain[1])
            cost = event.get('cost')
            if not self.ordinary_resource_only or cost is None:
                low, high = 0, 100000
            elif type(cost) is not int or cost < 0:
                raise ValueError('Invalid public consumed cost')
            elif high < cost*10000:
                # Don't silently exclude an inconvenient real event. It means
                # model/scope/history inconsistency, so uncertainty resets.
                consistent = False; low,high = 0,100000
            else:
                # A consumed action proves pre-spend balance >= cost. The
                # post-spend state itself remains unobserved by this actor.
                low, high = max(0,low-cost*10000), high-cost*10000
            cursor = event['tick']
        gain = self._regeneration(cursor, tick) if self.schedule and self.ordinary_resource_only else None
        if gain is None:
            low,high = 0,100000
        else:
            low,high = min(100000,low+gain[0]), min(100000,high+gain[1])
        counts = Counter(e['card'] for e in enemy if e.get('card') is not None)
        count_total = sum(counts.values())
        seen = []
        for card in sorted(counts):
            matches = [e for e in enemy if e.get('card') == card]
            seen.append({'card': card, 'observed_count': counts[card],
                'observed_frequency': counts[card]/count_total,
                'last_seen_seconds_ago': (tick-matches[-1]['tick'])/20,
                'hand_presence_probability': None,
                'minimum_other_plays_until_return': None})
        result = {'schema': BELIEF_SCHEMA,
            'opponent_elixir_interval': [low/10000, high/10000],
            'opponent_seen_cards': seen,
            'opponent_unknown_deck': True,
            'opponent_hand_distribution': None,
            'opponent_next_card_distribution': None,
            'opponent_last_play_seconds_ago': (tick-enemy[-1]['tick'])/20 if enemy else None,
            'opponent_observed_play_count': len(enemy)}
        self.diagnostics = {'resource_model_consistent': consistent}
        self._tick,self._owner,self._events = tick,owner,history
        self._low,self._high = low,high
        self._previous_key,self._previous_output = key,deepcopy(result)
        return result


BeliefTracker = PublicBelief


def encode_belief(belief, cards=DEFAULT_CARDS):
    """Fixed 2 + 2 + vocabulary + unknown + age/mask public features."""
    if belief.get('schema') != BELIEF_SCHEMA:
        raise ValueError('Phase3 public belief required')
    low,high = belief['opponent_elixir_interval']
    seen = {r['card']: r for r in belief['opponent_seen_cards']}
    total = belief['opponent_observed_play_count']
    values = [low/10,high/10,float(total>0),min(total,100)/100]
    values += [seen.get(card,{}).get('observed_frequency',0.) for card in cards]
    values += [sum(r['observed_frequency'] for card,r in seen.items() if card not in cards)]
    age = belief['opponent_last_play_seconds_ago']
    values += [0.,0.] if age is None else [age/30,1.]
    return values

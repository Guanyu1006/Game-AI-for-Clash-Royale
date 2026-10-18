"""Versioned deployment input over the existing phase2 battlefield projection.

The pinned capture cannot prove that ordinary objects are visible. Therefore
``fair`` currently excludes ALL non-tower objects, including own objects. This
is intentionally less information than a person sees. ``research_battlefield``
is an explicitly separate, non-fair input for diagnostic experiments.

No oracle, journal position, source suffix, native entity ID, file path or
quality counter is passed to the actor. Diagnostics live on the projector,
outside its returned value. A future visibility expansion needs a separate
implementation and evidence review; a caller's ``visibility='visible'`` is not
accepted as an attestation.
"""
from copy import deepcopy
import hashlib
import json
import math

SCHEMA = 'royale-phase3-player-observation.v1'
ENCODING_SCHEMA = 'royale-phase3-player-vector.v1'
PROFILES = ('fair', 'research_battlefield')
DEFAULT_CARDS = (26000000, 26000001, 26000003, 26000014, 26000018,
                 27000000, 28000000, 28000001)


def _integer(value, name, low=0, high=2**63-1, optional=False):
    if value is None and optional:
        return None
    if type(value) is not int or not low <= value <= high:
        raise ValueError('Invalid ' + name)
    return value


def actor_hash(observation):
    return hashlib.sha256(json.dumps(observation, sort_keys=True,
        ensure_ascii=False, separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def _side(owner, native_owner):
    return 'own' if owner == native_owner else 'opponent'


class ObservationProjector:
    """One instance per episode/perspective; generation reset is automatic.

    ``project`` is idempotent for the same validated public input at the same
    tick. Generation is used only for state isolation, never as an actor input.
    ``reset`` is also required when a new independent backend restarts at the
    same generation number; do not reuse a projector across backend lifetimes.
    """
    def __init__(self, profile='fair', ticks_per_second=20, visibility_evidence=None):
        if profile not in PROFILES:
            raise ValueError('Unknown observation profile')
        if ticks_per_second != 20:
            raise ValueError('Only the pinned measured 20 Hz version is supported')
        if visibility_evidence is not None:
            raise ValueError('No non-tower visibility evidence adapter is certified in v1')
        self.profile = profile
        self.ticks_per_second = ticks_per_second
        self.reset()

    def reset(self):
        self._generation = self._tick = self._owner = None
        self._tracks = {}
        self._present = {}
        self._history_records = []
        self._last_input_hash = self._last_output = None
        self.diagnostics = {}

    def project(self, battlefield):
        if not isinstance(battlefield, dict) or battlefield.get('schema') != 'royale-platform-battlefield-observation.v1':
            raise ValueError('Phase2 battlefield observation required')
        generation = _integer(battlefield.get('generation'), 'generation', 1)
        tick = _integer(battlefield.get('tick'), 'tick')
        owner = _integer(battlefield.get('native_owner'), 'owner', 0, 1)
        reset = self._generation is not None and generation != self._generation
        if reset:
            self.reset()
        if self._tick is not None and tick < self._tick:
            raise ValueError('Tick regressed without an episode reset')
        if self._owner is not None and owner != self._owner:
            raise ValueError('Perspective changed without reset')
        hand = []
        for row in battlefield.get('hand', []):
            hand.append({'slot': _integer(row.get('slot'), 'slot', 0, 3),
                         'card': _integer(row.get('card'), 'card', 1),
                         'cost': _integer(row.get('cost'), 'cost', 0, 100)})
        if len({row['slot'] for row in hand}) != len(hand):
            raise ValueError('Duplicate own hand slots')
        complete = battlefield.get('hand_complete')
        if type(complete) is not bool or complete != (len(hand) == 4):
            raise ValueError('Invalid hand completeness')
        crowns = battlefield.get('crowns', {})
        towers = []
        tower_rows = battlefield.get('towers', []) if battlefield.get('tower_identity_basis') == 'reset_attested_canonical_six_towers' else []
        for row in tower_rows:
            if row.get('side') not in ('own', 'opponent') or row.get('role') not in ('left', 'right', 'king'):
                raise ValueError('Invalid tower descriptor')
            if type(row.get('present')) is not bool:
                raise ValueError('Tower presence must be known boolean')
            towers.append({'side': row['side'], 'role': row['role'], 'present': row['present'],
                'hp': _integer(row.get('hp'), 'tower HP', optional=True),
                'max_hp': _integer(row.get('max_hp'), 'tower max HP', 1, optional=True)})
        if len({(r['side'], r['role']) for r in towers}) != len(towers):
            raise ValueError('Duplicate tower descriptor')
        history = []
        history_records = []
        seen = set()
        for row in battlefield.get('history', []):
            if row.get('source') != 'native_card_consumption_event':
                raise ValueError('History must come from actual native consumption')
            seq = _integer(row.get('sequence'), 'event sequence', 1)
            if seq in seen:
                raise ValueError('Duplicate event sequence')
            seen.add(seq)
            event_tick = _integer(row.get('tick'), 'event tick', 0, tick)
            event_owner = _integer(row.get('owner'), 'event owner', 0, 1)
            card = _integer(row.get('native_card_id'), 'event card', 0)
            # Actual native identity is an event, NOT a resolved root/evolution
            # identity. Opaque ID 0 gives no card/cost inference. Requested
            # coordinates are controller intentions, not public placements.
            history.append((seq, {'tick': event_tick, 'side': _side(event_owner, owner),
                'card': card if card > 0 else None,
                'cost': _integer(row.get('cost_elixir'), 'event cost', 0, 100) if card > 0 else None,
                'x': None, 'y': None}))
            history_records.append((seq, event_tick, event_owner, card,
                _integer(row.get('cost_elixir'), 'event cost', 0, 100)))
        history = [row for _,row in sorted(history)]
        history_records.sort()
        if any(a['tick'] > b['tick'] for a,b in zip(history,history[1:])):
            raise ValueError('Native public history event order regressed')
        if history_records[:len(self._history_records)] != self._history_records:
            raise ValueError('Public event history changed or regressed without reset')
        entities = []
        tracks = self._tracks.copy()
        if self.profile == 'research_battlefield':
            raw_entities = battlefield.get('entities', [])
            if not isinstance(raw_entities, list):
                raise ValueError('Entities must be an array')
            # Stable in-episode tracking only. IDs never enter the vector.
            for row in raw_entities:
                native_id = _integer(row.get('entity_id'), 'entity identity', 1)
                if native_id not in tracks:
                    tracks[native_id] = len(tracks) + 1
                kind = row.get('native_kind')
                if not isinstance(kind, str):
                    raise ValueError('Invalid producer kind')
                raw_card = _integer(row.get('card_id_raw'), 'entity card', -1)
                entities.append({'track': tracks[native_id],
                    'side': _side(_integer(row.get('owner'), 'entity owner', 0, 1), owner),
                    'card': raw_card if raw_card > 0 else None,
                    # Captured objects can travel outside the legal deployment
                    # rectangle. Preserve measured signed positions; action
                    # bounds are a different contract and must not clip these.
                    'kind': kind, 'x': _integer(row.get('x_raw'), 'entity x', -(2**31), 2**31-1, optional=True),
                    'y': _integer(row.get('y_raw'), 'entity y', -(2**31), 2**31-1, optional=True),
                    'hp': _integer(row.get('hp'), 'entity HP', optional=True),
                    'max_hp': _integer(row.get('max_hp'), 'entity max HP', 1, optional=True),
                    'present': True})
            if len({r['track'] for r in entities}) != len(entities):
                raise ValueError('Duplicate entity identity')
        entities.sort(key=lambda r: (r['side'], -1 if r['x'] is None else r['x'],
            -1 if r['y'] is None else r['y'], r['card'] or 0, r['kind'], r['track']))
        base = {'schema': SCHEMA, 'tick': tick, 'native_owner': owner,
            'hand': sorted(hand, key=lambda r: r['slot']), 'hand_complete': complete,
            'own_elixir_raw': _integer(battlefield.get('own_elixir_raw'), 'own elixir', 0, 100000),
            'crowns': {key: _integer(crowns.get(key), 'crowns', 0, 3) for key in ('own', 'opponent')},
            'towers': sorted(towers, key=lambda r: (r['side'], r['role'])),
            'entities': entities, 'history': history}
        key = actor_hash(base)
        if key == self._last_input_hash and self._last_output is not None:
            return deepcopy(self._last_output)
        current = {r['track']: r for r in entities}
        base['disappeared_tracks'] = sorted(set(self._present) - set(current))
        base['dt_seconds'] = None if self._tick is None else (tick - self._tick) / self.ticks_per_second
        self.diagnostics = {'profile': self.profile,
            'fairness_scope': 'conservative_public_no_non_tower_objects' if self.profile == 'fair' else 'unverified_non_tower_visibility_research_only',
            'non_tower_visibility_verified': False, 'certified_non_tower_cards': [],
            'excluded_objects': len(battlefield.get('entities', [])) if self.profile == 'fair' else 0,
            'generation_reset': reset, 'generation': generation,
            'history_semantics': 'actual consumed card event; requested/final positions withheld; opaque card masks cost',
            'missingness': 'None and explicit vector masks; disappearance does not certify death'}
        self._generation, self._tick, self._owner = generation, tick, owner
        self._tracks, self._history_records = tracks, history_records
        self._present = current
        self._last_input_hash, self._last_output = key, deepcopy(base)
        return base


def project_observation(battlefield, profile='fair'):
    """Stateless projection. Use ObservationProjector for live/history tracking."""
    return ObservationProjector(profile=profile).project(battlefield)


def affordability_mask(observation):
    """Budget only; no placement/readiness/hidden state legality claim."""
    if observation.get('schema') != SCHEMA:
        raise ValueError('Phase3 player observation required')
    result = [True, False, False, False, False]
    for card in observation['hand']:
        result[card['slot']+1] = card['cost']*10000 <= observation['own_elixir_raw']
    return result


def feature_spec(cards=DEFAULT_CARDS, max_entities=16, history_steps=16,
                 include_entities=True, include_history=True):
    if len(set(cards)) != len(cards) or any(type(c) is not int or c <= 0 for c in cards):
        raise ValueError('Distinct positive card vocabulary required')
    if type(max_entities) is not int or max_entities < 0 or type(history_steps) is not int or history_steps < 0:
        raise ValueError('Nonnegative fixed encoding dimensions required')
    # Hand: slot present + card onehot + unknown + cost/known + affordability.
    hand_width = len(cards) + 5
    # Entity: present, side, card onehot/unknown, x/y/hp/maxhp value+mask,
    # four producer-kind indicators. Track IDs are NEVER encoded.
    entity_width = len(cards) + 15
    # History: present, side, age, card onehot/unknown, cost value+mask.
    history_width = len(cards) + 6
    return {'schema': ENCODING_SCHEMA, 'cards': list(cards), 'max_entities': max_entities,
        'history_steps': history_steps, 'include_entities': bool(include_entities),
        'include_history': bool(include_history), 'ticks_per_second': 20,
        'dimension': 8 + 4*hand_width + 6*6 + (max_entities*entity_width if include_entities else 0)
                     + (history_steps*history_width if include_history else 0),
        'widths': {'scalar': 8, 'hand': hand_width, 'tower': 6,
                   'entity': entity_width, 'history': history_width},
        'coordinate_convention': 'native units; normalized actor-own half on top via 17999-x,31999-y for owner1',
        'not_encoded': ['native entity IDs', 'anonymous track IDs', 'generation', 'diagnostics',
                        'quality', 'paths', 'requested positions', 'hidden states']}


def encode_observation(observation, cards=DEFAULT_CARDS, max_entities=16, history_steps=16,
                       include_entities=True, include_history=True):
    """Return a pure fixed-length float list; metadata is feature_spec(...).

    Missing scalars use [0, 0] and known zero uses [0, 1]. The vector's numeric
    zero alone must never be interpreted as a measured value.
    """
    if observation.get('schema') != SCHEMA:
        raise ValueError('Phase3 player observation required')
    spec = feature_spec(cards, max_entities, history_steps, include_entities, include_history)
    def pair(value, scale=1):
        return [0., 0.] if value is None else [float(value)/scale, 1.]
    def onehot(card):
        return [float(card == c) for c in cards] + [float(card not in cards)]
    values = [observation['tick']/20/300, observation['own_elixir_raw']/100000,
        float(observation['hand_complete']), observation['crowns']['own']/3,
        observation['crowns']['opponent']/3, float(observation['native_owner'])]
    values += pair(observation.get('dt_seconds'), 1)
    hands = {r['slot']: r for r in observation['hand']}
    allowed = affordability_mask(observation)
    for slot in range(4):
        row = hands.get(slot)
        values += ([1.] + onehot(row['card']) + pair(row['cost'], 10) + [float(allowed[slot+1])]
                   if row else [0.]*spec['widths']['hand'])
    towers = {(r['side'], r['role']): r for r in observation['towers']}
    for side in ('own', 'opponent'):
        for role in ('left', 'right', 'king'):
            row = towers.get((side, role))
            values += ([1., float(row['present'])] + pair(row['hp'], 10000) + pair(row['max_hp'], 10000)
                       if row else [0.]*6)
    if include_entities:
        entities = observation['entities']
        # IDs do not affect feature order. Convert coordinates only once here.
        def position(row):
            x,y = row['x'],row['y']
            if observation['native_owner'] == 1:
                x = None if x is None else 17999-x
                y = None if y is None else 31999-y
            return x,y
        entities = sorted(entities, key=lambda r:(r['side'], *(v if v is not None else -1 for v in position(r)), r['card'] or 0, r['kind'],
            -1 if r['hp'] is None else r['hp'], -1 if r['max_hp'] is None else r['max_hp']))
        for index in range(max_entities):
            if index >= len(entities):
                values += [0.]*spec['widths']['entity']; continue
            row = entities[index]; x,y = position(row)
            values += [float(row['present']), float(row['side']=='own')] + onehot(row['card'])
            values += pair(x, 18000) + pair(y, 32000) + pair(row['hp'], 10000) + pair(row['max_hp'], 10000)
            values += [float(row['kind']==kind) for kind in ('troop', 'building', 'projectile', 'area')]
    if include_history:
        history = observation['history'][-history_steps:] if history_steps else []
        for index in range(history_steps):
            if index >= len(history):
                values += [0.]*spec['widths']['history']; continue
            row = history[index]
            values += [1., float(row['side']=='own'), (observation['tick']-row['tick'])/20/30]
            values += onehot(row['card']) + pair(row['cost'], 10)
    if len(values) != spec['dimension'] or any(not math.isfinite(v) for v in values):
        raise AssertionError('Observation encoder shape/finite contract violated')
    return values

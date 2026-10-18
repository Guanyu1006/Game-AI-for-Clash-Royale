"""Conservative actor projection for cr-minimal.v4; no combat simulation.

Only own current hand/cost/elixir and public clock/crowns are selected. Troops,
projectiles, target IDs, cooldowns, both cycles and all enemy private state are
omitted. Towers require separate reset-time identity attestation. This policy
has less information than a human; it does not claim full visual perception.
Runtime field validation against the paired client is still required.
"""
from dataclasses import dataclass


def integer(value, name, low=0, high=2**63-1):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f'Invalid {name}')
    return value


@dataclass(frozen=True)
class TowerIdentity:
    entity_id: int
    owner: int
    role: str
    x: int
    y: int


@dataclass(frozen=True)
class TowerAttestation:
    generation: int
    identities: tuple[TowerIdentity, ...]
    # Source code defines six standard tower locations. This is not a claim
    # that these coordinates are valid for every arena or special game mode.
    standard_locations = {
        (0, 9000, 3000): 'king', (0, 3500, 6500): 'left', (0, 14500, 6500): 'right',
        (1, 9000, 29000): 'king', (1, 3500, 25500): 'left', (1, 14500, 25500): 'right',
    }

    def __post_init__(self):
        integer(self.generation, 'tower generation', 1)
        if type(self.identities) is not tuple or len(self.identities) != 6:
            raise ValueError('Exactly six canonical reset tower identities required')
        locations = set(); ids = set()
        for identity in self.identities:
            if not isinstance(identity, TowerIdentity): raise ValueError('Invalid tower identity')
            integer(identity.entity_id, 'tower entity ID', 1)
            integer(identity.owner, 'tower owner', 0, 1)
            location = (identity.owner, identity.x, identity.y)
            if self.standard_locations.get(location) != identity.role or location in locations or identity.entity_id in ids:
                raise ValueError('Noncanonical or ambiguous tower roster')
            locations.add(location); ids.add(identity.entity_id)
        if locations != set(self.standard_locations): raise ValueError('Incomplete standard tower roster')

    @classmethod
    def from_reset(cls, state):
        if state.get('schema') != 'cr-minimal.v4' or state.get('ok') is not True:
            raise ValueError('Unsupported native capture schema')
        generation = integer(state.get('generation'), 'generation', 1)
        integer(state.get('tick'), 'reset initialization tick', 0, 90)
        if state.get('finalized') is not False:
            raise ValueError('Tower identities require a nonterminal reset')
        found = {}; ids = set()
        for entity in state['entities']:
            if len(entity) != 11:
                raise ValueError('Unsupported native entity layout')
            eid, owner, card, x, y, hp, max_hp, _, _, kind, _ = entity
            location = (owner, x, y)
            if card != -1 or kind != 'building' or location not in cls.standard_locations:
                continue
            integer(eid, 'tower entity ID', 1)
            integer(hp, 'tower HP', 1); integer(max_hp, 'tower max HP', hp)
            if location in found or eid in ids:
                raise ValueError('Ambiguous tower identity')
            found[location] = TowerIdentity(eid, owner, cls.standard_locations[location], x, y)
            ids.add(eid)
        if set(found) != set(cls.standard_locations):
            raise ValueError('Six standard reset towers not identified; do not guess')
        return cls(generation, tuple(found[key] for key in sorted(found)))


def project_actor(state, owner, towers=None):
    integer(owner, 'native owner', 0, 1)
    if state.get('schema') != 'cr-minimal.v4' or state.get('ok') is not True:
        raise ValueError('Unsupported or invalid native capture')
    generation = integer(state.get('generation'), 'generation', 1)
    tick = integer(state.get('tick'), 'tick')
    owners = [integer(p.get('owner'), 'native player owner', 0, 1) for p in state['players']]
    if sorted(owners) != [0, 1]:
        raise ValueError('Two distinct native player owners required')
    players = [p for p in state['players'] if p['owner'] == owner]
    if len(players) != 1:
        raise ValueError('Own player absent or ambiguous')
    me = players[0]
    hand = []
    for card in me['hand']:
        if len(card) != 5:
            raise ValueError('Unsupported hand layout')
        slot, card_id, cost, _, _ = card
        hand.append({'slot': integer(slot, 'hand slot', 0, 3),
                     'card': integer(card_id, 'card ID', 1), 'cost': integer(cost, 'cost', 0, 100)})
    if len(hand) > 4 or len({c['slot'] for c in hand}) != len(hand):
        raise ValueError('Ambiguous own hand')
    crowns = state.get('crowns')
    if not isinstance(crowns, list) or len(crowns) != 2:
        raise ValueError('Unsupported public crowns')
    for value in crowns:
        integer(value, 'crowns', 0, 3)
    result = {'schema': 'royale-phase2-actor-conservative.v1', 'tick': tick,
              'native_owner': owner, 'hand': sorted(hand, key=lambda c: c['slot']),
              'hand_complete': len(hand) == 4,
              'own_elixir_raw': integer(me['elixir'], 'own elixir', 0, 100000),
              'crowns': {'own': crowns[owner], 'opponent': crowns[1-owner]}, 'towers': []}
    if towers is not None:
        if towers.generation != generation:
            raise ValueError('Reset invalidated tower identities')
        entities = {}
        for entity in state['entities']:
            if len(entity) != 11:
                raise ValueError('Unsupported native entity layout')
            if entity[0] in entities:
                raise ValueError('Duplicate entity ID')
            entities[entity[0]] = entity
        for identity in towers.identities:
            entity = entities.get(identity.entity_id)
            hp = max_hp = None
            if entity is not None:
                if (entity[1], entity[2], entity[3], entity[4], entity[9]) != (identity.owner, -1, identity.x, identity.y, 'building'):
                    raise ValueError('Attested tower identity changed semantics')
                hp = integer(entity[5], 'tower HP')
                max_hp = integer(entity[6], 'tower max HP', max(1, hp))
            result['towers'].append({'side': 'own' if identity.owner == owner else 'opponent',
                                     'role': identity.role, 'hp': hp, 'max_hp': max_hp,
                                     'present': entity is not None})
    return result


def affordability_mask(observation):
    """Own-information budget prefilter only, NOT a full legal placement mask."""
    if observation.get('schema') != 'royale-phase2-actor-conservative.v1':
        raise ValueError('Unsupported actor schema')
    mask = [True, False, False, False, False]  # WAIT then current four hand slots
    for card in observation['hand']:
        mask[card['slot'] + 1] = card['cost'] * 10000 <= observation['own_elixir_raw']
    return {'schema': 'royale-phase2-affordability-only.v1', 'values': mask,
            'full_legality_verified': False, 'excludes': ['placement', 'native command readiness', 'abilities']}

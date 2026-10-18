"""Research battlefield projection with happened-only card history.

The probe has no dependable per-unit visibility flags. This view therefore
includes captured non-tower objects with explicitly unknown visibility; it is
not a certified fair human-player observation. Enemy private resources, raw
captures, attack targets/cooldowns and future replay commands are never passed.
"""
from collections import Counter
from copy import deepcopy

from .visibility import TowerAttestation, TowerIdentity


SCHEMA = 'royale-platform-battlefield-observation.v1'


def _integer(value, name, low=None, high=None):
    if type(value) is not int or low is not None and value < low or high is not None and value > high:
        raise ValueError('Invalid integer: ' + name)
    return value


def _actor(actor, tick):
    if not isinstance(actor, dict) or actor.get('schema') != 'royale-phase2-actor-conservative.v1':
        raise ValueError('Conservative actor observation required')
    if actor.get('tick') != tick or type(actor.get('tick')) is not int:
        raise ValueError('Actor/native tick mismatch')
    owner = _integer(actor.get('native_owner'), 'native_owner', 0, 1)
    hand = []
    if not isinstance(actor.get('hand'), list): raise ValueError('Actor hand array required')
    for row in actor['hand']:
        if not isinstance(row, dict): raise ValueError('Invalid actor hand row')
        hand.append({'slot': _integer(row.get('slot'), 'slot', 0, 3),
                     'card': _integer(row.get('card'), 'card', 1),
                     'cost': _integer(row.get('cost'), 'cost', 0, 100)})
    if len({row['slot'] for row in hand}) != len(hand): raise ValueError('Duplicate actor hand slot')
    if type(actor.get('hand_complete')) is not bool or actor['hand_complete'] != (len(hand) == 4):
        raise ValueError('Actor hand completeness mismatch')
    crowns = actor.get('crowns')
    if not isinstance(crowns, dict): raise ValueError('Actor crowns required')
    towers = []
    if not isinstance(actor.get('towers'), list): raise ValueError('Actor tower array required')
    for row in actor['towers']:
        if not isinstance(row, dict) or row.get('side') not in ('own', 'opponent') or row.get('role') not in ('left', 'right', 'king'):
            raise ValueError('Invalid actor tower descriptor')
        if type(row.get('present')) is not bool: raise ValueError('Invalid tower presence')
        for key in ('hp', 'max_hp'):
            if row.get(key) is not None: _integer(row[key], 'tower ' + key, 0)
        towers.append({key: deepcopy(row.get(key)) for key in ('side', 'role', 'hp', 'max_hp', 'present')})
    return {'native_owner': owner, 'hand': sorted(hand, key=lambda row: row['slot']),
            'hand_complete': actor['hand_complete'],
            'own_elixir_raw': _integer(actor.get('own_elixir_raw'), 'own_elixir_raw', 0, 100000),
            'crowns': {key: _integer(crowns.get(key), 'crowns ' + key, 0, 3) for key in ('own', 'opponent')},
            'towers': towers}


def _event(row, tick):
    if not isinstance(row, (list, tuple)) or len(row) != 5:
        raise ValueError('Native card event requires exactly five columns')
    sequence, event_tick, owner, card_id, cost = row
    event = (_integer(sequence, 'event sequence', 1), _integer(event_tick, 'event tick', 0, tick),
             _integer(owner, 'event owner', 0, 1), _integer(card_id, 'native card identity', 0),
             _integer(cost, 'event cost', 0, 100))
    return event


def _prior_event(row, tick):
    if not isinstance(row, dict) or row.get('source') != 'native_card_consumption_event':
        raise ValueError('Unsupported previous history entry')
    return _event([row.get(key) for key in ('sequence', 'tick', 'owner', 'native_card_id', 'cost_elixir')], tick)


def _tower_roster(raw, previous):
    if previous is not None and previous.get('tower_identity_basis') == 'reset_attested_canonical_six_towers':
        entries = previous.get('tower_identities')
        if not isinstance(entries, list): raise ValueError('Invalid previous tower identities')
        roster = tuple(TowerIdentity(row['entity_id'], row['owner'], row['role'], row['x_raw'], row['y_raw']) for row in entries)
        return TowerAttestation(raw['generation'], roster), 'reset_attested_canonical_six_towers'
    try:
        return TowerAttestation.from_reset(raw), 'reset_attested_canonical_six_towers'
    except (ValueError, KeyError, TypeError):
        # A mid-match first capture has no reset identity proof. Preserve all
        # unclassified objects rather than guess that every -1 ID is a tower.
        return None, 'unknown_without_reset_attestation'


def _entities(raw, attested):
    if not isinstance(raw.get('entities'), list): raise ValueError('Native entities array required')
    towers = {row.entity_id: row for row in attested.identities} if attested else {}
    result = []; seen = set()
    for row in raw['entities']:
        if not isinstance(row, list) or len(row) != 11: raise ValueError('Unsupported native entity layout')
        entity_id = _integer(row[0], 'entity ID', 1)
        if entity_id in seen: raise ValueError('Duplicate native entity ID')
        seen.add(entity_id)
        for index in (1, 2, 3, 4): _integer(row[index], 'entity field')
        for index in (5, 6, 10):
            if row[index] is not None: _integer(row[index], 'entity nullable field', 0)
        if not isinstance(row[9], str): raise ValueError('Invalid producer entity kind')
        if entity_id in towers:
            identity = towers[entity_id]
            if (row[1], row[2], row[3], row[4], row[9]) != (identity.owner, -1, identity.x, identity.y, 'building'):
                raise ValueError('Attested tower ID changed semantics')
            continue
        result.append({'entity_id': entity_id, 'owner': row[1], 'card_id_raw': row[2],
                       'x_raw': row[3], 'y_raw': row[4], 'hp': row[5], 'max_hp': row[6],
                       'native_kind': row[9], 'deployment_remaining_ms': row[10],
                       'visibility': 'unknown',
                       'visibility_reason': 'No validated visibility, invisibility or underground flags in this capture profile'})
    return result


def _requested_position(event, peers, requests, prior):
    sequence, tick, owner, card_id, _ = event
    if card_id == 0: return None, 'opaque_native_card_id'
    if peers[(owner, tick)] != 1: return None, 'ambiguous_native_owner_tick'
    candidates = [action for action in requests if isinstance(action, dict)
                  and type(action.get('tick')) is int and action['tick'] == tick
                  and type(action.get('owner')) is int and action['owner'] == owner]
    if len(candidates) > 1: return None, 'ambiguous_request_owner_tick'
    if not candidates:
        position = prior.get('requested_position') if prior else None
        if (prior and prior.get('position_reason') == 'unique_owner_tick_exact_native_card_id_request'
                and prior.get('position_basis_card_id') == card_id and isinstance(position, dict)
                and type(position.get('x')) is int and 0 <= position['x'] <= 17999
                and type(position.get('y')) is int and 0 <= position['y'] <= 31999):
            return {'x': position['x'], 'y': position['y'], 'units': 'native_requested_coordinate'}, 'unique_owner_tick_exact_native_card_id_request'
        return None, 'matching_request_not_available'
    action = candidates[0]
    if action.get('kind') != 'play_card': return None, 'matching_request_is_not_card_play'
    if type(action.get('card_id')) is not int or action['card_id'] != card_id:
        return None, 'native_card_identity_differs_from_request_no_alias_inference'
    if (type(action.get('x')) is not int or not 0 <= action['x'] <= 17999
            or type(action.get('y')) is not int or not 0 <= action['y'] <= 31999):
        return None, 'requested_coordinates_missing_or_invalid'
    return {'x': action['x'], 'y': action['y'], 'units': 'native_requested_coordinate'}, 'unique_owner_tick_exact_native_card_id_request'


def build_battlefield_observation(raw, actor, actual_events, journal=(), previous=None):
    """Project current objects and real consumption history without private state.

    ``actual_events`` is the current generation's native five-column event list;
    callers must not substitute source replay commands. Journal actions only
    annotate already observed events and never create history. ``previous``
    preserves history/attested tower identities within the same generation.
    Future event ticks, duplicate sequence conflicts and time regression fail
    closed. The native event format has no epoch column; epoch binding of the
    supplied current list remains the caller's responsibility.
    """
    if not isinstance(raw, dict) or raw.get('schema') != 'cr-minimal.v4' or raw.get('ok') is not True:
        raise ValueError('Successful native cr-minimal.v4 capture required')
    generation = _integer(raw.get('generation'), 'generation', 1)
    tick = _integer(raw.get('tick'), 'tick', 0)
    own = _actor(actor, tick)
    if previous is not None:
        if not isinstance(previous, dict) or previous.get('schema') != SCHEMA:
            raise ValueError('Previous battlefield observation schema differs')
        _integer(previous.get('generation'), 'previous generation', 1)
        _integer(previous.get('tick'), 'previous tick', 0)
    reset = previous is not None and previous['generation'] != generation
    previous = None if reset else previous
    if previous is not None and previous['tick'] > tick: raise ValueError('Battlefield tick regressed without a reset')
    if previous is not None and previous.get('native_owner') != own['native_owner']:
        raise ValueError('Native player perspective changed within generation')
    if isinstance(journal, dict): journal = journal.get('actions')
    if not isinstance(journal, (list, tuple)): raise ValueError('Journal action sequence required')
    if not isinstance(actual_events, (list, tuple)): raise ValueError('Native event sequence required')
    old = {}; events = {}
    if previous is not None:
        if not isinstance(previous.get('history'), list): raise ValueError('Previous event history array required')
        for row in previous['history']:
            event = _prior_event(row, previous['tick'])
            if event[0] in events and events[event[0]] != event: raise ValueError('Conflicting previous event sequence')
            events[event[0]] = event; old[event[0]] = row
    for row in actual_events:
        event = _event(row, tick)
        if event[0] in events and events[event[0]] != event: raise ValueError('Conflicting native event sequence')
        events[event[0]] = event
    ordered = [events[key] for key in sorted(events)]
    if any(a[1] > b[1] for a, b in zip(ordered, ordered[1:])):
        raise ValueError('Native event tick order regresses across sequence IDs')
    peers = Counter((event[2], event[1]) for event in ordered)
    history = []
    for event in ordered:
        position, reason = _requested_position(event, peers, journal, old.get(event[0]))
        history.append({'source': 'native_card_consumption_event', 'sequence': event[0], 'tick': event[1],
                        'owner': event[2], 'native_card_id': event[3],
                        'card_identity_status': 'opaque_unknown' if event[3] == 0 else 'native_event_identity_only',
                        'cost_elixir': event[4], 'requested_position': position,
                        'position_basis_card_id': event[3] if position is not None else None,
                        'actual_position': None, 'position_reason': reason})
    attested, tower_basis = _tower_roster(raw, previous)
    entities = _entities(raw, attested)
    return {'schema': SCHEMA, 'information_mode': 'battlefield', 'generation': generation,
            'tick': tick, **own, 'entities': entities, 'history': history,
            'history_reset': reset,
            'tower_identity_basis': tower_basis,
            'tower_identities': [{'entity_id': row.entity_id, 'owner': row.owner, 'role': row.role,
                                  'x_raw': row.x, 'y_raw': row.y} for row in attested.identities] if attested else [],
            'visibility_fairness_verified': False,
            'field_coverage': {
                'may_include_unattested_towers': attested is None,
                'entity_scope': 'All captured objects except reset-attested tower identities; capture sampling may omit transient objects.',
                'visibility': 'Unknown for every object. No assertion that invisible/underground entities would be human-visible.',
                'native_kind': 'Producer category only; does not certify semantic unit/building type.',
                'history_scope': 'Only supplied real native events at or before current tick, plus validated previous same-generation history.',
                'event_epoch': 'Caller must supply actual_events from current generation; native event rows contain no generation column.',
                'position_scope': 'Unique same-owner/tick exact-card request annotation only. Final engine placement and attack/damage causality are unavailable.',
                'excluded': ['enemy hand', 'enemy elixir', 'both private cycles and abilities', 'raw captures',
                             'native attack targets and cooldowns', 'future replay/source commands'],
                'training_visibility_status': 'research_projection_unverified_not_a_fairness_certificate'}}

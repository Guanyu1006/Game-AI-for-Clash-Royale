"""Lossless researcher/oracle interpretation of saved ``cr-minimal.v4`` captures.

This module never contacts a runtime, selects actions, or changes the conservative
actor policy. ``raw`` retains the entire input JSON object. Named columns only
describe what the pinned native producer actually emits; absent data stays null.
In particular a snapshot HP decrease is not an attributed damage/attack event.
"""
from copy import deepcopy
import json


SCHEMA = 'royale-platform-oracle-observation.v1'
SOURCE = 'third_party/phase2/jason/vendor/firstlight/probe/minimal_capture.inc'
ENTITY_FIELDS = ('entity_id', 'owner', 'card_id_raw', 'x_raw', 'y_raw', 'hp',
                 'max_hp', 'target_entity_id', 'attack_cooldown_ms', 'native_kind',
                 'deployment_remaining_ms')
HAND_FIELDS = ('slot', 'card_id', 'cost_elixir', 'packed_card_parameter', 'command_card_id')
ABILITY_FIELDS = ('button_state_raw', 'remaining_cooldown_ms', 'configured_cooldown_ms',
                  'remaining_charges_raw', 'max_charges', 'unit_entity_ids')
EVOLUTION_FIELDS = ('deck_slot', 'card_id', 'evolvable_raw', 'cycle_required',
                    'cycle_remaining', 'ready_raw')
PLAY_FIELDS = ('sequence', 'tick', 'owner', 'effective_card_global_id', 'cost_elixir')


def _int(value, name, minimum=None):
    if type(value) is not int or minimum is not None and value < minimum:
        raise ValueError('Invalid integer at ' + name)
    return value


def _rows(value, name, width):
    if not isinstance(value, list):
        raise ValueError('Expected array at ' + name)
    for index, row in enumerate(value):
        if not isinstance(row, list) or len(row) != width:
            raise ValueError(f'Unsupported {name}[{index}] layout: expected {width} columns')
    return value


def _column_sources(path, fields):
    return {name: f'{path}/{index}' for index, name in enumerate(fields)}


def _unknown(path, reason):
    return {'path': path, 'reason': reason}


def _capture(raw):
    if not isinstance(raw, dict) or raw.get('schema') != 'cr-minimal.v4' or raw.get('ok') is not True:
        raise ValueError('Expected successful cr-minimal.v4 capture')
    # Validate JSON serializability (including rejection of NaN), without mutating input.
    try:
        json.dumps(raw, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Capture must contain finite JSON data') from exc
    _int(raw.get('generation'), '/generation', 1)
    _int(raw.get('tick'), '/tick', 0)
    if not isinstance(raw.get('players'), list) or not isinstance(raw.get('entities'), list):
        raise ValueError('Capture requires players and entities arrays')
    if raw.get('finalized') is not None and type(raw['finalized']) is not bool:
        raise ValueError('Invalid /finalized')
    players = raw['players']
    if len(players) != 2 or any(not isinstance(p, dict) for p in players):
        raise ValueError('Exactly two player objects required')
    owners = [_int(p.get('owner'), '/players/owner') for p in players]
    if sorted(owners) != [0, 1]:
        raise ValueError('Distinct player owners 0 and 1 required')
    return deepcopy(raw)


def _player(player, index, unknown):
    path = f'/players/{index}'
    result = {'owner': player['owner'], 'elixir_raw': player.get('elixir'),
              'elixir': None, 'hand': None, 'hand_complete': None, 'cycle': None,
              'abilities': None, 'evolution': None, 'raw': deepcopy(player),
              'sources': {key: f'{path}/{native}' for key, native in
                          (('owner', 'owner'), ('elixir_raw', 'elixir'),
                           ('hand', 'hand'), ('cycle', 'cycle'),
                           ('abilities', 'abilities'), ('evolution', 'evolution'))}}
    if player.get('elixir') is not None:
        result['elixir'] = _int(player['elixir'], path + '/elixir', 0) / 10000
    else:
        unknown.append(_unknown(path + '/elixir', 'missing_or_null_native_field'))
    for field in ('hand', 'cycle', 'abilities', 'evolution'):
        if player.get(field) is None:
            unknown.append(_unknown(path + '/' + field, 'missing_or_null_native_field'))
    if player.get('hand') is not None:
        hand = _rows(player['hand'], path + '/hand', 5)
        result['hand'] = []
        slots = set()
        for i, row in enumerate(hand):
            for j, value in enumerate(row):
                _int(value, f'{path}/hand/{i}/{j}', 0)
            if row[0] > 3 or row[0] in slots:
                raise ValueError('Duplicate or invalid native hand slot')
            slots.add(row[0])
            named = dict(zip(HAND_FIELDS, row))
            named.update(name=None, sources=_column_sources(f'{path}/hand/{i}', HAND_FIELDS), raw=list(row))
            result['hand'].append(named)
        result['hand_complete'] = slots == {0, 1, 2, 3}
    if player.get('cycle') is not None:
        if not isinstance(player['cycle'], list):
            raise ValueError('Invalid native cycle array')
        result['cycle'] = [_int(value, path + '/cycle', 0) for value in player['cycle']]
    if player.get('abilities') is not None:
        result['abilities'] = []
        for i, row in enumerate(_rows(player['abilities'], path + '/abilities', 6)):
            for j, value in enumerate(row[:5]):
                _int(value, f'{path}/abilities/{i}/{j}')
            if not isinstance(row[5], list):
                raise ValueError('Invalid champion entity IDs')
            for value in row[5]:
                _int(value, path + '/abilities/unit_entity_ids', 1)
            named = dict(zip(ABILITY_FIELDS, deepcopy(row)))
            named.update(ability_identity=None, button_state_meaning=None,
                         sources=_column_sources(f'{path}/abilities/{i}', ABILITY_FIELDS), raw=deepcopy(row))
            result['abilities'].append(named)
        if not result['abilities']:
            unknown.append(_unknown(path + '/abilities',
                           'empty_array_does_not_distinguish_no_controller_from_runtime_validity_flags'))
    if player.get('evolution') is not None:
        result['evolution'] = []
        slots = set()
        for i, row in enumerate(_rows(player['evolution'], path + '/evolution', 6)):
            for j, value in enumerate(row):
                _int(value, f'{path}/evolution/{i}/{j}', 0)
            if row[0] > 7 or row[0] in slots or row[2] not in (0, 1) or row[5] not in (0, 1):
                raise ValueError('Invalid native evolution slot/flag')
            slots.add(row[0])
            named = dict(zip(EVOLUTION_FIELDS, row))
            named.update(evolvable=bool(row[2]), ready=bool(row[5]),
                         sources=_column_sources(f'{path}/evolution/{i}', EVOLUTION_FIELDS), raw=list(row))
            result['evolution'].append(named)
        if not result['evolution']:
            unknown.append(_unknown(path + '/evolution', 'runtime_validity_flag_not_exported'))
    return result


def _entities(raw, unknown):
    result = []
    ids = set()
    for i, row in enumerate(_rows(raw['entities'], '/entities', 11)):
        path = f'/entities/{i}'
        for j in range(5):
            _int(row[j], f'{path}/{j}', 1 if j == 0 else None)
        for j in (5, 6, 7, 8, 10):
            if row[j] is not None:
                _int(row[j], f'{path}/{j}', 0)
        if not isinstance(row[9], str):
            raise ValueError('Invalid native entity kind')
        if row[0] in ids:
            raise ValueError('Duplicate entity ID')
        ids.add(row[0])
        named = dict(zip(ENTITY_FIELDS, row))
        named.update(name=None, semantic_type=None,
                     sources=_column_sources(path, ENTITY_FIELDS), raw=list(row))
        for j in (5, 6, 7, 8, 10):
            if row[j] is None:
                unknown.append(_unknown(f'{path}/{j}',
                               'native_null_not_applicable_no_target_or_unavailable_not_disambiguated'))
        result.append(named)
    return result


def _plays(raw, unknown):
    if raw.get('plays') is None:
        unknown.append(_unknown('/plays', 'missing_or_null_native_field'))
        return None
    result = []
    sequences = set()
    for i, row in enumerate(_rows(raw['plays'], '/plays', 5)):
        for j, value in enumerate(row):
            _int(value, f'/plays/{i}/{j}', 1 if j == 0 else 0)
        if row[0] in sequences or row[1] > raw['tick'] or row[2] not in (0, 1):
            raise ValueError('Invalid or duplicate consumption event')
        sequences.add(row[0])
        named = dict(zip(PLAY_FIELDS, row))
        named.update(kind='native_card_consumption',
                     sources=_column_sources(f'/plays/{i}', PLAY_FIELDS), raw=list(row),
                     requested_card_id=None, x_raw=None, y_raw=None,
                     caveat='effective native identity; requested root, coordinates and causal effects are not exported')
        result.append(named)
    if raw.get('next') is not None:
        next_sequence = _int(raw['next'], '/next', 1)
        if any(play['sequence'] >= next_sequence for play in result):
            raise ValueError('Consumption sequence must precede native next cursor')
    return result


def _deltas(current, previous):
    base = {'comparable': False, 'reason': 'no_previous_capture',
            'from_tick': None, 'to_tick': current['tick'], 'tick_gap': None,
            'added_entities': [], 'disappeared_entities': [], 'entity_changes': [],
            'player_changes': [], 'native_consumption_events': deepcopy(current['native_consumption_events']),
            'causal_combat_events': None,
            'caveat': 'Snapshot differences only. Presence is not spawn/death proof; HP loss has no damage source attribution.'}
    if previous is None:
        return base
    before_raw = previous.get('raw') if isinstance(previous, dict) and previous.get('schema') == SCHEMA else previous
    before = build_observation(before_raw)
    base['from_tick'] = before['tick']
    if before['generation'] != current['generation']:
        base['reason'] = 'generation_changed'
        return base
    if before['tick'] > current['tick']:
        base['reason'] = 'tick_regressed'
        return base
    base.update(comparable=True, reason=None, tick_gap=current['tick'] - before['tick'])
    old = {row['entity_id']: row for row in before['entities']}
    new = {row['entity_id']: row for row in current['entities']}
    base['added_entities'] = [deepcopy(new[eid]) for eid in sorted(new.keys() - old.keys())]
    base['disappeared_entities'] = [deepcopy(old[eid]) for eid in sorted(old.keys() - new.keys())]
    for eid in sorted(old.keys() & new.keys()):
        changes = {key: {'before': old[eid][key], 'after': new[eid][key]}
                   for key in ENTITY_FIELDS[1:] if old[eid][key] != new[eid][key]}
        if changes:
            base['entity_changes'].append({'entity_id': eid, 'fields': changes})
    old_players = {p['owner']: p for p in before['players']}
    for player in current['players']:
        old_player = old_players[player['owner']]
        changes = {key: {'before': deepcopy(old_player['raw'].get(native)),
                         'after': deepcopy(player['raw'].get(native))}
                   for key, native in (('elixir_raw', 'elixir'), ('hand', 'hand'), ('cycle', 'cycle'),
                                       ('abilities', 'abilities'), ('evolution', 'evolution'))
                   if old_player['raw'].get(native) != player['raw'].get(native)}
        if changes:
            base['player_changes'].append({'owner': player['owner'], 'fields': changes})
    if current['native_consumption_events'] is not None:
        seen = {e['sequence'] for e in before['native_consumption_events'] or []}
        cursor = before['event_cursor_next']
        base['native_consumption_events'] = [deepcopy(e) for e in current['native_consumption_events']
                                             if e['sequence'] not in seen and
                                             (cursor is None or e['sequence'] >= cursor)]
    return base


def build_observation(raw, previous=None):
    """Build JSON-serializable oracle telemetry without runtime access.

    ``raw`` must be a successful v4 capture object, not a state-file envelope.
    ``previous`` can be a raw capture or a prior observation. Missing optional
    data becomes null plus an unknown reason; malformed rows fail closed with
    ValueError. This is researcher information, including enemy private state.
    It does not certify complete game-state coverage or official equivalence.
    """
    raw = _capture(raw)
    unknown = []
    if raw.get('next') is not None:
        _int(raw['next'], '/next', 1)
    else:
        unknown.append(_unknown('/next', 'missing_or_null_event_cursor'))
    if raw.get('finalized') is None:
        unknown.append(_unknown('/finalized', 'missing_or_null_native_field'))
    players = [_player(player, i, unknown) for i, player in enumerate(raw['players'])]
    entities = _entities(raw, unknown)
    plays = _plays(raw, unknown)
    crowns = raw.get('crowns')
    if crowns is not None:
        if not isinstance(crowns, list) or len(crowns) != 2:
            raise ValueError('Invalid native crowns')
        for value in crowns:
            _int(value, '/crowns', 0)
    else:
        unknown.append(_unknown('/crowns', 'missing_or_null_native_field'))
    result = {'schema': SCHEMA, 'information_mode': 'researcher_oracle',
              'actor_safe': False, 'native_schema': raw['schema'],
              'generation': raw['generation'], 'tick': raw['tick'],
              'finalized': raw.get('finalized'), 'result_raw': raw.get('result'),
              'crowns': deepcopy(crowns), 'players': players, 'entities': entities,
              'native_consumption_events': plays, 'event_cursor_next': raw.get('next'),
              'raw': raw,
              'field_coverage': {
                  'complete_game_state': False, 'all_input_json_preserved': True,
                  'all_entity_rows_preserved': True,
                  'producer': {'path': SOURCE, 'function': 'build_minimal_capture',
                               'entity_layout': list(ENTITY_FIELDS), 'hand_layout': list(HAND_FIELDS),
                               'ability_layout': list(ABILITY_FIELDS),
                               'evolution_layout': list(EVOLUTION_FIELDS), 'play_layout': list(PLAY_FIELDS)},
                  'units': {'position': 'native raw coordinates; no tile conversion assumed',
                            'elixir_raw_per_elixir': 10000,
                            'elixir_scale_source': 'third_party/phase2/jason/vendor/firstlight/probe/remaining_runtime_layout.h:kResourceFixedPointScale',
                            'cooldown_and_deployment': 'milliseconds, producer rounds remaining values upward to 50 ms'},
                  'unknown': unknown,
                  'not_exported': ['verified entity display names and semantic unit/building types',
                                   'damage/projectile-hit/attack/death causal events',
                                   'buffs, shields, invisibility, collision/path state and full component inventory',
                                   'projectile source, velocity, damage and complete area-effect parameters',
                                   'requested root card identity and play coordinates in consumption events',
                                   'ability identity, button-state meaning and runtime validity flags'],
                  'notes': [
                      'native_kind is producer classification only: projectile, card_id==-1 building, other HP-bearing object troop, remaining object area. It is not verified semantic type.',
                      'Null HP/target/cooldown/deployment does not imply zero; native absence, inapplicability and unavailable values are not fully distinguished.',
                      'Snapshot sampling can miss objects and interactions occurring between captures.',
                      'plays contains actual native consumption events in the supplied cursor window; complete battle event history is not guaranteed by one capture.',
                      'Complete raw capture is not complete internal engine memory, human-visible observation, or a proof of official battle equivalence.']}}
    result['deltas'] = _deltas(result, previous)
    return result

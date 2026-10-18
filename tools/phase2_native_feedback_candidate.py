"""Uninstalled pure feedback proposal. No socket/client construction or RPC.

This file is an independently testable source candidate for a later reviewed
backend patch, not the backend used by the ongoing frozen formal batch.
"""
import csv
import hashlib
import io
import lzma

from royale_phase2.runtime import local


def _read_locked_elixir_rates(manifest):
    """Resolve mode IDs -> named CSV timelines, verifying pinned bytes first."""
    prefix = 'third_party/phase2/runtime-assets/full-update/csv_logic/'
    assets = {row['path'].replace('\\', '/'): row for row in manifest['assets']}
    def rows(name):
        path = prefix + name + '.csv'
        if path not in assets:
            return None
        data = local(path).read_bytes()
        if hashlib.sha256(data).hexdigest() != assets[path]['sha256']:
            raise ValueError('Elixir timeline source differs from locked manifest: ' + path)
        plain = data if data.startswith(b'"Name"') else lzma.decompress(
            data[:5] + data[5:9] + b'\0' * 4 + data[9:], format=lzma.FORMAT_ALONE)
        return list(csv.DictReader(io.StringIO(plain.decode('utf-8-sig'))))[1:]
    modes = rows('game_modes'); timelines = rows('battle_timelines')
    if modes is None or timelines is None:
        return {}
    rates = {}; current = None; tick = 0; unsupported = set()
    for row in timelines:
        if row.get('Name'):
            current = row['Name']; rates[current] = []; tick = 0
        length = row.get('ElixirRateLength'); bar = row.get('ElixirFullBarMS')
        if current is not None and length and bar:
            duration = int(length) * 20; full_bar_ms = int(bar)
            if duration <= 0 or full_bar_ms <= 0:
                # Zero-length/PvE conventions need separate native evidence.
                unsupported.add(current)
                continue
            rates[current].append((tick, tick + duration, 100000 * 50 // full_bar_ms))
            tick += duration
    # Only Ladder/Default has measured native integer increments in the
    # current fixture suite; existence of another CSV mode is not evidence.
    return {72000000 + index: tuple(rates.get(row['BattleTimeline'], ()))
            if (72000000 + index == 72000006 and row['Name'] == 'Ladder'
                and row['BattleTimeline'] == 'Default' and row['BattleTimeline'] not in unsupported) else ()
            for index, row in enumerate(r for r in modes if r.get('Name'))}


def _pending_draw_boundary(action, receipt, candidates, competing, embedded, schedules, match):
    """Prove selected-slot removal while the next draw is pending.

    Unknown mode, phase boundary, missing exact-tick state, any other hand
    mutation, differing cycle, cost, packed parameter or elixir -> no proof.
    Event card identity remains the caller's exact/explicit-alias gate.
    """
    before = action.get('before_registration', {}); after = action.get('after_execution', {})
    tick = action['tick']
    if (action['kind'] != 'play_card' or receipt.get('state') != 'succeeded'
            or embedded or len(candidates) != 1 or len(competing) != 1
            or before.get('tick') != tick-1 or after.get('tick') != tick):
        return False
    registration = action.get('registration_receipt', {})
    if (receipt.get('owner') != action['owner'] or receipt.get('cardId') != action['card_id']
            or receipt.get('executeTick') != tick or receipt.get('registeredAtTick') != tick-1
            or receipt.get('queuedAtTick') != tick-1
            or receipt.get('sequence') != registration.get('sequence')
            or receipt.get('generation') != action.get('generation')
            or registration.get('executeTick') != tick or registration.get('registeredAtTick') != tick-1
            or candidates[0][1] != tick or candidates[0][2] != action['owner']):
        return False
    selected = [h for h in before.get('hand', ()) if h[1] == action.get('card_id')]
    if len(selected) != 1:
        return False
    selected = selected[0]
    hand_before = before.get('hand', ()); hand_after = after.get('hand', ())
    if (len(hand_before) != 4 or len(hand_after) != 3
            or len({h[0] for h in hand_before}) != 4 or len({h[0] for h in hand_after}) != 3
            or sorted(hand_after) != sorted(h for h in hand_before if h[0] != selected[0])
            or after.get('cycle') != before.get('cycle', []) + [action['card_id']]
            or receipt.get('resolvedCommandCardId') != action['card_id']
            or receipt.get('resolvedCardParameter') != selected[3]
            or candidates[0][4] != selected[2]):
        return False
    game_mode = match.get('battle', {}).get('gamemode')
    # Never guess whether the rate switch applies before or after a boundary
    # tick. Such uncommon boundaries remain unconfirmed until measured.
    regen = next((rate for start, end, rate in schedules.get(game_mode, ())
                  if start < tick < end and start <= before['tick'] < end), None)
    if regen is None or type(before.get('elixir')) is not int or type(after.get('elixir')) is not int:
        return False
    cost = selected[2] * 10000
    return bool(before['elixir'] >= cost
                and after['elixir'] == min(100000, before['elixir'] - cost + regen))

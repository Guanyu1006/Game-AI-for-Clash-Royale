"""Small, replaceable rule that actually reads battlefield entities/history.

run_platform.ps1 -Mode Agent -PolicyFile examples/platform_battlefield_policy.py
This demonstrates the input contract, not an optimized or validated strategy.
Card IDs, raw positions and ticks refer to the pinned native version.
"""


def decide(observation):
    if observation.get('information_mode') != 'battlefield':
        raise ValueError('This example requires -InformationMode battlefield')
    owner = observation['native_owner']; tick = observation['tick']
    own_plays = [row for row in observation['history'] if row['owner'] == owner]
    # History is actual consumption, not merely our submitted commands.
    if own_plays and tick - own_plays[-1]['tick'] < 40:
        return {'kind': 'wait'}
    cards = [card for card in observation['hand']
             if card['cost'] * 10000 <= observation['own_elixir_raw']]
    if not cards: return {'kind': 'wait'}
    enemies = [entity for entity in observation['entities']
               if entity['owner'] == 1-owner and (entity['hp'] or 0) > 0]
    # owner 0's towers are at small y. Use nearest threat's x for defense.
    enemies.sort(key=lambda row: row['y_raw'] if owner == 0 else -row['y_raw'])
    spells = [card for card in cards if 28000000 <= card['card'] < 29000000]
    if enemies and spells:
        card = min(spells, key=lambda row: (row['cost'], row['slot']))
        x, y = enemies[0]['x_raw'], enemies[0]['y_raw']
    else:
        units = [card for card in cards if 26000000 <= card['card'] < 28000000]
        if not units: return {'kind': 'wait'}
        card = min(units, key=lambda row: (row['cost'], row['slot']))
        # Alternate lanes from happened-only history when no threat exists.
        x = enemies[0]['x_raw'] if enemies else (3500 if len(own_plays) % 2 == 0 else 14500)
        y = 11000 if owner == 0 else 21000
    return {'kind': 'play_card', 'slot': card['slot'],
            'x': max(0, min(17999, int(x))), 'y': max(0, min(31999, int(y)))}

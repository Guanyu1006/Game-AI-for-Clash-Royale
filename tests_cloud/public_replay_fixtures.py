"""Synthetic card identities for public-replay contracts, never game assets.

Only catalog loading is substituted. The real alias resolver, source converter,
deal selection, match template and archived commands remain under test.
"""
from contextlib import contextmanager
import os
import sys
from unittest.mock import patch


@contextmanager
def public_replay_card_catalog():
    from royale_phase2.public_replay import _converter

    original_path = sys.path[:]
    # The converter sets two deployment variables and its import path. Keep
    # those test-local, and clear its index cache so synthetic cards cannot leak.
    with patch.dict(os.environ):
        replay = None
        try:
            _converter()
            from native_runner import card_specs, royaleapi_replay as replay
            from native_runner.contracts import CardSpecV1

            cards = (
                (26000000, 'knight', 'troop'),
                (26000001, 'archers', 'troop'),
                (26000002, 'goblins', 'troop'),
                (26000003, 'giant', 'troop'),
                (26000014, 'musketeer', 'troop'),
                (26000018, 'mini-pekka', 'troop'),
                (27000000, 'cannon', 'building'),
                (28000000, 'fireball', 'spell'),
                (28000001, 'arrows', 'spell'),
            )
            catalog = card_specs.CardSpecCatalog(
                specs=tuple(CardSpecV1(card_id=card_id, name=name, kind=kind)
                            for card_id, name, kind in cards),
                source_release='synthetic-public-replay-test-only',
                warnings=('Identity fixture only; no balance or native-runtime evidence.',),
            )
            replay._load_card_index.cache_clear()
            with patch.object(card_specs, 'build_card_catalog', return_value=catalog) as load:
                yield load
        finally:
            if replay is not None:
                replay._load_card_index.cache_clear()
            sys.path[:] = original_path

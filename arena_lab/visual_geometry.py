"""Frozen public placement geometry; no live state or private hand is consulted.

Regenerate explicitly with ``python -m arena_lab.visual_geometry --output PATH``.
The exporter passes a deliberately minimal opening-board object to point_grid:
only map geometry, tower boxes/radii and the fact that all towers are alive exist.
It contains no elixir, hand, queue, tick, HP quantity, or dynamic entity state.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import inspect
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np

SCHEMA = "arena-public-static-geometry-v1"
ASSET = Path(__file__).with_name("visual_geometry_v1.json")


def object_sha256(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     allow_nan=False).encode()).hexdigest()


def load_geometry(base_identity, native_names, geometry=None):
    value = json.loads(ASSET.read_text()) if geometry is None else deepcopy(geometry)
    if value.get("schema") != SCHEMA or value.get("grid_shape") != [32, 18]:
        raise ValueError("Unsupported static visual geometry schema/grid")
    engine_hash = value.get("engine_binary_sha256")
    if not isinstance(engine_hash, str) or len(engine_hash) != 64 or engine_hash != base_identity.get("binary_sha256"):
        raise ValueError("Static visual geometry engine binary SHA differs from base identity")
    if value.get("source_sha256") != object_sha256(value.get("source")):
        raise ValueError("Static visual geometry source checksum drift")
    cards = value.get("cards", {})
    if set(cards) != {str(native) for native in native_names}:
        raise ValueError("Static visual geometry card set differs")
    masks = {}
    for native, name in native_names.items():
        row = cards[str(native)]
        mask = row.get("candidate_mask")
        if row.get("name") != name or not isinstance(mask, list) or len(mask) != 576 or any(type(x) is not bool for x in mask):
            raise ValueError("Static visual geometry card/mask malformed")
        masks[native] = np.asarray(mask, dtype=bool)
        masks[native].flags.writeable = False
    return value, masks


def export_geometry():
    # Import the engine only when exporting; runtime deployment needs JSON only.
    from .environment import ArenaConfig, ArenaEnv
    from .pixel_policy import NATIVE_NAMES
    import msgspec
    from royalegym.action import PlacementOracle
    from royalegym.protocol import EntityKind

    env = ArenaEnv(ArenaConfig(seed=0, shuffle=0))
    try:
        env.reset()
        initial = env.state
        if initial.tick != 0 or len(initial.entities) != 6 or initial.spells:
            raise ValueError("Expected only six public towers on the opening board")
        towers = []
        for entity in initial.entities:
            if entity.kind not in (EntityKind.KING_TOWER, EntityKind.PRINCESS_TOWER) or entity.hp <= 0:
                raise ValueError("Opening board contains a non-tower or destroyed tower")
            towers.append({"x": entity.x, "y": entity.y, "radius": entity.radius,
                           "kind": int(entity.kind), "team": entity.team,
                           "hp": 1, "footprint": list(entity.footprint) if entity.footprint else None})
        # hp is a boolean alive sentinel, never the engine's actual hit points.
        public = SimpleNamespace(entities=[SimpleNamespace(**dict(t, footprint=tuple(t["footprint"]) if t["footprint"] else None)) for t in towers],
                                 players=[SimpleNamespace(tower_hp=[1, 1, 1]) for _ in range(2)], spells=[])
        identity = env.identity()
        source = {"engine_version": identity["engine_version"], "gym_version": identity["gym_version"],
                  "arena": msgspec.to_builtins(env.arena),
                  "rules": msgspec.to_builtins(env.engine.rules()), "initial_public_towers": towers,
                  "oracle_source_sha256": hashlib.sha256(Path(inspect.getfile(PlacementOracle)).read_bytes()).hexdigest(),
                  "exporter_source_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                  "recipe": "point_grid(public opening geometry, seat, card, pitch_div=1); rotate red to own frame; intersect seats; non-spells restricted to own rows 0..14"}
        result = {"schema": SCHEMA, "grid_shape": [32, 18],
                  "engine_binary_sha256": identity["binary_sha256"],
                  "source": source, "source_sha256": object_sha256(source),
                  "semantics": "Static candidate positions, all six initial towers standing. Dynamic occupancy, destroyed towers, deployment lockout and command readiness remain unknown; native feedback is authoritative.",
                  "cards": {}}
        for native, name in NATIVE_NAMES.items():
            card = next(card for card in env.cards if card.name == name)
            own = []
            for seat in (0, 1):
                grid = env.oracle.point_grid(public, seat, card, 1).copy()
                if seat == 1:
                    grid = grid[::-1, ::-1].copy()
                if card.card_kind != "SPELL":
                    grid[15:] = False
                own.append(grid)
            mask = own[0] & own[1]
            result["cards"][str(native)] = {"name": name, "candidate_mask": mask.reshape(-1).tolist(),
                "candidate_count": int(mask.sum()), "own_frame_seat_masks_equal": bool(np.array_equal(*own)),
                "seat_candidate_counts": [int(g.sum()) for g in own]}
        load_geometry(identity, NATIVE_NAMES, result)
        return result
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    destination = Path(args.output)
    if destination.exists():
        raise FileExistsError("Preserve frozen geometry: export to a new path for review")
    value = export_geometry()
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(value, sort_keys=True, separators=(",", ":"))+"\n")
    print(json.dumps({"path": str(destination), "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
                      "candidate_counts": {row["name"]: row["candidate_count"] for row in value["cards"].values()}}))


if __name__ == "__main__":
    main()

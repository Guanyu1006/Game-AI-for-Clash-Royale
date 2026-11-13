"""Declared public-observation heuristic opponent, never an expert-data source."""
from __future__ import annotations

import numpy as np


def scripted_action(observation, *, catalogue, grid_w=18, grid_h=32, rng=None):
    """Defend visible invasions, reserve elixir, then push the weaker lane.

    This deliberately transparent opponent only reads the same actor arrays. It
    does not inspect engine state, enemy hand/elixir, future trajectories or RNG.
    Its strength is uncalibrated and a win against it is not a ladder rating.
    """
    obs = observation
    legal = np.asarray(obs["legal_cards"], dtype=bool)
    positions = np.asarray(obs["legal_positions"], dtype=bool)
    units = np.asarray(obs["entities"])[np.asarray(obs["entity_mask"], dtype=bool)]
    enemy = units[(units[:, 3] < .5) & ((units[:, 4] > .5) | (units[:, 5] > .5))]
    threats = enemy[enemy[:, 1] < .54]
    towers = units[(units[:, 3] < .5) & (units[:, 7] > .5)]
    target_tower = min(towers, key=lambda row: float(row[2])) if len(towers) else None
    lane = float(target_tower[0]) if target_tower is not None else .2
    elixir = float(obs["global_features"][4]) * 10
    candidates = []

    def nearest(slot, x, y):
        indices = np.flatnonzero(positions[slot])
        if not len(indices):
            return None
        xgrid = (indices % grid_w + .5) / grid_w
        ygrid = (indices // grid_w + .5) / grid_h
        return int(indices[np.argmin(((xgrid-x)*grid_w)**2 + ((ygrid-y)*grid_h)**2)])

    damage_spells = {"Fireball", "Arrows", "Zap", "Poison", "Rocket", "Lightning",
                     "Log", "Snowball", "BarbLog", "Earthquake", "DarkMagic", "Vines"}
    for slot, shifted_id in enumerate(obs["hand_ids"]):
        if not legal[slot+1] or shifted_id <= 0:
            continue
        card = catalogue[int(shifted_id)-1]
        get = (lambda key: card[key]) if isinstance(card, dict) else (lambda key: getattr(card, key))
        name, kind = get("name"), get("card_kind")
        cost = float(obs["global_features"][16+slot])*10
        score, point = -100., None
        if kind == "SPELL":
            if name in damage_spells:
                for target in enemy:
                    distance = np.linalg.norm((enemy[:, :2]-target[:2])*[grid_w, grid_h], axis=1)
                    hits = int((distance < 2.5).sum())
                    value = 1.2*hits - .4*cost + (1 if target[1] < .5 else 0)
                    if value > score and hits >= 2:
                        score, point = value, target[:2]
                if target_tower is not None and target_tower[2] < .08:
                    score, point = 7., target_tower[:2]
            elif name in {"GoblinBarrel", "Graveyard"} and elixir >= 7:
                score, point = 1.5, (lane, .79)
        elif len(threats):
            threat = min(threats, key=lambda row: float(row[1]))
            score = 4. + .5*len(threats) - .15*cost
            if kind == "BUILDING":
                point = (.5, .34)
            else:
                point = (float(threat[0]), max(.15, min(.44, float(threat[1])-.07)))
        elif elixir >= 8:
            if kind == "BUILDING":
                score, point = .1, (.5, .34)
            else:
                score, point = 1. + .2*cost, (lane, .40 if cost <= 4 else .14)
        if point is not None and score > 0:
            pos = nearest(slot, *point)
            if pos is not None:
                candidates.append((score, slot+1, pos))
    # Ability ownership/cooldown/cost are reflected in the public legality mask.
    if len(threats) >= 2:
        for ability in range(5, len(legal)):
            if legal[ability]:
                candidates.append((5., ability, 0))
    if not candidates:
        return {"card": 0, "position": 0}
    _, card, position = max(candidates)
    return {"card": card, "position": position}

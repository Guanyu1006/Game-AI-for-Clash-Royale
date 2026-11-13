"""Explicitly masked visual observation contract and learned screenshot controller.

This bridge is intentionally a new model input version. An old fully observed
checkpoint cannot be used: absent HP, clock, levels and abilities have known=0.
Simulator-projected training is a transfer candidate, not measured real pixels.
"""
from __future__ import annotations

from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path

import numpy as np

from .model import PolicyAgent, load_policy
from .visual_geometry import load_geometry, object_sha256

VERSION = "arena-masked-pixel-v3"
NATIVE_NAMES = {26000000: "Knight", 26000001: "Archer", 26000003: "Giant",
                26000014: "Musketeer", 26000018: "MiniPekka", 27000000: "Cannon",
                28000000: "Fireball", 28000001: "Arrows"}
DETECTOR_UNITS = {"knight": "Knight", "archer": "Archer", "archers": "Archer",
                  "giant": "Giant", "musketeer": "Musketeer", "mini_pekka": "MiniPekka",
                  "minipekka": "MiniPekka", "mini-pekka": "MiniPekka", "cannon": "Cannon"}


class PixelProjection:
    def __init__(self, base_identity, catalogue, geometry=None):
        self.base = deepcopy(base_identity)
        self.catalogue = catalogue
        self.by_name = {card["name"]: card for card in catalogue}
        self.cards = self.base["cards"]
        self.units = self.base["unit_types"]
        self.gdim = self.base["obs_spec"]["global_dim"]
        self.edim = self.base["obs_spec"]["entity_dim"]
        self.capacity = self.base["obs_spec"]["max_entities"]
        self.native_to_shifted = {native: self.cards.index(name)+1 for native, name in NATIVE_NAMES.items()}
        self.shifted_to_native = {value: key for key, value in self.native_to_shifted.items()}
        self.geometry, self.position_candidates = load_geometry(self.base, NATIVE_NAMES, geometry)

    def identity(self):
        result = deepcopy(self.base)
        result["observation_version"] = VERSION
        result["obs_spec"]["global_dim"] = self.gdim*2
        result["obs_spec"]["entity_dim"] = self.edim*2
        result["visual_projection"] = {
            "schema": VERSION, "base_identity": self.base, "catalogue": self.catalogue,
            "known_masks": "Second half of each feature vector; missing numerical values always zero with known=0",
            "training_scope": "same-version simulator projection with unavailable pixel fields removed; not real detector outputs",
            "supported_native_cards": NATIVE_NAMES,
            "team_known": False,
            "position_mask": "frozen initial public map/tower geometry; conservative own half; candidate mask, dynamic occupancy/lockout unknown; abilities disabled",
            "geometry": self.geometry, "geometry_sha256": object_sha256(self.geometry),
            "unknown_fields": ["clock", "HP", "crowns", "levels", "statuses", "abilities", "next_card", "enemy_private_state"],
        }
        return json.loads(json.dumps(result))

    def empty(self):
        return {"global_features": np.zeros(self.gdim*2, np.float32),
                "entities": np.zeros((self.capacity, self.edim*2), np.float32),
                "entity_mask": np.zeros(self.capacity, bool), "hand_ids": np.zeros(4, np.int64),
                "legal_cards": np.array([True]+[False]*7), "legal_positions": np.zeros((4, 576), bool)}

    def masks(self, observation, ready):
        elixir_known = observation["global_features"][self.gdim+4] > .5
        elixir = observation["global_features"][4]*10
        for slot, cid in enumerate(observation["hand_ids"]):
            if cid not in self.shifted_to_native:
                continue
            card = self.by_name[self.cards[cid-1]]
            observation["global_features"][16+slot] = card["elixir"]/10
            observation["global_features"][self.gdim+16+slot] = 1
            candidates = self.position_candidates[self.shifted_to_native[cid]]
            available = elixir_known and ready[slot] and card["elixir"] <= elixir+1e-5 and bool(candidates.any())
            observation["legal_cards"][slot+1] = available
            if available:
                observation["legal_positions"][slot] = candidates
        return observation

    def entity(self, x, y, unit):
        row = np.zeros(self.edim*2, np.float32)
        row[:2] = [x, y]
        row[self.edim:self.edim+2] = 1
        if unit in self.units and unit in set(DETECTOR_UNITS.values()):
            kind = 5 if unit == "Cannon" else 4
            row[kind] = 1
            row[self.edim+4:self.edim+6] = 1
            unit_start = 21
            row[unit_start+self.units.index(unit)+1] = 1
            row[self.edim+unit_start:self.edim+unit_start+len(self.units)+1] = 1
        # Team classifier has known domain errors. Keep it explicitly unknown
        # in BOTH training and deployment until independently calibrated.
        return row

    def simulator(self, source):
        target = self.empty()
        elixir = float(source["global_features"][4])*10
        target["global_features"][4] = max(0, np.floor(elixir-.25))/10
        target["global_features"][self.gdim+4] = 1
        for slot, cid in enumerate(source["hand_ids"]):
            if int(cid) in self.shifted_to_native:
                target["hand_ids"][slot] = int(cid)
        rows = []
        for row in np.asarray(source["entities"])[np.asarray(source["entity_mask"], bool)]:
            index = int(np.argmax(row[21:21+len(self.units)+1]))-1
            unit = self.units[index] if index >= 0 else None
            if unit in set(DETECTOR_UNITS.values()) and (row[4] > .5 or row[5] > .5):
                rows.append(self.entity(float(row[0]), float(row[1]), unit))
        self._set_entities(target, rows)
        # Readiness beyond visible affordability is deliberately not read from
        # native queue/cooldown state; the simulator remains final authority.
        return self.masks(target, [True]*4)

    def _set_entities(self, target, rows):
        if len(rows) > self.capacity:
            raise ValueError("Pixel observation capacity exceeded; no silent entity dropping")
        rows.sort(key=lambda row: (float(row[0]), float(row[1]), tuple(np.flatnonzero(row[21:self.edim]))))
        if rows:
            target["entities"][:len(rows)] = rows
            target["entity_mask"][:len(rows)] = True

    def pixels(self, source):
        if source.get("source") != "screenshot_pixels_only":
            raise ValueError("Pixel model accepts real screenshot-derived observations only")
        target = self.empty()
        if source.get("scene_ready") is False:
            return self.masks(target, [False]*4)
        value = source.get("elixir", {}).get("lower_bound")
        if value is not None and np.isfinite(value) and 0 <= value <= 10:
            target["global_features"][4] = value/10
            target["global_features"][self.gdim+4] = 1
        ready = [False]*4
        for hand in source.get("hand", []):
            slot = hand["slot"]
            if type(slot) is not int or not 0 <= slot <= 3:
                raise ValueError("Invalid recognized hand slot")
            if hand.get("known") and hand.get("card_id") in self.native_to_shifted:
                target["hand_ids"][slot] = self.native_to_shifted[hand["card_id"]]
                ready[slot] = bool(hand.get("visually_ready"))
        rows = []
        for detection in source.get("detections", []):
            x, y = detection.get("xy_native_estimate", [None, None])
            if x is None or y is None or not np.isfinite([x, y]).all() or not (0 <= x <= 18000 and 0 <= y <= 32000):
                continue
            unit = DETECTOR_UNITS.get(detection["label"])
            # Unknown classes still occupy the observed battlefield. Dropping
            # them would turn classification uncertainty into invisible units.
            rows.append(self.entity(x/18000, 1-y/32000, unit))
        self._set_entities(target, rows)
        return self.masks(target, ready)


class MaskedArenaEnv:
    """Same simulator, matching projected training/deployment observation fields."""
    def __init__(self, config):
        from .environment import ArenaEnv
        self.env = ArenaEnv(config)
        self.projection = PixelProjection(self.env.identity(), self.env.catalogue())
        self.obs_spec = self.projection.identity()["obs_spec"]

    def identity(self):
        return self.projection.identity()

    def reset(self, **kwargs):
        obs, info = self.env.reset(**kwargs)
        self._public = obs
        return {seat: self.projection.simulator(value) for seat, value in obs.items()}, info

    def step(self, actions):
        obs, rewards, terminated, truncated, info = self.env.step(actions)
        self._public = obs
        return {seat: self.projection.simulator(value) for seat, value in obs.items()}, rewards, terminated, truncated, info

    def scripted_action(self, obs, player=0, rng=None):
        """Reference opponent retains the baseline's declared public numeric view.

        The learned actor still receives only the projected observation. This is
        a stronger-information reference, explicitly not a pixel-only opponent.
        """
        return self.env.scripted_action(self._public[player], player=player, rng=rng)

    def close(self):
        self.env.close()


class PixelModelController:
    def __init__(self, checkpoint, *, maximum_observation_gap_seconds=2.0):
        self.model, self.metadata = load_policy(checkpoint)
        from .timing import TimingGRUPolicy
        if (not isinstance(self.model, TimingGRUPolicy) or not self.metadata.get("trained")
                or self.metadata.get("training_steps", 0) <= 0):
            raise ValueError("Screenshot deployment requires an actually trained TimingGRUPolicy")
        identity = self.metadata["environment_identity"]
        if self.model.spec.observation_version != VERSION or identity.get("observation_version") != VERSION:
            raise ValueError("Real pixels require a trained masked-pixel-v3 checkpoint; v2 cannot represent unknown HP/clock")
        projection = identity["visual_projection"]
        if "geometry" not in projection or projection.get("geometry_sha256") != object_sha256(projection["geometry"]):
            raise ValueError("Trained visual geometry is missing or its checksum differs")
        self.projection = PixelProjection(projection["base_identity"], projection["catalogue"], geometry=projection["geometry"])
        if self.projection.identity() != identity:
            raise ValueError("Visual mapping/schema differs from trained checkpoint")
        self.agent = PolicyAgent(self.model, deterministic=True)
        self.last_timestamp = None
        self.maximum_gap = maximum_observation_gap_seconds

    def reset(self):
        self.agent.reset()
        self.last_timestamp = None

    def decide(self, observation):
        timestamp = float(observation["observation_timestamp"])
        dt = 0. if self.last_timestamp is None else timestamp-self.last_timestamp
        if not np.isfinite(timestamp) or dt < 0:
            raise ValueError("Pixel capture timestamps must be finite and monotonic")
        self.last_timestamp = timestamp
        arrays = self.projection.pixels(observation)
        # Large missing intervals must not silently masquerade as a .5s step.
        # Reset memory and use a current snapshot; this is a slow-loop fallback,
        # explicitly reported, not a claim of trained 8-second recurrence.
        feedback = observation.get("control_feedback", {})
        elapsed = feedback.get("elapsed_ticks")
        time_known = (feedback.get("provenance") == "confirmed_local_step" and
                      feedback.get("tick_hz") == 20 and type(elapsed) in (int, float) and
                      np.isfinite(elapsed) and elapsed >= 0)
        # Wall seconds != simulated seconds in paused/slow Android. Only the
        # controller's own completed step acknowledgement may supply elapsed
        # game time. No clock/private fields are copied from native state.
        wall_dt = dt
        game_ticks = float(elapsed) if time_known else 0.
        reset_due_to_gap = not time_known or game_ticks/20 > self.maximum_gap
        if reset_due_to_gap:
            self.agent.reset()
            game_ticks = 0.
        arrays["elapsed_ticks"] = np.float32(game_ticks)
        arrays["decision_ticks"] = np.float32(self.model.timing_config.decision_ticks)
        action = self.agent.act(arrays)
        common = {"policy": "trained_masked_pixel_v3", "reset_due_to_gap": reset_due_to_gap,
                  "wall_elapsed_seconds": wall_dt, "elapsed_game_time_known": time_known,
                  "elapsed_game_seconds": float(elapsed)/20 if time_known else None,
                  "selected_action_probability": action.get("card_probability")}
        if action["card"] == 0:
            return dict(common, kind="wait", reason="learned_event_wait_or_unknown_hand")
        if not 1 <= action["card"] <= 4:
            raise ValueError("Visual v3 does not support unobserved ability buttons")
        slot, pos = action["card"]-1, action["position"]
        native = self.projection.shifted_to_native[int(arrays["hand_ids"][slot])]
        return dict(common, kind="play_card", slot=slot, card=native,
                    x=int((pos%18+.5)*1000), y=int((32-pos//18-.5)*1000),
                    reason="learned_event_card_position")

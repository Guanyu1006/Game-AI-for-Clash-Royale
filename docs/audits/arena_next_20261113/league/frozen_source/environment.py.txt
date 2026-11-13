"""A two-seat, versioned arena over the real RoyaleSim wheel.

Actor tensors contain simulator-public information, not official-client observations.
Enemy hidden, underground and invisible units are conservatively omitted. Placement
masks are public proposals; only native deploy feedback certifies a play. In particular
we never query the live engine for a mask, which could reveal hidden enemy occupancy.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
import hashlib
import importlib.metadata
import json
from pathlib import Path
import os
import sys
from typing import Any, Mapping, Sequence

import numpy as np

from .bootstrap import activate

activate()
import msgspec
import royalesim
from royalegym import MatchSetup, RustEngine
from royalegym.action import PlacementOracle
from royalegym.protocol import (DeployCommand, DeployStatus, EntityKind, Placement,
                                STATUS_CLONE, STATUS_EVOLVED, STATUS_GROUNDED,
                                STATUS_HERO, STATUS_HIDDEN, STATUS_INVISIBLE,
                                STATUS_UNDERGROUND, slot_cost, to_engine, to_own)

DEFAULT_DECK = ("Knight", "Archer", "Goblins", "Giant", "Musketeer", "Fireball", "Arrows", "Skeletons")
GLOBAL_BASE_FIELDS = ("regulation_clock", "overtime_clock", "elixir_rate", "overtime",
                      "own_elixir", "own_pending_cost", "own_crowns", "enemy_crowns",
                      "own_king_hp", "own_left_hp", "own_right_hp", "enemy_king_hp",
                      "enemy_left_hp", "enemy_right_hp", "own_king_active", "enemy_king_active",
                      "hand0_cost", "hand1_cost", "hand2_cost", "hand3_cost") + tuple(
                          f"ability{k}_{name}" for k in range(3)
                          for name in ("known", "available", "spent", "cost", "cooldown"))
ENTITY_BASE_FIELDS = ("x", "y", "hp_ratio", "own_team", "troop", "building", "king", "princess",
                      "spell", "projectile", "radius", "flying", "deploying", "shield_ratio",
                      "level", "evolved", "clone", "hero", "grounded", "stunned", "out_of_arena")


@dataclass
class ArenaConfig:
    backend: str = "royalesim"
    decks: Sequence[Sequence[str | int]] = field(default_factory=lambda: [DEFAULT_DECK, DEFAULT_DECK])
    forms: Sequence[Sequence[int]] | None = None
    levels: Sequence[Sequence[int]] | None = None
    tower_levels: Sequence[int] | None = None
    seed: int = 0
    decision_ticks: int = 10
    max_ticks: int | None = None
    shuffle: int = 1
    elixir_milli: Sequence[int] | None = None
    engine_version: str = "0.1.51"
    expected_card_hash: str = "803ec02c7eac9fd3"
    command_delay_ticks: int | None = None
    max_entities: int = 128
    card_level: int = 11
    tower_level: int = 11
    crforge_data_version: str = "16.635.8"
    crforge_expected_content_sha: str = "062a11e0500c03cc041f123413b1ef1846b539a4"
    tower_types: Sequence[str] | None = None


class ArenaEnv:
    """Joint actions use own-frame tile centres: 0 wait, 1..4 cards, 5..7 abilities.

    reset -> (observations, info); step/joint_step -> (observations, rewards,
    terminated, truncated, info). Seat keys are 0/1. Native winner is -1 while
    running, 0/1 for a win and 2 for a draw. No horizon is a synthetic win.
    """

    def __new__(cls, config: ArenaConfig | None = None, **kwargs: Any):
        backend = config.backend if config is not None else kwargs.get("backend", "royalesim")
        if cls is ArenaEnv and backend == "crforge":
            return CrforgeEnv(config, **kwargs)
        if backend != "royalesim":
            raise ValueError(f"Unknown arena backend {backend!r}")
        return super().__new__(cls)

    def __init__(self, config: ArenaConfig | None = None, **kwargs: Any):
        self.config = config or ArenaConfig(**kwargs)
        if config is not None and kwargs:
            raise TypeError("Pass config or keyword settings, not both")
        c = self.config
        if c.decision_ticks < 1 or c.max_entities < 6 or (c.max_ticks is not None and c.max_ticks < 1):
            raise ValueError("decision_ticks/max_entities/max_ticks must be positive")
        if importlib.metadata.version("royalesim") != c.engine_version:
            raise ValueError("Requested engine version differs from pinned installed simulator")
        self.engine = RustEngine(command_delay_ticks=c.command_delay_ticks or 0)
        self.cards = list(self.engine.cards())
        self.card_by_name = {card.name: card.card_id for card in self.cards}
        self.unit_types = list(self.engine.unit_types())
        self.arena = self.engine.arena()
        self.oracle = PlacementOracle(self.arena, self.engine.rules(), self.cards)
        self.grid_w, self.grid_h = self.arena.tiles_x, self.arena.tiles_y
        self.card_vocab = len(self.cards) + 1  # zero is padding; native ids shifted by one
        self.obs_spec = {
            # next card, three own-deck forms, three ability-card identities.
            "global_dim": len(GLOBAL_BASE_FIELDS) + 7 * self.card_vocab,
            "entity_dim": len(ENTITY_BASE_FIELDS) + len(self.unit_types) + 1 + self.card_vocab,
            "num_card_types": self.card_vocab, "max_entities": c.max_entities,
            "grid_h": self.grid_h, "grid_w": self.grid_w, "n_card_actions": 8,
            "entity_x_index": 0, "entity_y_index": 1,
        }
        stamp = self.engine.card_table_stamp()
        if c.expected_card_hash and stamp["cards_json_fnv1a64"] != c.expected_card_hash:
            raise ValueError("Card table hash differs from requested version")
        self.state = None
        self.terminated = self.truncated = False
        self.accepted_total = self.refused_total = self.executed_total = self.run_refused_total = 0
        self._episode_setup: dict[str, Any] = {}

    def catalogue(self) -> list[dict]:
        return [msgspec.to_builtins(c) for c in self.cards]

    def identity(self) -> dict:
        binary = Path(royalesim.__file__)
        # The extension is nested in the wheel package; hash the binary, not just __init__.
        binaries = sorted(binary.parent.glob("*.so"))
        return {"backend": "royalesim", "engine_version": importlib.metadata.version("royalesim"),
                "gym_version": importlib.metadata.version("royalegym"),
                "engine": self.engine.config(), "provenance": list(self.engine._battle.provenance()),
                "binary_sha256": hashlib.sha256(binaries[0].read_bytes()).hexdigest() if binaries else None,
                "observation_version": "arena-public-v2", "obs_spec": dict(self.obs_spec),
                "global_base_fields": GLOBAL_BASE_FIELDS, "entity_base_fields": ENTITY_BASE_FIELDS,
                "unit_types": self.unit_types, "cards": [c.name for c in self.cards],
                "mask_semantics": "public-placement-proposal; native-feedback-is-authoritative",
                "visibility": "simulator-public-conservative; not official-client equivalence",
                "visibility_validation": {"adapter_private_state_invariance_tested": True,
                                          "official_client_equivalence_verified": False},
                "effective_command_delay_ticks": self.config.command_delay_ticks or 0,
                "coordinates": "own-frame [0,1], out-of-arena positions clipped and explicitly flagged",
                "config": asdict(self.config)}

    def _decks(self, decks: Sequence[Sequence[str | int]]) -> list[list[int]]:
        if len(decks) != 2 or any(len(d) != 8 for d in decks):
            raise ValueError("Exactly two complete eight-card decks are required")
        result = []
        for deck in decks:
            row = []
            for item in deck:
                if isinstance(item, str):
                    if item not in self.card_by_name:
                        raise ValueError(f"Unsupported card {item!r}; use catalogue() and explicit slug mapping")
                    item = self.card_by_name[item]
                if isinstance(item, bool) or not isinstance(item, (int, np.integer)) or not 0 <= int(item) < len(self.cards):
                    raise ValueError(f"Invalid native card id {item!r}")
                row.append(int(item))
            if len(set(row)) != 8:
                raise ValueError("Duplicate cards in a deck")
            result.append(row)
        return result

    def reset(self, seed: int | None = None, decks=None, forms=None, levels=None,
              tower_levels=None, shuffle: int | None = None, elixir_milli=None):
        c = self.config
        resolved = self._decks(c.decks if decks is None else decks)
        setup = MatchSetup(decks=resolved, forms=c.forms if forms is None else forms,
                           levels=(c.levels or [[c.card_level]*8]*2) if levels is None else levels,
                           tower_levels=(c.tower_levels or [c.tower_level]*2) if tower_levels is None else tower_levels,
                           shuffle=c.shuffle if shuffle is None else shuffle,
                           elixir_milli=c.elixir_milli if elixir_milli is None else elixir_milli)
        actual_seed = c.seed if seed is None else int(seed)
        self.engine.reset(actual_seed, setup)
        self.state = self.engine.state()
        self.terminated = self.truncated = False
        self.accepted_total = self.refused_total = self.executed_total = self.run_refused_total = 0
        self._episode_setup = {"seed": actual_seed, "setup": msgspec.to_builtins(setup),
                               "initial_hands": [list(p.hand) for p in self.state.players]}
        return self.observations(), self._info([], [])

    def _public_state(self, state, seat: int):
        entities = [e for e in state.entities if e.hp > 0 and
                    (e.team == seat or (e.status_flags >= 0 and not e.status_flags &
                     (STATUS_HIDDEN | STATUS_INVISIBLE | STATUS_UNDERGROUND)))]
        # Only geometry reaches the oracle. Own state is read separately for affordability.
        return msgspec.structs.replace(state, entities=entities)

    def _mask(self, state, seat: int, public):
        positions = np.zeros((4, self.grid_w * self.grid_h), dtype=np.bool_)
        legal = np.zeros(8, dtype=np.bool_)
        legal[0] = True
        rules = self.oracle.rules
        if state.game_over or self.truncated or state.tick < rules.deploy_lockout_ticks or rules.plays_stopped(state):
            return legal, positions
        own = state.players[seat]
        bar = own.elixir_milli - 1000 * own.pending_cost
        waiting = {(r[0], r[1]) for r in own.pending}
        for slot, cid in enumerate(own.hand):
            if cid < 0:
                continue
            card = self.cards[cid]
            cost = slot_cost(own, slot, card)
            if cost < 0 or bar < cost * 1000 or (0, cid) in waiting:
                continue
            if card.placement == Placement.MIRROR:
                if own.mirror_target < 0:
                    continue
                card = self.cards[own.mirror_target]
            grid = self.oracle.point_grid(public, seat, card, 1)
            if seat == 1:
                grid = grid[::-1, ::-1]
            positions[slot] = grid.reshape(-1)
            legal[slot + 1] = positions[slot].any()
        for button, row in enumerate(own.abilities[:3]):
            legal[5 + button] = bool(row[0] and not row[1] and bar >= row[2] * 1000
                                      and (1, 4 + button) not in waiting)
        return legal, positions

    def observe(self, seat: int, state=None) -> dict[str, np.ndarray]:
        if seat not in (0, 1):
            raise ValueError("seat must be 0 or 1")
        state = self.state if state is None else state
        if state is None:
            raise RuntimeError("Call reset before observing")
        public = self._public_state(state, seat)
        own, enemy = state.players[seat], state.players[1 - seat]
        if len(own.abilities) > 3:
            raise ValueError("Engine exposes more than three ability buttons; action schema must be extended")
        features = [min(state.tick / max(state.regular_ticks, 1), 1),
                    max(0, state.tick - state.regular_ticks) / max(state.overtime_ticks, 1),
                    state.elixir_rate / 3, float(state.overtime), own.elixir_milli / 10000,
                    own.pending_cost / 10, own.crowns / 3, enemy.crowns / 3]
        for player in (own, enemy):
            features.extend(hp / max(mx, 1) for hp, mx in zip(player.tower_hp, player.tower_max_hp))
        features.extend([float(own.king_active), float(enemy.king_active)])
        features.extend(slot_cost(own, i, self.cards[cid]) / 10 if cid >= 0 else 0
                        for i, cid in enumerate(own.hand))
        for button in range(3):
            row = own.abilities[button] if button < len(own.abilities) else None
            features.extend([1., float(row[0]), float(row[1]), row[2]/10,
                             max(row[4], 0)/1200] if row is not None else [0.] * 5)
        global_features = np.zeros(self.obs_spec["global_dim"], dtype=np.float32)
        global_features[:len(features)] = features
        if own.next_card >= 0:
            global_features[len(features) + own.next_card + 1] = 1
        for i, cid in enumerate(own.deck):
            form = own.forms[i] if i < len(own.forms) else 0
            if form not in (0, 1, 2):
                raise ValueError(f"Unsupported own deck form {form}")
            global_features[len(features) + (1 + form) * self.card_vocab + cid + 1] = 1
        for button, row in enumerate(own.abilities):
            if len(row) > 3 and 0 <= row[3] < len(self.cards):
                global_features[len(features) + (4 + button) * self.card_vocab + row[3] + 1] = 1
        rows = []
        w, h = self.grid_w * self.arena.subtile, self.grid_h * self.arena.subtile
        nbase, nunit = len(ENTITY_BASE_FIELDS), len(self.unit_types) + 1
        for e in public.entities:
            x, y = to_own(self.arena, seat, e.x, e.y)
            status = max(e.status_flags, 0)
            row = np.zeros(self.obs_spec["entity_dim"], dtype=np.float32)
            row[:nbase] = [np.clip(x/w, 0, 1), np.clip(y/h, 0, 1), e.hp/max(e.max_hp, 1), float(e.team == seat),
                           *[float(e.kind == kind) for kind in range(4)], 0, 0,
                           e.radius/(4*self.arena.subtile), float(e.flying), float(e.deploy_ticks > 0),
                           e.shield/max(e.max_hp, 1), max(e.level, 0)/16,
                           float(bool(status & STATUS_EVOLVED)), float(bool(status & STATUS_CLONE)),
                           float(bool(status & STATUS_HERO)), float(bool(status & STATUS_GROUNDED)),
                           float(e.stun_ticks > 0), float(not (0 <= x <= w and 0 <= y <= h))]
            row[nbase + (e.unit_type + 1 if 0 <= e.unit_type < len(self.unit_types) else 0)] = 1
            rows.append(row)
        for is_projectile, objects in ((False, state.spells), (True, state.projectiles)):
            for obj in objects:
                x, y = to_own(self.arena, seat, obj.x, obj.y)
                row = np.zeros(self.obs_spec["entity_dim"], dtype=np.float32)
                row[:4] = [np.clip(x/w, 0, 1), np.clip(y/h, 0, 1), 0, float(obj.team == seat)]
                row[20] = float(not (0 <= x <= w and 0 <= y <= h))
                row[9 if is_projectile else 8] = 1
                if not is_projectile and 0 <= obj.card_id < len(self.cards):
                    row[nbase + nunit + obj.card_id + 1] = 1
                rows.append(row)
        if len(rows) > self.config.max_entities:
            raise RuntimeError(f"Public entity capacity {self.config.max_entities} exceeded ({len(rows)}); increase max_entities")
        # Stable public sort, independent of hidden engine uid/allocation and future targets.
        rows.sort(key=lambda r: tuple(r[:10]) + tuple(np.flatnonzero(r[nbase:])))
        entities = np.zeros((self.config.max_entities, self.obs_spec["entity_dim"]), dtype=np.float32)
        entity_mask = np.zeros(self.config.max_entities, dtype=np.bool_)
        if rows:
            entities[:len(rows)] = rows
            entity_mask[:len(rows)] = True
        legal_cards, legal_positions = self._mask(state, seat, public)
        return {"global_features": global_features, "entities": entities, "entity_mask": entity_mask,
                "hand_ids": np.asarray([cid + 1 for cid in own.hand], dtype=np.int64),
                "legal_cards": legal_cards, "legal_positions": legal_positions}

    def observations(self):
        return {seat: self.observe(seat) for seat in (0, 1)}

    def _command(self, seat: int, action) -> DeployCommand | None:
        if isinstance(action, (int, np.integer)):
            card, pos = int(action), 0
        elif isinstance(action, Mapping):
            card, pos = action.get("card", 0), action.get("position", 0)
        else:
            card, pos = action
        if isinstance(card, bool) or int(card) != card or not 0 <= int(card) < 8:
            raise ValueError("card action must be integer 0..7")
        card = int(card)
        if card == 0:
            return None
        if card >= 5:
            return DeployCommand(seat, card - 1, 0, 0)
        if isinstance(pos, bool) or int(pos) != pos or not 0 <= int(pos) < self.grid_w * self.grid_h:
            raise ValueError("position must index the 18x32 own-frame grid")
        y, x = divmod(int(pos), self.grid_w)
        x, y = to_engine(self.arena, seat, x*self.arena.subtile + self.arena.subtile//2,
                          y*self.arena.subtile + self.arena.subtile//2)
        return DeployCommand(seat, card - 1, x, y)

    def joint_step(self, actions, ticks: int | None = None):
        if self.state is None:
            raise RuntimeError("Call reset before stepping")
        if self.terminated or self.truncated:
            raise RuntimeError("Episode has ended; call reset")
        commands = []
        for seat in (0, 1):
            action = actions.get(seat, {"card": 0}) if isinstance(actions, Mapping) else actions[seat]
            command = self._command(seat, action)
            if command is not None:
                commands.append(command)
        return self._advance(commands, self.config.decision_ticks if ticks is None else ticks)

    step = joint_step

    def advance_commands(self, commands: Sequence[Mapping], ticks: int = 1):
        """Replay raw world/subtile commands; a card not in hand is an explicit error.

        Each command has team, x, y and either hand_slot or native card_id. This never
        fabricates a hand from future plays. Callers must supply a justified opening.
        """
        if self.state is None or self.terminated or self.truncated:
            raise RuntimeError("Replay requires a live reset episode")
        native = []
        for c in commands:
            seat = int(c["team"])
            if seat not in (0, 1):
                raise ValueError("Invalid replay seat")
            if "hand_slot" in c:
                slot = int(c["hand_slot"])
            else:
                cid = c["card_id"]
                if isinstance(cid, str):
                    if cid not in self.card_by_name:
                        raise ValueError(f"Unknown replay card {cid}")
                    cid = self.card_by_name[cid]
                if cid not in self.state.players[seat].hand:
                    raise ValueError(f"Replay card {cid} absent from seat {seat} hand at tick {self.state.tick}")
                slot = self.state.players[seat].hand.index(cid)
            native.append(DeployCommand(seat, slot, int(c["x"]), int(c["y"])))
        return self._advance(native, ticks)

    def _advance(self, commands, ticks):
        if isinstance(ticks, bool) or int(ticks) != ticks or ticks < 1:
            raise ValueError("ticks must be a positive integer")
        if self.config.max_ticks is not None:
            ticks = min(ticks, self.config.max_ticks - self.state.tick)
        results = self.engine.step(commands, int(ticks))
        run = self.engine.step_commands_run() or []
        self.state = self.engine.state()
        self.terminated = bool(self.state.game_over)
        self.truncated = bool(not self.terminated and self.config.max_ticks is not None
                              and self.state.tick >= self.config.max_ticks)
        self.accepted_total += sum(r.status == DeployStatus.OK for r in results)
        self.refused_total += sum(r.status != DeployStatus.OK for r in results)
        # No-delay deployments execute synchronously and have no delayed-run event.
        self.executed_total += (sum(row[-1] == 0 for row in run) if self.config.command_delay_ticks
                                else sum(r.status == DeployStatus.OK for r in results))
        self.run_refused_total += sum(row[-1] != 0 for row in run)
        rewards = {seat: (0. if not self.terminated or self.state.winner == 2 else
                          1. if self.state.winner == seat else -1.) for seat in (0, 1)}
        return self.observations(), rewards, self.terminated, self.truncated, self._info(results, run)

    def _info(self, results, run):
        s = self.state
        return {"tick": s.tick, "elapsed_sim_seconds": s.tick * s.tick_ms / 1000,
                "winner": s.winner, "terminated": self.terminated, "truncated": self.truncated,
                "deploy_results": [dict(msgspec.to_builtins(r), reason=DeployStatus(r.status).name)
                                   for r in results],
                "commands_run": [list(r) for r in run],
                "accepted": sum(r.status == DeployStatus.OK for r in results),
                "refused": sum(r.status != DeployStatus.OK for r in results),
                "accepted_total": self.accepted_total, "refused_total": self.refused_total,
                "executed_total": self.executed_total,
                "run_refused": sum(row[-1] != 0 for row in run), "run_refused_total": self.run_refused_total,
                "pending_by_seat": [len(p.pending) for p in s.players],
                "state_hash": str(self.engine.state_hash()), "episode_setup": self._episode_setup,
                "crowns": [p.crowns for p in s.players], "tower_hp": [p.tower_hp for p in s.players]}

    def close(self):
        pass

    def scripted_action(self, obs, player=0, rng=None):
        from .baselines import scripted_action
        return scripted_action(obs, catalogue=self.catalogue(), grid_w=self.grid_w,
                               grid_h=self.grid_h, rng=rng)


class CrforgeEnv:
    """Normalized tensors from crforge's native SeatView, with a separate identity.

    Native header/entity buffers are the actor source. Java full battle records are
    read only to produce deployment audit feedback, never observations or masks.
    """

    def __init__(self, config: ArenaConfig | None = None, **kwargs):
        from .bootstrap import CACHE, CRFORGE_COMMIT, JDK_NAME
        self.config = config or ArenaConfig(**dict(kwargs, backend="crforge"))
        if config is not None and kwargs:
            raise TypeError("Pass config or keyword settings, not both")
        c = self.config
        if c.decision_ticks < 1 or c.max_entities < 6 or (c.max_ticks is not None and c.max_ticks < 1):
            raise ValueError("Invalid decision interval, capacity or horizon")
        source = CACHE / "crforge" / ("crforge-" + CRFORGE_COMMIT)
        if not (source / "env/build/install/env/lib").is_dir():
            raise RuntimeError("Run .venv-cloud/bin/python -m arena_lab.bootstrap install-crforge")
        os.environ["JAVA_HOME"] = str(CACHE / "jdk" / JDK_NAME)
        if str(source / "python") not in sys.path:
            sys.path.insert(0, str(source / "python"))
        from crforge_env import VectorBattle, LadderConfig
        if c.command_delay_ticks is not None and c.command_delay_ticks != 20:
            raise ValueError("Pinned crforge models the native 20-tick command delay; cannot override")
        if c.elixir_milli is not None:
            raise ValueError("crforge starting elixir comes from its mode; explicit override unsupported")
        self.vector = VectorBattle(1, 1, install=source / "env/build/install/env",
                                   tables=CACHE / "crforge-data/tables" / c.crforge_data_version,
                                   config=LadderConfig(decision_ticks=c.decision_ticks, level=c.card_level,
                                                       tower_level=c.tower_level, max_ticks=c.max_ticks or 0),
                                   jvm_args=["-XX:ActiveProcessorCount=2", "-Xmx1024m",
                                             "-Djava.io.tmpdir=" + str(CACHE / "tmp")])
        self.vector.set_autoreset(False)
        if c.crforge_expected_content_sha and self.vector.content_sha != c.crforge_expected_content_sha:
            self.vector.close()
            raise ValueError("crforge content SHA differs from the explicitly selected tables")
        self.schema = self.vector.schema
        if self.schema.ability_slots > 3:
            raise ValueError("Native ability count exceeds model action schema")
        self.grid_w, self.grid_h = self.schema.cols, self.schema.rows
        types = self.vector.card_types()
        self._card_native = sorted(types)
        self._native_to_card = {native: i for i, native in enumerate(self._card_native)}
        self.cards = [{"card_id": i, "name": self.vector.vocabulary[native],
                       "card_kind": types[native].upper(), "elixir": None}
                      for i, native in enumerate(self._card_native)]
        self.card_by_name = {c["name"]: c["card_id"] for c in self.cards}
        self.card_vocab = len(self.cards) + 1
        self._extra_entity = ("hp_known", "shield_known", "row_known", "velocity_x",
                              "velocity_y", "ability_ticks_known", "ability_ticks", "form_flags_known")
        self.obs_spec = {"global_dim": len(GLOBAL_BASE_FIELDS) + 8*self.card_vocab + 12,
                         "entity_dim": len(ENTITY_BASE_FIELDS) + len(self._extra_entity) +
                                       len(self.schema.state_names) + self.vector.vocabulary_size + 1,
                         "num_card_types": self.card_vocab, "max_entities": c.max_entities,
                         "grid_h": self.grid_h, "grid_w": self.grid_w, "n_card_actions": 8,
                         "entity_x_index": 0, "entity_y_index": 1}
        self._commit = CRFORGE_COMMIT
        import jpype
        self._records = jpype.JClass("org.crforge.core.battle.data.BattleRecords")(self.vector._tables)
        self._ability_costs = {}
        self._episode_setup = {}
        self.terminated = self.truncated = False
        self.accepted_total = self.refused_total = self.executed_total = self.run_refused_total = 0
        self._plays_seen = self._abilities_seen = 0
        self.state = None

    def catalogue(self):
        return [dict(c) for c in self.cards]

    def identity(self):
        return {"backend": "crforge", "engine_version": self._commit,
                "data_version": self.vector.data_version, "content_sha": self.vector.content_sha,
                "vocabulary_hash": self.vector.vocabulary_hash,
                "observation_version": "arena-crforge-public-v1", "obs_spec": dict(self.obs_spec),
                "global_base_fields": GLOBAL_BASE_FIELDS, "entity_base_fields": ENTITY_BASE_FIELDS,
                "entity_extra_fields": self._extra_entity, "native_metadata": self.vector.metadata,
                "cards": [c["name"] for c in self.cards], "config": asdict(self.config),
                "visibility": "native SeatView schema 4; unknown hp/shield/type remain explicitly unknown",
                "visibility_validation": {"native_mask_field_filter": "SeatView.sees before placementEntities",
                                          "hidden_occupancy_side_channel_exhaustively_tested": False,
                                          "official_client_equivalence_verified": False},
                "mask_semantics": "native landing-tile mask; run may still refuse",
                "coordinates": "native seat frame [0,1]; out-of-arena positions clipped and flagged",
                "command_delay_ticks": self.schema.play_delay_ticks,
                "effective_command_delay_ticks": self.schema.play_delay_ticks,
                "feedback_what": "upstream command name; commands_run reason0 confirms placement/ability",
                "form_flags": "entity convenience evolved/hero unset+unknown; type onehot and own forms retained"}

    def _decks(self, decks):
        if len(decks) != 2 or any(len(d) != 8 for d in decks):
            raise ValueError("Exactly two complete eight-card decks required")
        result = []
        for deck in decks:
            row = []
            for item in deck:
                if isinstance(item, str):
                    if item not in self.card_by_name:
                        raise ValueError(f"Unsupported crforge card {item!r}")
                    row.append(item)
                elif isinstance(item, (int, np.integer)) and not isinstance(item, bool) and 0 <= item < len(self.cards):
                    row.append(self.cards[int(item)]["name"])
                else:
                    raise ValueError(f"Invalid compact crforge card id {item!r}")
            if len(set(row)) != 8:
                raise ValueError("Duplicate cards in deck")
            result.append(row)
        return result

    def reset(self, seed=None, decks=None, forms=None, levels=None, tower_levels=None,
              shuffle=None, elixir_milli=None, replay=None, start_tick=0):
        from crforge_env import SideSetup
        c = self.config
        if shuffle not in (None, 1) or (shuffle is None and c.shuffle != 1):
            raise ValueError("crforge uses native seed-based dealing; arbitrary initial-hand injection unsupported")
        if elixir_milli is not None:
            raise ValueError("crforge arbitrary initial elixir injection unsupported")
        ds = self._decks(c.decks if decks is None else decks)
        slots = c.forms if forms is None else forms
        if slots is None:
            slots = [[0]*8, [0]*8]
        if len(slots) != 2 or any(len(row) != 8 or any(x not in (0, 1, 2, 3) for x in row) for row in slots):
            raise ValueError("crforge forms are explicit slot flags 0/1evo/2hero/3both")
        levels = c.levels if levels is None else levels
        tower_levels = c.tower_levels if tower_levels is None else tower_levels
        setups = [SideSetup(card_levels=None if levels is None else levels[s],
                            king_level=c.tower_level if tower_levels is None else tower_levels[s],
                            tower_level=c.tower_level if tower_levels is None else tower_levels[s],
                            towers=None if c.tower_types is None else c.tower_types[s]) for s in (0, 1)]
        actual_seed = c.seed if seed is None else int(seed)
        if replay is None:
            self.vector.reset([actual_seed], ds[0], ds[1], slots[0], slots[1], setups[0], setups[1])
        else:
            self.vector.start_from_replay(0, replay, int(start_tick))
        if self.vector.fault(0):
            raise RuntimeError(f"crforge reset fault (not a game result): {self.vector.fault(0)}")
        self.terminated = self.truncated = False
        self.accepted_total = self.refused_total = self.executed_total = self.run_refused_total = 0
        battle = self.vector._env.env(0).battle()
        self._plays_seen, self._abilities_seen = len(battle.getPlays()), len(battle.getAbilityUses())
        actual_decks = [[self.vector.vocabulary[int(cid)] for cid in self._header(seat,"deck")] for seat in (0,1)]
        self._episode_setup = {"seed": self.vector.episode_seed(0), "decks": actual_decks,
                               "slots": [self._header(seat,"deck_slot").tolist() for seat in (0,1)],
                               "levels": [self._header(seat,"deck_level").tolist() for seat in (0,1)],
                               "tower_levels": tower_levels,
                               "initial_hands": [self._header(s, "hand_card").tolist() for s in (0, 1)],
                               "initial_hand_encoding": "native vocabulary ids", "from_replay": replay is not None}
        self.state = True
        return self.observations(), self._info([], [])

    def _header(self, seat, name):
        return self.vector.observations[0, seat, list(self.schema.header[name])]

    def _card(self, native):
        return self._native_to_card.get(int(native), -1) + 1

    def observe(self, seat, state=None):
        if seat not in (0, 1):
            raise ValueError("seat must be 0 or 1")
        if self.state is None:
            raise RuntimeError("Call reset before observe")
        if state is not None:
            raise ValueError("crforge actor reads only native SeatView buffers")
        s = self.schema
        get = lambda name: int(self._header(seat, name)[0])
        mask = self.vector.masks[0, seat].astype(bool)
        positions = mask[1:s.first_ability].reshape(4, self.grid_h*self.grid_w).copy()
        legal = np.zeros(8, dtype=bool)
        legal[0] = True
        legal[1:5] = positions.any(axis=1)
        legal[5:5+s.ability_slots] = mask[s.first_ability:]
        features = [min(get("time_ms")/180000, 1), max(get("time_ms")-180000, 0)/120000,
                    min(28000/max(get("full_bar_ms"), 1)/3, 1), float(get("overtime")),
                    get("elixir")/100000, get("elixir_reserved")/100000,
                    get("crowns")/3, get("crowns_enemy")/3]
        for owner in ("own", "enemy"):
            features.extend(get(owner+"_"+tower+"_hp")/max(get(owner+"_"+tower+"_max_hp"), 1)
                            for tower in ("king", "left", "right"))
        # Native SeatView exposes king awake as an entity flag, populated below.
        features.extend([0., 0.])
        features.extend(self._header(seat, "hand_cost")/10)
        champs = self._header(seat, "ability_champion")
        cooldown = self._header(seat, "ability_cooldown_ms")
        charges = self._header(seat, "ability_charges")
        for button in range(3):
            if button < s.ability_slots:
                champion = int(champs[button])
                if champion >= 0 and champion not in self._ability_costs:
                    ability = self._records.unit(self.vector.vocabulary[champion]).ability()
                    self._ability_costs[champion] = int(ability.manaCost()) if ability is not None else 0
                features.extend([float(champs[button] >= 0), float(legal[5+button]),
                                 float(charges[button] == 0), self._ability_costs.get(champion,0)/10,
                                 max(cooldown[button], 0)/60000])
            else:
                features.extend([0.]*5)
        global_features = np.zeros(self.obs_spec["global_dim"], dtype=np.float32)
        global_features[:len(features)] = features
        offset = len(features)
        nextcard = self._card(get("next_card"))
        if nextcard:
            global_features[offset+nextcard] = 1
        deck, slots = self._header(seat, "deck"), self._header(seat, "deck_slot")
        for native, flags in zip(deck, slots):
            card = self._card(native)
            if card:
                global_features[offset+(1+(2 if flags & 2 else 1 if flags & 1 else 0))*self.card_vocab+card] = 1
        for button, native in enumerate(champs):
            card = self._card(native)
            if card:
                global_features[offset+(4+button)*self.card_vocab+card] = 1
        for native in self._header(seat, "enemy_seen"):
            card = self._card(native)
            if card:
                global_features[offset+7*self.card_vocab+card] = 1
        extra_offset = offset+8*self.card_vocab
        global_features[extra_offset:extra_offset+4] = self._header(seat, "hand_form")/2
        global_features[extra_offset+4:extra_offset+6] = charges/10
        global_features[extra_offset+6:extra_offset+8] = self._header(seat, "ability_state")/8
        global_features[extra_offset+8:] = [get("play_open_in_ticks")/100, get("enemy_elixir")/100000,
                                           get("hand_refill_ms")/1000, get("pending_count")/4]
        count = get("entity_count")
        if get("entity_overflow") or count > self.config.max_entities:
            raise RuntimeError(f"Native/public entity overflow ({count}, native overflow={get('entity_overflow')}); increase capacity")
        native_rows = self.vector.observations[0, seat, s.header_ints:].reshape(s.max_entities, s.entity_ints)[:count]
        entities = np.zeros((self.config.max_entities, self.obs_spec["entity_dim"]), np.float32)
        entity_mask = np.zeros(self.config.max_entities, bool)
        nbase, nextra = len(ENTITY_BASE_FIELDS), len(self._extra_entity)
        for i, source in enumerate(native_rows):
            e = {name: int(source[index]) for name, index in s.entity.items()}
            row = entities[i]
            flags, kind = e["flags"], e["kind"]
            x, y = e["x"]/s.width_units, e["y"]/s.height_units
            # Native SeatView already canonicalizes sides: 0 own, 1 enemy, 2 neither.
            own = e["side"] == 0
            is_tower = kind == s.kinds["tower"]
            building = bool(flags & s.flags["building"])
            king = bool(is_tower and flags & s.flags["king"])
            row[:nbase] = [np.clip(x,0,1), np.clip(y,0,1), max(e["hp"],0)/max(e["max_hp"],1), float(own),
                           float(kind == s.kinds["character"] and not building), float(building and not is_tower),
                           float(king), float(is_tower and not king), float(kind == s.kinds["area"]),
                           float(kind == s.kinds["projectile"]), max(e["radius"],0)/(4*s.tile_units),
                           float(bool(flags & s.flags["air"])), float(e["deploy_ms"]>0),
                           max(e["shield"],0)/max(e["max_hp"],1), max(e["level"],0)/16,
                           0, float(bool(flags & s.flags["clone"])), 0, 0, 0,
                           float(not (0 <= x <= 1 and 0 <= y <= 1))]
            row[nbase:nbase+nextra] = [float(e["hp"]>=0), float(e["shield"]>=0), float(e["row"]>=0),
                                      e["dx"]/s.tile_units, e["dy"]/s.tile_units,
                                      float(e["ability_ticks"]>=0), max(e["ability_ticks"],0)/1200, 0]
            if 0 <= e["state"] < len(s.state_names):
                row[nbase+nextra+e["state"]] = 1
            type_offset = nbase+nextra+len(s.state_names)
            row[type_offset+(e["row"]+1 if e["row"]>=0 else 0)] = 1
            entity_mask[i] = True
            if king:
                global_features[14 if own else 15] = float(bool(flags & s.flags["awake"]))
        return {"global_features": global_features, "entities": entities, "entity_mask": entity_mask,
                "hand_ids": np.asarray([self._card(cid) for cid in self._header(seat,"hand_card")],np.int64),
                "legal_cards": legal, "legal_positions": positions}

    def observations(self):
        return {seat:self.observe(seat) for seat in (0,1)}

    def joint_step(self, actions, ticks=None):
        if self.state is None or self.terminated or self.truncated:
            raise RuntimeError("Step requires a live reset episode")
        if ticks is not None and ticks != self.config.decision_ticks:
            raise ValueError("crforge decision_ticks is fixed at construction; use 1 for replay timelines")
        native_actions, proposed = [], []
        before_tick = int(self._header(0,"tick")[0])
        for seat in (0,1):
            action = actions.get(seat,{"card":0}) if isinstance(actions,Mapping) else actions[seat]
            if isinstance(action,(int,np.integer)):
                card,pos=int(action),0
            elif isinstance(action,Mapping):
                card,pos=action.get("card",0),action.get("position",0)
            else:
                card,pos=action
            if isinstance(card,bool) or int(card)!=card or not 0<=int(card)<8:
                raise ValueError("card action must be integer0..7")
            card=int(card)
            if card==0:
                native=0
            elif card>=5:
                if card-5>=self.schema.ability_slots:
                    raise ValueError("This crforge schema has only two ability buttons")
                native=self.schema.first_ability+card-5
            else:
                if isinstance(pos,bool) or int(pos)!=pos or not 0<=int(pos)<self.grid_w*self.grid_h:
                    raise ValueError("Invalid action position")
                native=1+(card-1)*self.grid_w*self.grid_h+int(pos)
            native_actions.append(native)
            if card:
                proposed.append({"team":seat,"hand_slot":card-1,"card_id":
                                 self._card(self._header(seat,"hand_card")[card-1])-1 if card<=4 else -1,
                                 "tick":before_tick,"requested_position":int(pos),"action_card":card})
        self.vector.step(np.asarray([native_actions],np.int32))
        native_info = [self.vector.seat_info(0,seat) for seat in (0,1)]
        done=bool(self.vector.dones[0]);self.truncated=bool(done and
             (native_info[0]["truncated"] or native_info[0]["fault"]!="none"))
        self.terminated=bool(done and not self.truncated)
        results=[]
        for result in proposed:
            ni=native_info[result["team"]]
            result.update(status=0 if ni["last_refusal"]=="NONE" else 1,reason=ni["last_refusal"],
                          resolved_position=None)
            results.append(result)
        run=[];battle=self.vector._env.env(0).battle()
        plays=battle.getPlays();abilities=battle.getAbilityUses()
        for play in list(plays)[self._plays_seen:]:
            reason=int(play.matchCode())
            if reason==0 and (play.result() is None or not play.result().placed()):
                reason=1000
            run.append([int(play.tick()),int(play.side()),"deploy",str(play.name()),reason])
        for use in list(abilities)[self._abilities_seen:]:
            run.append([int(use.tick()),int(use.side()),"ability",str(use.name()),int(use.outcome().code())])
        run.sort(key=lambda r:r[0]);self._plays_seen=len(plays);self._abilities_seen=len(abilities)
        self.accepted_total+=sum(r["status"]==0 for r in results)
        self.refused_total+=sum(r["status"]!=0 for r in results)
        self.executed_total+=sum(r[-1]==0 for r in run)
        self.run_refused_total+=sum(r[-1]!=0 for r in run)
        rewards={seat:float(self.vector.rewards[0,seat]) if not self.truncated else 0. for seat in (0,1)}
        return self.observations(),rewards,self.terminated,self.truncated,self._info(results,run)

    step=joint_step

    def advance_commands(self, commands,ticks=1):
        if ticks!=self.config.decision_ticks:
            raise ValueError("Construct crforge with decision_ticks=1 for per-tick replay")
        actions={0:{"card":0},1:{"card":0}}
        for command in commands:
            seat=int(command["team"])
            if seat not in (0,1) or actions[seat]["card"]:
                raise ValueError("One replay command per seat per native decision")
            if "hand_slot" in command:
                slot=int(command["hand_slot"])
            else:
                cid=command["card_id"]
                if isinstance(cid,str):
                    if cid not in self.card_by_name:
                        raise ValueError(f"Unsupported replay card {cid}")
                    cid=self.card_by_name[cid]
                hand=list(self.observe(seat)["hand_ids"]-1)
                if cid not in hand:
                    raise ValueError(f"Replay card absent from current hand at tick{int(self._header(seat,'tick')[0])}")
                slot=hand.index(cid)
            x,y=int(command["x"]),int(command["y"])
            x,y=(self.schema.width_units-x,y) if seat==0 else (x,self.schema.height_units-y)
            col,row=x//self.schema.tile_units,y//self.schema.tile_units
            if not (0<=col<self.grid_w and 0<=row<self.grid_h):
                raise ValueError("Replay position outside arena")
            actions[seat]={"card":slot+1,"position":row*self.grid_w+col}
        return self.joint_step(actions)

    def _info(self,results,run):
        native=[self.vector.seat_info(0,seat) for seat in (0,1)]
        clean=[{k:v for k,v in item.items() if k!="action_mask"} for item in native]
        tick=native[0]["tick"]
        digest=hashlib.sha256(self.vector.observations.tobytes()+self.vector.infos.tobytes()).hexdigest()
        return {"tick":tick,"elapsed_sim_seconds":tick*self.schema.scales["ms_per_tick"]/1000,
                "winner":native[0]["winner"],"terminated":self.terminated,"truncated":self.truncated,
                "deploy_results":results,"commands_run":run,
                "accepted":sum(r["status"]==0 for r in results),"refused":sum(r["status"]!=0 for r in results),
                "accepted_total":self.accepted_total,"refused_total":self.refused_total,
                "executed_total":self.executed_total,"state_hash":digest,
                "run_refused":sum(r[-1]!=0 for r in run),"run_refused_total":self.run_refused_total,
                "pending_by_seat":[int(self._header(seat,"pending_count")[0])+
                                   int((self._header(seat,"ability_command_ticks")>0).sum()) for seat in (0,1)],
                "state_hash_scope":"native public observation+info buffers (not full hidden battle)",
                "episode_setup":self._episode_setup,"native_seat_info":clean,
                "crowns":[item["crowns"] for item in native],
                "tower_hp":[[int(self._header(seat,'own_'+tower+'_hp')[0]) for tower in ('king','left','right')]
                            for seat in (0,1)]}

    def scripted_action(self,obs,player=0,rng=None):
        from .baselines import scripted_action
        return scripted_action(obs,catalogue=self.catalogue(),grid_w=self.grid_w,grid_h=self.grid_h,rng=rng)

    def close(self):
        self.vector.close()


ArenaEnvironment = ArenaEnv


def benchmark(*, backend="royalesim", seed=7, games=2, decision_ticks=10,
              output=None, decks=None, forms=None, card_level=11):
    """Natural full games with public scripted/random policies; no policy-strength claim."""
    import time
    env = ArenaEnv(ArenaConfig(backend=backend, seed=seed, decision_ticks=decision_ticks,
                              max_entities=200, decks=decks or [DEFAULT_DECK,DEFAULT_DECK],
                              forms=forms, card_level=card_level,tower_level=card_level))
    reports=[]
    try:
        for game in range(games):
            obs,info=env.reset(seed=seed+game)
            rng=np.random.default_rng(seed+game+100000)
            start=time.perf_counter();steps=0;latency=[];trace=[]
            while not (env.terminated or env.truncated):
                actions={}
                for seat in (0,1):
                    if seat==game%2:
                        actions[seat]=env.scripted_action(obs[seat],seat,rng)
                    else:
                        choices=np.flatnonzero(obs[seat]["legal_cards"])[1:]
                        card=int(rng.choice(choices)) if len(choices) and rng.random()<.3 else 0
                        pos=0
                        if 1<=card<=4:
                            pos=int(rng.choice(np.flatnonzero(obs[seat]["legal_positions"][card-1])))
                        actions[seat]={"card":card,"position":pos}
                before=time.perf_counter()
                obs,rewards,terminated,truncated,info=env.step(actions)
                latency.append(time.perf_counter()-before);steps+=1
                if info["deploy_results"] or info["commands_run"]:
                    trace.append({"actions":actions,"tick":info["tick"],
                                  "deploy_results":info["deploy_results"],"commands_run":info["commands_run"]})
                for seat in (0,1):
                    xy=obs[seat]["entities"][obs[seat]["entity_mask"],:2]
                    if not np.isfinite(xy).all() or np.any(xy<0) or np.any(xy>1):
                        raise AssertionError("Actor coordinates outside promised normalized range")
                if steps*decision_ticks>20000:
                    raise RuntimeError("No native terminal after 1000 simulated seconds; preserving episode")
            wall=time.perf_counter()-start
            reports.append({"seed":seed+game,"scripted_seat":game%2,"steps":steps,"wall_seconds":wall,
                            "joint_decisions_per_second":steps/wall,"simulated_ticks_per_second":info["tick"]/wall,
                            "step_latency_ms":{"p50":float(np.percentile(latency,50)*1000),
                                               "p95":float(np.percentile(latency,95)*1000)},
                            "natural_terminal":terminated and not truncated,"final":info,"trace":trace})
        result={"identity":env.identity(),"measurement":"fullgame public scripted versus random; no neural inference",
                "games":reports}
        if output:
            path=Path(output);path.parent.mkdir(parents=True,exist_ok=True)
            path.write_text(json.dumps(result,ensure_ascii=False,indent=2)+"\n")
        return result
    finally:
        env.close()


def main():
    import argparse
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",choices=["benchmark"])
    parser.add_argument("--backend",choices=["royalesim","crforge"],default="royalesim")
    parser.add_argument("--seed",type=int,default=7)
    parser.add_argument("--games",type=int,default=2)
    parser.add_argument("--decision-ticks",type=int,default=10)
    parser.add_argument("--card-level",type=int,default=11)
    parser.add_argument("--output",default="outputs/arena-lab-20261113/env-benchmark.json")
    parser.add_argument("--decks-json",help="JSON file with two complete internal-name decks and optional forms")
    args=parser.parse_args()
    if args.games<1:
        parser.error("--games must be positive")
    decks=json.loads(Path(args.decks_json).read_text()) if args.decks_json else {}
    result=benchmark(backend=args.backend,seed=args.seed,games=args.games,decision_ticks=args.decision_ticks,
                     card_level=args.card_level,output=args.output,decks=decks.get("decks"),forms=decks.get("forms"))
    print(json.dumps({"backend":result["identity"]["backend"],"output":args.output,
                      "games":[{k:v for k,v in game.items() if k not in ("trace","final")}
                               for game in result["games"]]},indent=2))


if __name__=="__main__":
    main()

"""Small recurrent imitation policy shared by offline training and online play.

The model consumes the public observation contract supplied by ``arena_lab.environment``.
It neither reads simulator state nor reconstructs hidden opponent information.
Card 0 means WAIT; cards 1..4 refer to hand slots; 5+ refer to ability buttons. Positions use
the environment's row-major grid without coordinate conversion in this module.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
import hashlib
import io
import json
import math
from pathlib import Path
import os
import tempfile
from typing import Any, Mapping

import torch
from torch import Tensor, nn


CHECKPOINT_FORMAT = "arena-lab-public-entity-gru-v1"
OBSERVATION_KEYS = (
    "global_features", "entities", "entity_mask", "hand_ids",
    "legal_cards", "legal_positions",
)


@dataclass(frozen=True)
class PolicySpec:
    global_dim: int
    entity_dim: int
    card_vocab_size: int
    grid_h: int = 32
    grid_w: int = 18
    n_card_actions: int = 8
    hidden_dim: int = 96
    entity_hidden: int = 48
    card_embedding_dim: int = 16
    entity_x_index: int = 0
    entity_y_index: int = 1
    spatial_h: int = 8
    spatial_w: int = 6
    spatial_channels: int = 16
    use_entities: bool = True
    use_spatial: bool = True
    use_recurrent: bool = True
    observation_version: str = "arena-public-v1"

    def __post_init__(self) -> None:
        for name, value in asdict(self).items():
            if name == "observation_version":
                if not isinstance(value, str) or not value:
                    raise ValueError("observation_version must be a nonempty string")
            elif name in {"use_entities", "use_spatial", "use_recurrent"}:
                if type(value) is not bool:
                    raise ValueError(f"{name} must be a boolean")
            elif name in {"entity_x_index", "entity_y_index"}:
                if type(value) is not int or not 0 <= value < self.entity_dim:
                    raise ValueError(f"{name} must index a normalized entity coordinate")
            elif type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.entity_x_index == self.entity_y_index:
            raise ValueError("Entity x/y coordinates must have different feature indices")
        if self.n_card_actions < 5:
            raise ValueError("n_card_actions must include WAIT and four hand slots")

    @property
    def positions(self) -> int:
        return self.grid_h * self.grid_w


def tensor_observation(
    observation: Mapping[str, Any], *, device: torch.device | str = "cpu",
    single: bool = False,
) -> dict[str, Tensor]:
    """Convert arrays; ``single`` adds batch and time axes for one live frame."""
    missing = set(OBSERVATION_KEYS) - observation.keys()
    if missing:
        raise ValueError(f"Missing observation fields: {sorted(missing)}")
    result = {}
    for key in OBSERVATION_KEYS:
        dtype = (torch.long if key == "hand_ids" else
                 torch.bool if key in {"entity_mask", "legal_cards", "legal_positions"}
                 else torch.float32)
        value = torch.as_tensor(observation[key], dtype=dtype, device=device)
        result[key] = value[None, None] if single else value
    # Explicit time is optional for historical checkpoints. New timing policies
    # consume it; request/confirmation timestamps are labels, never actor input.
    for key in ("elapsed_ticks", "decision_ticks"):
        if key in observation:
            value = torch.as_tensor(observation[key], dtype=torch.float32, device=device)
            result[key] = value[None, None] if single else value
    return result


def _sample(logits: Tensor, deterministic: bool, generator: torch.Generator | None) -> Tensor:
    if deterministic:
        return logits.argmax(dim=-1)
    flat = logits.softmax(dim=-1).reshape(-1, logits.shape[-1])
    return torch.multinomial(flat, 1, generator=generator).reshape(logits.shape[:-1])


def decode_actions(
    output: Mapping[str, Tensor], *, deterministic: bool = True,
    generator: torch.Generator | None = None,
) -> dict[str, Tensor]:
    """The single decoder used by both evaluation and live action selection."""
    card_logits = output["card_logits"]
    position_logits = output["position_logits"]
    if deterministic and "event_probability" in output:
        # Joint MAP can pick WAIT even when most probability is non-WAIT,
        # because that mass is distributed over several different hand slots.
        event = output["event_probability"] >= output["event_threshold"]
        event &= output["effective_legal_cards"][..., 1:].any(-1)
        cards = torch.where(event, output["conditional_card_logits"].argmax(-1) + 1,
                            torch.zeros_like(event, dtype=torch.long))
    else:
        cards = _sample(card_logits, deterministic, generator)
    slots = (cards - 1).clamp(min=0, max=3)
    is_deploy = (cards >= 1) & (cards <= 4)
    selected = position_logits.gather(
        -2, slots[..., None, None].expand(*slots.shape, 1, position_logits.shape[-1])
    ).squeeze(-2)
    positions = _sample(selected, deterministic, generator)
    positions = torch.where(is_deploy, positions, torch.zeros_like(positions))
    card_log_prob = card_logits.log_softmax(-1).gather(-1, cards[..., None]).squeeze(-1)
    position_log_prob = selected.log_softmax(-1).gather(-1, positions[..., None]).squeeze(-1)
    position_log_prob = torch.where(is_deploy, position_log_prob, torch.zeros_like(position_log_prob))
    return {
        "card": cards, "position": positions,
        "card_probability": card_log_prob.exp(),
        "position_probability": position_log_prob.exp(),
        "log_probability": card_log_prob + position_log_prob,
    }


class EntityGRUPolicy(nn.Module):
    """Entity attention + spatial CNN + GRU + card-conditioned location logits.

    Spatial input comes only from visible entities and their normalized [0,1]
    positions. Cell features retain entity identity/health through the learned
    encoder; a separate occupancy channel retains multiple units in a cell.
    """

    def __init__(self, spec: PolicySpec):
        super().__init__()
        self.spec = spec
        eh, ch, hh = spec.entity_hidden, spec.card_embedding_dim, spec.hidden_dim
        self.card_embedding = nn.Embedding(spec.card_vocab_size, ch)
        self.global_encoder = nn.Sequential(nn.Linear(spec.global_dim, eh), nn.Tanh())
        self.entity_encoder = nn.Sequential(
            nn.Linear(spec.entity_dim, eh), nn.ReLU(), nn.Linear(eh, eh), nn.Tanh()
        )
        self.attention_query = nn.Linear(eh + 4 * ch, eh)
        sh = spec.spatial_channels
        self.spatial_encoder = nn.Sequential(
            nn.Conv2d(eh + 1, sh, 3, padding=1), nn.ReLU(),
            nn.Conv2d(sh, sh, 3, padding=1), nn.ReLU(),
            nn.AdaptiveAvgPool2d((2, 3)), nn.Flatten(),
            nn.Linear(sh * 6, eh), nn.Tanh(),
        )
        self.fusion = nn.Sequential(nn.Linear(3 * eh + 4 * ch + 1, hh), nn.Tanh())
        self.gru = nn.GRU(hh, hh, batch_first=True)
        self.wait_head = nn.Linear(hh, 1)
        self.ability_head = nn.Linear(hh, spec.n_card_actions - 5) if spec.n_card_actions > 5 else None
        self.card_head = nn.Sequential(nn.Linear(hh + ch, eh), nn.Tanh(), nn.Linear(eh, 1))
        self.position_head = nn.Sequential(
            nn.Linear(hh + ch, hh), nn.Tanh(), nn.Linear(hh, spec.positions)
        )

    def initial_state(self, batch_size: int = 1) -> Tensor:
        return next(self.parameters()).new_zeros(1, batch_size, self.spec.hidden_dim)

    def _spatial_features(self, raw: Tensor, encoded: Tensor, mask: Tensor) -> Tensor:
        b, t, n, width = encoded.shape
        spec = self.spec
        x = (raw[..., spec.entity_x_index] * spec.spatial_w).long().clamp(0, spec.spatial_w - 1)
        y = (raw[..., spec.entity_y_index] * spec.spatial_h).long().clamp(0, spec.spatial_h - 1)
        indices = (y * spec.spatial_w + x).reshape(b * t, 1, n)
        values = (encoded * mask[..., None]).reshape(b * t, n, width).transpose(1, 2)
        cells = spec.spatial_h * spec.spatial_w
        summed = encoded.new_zeros(b * t, width, cells)
        summed.scatter_add_(2, indices.expand(-1, width, -1), values)
        counts = encoded.new_zeros(b * t, 1, cells)
        counts.scatter_add_(2, indices, mask.reshape(b * t, 1, n).to(encoded.dtype))
        spatial = torch.cat((summed / counts.clamp_min(1), torch.log1p(counts) / 3), 1)
        raster = spatial.reshape(b * t, width + 1, spec.spatial_h, spec.spatial_w)
        return self.spatial_encoder(raster).reshape(b, t, spec.entity_hidden)

    def _validate(self, obs: Mapping[str, Tensor]) -> tuple[int, int]:
        missing = set(OBSERVATION_KEYS) - obs.keys()
        if missing:
            raise ValueError(f"Missing observation fields: {sorted(missing)}")
        glob = obs["global_features"]
        if glob.ndim != 3 or glob.shape[-1] != self.spec.global_dim:
            raise ValueError("global_features must have shape [batch,time,global_dim]")
        b, t = glob.shape[:2]
        entities = obs["entities"]
        if entities.ndim != 4 or entities.shape[:2] != (b, t) or entities.shape[-1] != self.spec.entity_dim:
            raise ValueError("entities must have shape [batch,time,entities,entity_dim]")
        expected = {
            "entity_mask": entities.shape[:-1], "hand_ids": (b, t, 4),
            "legal_cards": (b, t, self.spec.n_card_actions), "legal_positions": (b, t, 4, self.spec.positions),
        }
        for key, shape in expected.items():
            if tuple(obs[key].shape) != tuple(shape):
                raise ValueError(f"{key} must have shape {tuple(shape)}")
        if not bool(torch.isfinite(glob).all()):
            raise ValueError("Nonfinite global observation")
        if not bool(torch.isfinite(entities.masked_fill(~obs["entity_mask"].bool()[..., None], 0)).all()):
            raise ValueError("Nonfinite visible entity observation")
        xy = entities[..., [self.spec.entity_x_index, self.spec.entity_y_index]]
        visible_xy = xy.masked_fill(~obs["entity_mask"].bool()[..., None], 0)
        if bool(((visible_xy < 0) | (visible_xy > 1)).any()):
            raise ValueError("Visible entity x/y must be normalized to [0,1]")
        if bool(((obs["hand_ids"] < 0) | (obs["hand_ids"] >= self.spec.card_vocab_size)).any()):
            raise ValueError("Hand identity outside checkpoint vocabulary")
        if not bool(obs["legal_cards"][..., 0].all()):
            raise ValueError("WAIT must be legal in every observation")
        return b, t

    def forward(
        self, observation: Mapping[str, Tensor], hidden: Tensor | None = None,
        *, episode_starts: Tensor | None = None, ablate_entities: bool = False,
    ) -> dict[str, Tensor]:
        """Process [B,T,...]; optional starts reset recurrent memory before a row."""
        b, t = self._validate(observation)
        mask = observation["entity_mask"].bool()
        if ablate_entities or not self.spec.use_entities:
            mask = torch.zeros_like(mask)
        clean_entities = observation["entities"].masked_fill(~mask[..., None], 0)
        entities = self.entity_encoder(clean_entities)
        glob = self.global_encoder(observation["global_features"])
        hand = self.card_embedding(observation["hand_ids"].long())
        hand_flat = hand.flatten(-2)
        query = self.attention_query(torch.cat((glob, hand_flat), -1))
        scores = (entities * query[..., None, :]).sum(-1) / math.sqrt(self.spec.entity_hidden)
        # A multiplicative mask and renormalization keep all-empty scenes finite.
        attention = scores.masked_fill(~mask, -1e4).softmax(-1) * mask
        attention = attention / attention.sum(-1, keepdim=True).clamp_min(1e-12)
        pooled = (entities * attention[..., None]).sum(-2)
        count = torch.log1p(mask.sum(-1, keepdim=True).to(glob.dtype)) / 5.0
        spatial = self._spatial_features(clean_entities, entities, mask) if self.spec.use_spatial else torch.zeros_like(pooled)
        if ablate_entities or not self.spec.use_entities:
            spatial = torch.zeros_like(spatial)
        encoded = self.fusion(torch.cat((glob, pooled, spatial, hand_flat, count), -1))
        encoded = self._augment_fused(observation, encoded)
        if hidden is None:
            hidden = self.initial_state(b)
        if tuple(hidden.shape) != (1, b, self.spec.hidden_dim):
            raise ValueError("Recurrent state does not match batch or policy")
        if not self.spec.use_recurrent:
            recurrent, next_hidden = encoded, encoded[:, -1:, :].transpose(0, 1)
        elif episode_starts is None:
            recurrent, next_hidden = self.gru(encoded, hidden)
        else:
            if tuple(episode_starts.shape) != (b, t):
                raise ValueError("episode_starts must have shape [batch,time]")
            pieces = []
            next_hidden = hidden
            for index in range(t):
                next_hidden = next_hidden * (~episode_starts[:, index].bool())[None, :, None]
                piece, next_hidden = self.gru(encoded[:, index:index + 1], next_hidden)
                pieces.append(piece)
            recurrent = torch.cat(pieces, dim=1)
        per_card = torch.cat((recurrent[..., None, :].expand(b, t, 4, -1), hand), -1)
        card_logits = torch.cat((self.wait_head(recurrent), self.card_head(per_card).squeeze(-1)), -1)
        if self.ability_head is not None:
            card_logits = torch.cat((card_logits, self.ability_head(recurrent)), -1)
        position_logits = self.position_head(per_card)
        positions_legal = observation["legal_positions"].bool()
        cards_legal = observation["legal_cards"].bool().clone()
        cards_legal[..., 1:5] &= positions_legal.any(-1)
        # Inactive slots get a deterministic dummy position to avoid NaNs. Their
        # card probability remains exactly zero, so this cannot permit a play.
        safe_positions = positions_legal.clone()
        safe_positions[..., 0] |= ~positions_legal.any(-1)
        return {
            "card_logits": card_logits.masked_fill(~cards_legal, -torch.inf),
            "position_logits": position_logits.masked_fill(~safe_positions, -torch.inf),
            "hidden": next_hidden,
            "recurrent_features": recurrent,
            "entity_attention": attention,
            "spatial_features": spatial,
            "effective_legal_cards": cards_legal,
        }

    def _augment_fused(self, observation: Mapping[str, Tensor], encoded: Tensor) -> Tensor:
        """Subclass hook without altering the historical policy parameter schema."""
        return encoded

    @torch.inference_mode()
    def act(
        self, observation: Mapping[str, Any], hidden: Tensor | None = None,
        *, deterministic: bool = True, generator: torch.Generator | None = None,
        ablate_entities: bool = False,
    ) -> tuple[dict[str, int | float], Tensor]:
        """Decode one live frame, returning the action and explicit next state."""
        obs = tensor_observation(observation, device=next(self.parameters()).device, single=True)
        output = self(obs, hidden, ablate_entities=ablate_entities)
        decoded = decode_actions(output, deterministic=deterministic, generator=generator)
        action = {key: int(value.item()) if key in {"card", "position"} else float(value.item())
                  for key, value in decoded.items()}
        return action, output["hidden"]


class PolicyAgent:
    """Minimal stateful runner: reset once per battle and once per player view."""

    def __init__(self, model: EntityGRUPolicy, *, deterministic: bool = True, seed: int = 0,
                 ablate_entities: bool = False):
        self.model = model.eval()
        self.deterministic = deterministic
        self.ablate_entities = ablate_entities
        self.generator = torch.Generator(device=next(model.parameters()).device).manual_seed(seed)
        self.hidden: Tensor | None = None

    def reset(self) -> None:
        self.hidden = None

    def act(self, observation: Mapping[str, Any]) -> dict[str, int | float]:
        action, self.hidden = self.model.act(
            observation, self.hidden, deterministic=self.deterministic, generator=self.generator,
            ablate_entities=self.ablate_entities,
        )
        return action


def save_policy(
    model: EntityGRUPolicy, path: str | Path, *, metadata: Mapping[str, Any] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Atomically save tensors and plain metadata; overwriting requires opt-in."""
    path = Path(path)
    if path.exists() and not overwrite:
        raise FileExistsError(path)
    # Round-trip limits metadata to JSON values rather than executable objects.
    clean_metadata = json.loads(json.dumps(dict(metadata or {}), allow_nan=False))
    payload = {
        "format": getattr(model, "checkpoint_format", CHECKPOINT_FORMAT), "spec": asdict(model.spec),
        "state_dict": {key: value.detach().cpu() for key, value in model.state_dict().items()},
        "metadata": clean_metadata,
    }
    if hasattr(model, "timing_config"):
        payload["timing_config"] = asdict(model.timing_config)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(fd)
    try:
        # torch.save(path) embeds that path's basename in its ZIP members.
        # Serialize to a stream so equal weights/metadata have equal bytes even
        # when the atomic temporary filename or destination directory differs.
        buffer = io.BytesIO()
        torch.save(payload, buffer)
        Path(temporary).write_bytes(buffer.getvalue())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {"path": str(path), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "parameters": sum(parameter.numel() for parameter in model.parameters()),
            "spec": asdict(model.spec), "metadata": clean_metadata}


def load_policy(
    path: str | Path, *, expected_spec: PolicySpec | None = None,
    expected_sha256: str | None = None, device: torch.device | str = "cpu",
) -> tuple[EntityGRUPolicy, dict[str, Any]]:
    """Strict schema/state loading with PyTorch's non-executable weights loader."""
    path = Path(path)
    if expected_sha256 is not None and hashlib.sha256(path.read_bytes()).hexdigest() != expected_sha256:
        raise ValueError("Checkpoint SHA256 mismatch")
    payload = torch.load(path, map_location="cpu", weights_only=True)
    if not isinstance(payload, dict):
        raise ValueError("Checkpoint fields do not match the policy contract")
    timing = payload.get("format") == "arena-lab-public-timing-gru-v1"
    expected_fields = {"format", "spec", "state_dict", "metadata"} | ({"timing_config"} if timing else set())
    if set(payload) != expected_fields:
        raise ValueError("Checkpoint fields do not match the policy contract")
    if payload["format"] != CHECKPOINT_FORMAT and not timing:
        raise ValueError("Unsupported checkpoint format")
    if not isinstance(payload["spec"], dict) or set(payload["spec"]) != set(PolicySpec.__dataclass_fields__):
        raise ValueError("Checkpoint spec fields do not match the policy contract")
    spec = PolicySpec(**payload["spec"])
    if expected_spec is not None and expected_spec != spec:
        raise ValueError("Observation/model spec does not match checkpoint")
    metadata = json.loads(json.dumps(payload["metadata"], allow_nan=False))
    if not isinstance(metadata, dict):
        raise ValueError("Checkpoint metadata must be an object")
    if timing:
        from .timing import TimingConfig, TimingGRUPolicy
        model = TimingGRUPolicy(spec, TimingConfig(**payload["timing_config"]))
    else:
        model = EntityGRUPolicy(spec)
    model.load_state_dict(payload["state_dict"], strict=True)
    for value in model.state_dict().values():
        if not bool(torch.isfinite(value).all()):
            raise ValueError("Checkpoint contains nonfinite weights")
    return model.to(device).eval(), metadata

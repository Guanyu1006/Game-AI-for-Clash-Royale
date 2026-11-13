"""Causal event hazard, conditional card/location policy, and shared likelihoods.

The hazard predicts a non-WAIT event over an explicitly declared decision horizon.
Elapsed time enters recurrent state separately. Request/confirmation timestamps are
supervision/audit fields and are never consumed by the actor.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Mapping

import torch
from torch import Tensor, nn
from torch.nn import functional as F

from .model import EntityGRUPolicy, PolicySpec


@dataclass(frozen=True)
class TimingConfig:
    tick_hz: float = 20.0
    decision_ticks: int = 10
    event_threshold: float = 0.5
    hazard_temperature: float = 1.0
    hazard_log_scale: float = 0.0

    def __post_init__(self):
        if not math.isfinite(self.tick_hz) or self.tick_hz <= 0:
            raise ValueError("tick_hz must be finite and positive")
        if type(self.decision_ticks) is not int or self.decision_ticks <= 0:
            raise ValueError("decision_ticks must be a positive integer")
        if not 0 < self.event_threshold <= 1:
            raise ValueError("event_threshold must lie in (0,1]")
        if not math.isfinite(self.hazard_temperature) or self.hazard_temperature <= 0:
            raise ValueError("hazard_temperature must be finite and positive")
        if not math.isfinite(self.hazard_log_scale):
            raise ValueError("hazard_log_scale must be finite")


def hazard_log_probabilities(rate: Tensor, dt: Tensor) -> tuple[Tensor, Tensor]:
    """Log P(no event), log P(event); stable for small positive rates."""
    mass = (rate * dt).clamp_min(torch.finfo(rate.dtype).tiny)
    return -mass, torch.log(-torch.expm1(-mass))


class TimingGRUPolicy(EntityGRUPolicy):
    checkpoint_format = "arena-lab-public-timing-gru-v1"

    def __init__(self, spec: PolicySpec, timing_config: TimingConfig | None = None):
        super().__init__(spec)
        self.timing_config = timing_config or TimingConfig()
        self.elapsed_encoder = nn.Sequential(nn.Linear(1, spec.hidden_dim), nn.Tanh())
        self.event_head = nn.Linear(spec.hidden_dim, 1)
        # WAIT is represented by the hazard survival probability, so the old
        # wait head is retained for structural compatibility but never trained.
        self.wait_head.requires_grad_(False)

    def _ticks(self, observation: Mapping[str, Tensor], key: str, like: Tensor) -> Tensor:
        if key in observation:
            ticks = observation[key].to(dtype=like.dtype, device=like.device)
            if tuple(ticks.shape) != tuple(like.shape[:2]):
                raise ValueError(f"{key} must have shape [batch,time]")
            if not bool(torch.isfinite(ticks).all()) or bool((ticks < 0).any()):
                raise ValueError(f"{key} must be finite and nonnegative")
            if key == "decision_ticks":
                # Padded, loss-masked frames have zero duration and only WAIT.
                real = observation["legal_cards"][..., 1:].any(-1)
                if bool(((ticks <= 0) & real).any()):
                    raise ValueError("Actionable observations require positive decision_ticks")
            return ticks
        return like.new_full(like.shape[:2], self.timing_config.decision_ticks)

    def _augment_fused(self, observation: Mapping[str, Tensor], encoded: Tensor) -> Tensor:
        elapsed = self._ticks(observation, "elapsed_ticks", encoded) / self.timing_config.tick_hz
        return encoded + self.elapsed_encoder(torch.log1p(elapsed)[..., None])

    def forward(self, observation: Mapping[str, Tensor], hidden: Tensor | None = None,
                *, episode_starts: Tensor | None = None, ablate_entities: bool = False) -> dict[str, Tensor]:
        if "elapsed_ticks" not in observation:
            # Fixed-cadence live callers need the same first-frame zero used by
            # collected trajectories. Explicit device elapsed time takes priority.
            elapsed = observation["global_features"].new_full(
                observation["global_features"].shape[:2], self.timing_config.decision_ticks)
            if hidden is None:
                elapsed[:, 0] = 0
            if episode_starts is not None:
                elapsed = elapsed.masked_fill(episode_starts.bool(), 0)
            observation = dict(observation, elapsed_ticks=elapsed)
        output = super().forward(observation, hidden, episode_starts=episode_starts,
                                 ablate_entities=ablate_entities)
        recurrent = output["recurrent_features"]
        dt = self._ticks(observation, "decision_ticks", recurrent) / self.timing_config.tick_hz
        raw_rate = F.softplus(self.event_head(recurrent).squeeze(-1)).clamp_min(1e-8)
        rate = torch.exp(torch.log(raw_rate) / self.timing_config.hazard_temperature
                         + self.timing_config.hazard_log_scale).clamp_max(1e6)
        log_wait, log_event = hazard_log_probabilities(rate, dt)
        available = output["effective_legal_cards"][..., 1:].any(-1)
        log_wait = torch.where(available, log_wait, torch.zeros_like(log_wait))
        log_event = torch.where(available, log_event, torch.full_like(log_event, -torch.inf))
        conditional = output["card_logits"][..., 1:].clone()
        # Finite dummy conditional distribution when only WAIT is legal.
        conditional[..., 0] = torch.where(available, conditional[..., 0], torch.zeros_like(log_wait))
        conditional_logp = conditional.log_softmax(-1)
        output.update({
            "card_logits": torch.cat((log_wait[..., None], log_event[..., None] + conditional_logp), -1),
            "conditional_card_logits": conditional,
            "raw_hazard_rate": raw_rate,
            "hazard_rate": rate,
            "decision_dt": dt,
            "event_logits": log_event - log_wait,
            "event_probability": log_event.exp(),
            "event_threshold": recurrent.new_tensor(self.timing_config.event_threshold),
        })
        return output


def timing_losses(output: Mapping[str, Tensor], batch: Mapping[str, object]) -> tuple[Tensor, Tensor, Tensor]:
    """Natural-time event NLL, per-event card NLL, per-deployment location NLL."""
    device = output["card_logits"].device
    valid = torch.as_tensor(batch["label_valid"], dtype=torch.bool, device=device)
    cards = torch.as_tensor(batch["target_card"], dtype=torch.long, device=device)
    actionable = valid & output["effective_legal_cards"][..., 1:].any(-1)
    events = valid & (cards > 0)
    zero = output["hidden"].sum() * 0
    if actionable.any():
        # Explicit Bernoulli loss avoids splitting positive event mass across slots.
        event_loss = F.binary_cross_entropy_with_logits(output["event_logits"][actionable],
                                                        events[actionable].to(output["event_logits"].dtype))
    else:
        event_loss = zero
    card_loss = F.cross_entropy(output["conditional_card_logits"][events], cards[events] - 1) if events.any() else zero
    positions = torch.as_tensor(batch["position_valid"], dtype=torch.bool, device=device) & events & (cards <= 4)
    if positions.any():
        target = torch.as_tensor(batch["target_position"], dtype=torch.long, device=device)[positions]
        selected = output["position_logits"][positions][torch.arange(int(positions.sum()), device=device), cards[positions]-1]
        position_loss = F.cross_entropy(selected, target)
    else:
        position_loss = zero
    return event_loss, card_loss, position_loss


def timing_action_log_prob_entropy(output: Mapping[str, Tensor], cards: Tensor,
                                   positions: Tensor) -> tuple[Tensor, Tensor]:
    """Exact joint action likelihood and entropy for BC/PPO and live sampling.

    H(A)=H(card including WAIT)+sum(slot probability * H(location|slot)).
    The deterministic threshold decoder is not represented as a sampled policy.
    """
    card_logp = output["card_logits"].log_softmax(-1)
    location_logp = output["position_logits"].log_softmax(-1)
    logp = card_logp.gather(-1, cards[..., None]).squeeze(-1)
    slots = (cards - 1).clamp(0, 3)
    selected = location_logp.gather(-2, slots[..., None, None].expand(*slots.shape, 1, location_logp.shape[-1])).squeeze(-2)
    deployment = (cards >= 1) & (cards <= 4)
    selected_logp = selected.gather(-1, positions[..., None]).squeeze(-1)
    logp = logp + torch.where(deployment, selected_logp, torch.zeros_like(selected_logp))
    card_entropy = -(card_logp.exp() * card_logp.masked_fill(~torch.isfinite(card_logp), 0)).sum(-1)
    location_entropy = -(location_logp.exp() * location_logp.masked_fill(~torch.isfinite(location_logp), 0)).sum(-1)
    entropy = card_entropy + (card_logp[..., 1:5].exp() * location_entropy).sum(-1)
    return logp, entropy

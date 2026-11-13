"""Recurrent PPO against immutable historical opponents, with resumable rounds.

Actor and critic consume public observations only. Both seats continue on every
joint step. Rewards are terminal score plus an explicitly declared public tower
potential; simulator wins never imply official ladder ability.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
import time

import numpy as np
import torch
from torch import nn

from .league import OpponentPool, canonical, digest, write_json
from .model import OBSERVATION_KEYS, decode_actions, load_policy, save_policy, tensor_observation


@dataclass(frozen=True)
class PPOConfig:
    rounds: int = 3
    games_per_round: int = 8
    epochs: int = 2
    sequence: int = 32
    learning_rate: float = 0.00003
    clip: float = .15
    entropy_coefficient: float = .001
    value_coefficient: float = .5
    gamma_per_second: float = .997
    lambda_per_second: float = .95
    tower_potential_weight: float = .2
    max_grad_norm: float = .5
    max_decisions: int = 1500
    seed: int = 310001
    torch_threads: int = 1

    def __post_init__(self):
        for key in ("rounds", "games_per_round", "epochs", "sequence", "max_decisions", "torch_threads"):
            if type(getattr(self, key)) is not int or getattr(self, key) < 1:
                raise ValueError(key + " must be a positive integer")
        if not 0 < self.gamma_per_second <= 1 or not 0 < self.lambda_per_second <= 1:
            raise ValueError("Discounts must be defined per real simulated second")
        if not 0 < self.clip < 1 or self.learning_rate <= 0 or self.max_grad_norm <= 0:
            raise ValueError("Invalid PPO optimization configuration")


def action_statistics(output, cards, positions):
    """Exact joint entropy includes position entropy weighted by slot probability."""
    card_dist = torch.distributions.Categorical(logits=output["card_logits"])
    position_dist = torch.distributions.Categorical(logits=output["position_logits"])
    slot = (cards-1).clamp(0, 3)
    chosen_logits = output["position_logits"].gather(-2, slot[..., None, None].expand(*slot.shape, 1,
                                                 output["position_logits"].shape[-1])).squeeze(-2)
    selected_dist = torch.distributions.Categorical(logits=chosen_logits)
    is_deploy = (cards >= 1) & (cards <= 4)
    logp = card_dist.log_prob(cards) + torch.where(is_deploy, selected_dist.log_prob(positions), 0.)
    entropy = card_dist.entropy() + (card_dist.probs[..., 1:5]*position_dist.entropy()).sum(-1)
    return logp, entropy


def generalized_advantages(rewards, values, elapsed_seconds, *, gamma_per_second, lambda_per_second,
                           terminal, bootstrap_value=0.):
    """Variable-time GAE; natural termination zeroes bootstrap, truncation does not."""
    rewards, values, dt = map(lambda x: np.asarray(x, dtype=np.float64), (rewards, values, elapsed_seconds))
    if rewards.shape != values.shape or rewards.shape != dt.shape or rewards.ndim != 1 or not len(rewards):
        raise ValueError("GAE requires nonempty, aligned one-dimensional arrays")
    if not np.isfinite(np.concatenate((rewards, values, dt))).all() or (dt <= 0).any():
        raise ValueError("GAE values must be finite and dt positive")
    if not 0 < gamma_per_second <= 1 or not 0 < lambda_per_second <= 1:
        raise ValueError("Invalid time discounts")
    advantage = np.zeros_like(rewards)
    tail, next_value = 0., 0. if terminal else float(bootstrap_value)
    for i in range(len(rewards)-1, -1, -1):
        gamma, lam = gamma_per_second**dt[i], lambda_per_second**dt[i]
        delta = rewards[i] + gamma*next_value-values[i]
        tail = delta + gamma*lam*tail
        advantage[i], next_value = tail, values[i]
    return advantage.astype(np.float32), (advantage+values).astype(np.float32)


def public_potential(obs):
    entities = np.asarray(obs["entities"])[np.asarray(obs["entity_mask"], dtype=bool)]
    towers = entities[(entities[:, 6] > .5) | (entities[:, 7] > .5)]
    return float((towers[:, 2]*(2*towers[:, 3]-1)).sum()/3)


def collect_game(model, critic, env, opponent, *, seed, side, decks, options, config, generator):
    observations, info = env.reset(seed=seed, decks=decks, **options)
    frames, cards, positions, log_probs, values, rewards, times = [], [], [], [], [], [], []
    hidden = None
    row = {"seed": seed, "side": side, "status": "truncated", "outcome": None,
           "executed": 0, "attempts": 0, "refused": 0, "decisions": 0}
    model.eval()
    terminal = False
    delayed = env.identity()["effective_command_delay_ticks"]
    start = time.perf_counter()
    for _ in range(config.max_decisions):
        obs = observations[side]
        with torch.no_grad():
            output = model(tensor_observation(obs, single=True), hidden)
            hidden = output["hidden"]
            decoded = decode_actions(output, deterministic=False, generator=generator)
            card, pos = int(decoded["card"].item()), int(decoded["position"].item())
            logp, _ = action_statistics(output, decoded["card"], decoded["position"])
            value = critic(output["recurrent_features"]).item()
        # Keep actor arrays exactly as observed; no float16 quantization drift.
        frames.append({key: np.array(obs[key], copy=True) for key in OBSERVATION_KEYS})
        cards.append(card); positions.append(pos); log_probs.append(float(logp.item())); values.append(value)
        old_tick, phi = info["tick"], public_potential(obs)
        enemy_action = opponent(observations[1-side])
        observations, raw_rewards, terminal, truncated, info = env.step({side: {"card": card, "position": pos},
                                                                        1-side: enemy_action})
        dt = (info["tick"]-old_tick)/20.
        next_phi = 0. if terminal else public_potential(observations[side])
        rewards.append(float(raw_rewards[side]) + config.tower_potential_weight *
                       (config.gamma_per_second**dt * next_phi-phi))
        times.append(dt)
        own = [r for r in info["deploy_results"] if r["team"] == side]
        row["attempts"] += int(card != 0)
        row["refused"] += sum(r["status"] != 0 for r in own)
        row["executed"] += sum(r[1] == side and r[-1] == 0 for r in info["commands_run"]) if delayed else sum(r["status"] == 0 for r in own)
        if delayed:
            row["refused"] += sum(r[1] == side and r[-1] != 0 for r in info["commands_run"])
        row["decisions"] += 1
        if terminal:
            row.update(status="natural_end", outcome="draw" if info["winner"] == 2 else
                       "win" if info["winner"] == side else "loss")
        if terminal or truncated:
            break
    bootstrap = 0.
    if not terminal:
        with torch.no_grad():
            tail_output = model(tensor_observation(observations[side], single=True), hidden)
            bootstrap = critic(tail_output["recurrent_features"]).item()
    advantage, returns = generalized_advantages(rewards, values, times,
        gamma_per_second=config.gamma_per_second, lambda_per_second=config.lambda_per_second,
        terminal=terminal, bootstrap_value=bootstrap)
    row.update(final_tick=info["tick"], final_state_hash=info["state_hash"], wall_seconds=time.perf_counter()-start)
    trajectory = {key: np.stack([frame[key] for frame in frames]) for key in OBSERVATION_KEYS}
    trajectory.update(cards=np.asarray(cards), positions=np.asarray(positions), old_logp=np.asarray(log_probs),
                      old_values=np.asarray(values), advantage=advantage, returns=returns)
    return trajectory, row


def update_ppo(model, critic, optimizer, trajectories, config):
    total = sum(len(t["cards"]) for t in trajectories)
    advantages = np.concatenate([t["advantage"] for t in trajectories])
    mean, std = float(advantages.mean()), max(float(advantages.std()), 1e-6)
    logs = []
    for epoch in range(config.epochs):
        optimizer.zero_grad()
        metrics = dict(policy_loss=0., value_loss=0., entropy=0., approximate_kl=0., clip_fraction=0.)
        for trajectory in trajectories:
            hidden = None  # replay history with current weights at each PPO epoch
            for start in range(0, len(trajectory["cards"]), config.sequence):
                stop = min(start+config.sequence, len(trajectory["cards"]))
                obs = tensor_observation({k: trajectory[k][None, start:stop] for k in OBSERVATION_KEYS})
                output = model(obs, hidden)
                hidden = output["hidden"].detach()
                cards = torch.as_tensor(trajectory["cards"][None, start:stop], dtype=torch.long)
                positions = torch.as_tensor(trajectory["positions"][None, start:stop], dtype=torch.long)
                logp, entropy = action_statistics(output, cards, positions)
                old_logp = torch.as_tensor(trajectory["old_logp"][None, start:stop], dtype=torch.float32)
                advantage = torch.as_tensor((trajectory["advantage"][None, start:stop]-mean)/std)
                target = torch.as_tensor(trajectory["returns"][None, start:stop])
                ratio = (logp-old_logp).exp()
                policy_loss = -torch.minimum(ratio*advantage, ratio.clamp(1-config.clip, 1+config.clip)*advantage).mean()
                value_loss = .5*(critic(output["recurrent_features"]).squeeze(-1)-target).square().mean()
                loss = policy_loss + config.value_coefficient*value_loss-config.entropy_coefficient*entropy.mean()
                if not torch.isfinite(loss):
                    raise ValueError("Non-finite PPO loss; preserving last complete round")
                weight = (stop-start)/total
                (loss*weight).backward()
                with torch.no_grad():
                    metrics["policy_loss"] += float(policy_loss)*weight
                    metrics["value_loss"] += float(value_loss)*weight
                    metrics["entropy"] += float(entropy.mean())*weight
                    metrics["approximate_kl"] += float(((ratio-1)-(logp-old_logp)).mean())*weight
                    metrics["clip_fraction"] += float(((ratio-1).abs()>config.clip).float().mean())*weight
        grad = nn.utils.clip_grad_norm_([*model.parameters(), *critic.parameters()], config.max_grad_norm)
        if not torch.isfinite(grad):
            raise ValueError("Non-finite PPO gradient")
        optimizer.step()
        logs.append(dict(epoch=epoch+1, gradient_norm=float(grad), **metrics))
    return logs


def train_selfplay(checkpoint, output, *, matchups, config=PPOConfig(), resume=None, stop_after_round=None):
    from .environment import ArenaConfig, ArenaEnv
    output = Path(output)
    output.mkdir(parents=True, exist_ok=resume is not None)
    if resume is None and any(output.iterdir()):
        raise FileExistsError("Self-play output must be new")
    torch.set_num_threads(config.torch_threads)
    torch.manual_seed(config.seed)
    rng = random.Random(config.seed)
    generator = torch.Generator().manual_seed(config.seed)
    model, metadata = load_policy(checkpoint)
    if not metadata.get("trained") or not metadata.get("training_steps", 0):
        raise ValueError("Start self-play from a trained policy")
    critic = nn.Linear(model.spec.hidden_dim, 1)
    optimizer = torch.optim.Adam([*model.parameters(), *critic.parameters()], lr=config.learning_rate)
    env = ArenaEnv(ArenaConfig(**metadata["environment_identity"]["config"]))
    pool = OpponentPool(output / "pool")
    for name in ("random", "scripted"):
        pool.add_baseline(name)
    initial = pool.add_checkpoint(checkpoint, label="imitation-foundation", foundation=True)
    current, start_round, steps = initial, 0, 0
    plan = {"config": asdict(config), "matchups": matchups, "checkpoint_sha256": digest(checkpoint),
            "environment_identity": env.identity(), "reward": "terminal +/-1 + time-discounted public tower potential",
            "partition": "training_only", "sampling": "uniform25pct + smoothed loss-squared PFSP75pct",
            "implementation_sha256": {name: digest(Path(__file__).with_name(name))
                                      for name in ("selfplay.py", "league.py", "model.py", "timing.py")},
            "seed_range": [config.seed, config.seed+config.rounds*config.games_per_round-1]}

    def save_resume(completed):
        state = {"schema": "arena-selfplay-resume-v1", "plan": json.loads(canonical(plan)),
            "model": model.state_dict(), "critic": critic.state_dict(), "optimizer": optimizer.state_dict(),
            "generator": generator.get_state(), "torch_rng": torch.get_rng_state(), "python_rng": rng.getstate(),
            "current": current, "completed_rounds": completed, "steps": steps,
            "pool_sha256": digest(pool.path), "pool_state": pool.state}
        temporary = output / "resume.tmp"
        torch.save(state, temporary)
        temporary.replace(output / "resume.pt")

    if resume is not None:
        saved = torch.load(resume, weights_only=True, map_location="cpu")
        if saved["plan"] != json.loads(canonical(plan)):
            raise ValueError("Resume configuration/identity mismatch")
        model.load_state_dict(saved["model"]); critic.load_state_dict(saved["critic"])
        optimizer.load_state_dict(saved["optimizer"])
        generator.set_state(saved["generator"]); torch.set_rng_state(saved["torch_rng"])
        rng.setstate(saved["python_rng"])
        current, start_round, steps = saved["current"], saved["completed_rounds"], saved["steps"]
        if saved["pool_sha256"] != digest(pool.path):
            # A killed collection may have appended unfinished training results.
            # Preserve them, then restore only the last complete transaction.
            previous = saved["pool_state"]
            if (pool.state["promotions"] != previous["promotions"] or
                    any(pool.state["members"].get(k) != v for k, v in previous["members"].items()) or
                    pool.state["training_results"][:len(previous["training_results"])] != previous["training_results"]):
                raise ValueError("Pool has unrelated edits; cannot restore interrupted training")
            archive = output / f"interrupted-pool-{time.time_ns()}.json"
            write_json(archive, pool.state)
            pool.state = previous
            pool._save()
            if digest(pool.path) != saved["pool_sha256"]:
                raise ValueError("Restored pool differs from saved complete round")
    else:
        write_json(output / "plan.json", plan)
        save_resume(0)
    reports = []
    started = time.perf_counter()
    try:
        for round_index in range(start_round, config.rounds):
            attempt = len(list(output.glob(f"round-{round_index:03d}-attempt-*")))
            round_output = output / f"round-{round_index:03d}-attempt-{attempt:03d}"
            round_output.mkdir()
            trajectories, rows = [], []
            for game in range(config.games_per_round):
                index = round_index*config.games_per_round+game
                seed, side = config.seed+index, index%2
                matchup = matchups[(index//2)%len(matchups)]
                decks = matchup["decks"] if side == 0 else matchup["decks"][::-1]
                options = dict(matchup.get("reset_options", {}))
                if side == 1:
                    for key in ("forms", "levels", "tower_levels", "elixir_milli"):
                        if options.get(key) is not None:
                            options[key] = options[key][::-1]
                opponent_id = pool.sample(current, rng)
                opponent = pool.actor(opponent_id, environment=env, seed=seed*101+1-side,
                                      deterministic=False, side=1-side)
                try:
                    trajectory, row = collect_game(model, critic, env, opponent, seed=seed, side=side,
                        decks=decks, options=options, config=config, generator=generator)
                    trajectories.append(trajectory)
                    steps += row["decisions"]
                except Exception as error:
                    row = {"seed": seed, "side": side, "status": "fault", "outcome": None,
                           "decisions": 0, "error": type(error).__name__+": "+str(error)}
                row["opponent"] = opponent_id
                rows.append(row)
                pool.record_training(current, opponent_id, row["outcome"], status=row["status"], seed=seed, side=side)
                write_json(round_output / "episodes.json", rows)
            if not trajectories:
                raise RuntimeError("All self-play collection failed; no optimizer update")
            optimization = update_ppo(model, critic, optimizer, trajectories, config)
            checkpoint_path = round_output / "policy.pt"
            new_metadata = dict(metadata, trained=True,
                                training_steps=int(metadata["training_steps"])+(round_index+1)*config.epochs,
                                optimizer_updates_this_stage=(round_index+1)*config.epochs,
                                selfplay_environment_steps=steps, league_parent_member=current,
                                training_kind="historical-selfplay-ppo", selfplay_round=round_index+1,
                                parent_checkpoint_sha256=digest(checkpoint), selfplay_config=asdict(config))
            artifact = save_policy(model, checkpoint_path, metadata=new_metadata)
            current = pool.add_checkpoint(checkpoint_path, label=f"ppo-round-{round_index+1}", parent_member=current)
            report = {"round": round_index+1, "steps_total": steps, "episodes": rows,
                      "optimization": optimization, "artifact": artifact, "member": current,
                      "promoted": False, "reason": "snapshots require separate frozen promotion evaluation"}
            write_json(round_output / "report.json", report)
            reports.append(report)
            save_resume(round_index+1)
            if stop_after_round is not None and round_index+1 >= stop_after_round:
                result = {"schema": "arena-selfplay-report-v1", "completed_rounds": round_index+1,
                          "planned_rounds": config.rounds, "environment_steps": steps,
                          "status": "checkpointed_operational_stop", "resume": str(output/"resume.pt")}
                write_json(output/"progress.json", result)
                return result
        wall = time.perf_counter()-started
        result = {"schema": "arena-selfplay-report-v1", "completed_rounds": config.rounds,
                  "environment_steps": steps, "wall_seconds_this_session": wall, "current_member": current,
                  "champion": pool.state["champion"], "strength_improvement_verified": False,
                  "pool_sha256": digest(pool.path), "resume_sha256": digest(output/"resume.pt")}
        write_json(output / "report.json", result)
        return result
    finally:
        env.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--plan", required=True)
    parser.add_argument("--resume")
    parser.add_argument("--stop-after-round", type=int, help="Operational interruption audit; does not change the frozen training plan")
    args = parser.parse_args()
    plan = json.loads(Path(args.plan).read_text())
    print(canonical(train_selfplay(args.checkpoint, args.output, matchups=plan["matchups"],
                                  config=PPOConfig(**plan["config"]), resume=args.resume,
                                  stop_after_round=args.stop_after_round)))


if __name__ == "__main__":
    main()

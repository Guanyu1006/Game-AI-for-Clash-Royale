"""Immutable historical opponents, frozen paired matches and conservative promotion.

Only training results influence PFSP. Promotion results are separate, never fed
back as training statistics. A snapshot is not automatically a champion.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
import math
from pathlib import Path
import random
import shutil
import time
from typing import Any


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(canonical(value) + "\n")
    temporary.replace(path)


class OpponentPool:
    """One controller, content-addressed weights, append-only identity registry."""

    def __init__(self, directory):
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / "pool.json"
        if self.path.exists():
            self.state = json.loads(self.path.read_text())
            if self.state.get("schema") != "arena-league-v1":
                raise ValueError("Unsupported league registry")
        else:
            self.state = {"schema": "arena-league-v1", "members": {}, "training_results": [],
                          "champion": None, "promotions": []}
        for member in self.state["members"].values():
            if member["kind"] == "checkpoint":
                path = self.directory / member["path"]
                if digest(path) != member["sha256"]:
                    raise ValueError("Historical checkpoint was changed")

    def _save(self):
        write_json(self.path, self.state)

    def add_baseline(self, name):
        if name not in {"random", "scripted", "wait"}:
            raise ValueError("Unsupported baseline")
        identifier = "baseline-" + name
        source = Path(__file__).with_name("baselines.py" if name == "scripted" else "evaluation.py")
        identity = {"kind": "baseline", "name": name, "source_sha256": digest(source)}
        if identifier in self.state["members"] and self.state["members"][identifier] != identity:
            raise ValueError("Baseline code changed; use a new versioned pool")
        self.state["members"].setdefault(identifier, identity)
        self._save()
        return identifier

    def add_checkpoint(self, checkpoint, *, label="snapshot", foundation=False, parent_member=None):
        from .model import load_policy
        model, metadata = load_policy(checkpoint)
        if not metadata.get("trained") or metadata.get("training_steps", 0) <= 0:
            raise ValueError("A historical member must have actually trained")
        del model
        sha = digest(checkpoint)
        identifier = "policy-" + sha[:20]
        if parent_member is not None:
            parent = self.state["members"].get(parent_member)
            if parent is None or parent["kind"] != "checkpoint" or parent_member == identifier:
                raise ValueError("Snapshot parent must be an existing distinct checkpoint")
            if parent["environment_identity"] != metadata["environment_identity"]:
                raise ValueError("Snapshot parent environment differs")
            if identifier in self.state["members"] and self.state["members"][identifier].get("parent_member") != parent_member:
                raise ValueError("Immutable historical member cannot change its parent")
        if identifier not in self.state["members"]:
            target = self.directory / "weights" / (sha + ".pt")
            target.parent.mkdir(exist_ok=True)
            if target.exists() and digest(target) != sha:
                raise ValueError("Content-addressed checkpoint collision")
            if not target.exists():
                shutil.copyfile(checkpoint, target)
            self.state["members"][identifier] = {
                "kind": "checkpoint", "sha256": sha, "path": str(target.relative_to(self.directory)),
                "label": label, "environment_identity": metadata["environment_identity"],
            }
            if parent_member is not None:
                self.state["members"][identifier]["parent_member"] = parent_member
        if foundation:
            if self.state["champion"] not in {None, identifier}:
                raise ValueError("Foundation cannot replace a champion; use promotion")
            self.state["champion"] = identifier
        self._save()
        return identifier

    def record_training(self, candidate, opponent, outcome, *, status, seed, side):
        if candidate not in self.state["members"] or opponent not in self.state["members"]:
            raise ValueError("Unknown league member")
        if status not in {"natural_end", "fault", "truncated"}:
            raise ValueError("Invalid training status")
        if status == "natural_end" and outcome not in {"win", "draw", "loss"}:
            raise ValueError("Natural result must have an outcome")
        if status != "natural_end" and outcome is not None:
            raise ValueError("A failed game has no outcome")
        self.state["training_results"].append({"candidate": candidate, "opponent": opponent,
            "outcome": outcome, "status": status, "seed": int(seed), "side": int(side)})
        self._save()

    def probabilities(self, candidate, *, uniform_mix=.25):
        """Loss-squared PFSP; ancestors supply at most four prior pseudo-games.

        Current-policy results have full weight. The latest 32 natural games
        per opponent in the parent chain provide a bounded prior, so publishing
        each snapshot does not erase known weaknesses or freeze stale beliefs.
        """
        if not 0 < uniform_mix <= 1:
            raise ValueError("Coverage mix must be in (0,1]")
        members = sorted(self.state["members"])
        if not members:
            raise ValueError("Empty opponent pool")
        if candidate not in self.state["members"]:
            raise ValueError("Unknown candidate")
        ancestors, cursor = set(), candidate
        while (parent := self.state["members"][cursor].get("parent_member")) is not None:
            if parent == candidate or parent in ancestors or parent not in self.state["members"]:
                raise ValueError("Invalid or cyclic historical parent chain")
            ancestors.add(parent)
            cursor = parent
        weights = []
        for opponent in members:
            rows = [r for r in self.state["training_results"] if r["candidate"] == candidate
                    and r["opponent"] == opponent and r["status"] == "natural_end"]
            score = sum({"win": 1, "draw": .5, "loss": 0}[r["outcome"]] for r in rows)
            inherited = [r for r in self.state["training_results"] if r["candidate"] in ancestors
                         and r["opponent"] == opponent and r["status"] == "natural_end"][-32:]
            inherited_score = sum({"win": 1, "draw": .5, "loss": 0}[r["outcome"]] for r in inherited)
            prior_weight = min(4, len(inherited))
            prior_rate = (inherited_score+1)/(len(inherited)+2)
            win = (score + 1 + prior_weight*prior_rate)/(len(rows)+2+prior_weight)
            weights.append((1-win)**2)
        total = sum(weights)
        return {member: uniform_mix/len(members) + (1-uniform_mix)*w/total
                for member, w in zip(members, weights)}

    def sample(self, candidate, rng):
        probs = self.probabilities(candidate)
        return rng.choices(list(probs), list(probs.values()), k=1)[0]

    def promote(self, candidate, candidate_directory, incumbent_directory, *, rules=None):
        """Recompute from hash-bound complete evidence; never accept a passed flag."""
        if candidate not in self.state["members"] or self.state["members"][candidate]["kind"] != "checkpoint":
            raise ValueError("Only an immutable trained checkpoint can be promoted")
        incumbent = self.state["champion"]
        evidence = []
        for directory, expected in ((candidate_directory, candidate), (incumbent_directory, incumbent)):
            directory = Path(directory)
            summary = json.loads((directory / "summary.json").read_text())
            plan = json.loads((directory / "plan.json").read_text())
            if plan["candidate"] != expected or plan["partition"] != "promotion_frozen_not_training":
                raise ValueError("Wrong candidate, incumbent or evidence partition")
            for name in ("plan", "episodes"):
                suffix = ".jsonl" if name == "episodes" else ".json"
                if digest(directory / (name + suffix)) != summary[name + "_sha256"]:
                    raise ValueError("Promotion evidence hash mismatch")
            for member, identity in plan["members"].items():
                if self.state["members"].get(member) != identity:
                    raise ValueError("Opponent identity changed since evaluation")
            rows = [json.loads(line) for line in (directory / "episodes.jsonl").read_text().splitlines()]
            cases = {c["case_id"]: c for c in plan["cases"]}
            if len(rows) != len(cases) or {r["case_id"] for r in rows} != cases.keys():
                raise ValueError("Promotion episodes omit or duplicate frozen cases")
            for row in rows:
                if any(canonical(row.get(k)) != canonical(v) for k, v in cases[row["case_id"]].items()):
                    raise ValueError("Episode configuration differs from frozen plan")
            evidence.append((plan, rows))
        for field in ("cases", "environment_identity", "deterministic", "max_decisions", "max_wall_seconds"):
            if canonical(evidence[0][0][field]) != canonical(evidence[1][0][field]):
                raise ValueError("Promotion plans differ: " + field)
        report = promotion_report(evidence[0][1], evidence[1][1], rules=rules or PromotionRules())
        report.update(candidate=candidate, incumbent=incumbent,
                      candidate_summary_sha256=digest(Path(candidate_directory)/"summary.json"),
                      incumbent_summary_sha256=digest(Path(incumbent_directory)/"summary.json"))
        self.state["promotions"].append(report)
        if report["promoted"]:
            self.state["champion"] = candidate
        self._save()
        return report

    def actor(self, identifier, *, environment, seed, deterministic=False, side=0):
        member = self.state["members"][identifier]
        if member["kind"] == "baseline":
            from .evaluation import baseline_action
            source = Path(__file__).with_name("baselines.py" if member["name"] == "scripted" else "evaluation.py")
            if digest(source) != member["source_sha256"]:
                raise ValueError("Historical baseline code drift")
            rng = random.Random(seed)
            return lambda obs: baseline_action(member["name"], obs, rng, environment=environment, player=side)
        from .model import PolicyAgent, load_policy
        from .evaluation import validate_checkpoint_environment
        model, metadata = load_policy(self.directory / member["path"], expected_sha256=member["sha256"])
        validate_checkpoint_environment(model, metadata, environment)
        agent = PolicyAgent(model, deterministic=deterministic, seed=seed)
        return agent.act


@dataclass(frozen=True)
class PromotionRules:
    minimum_games_per_opponent: int = 50
    minimum_clusters_per_opponent: int = 25
    minimum_score: float = .55
    minimum_mean_gain: float = .05
    familywise_alpha: float = .05
    maximum_refused_fraction: float = .01

    def __post_init__(self):
        if self.minimum_games_per_opponent < 2 or self.minimum_clusters_per_opponent < 2:
            raise ValueError("Promotion requires multiple paired games/clusters")
        if not 0 < self.familywise_alpha < 1 or not 0 <= self.minimum_mean_gain <= 1:
            raise ValueError("Invalid significance or margin")
        if not 0 <= self.minimum_score <= 1 or not 0 <= self.maximum_refused_fraction <= 1:
            raise ValueError("Invalid score/refusal threshold")


def promotion_report(candidate_rows, incumbent_rows, *, rules=PromotionRules()):
    """Pair identical cases; cluster both seats/decks sharing RNG before sign test.

    An exact one-sided sign test, Bonferroni across opponents, demands broad
    improvement. Failures retain their denominator and block promotion.
    """
    def index(rows):
        result = {}
        for row in rows:
            if row["case_id"] in result:
                raise ValueError("Duplicate promotion case")
            result[row["case_id"]] = row
        return result
    cand, base = index(candidate_rows), index(incumbent_rows)
    if not cand or cand.keys() != base.keys():
        raise ValueError("Candidate and incumbent must play the same full case matrix")
    for key in cand:
        for field in ("seed", "side", "opponent", "decks", "reset_options"):
            if canonical(cand[key].get(field)) != canonical(base[key].get(field)):
                raise ValueError("Mismatched paired case: " + field)
    opponents = sorted({r["opponent"] for r in cand.values()})
    reasons, groups = [], {}
    scores = {"win": 1., "draw": .5, "loss": 0.}
    if len(opponents) < 2:
        reasons.append("fewer_than_two_opponent_types")
    for opponent in opponents:
        keys = [k for k in cand if cand[k]["opponent"] == opponent]
        valid = [k for k in keys if cand[k]["status"] == base[k]["status"] == "natural_end"]
        clusters = {}
        for key in valid:
            delta = scores[cand[key]["outcome"]] - scores[base[key]["outcome"]]
            clusters.setdefault(cand[key]["seed"], []).append(delta)
        deltas = [sum(v)/len(v) for v in clusters.values()]
        positive, negative = sum(d > 0 for d in deltas), sum(d < 0 for d in deltas)
        discordant = positive + negative
        p = sum(math.comb(discordant, k) for k in range(positive, discordant+1))/2**discordant if discordant else 1.
        candidate_score = sum(scores[cand[k]["outcome"]] for k in valid)/len(valid) if valid else 0.
        gain = sum(deltas)/len(deltas) if deltas else 0.
        attempts = sum(cand[k].get("attempts", 0) for k in keys)
        refused = sum(cand[k].get("refused", 0) for k in keys)
        consumption_counts_valid = all(
            type(cand[k].get("attempts")) is int and type(cand[k].get("refused")) is int
            and type(cand[k].get("executed")) is int and cand[k]["attempts"] >= 0
            and 0 <= cand[k]["refused"] <= cand[k]["attempts"]
            and 0 <= cand[k]["executed"] == cand[k]["attempts"]-cand[k]["refused"]
            for k in keys)
        executed = sum(cand[k]["executed"] for k in keys) if consumption_counts_valid else None
        checks = {
            "complete": len(valid) == len(keys),
            "enough_games": len(keys) >= rules.minimum_games_per_opponent,
            "enough_seed_clusters": len(clusters) >= rules.minimum_clusters_per_opponent,
            "both_seats": {cand[k]["side"] for k in keys} == {0, 1},
            "paired_seats_per_seed": all({cand[k]["side"] for k in keys if cand[k]["seed"] == seed} == {0, 1}
                                         for seed in {cand[k]["seed"] for k in keys}),
            "score_floor": candidate_score >= rules.minimum_score,
            "gain_floor": gain >= rules.minimum_mean_gain,
            "significant_gain": p <= rules.familywise_alpha/max(1, len(opponents)),
            "consumption": consumption_counts_valid and attempts > 0 and executed > 0
                           and refused/max(1, attempts) <= rules.maximum_refused_fraction,
        }
        groups[opponent] = {"attempted_games": len(keys), "valid_pairs": len(valid),
            "seed_clusters": len(clusters), "score": candidate_score, "mean_cluster_gain": gain,
            "positive_clusters": positive, "negative_clusters": negative, "one_sided_sign_p": p,
            "checks": checks}
        reasons.extend(opponent + ":" + k for k, passed in checks.items() if not passed)
    return {"schema": "arena-promotion-v1", "rules": asdict(rules), "promoted": not reasons,
            "reasons": reasons, "by_opponent": groups,
            "scope": "Frozen simulator opponents only; no official client/ladder claim"}


def run_matches(pool, candidate, cases, environment, output, *, deterministic=True, max_decisions=1500, max_wall_seconds=120):
    """Evaluate immutable members, preserving every fault/truncation as a row."""
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    if not cases or len({r["case_id"] for r in cases}) != len(cases):
        raise ValueError("Nonempty unique match cases required")
    signature = set()
    for case in cases:
        sig = canonical({k: v for k, v in case.items() if k != "case_id"})
        if sig in signature or case["side"] not in (0, 1):
            raise ValueError("Repeated case configuration or invalid side")
        signature.add(sig)
    write_json(output / "plan.json", {"candidate": candidate, "members": pool.state["members"],
        "cases": cases, "environment_identity": environment.identity(), "deterministic": deterministic,
        "max_decisions": max_decisions, "max_wall_seconds": max_wall_seconds,
        "partition": "promotion_frozen_not_training"})
    rows = []
    delayed = bool(environment.identity()["effective_command_delay_ticks"])
    started = time.perf_counter()
    with (output / "episodes.jsonl").open("w") as stream:
        for case in cases:
            start = time.perf_counter()
            row = dict(case, status="fault", outcome=None, executed=0, refused=0, attempts=0, decisions=0)
            side = case["side"]
            try:
                obs, info = environment.reset(seed=case["seed"], decks=case["decks"], **case.get("reset_options", {}))
                actor = pool.actor(candidate, environment=environment, seed=case["seed"]*101+side,
                                   deterministic=deterministic, side=side)
                opponent = pool.actor(case["opponent"], environment=environment, seed=case["seed"]*101+1-side,
                                      deterministic=deterministic, side=1-side)
                for _ in range(max_decisions):
                    if time.perf_counter()-start >= max_wall_seconds:
                        row.update(status="truncated", reason="wall_budget")
                        break
                    action = actor(obs[side])
                    enemy_action = opponent(obs[1-side])
                    row["attempts"] += int(action["card"] != 0)
                    obs, _, ended, truncated, info = environment.step({side: action, 1-side: enemy_action})
                    own = [r for r in info["deploy_results"] if r["team"] == side]
                    row["refused"] += sum(r["status"] != 0 for r in own)
                    if delayed:
                        row["executed"] += sum(r[1] == side and r[-1] == 0 for r in info["commands_run"])
                        row["refused"] += sum(r[1] == side and r[-1] != 0 for r in info["commands_run"])
                    else:
                        row["executed"] += sum(r["status"] == 0 for r in own)
                    row["decisions"] += 1
                    if ended:
                        row.update(status="natural_end", outcome="draw" if info["winner"] == 2 else
                                   "win" if info["winner"] == side else "loss")
                        break
                    if truncated:
                        row["status"] = "truncated"
                        break
                else:
                    row["status"] = "truncated"
                row.update(final_tick=info["tick"], final_state_hash=info["state_hash"])
            except Exception as error:
                row.update(status="fault", outcome=None, error=type(error).__name__+": "+str(error))
            row["wall_seconds"] = time.perf_counter()-start
            rows.append(row)
            stream.write(canonical(row)+"\n")
            stream.flush()
    wall = time.perf_counter()-started
    summary = {"attempted": len(rows), "natural": sum(r["status"] == "natural_end" for r in rows),
               "wins": sum(r["outcome"] == "win" for r in rows), "wall_seconds": wall,
               "decisions_per_second": sum(r["decisions"] for r in rows)/max(wall, 1e-9),
               "plan_sha256": digest(output / "plan.json"), "episodes_sha256": digest(output / "episodes.jsonl")}
    write_json(output / "summary.json", summary)
    return rows, summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pool", required=True)
    parser.add_argument("--checkpoint")
    parser.add_argument("--foundation", action="store_true")
    parser.add_argument("--candidate", help="Immutable member ID to evaluate")
    parser.add_argument("--cases", help="Frozen JSON case list")
    parser.add_argument("--output")
    parser.add_argument("--sample", action="store_true")
    parser.add_argument("--candidate-results")
    parser.add_argument("--incumbent-results")
    args = parser.parse_args()
    pool = OpponentPool(args.pool)
    for baseline in ("random", "scripted"):
        pool.add_baseline(baseline)
    if args.checkpoint:
        print(pool.add_checkpoint(args.checkpoint, foundation=args.foundation))
    if args.cases:
        if not args.candidate or not args.output:
            parser.error("--cases requires --candidate and --output")
        from .environment import ArenaConfig, ArenaEnv
        from .model import load_policy
        import torch
        torch.set_num_threads(1)
        member = pool.state["members"][args.candidate]
        if member["kind"] != "checkpoint":
            raise ValueError("Evaluation environment must come from a trained candidate")
        _, metadata = load_policy(pool.directory/member["path"])
        config = ArenaConfig(**metadata["environment_identity"]["config"])
        if metadata["environment_identity"]["observation_version"] == "arena-masked-pixel-v3":
            from .pixel_policy import MaskedArenaEnv
            env = MaskedArenaEnv(config)
        else:
            env = ArenaEnv(config)
        try:
            _, report = run_matches(pool, args.candidate, json.loads(Path(args.cases).read_text()),
                                    env, args.output, deterministic=not args.sample)
            print(canonical(report))
        finally:
            env.close()
    if args.candidate_results:
        if not args.candidate or not args.incumbent_results:
            parser.error("Promotion requires candidate and both result directories")
        report = pool.promote(args.candidate, args.candidate_results, args.incumbent_results)
        if args.output:
            write_json(Path(args.output)/"promotion.json", report)
        print(canonical(report))
    print(canonical({"members": list(pool.state["members"]), "champion": pool.state["champion"]}))


if __name__ == "__main__":
    main()

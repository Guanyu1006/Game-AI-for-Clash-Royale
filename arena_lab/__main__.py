"""Repository-local commands for the independent arena research system."""
from __future__ import annotations

import argparse
import importlib
import json
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser(description="Royale Arena Lab：环境、数据、模仿训练与闭环评估")
    parser.add_argument("command", choices=("bootstrap", "benchmark", "train", "audit", "evaluate", "view"))
    args, rest = parser.parse_known_args()
    if args.command in {"bootstrap", "benchmark", "train", "view"}:
        modules = {"bootstrap": "bootstrap", "benchmark": "environment", "train": "training", "view": "replay_viewer"}
        sys.argv = [sys.argv[0], *(["benchmark"] if args.command == "benchmark" else []), *rest]
        importlib.import_module("arena_lab."+modules[args.command]).main()
        return
    if args.command == "audit":
        sub = argparse.ArgumentParser(description="验证整局划分、SHA256、观察和动作标签")
        sub.add_argument("--index", required=True)
        parsed = sub.parse_args(rest)
        from .training import BattleDataset
        dataset = BattleDataset(parsed.index)
        print(json.dumps({"index_sha256": dataset.index_sha256, "splits": dataset.audit}, ensure_ascii=False, indent=2))
        return
    sub = argparse.ArgumentParser(description="执行预先声明、冻结的独立对局套件")
    sub.add_argument("--checkpoint", required=True)
    sub.add_argument("--suite", required=True)
    sub.add_argument("--output", required=True)
    parsed = sub.parse_args(rest)
    from .evaluation import EvaluationCase, evaluate_policy
    from .environment import ArenaConfig, ArenaEnv
    suite = json.loads(Path(parsed.suite).read_text())
    if suite.get("schema_version") != 1:
        raise ValueError("Unsupported evaluation suite schema")
    config = ArenaConfig(**suite["environment_config"])
    result = evaluate_policy(parsed.checkpoint, parsed.output,
        cases=[EvaluationCase(**row) for row in suite["cases"]],
        env_factory=lambda: ArenaEnv(config), **suite.get("evaluation_options", {}))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

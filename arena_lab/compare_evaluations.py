"""Compare completed, input-matched evaluations without loading a model or engine.

Only pairs with two natural endings receive scores. Faults and truncations stay
in the planned-pair denominator and in the per-case output. SHA256 receipts bind
the exact files read; optional caller-supplied summary hashes anchor those receipts.
"""

from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
from typing import Any, Mapping, Sequence


_CASE_FIELDS = {"case_id", "seed", "decks", "candidate_side", "opponent", "reset_options"}
_STATUSES = {"natural_end", "truncated", "fault"}
_SCORES = {"win": 1.0, "draw": 0.5, "loss": 0.0}
_CHECKPOINT_FIELDS = {"checkpoint", "checkpoint_sha256", "checkpoint_metadata"}
_DECODER_METADATA_FIELDS = {
    "decoder", "decoding", "decoder_settings", "inference", "policy_decoder", "action_decoding",
}


def _canonical(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _digest(value: Any, label: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
        raise ValueError(f"{label} must be a lowercase SHA256 digest")
    return value


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def _parse(data: bytes, label: str) -> dict[str, Any]:
    def invalid_constant(value: str) -> None:
        raise ValueError(f"Non-finite JSON constant in {label}: {value}")

    try:
        value = json.loads(data, object_pairs_hook=_object, parse_constant=invalid_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"Invalid JSON in {label}: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{label} must contain a JSON object")
    # Also reject finite-looking literals that overflow the float parser (1e999).
    try:
        _canonical(value)
    except ValueError as exc:
        raise ValueError(f"Non-finite JSON number in {label}") from exc
    return value


def _counts(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    natural = [row for row in rows if row["status"] == "natural_end"]
    outcomes = Counter(row["outcome"] for row in natural)
    return {
        "attempted_games": len(rows), "natural_games": len(natural),
        "truncated_games": sum(row["status"] == "truncated" for row in rows),
        "faulted_games": sum(row["status"] == "fault" for row in rows),
        "wins": outcomes["win"], "losses": outcomes["loss"], "draws": outcomes["draw"],
        "win_rate_natural": outcomes["win"] / len(natural) if natural else None,
        "score_natural": (outcomes["win"] + outcomes["draw"] / 2) / len(natural) if natural else None,
    }


def _check_counts(recorded: Any, rows: Sequence[Mapping[str, Any]], label: str) -> None:
    if not isinstance(recorded, dict):
        raise ValueError(f"Missing summary group: {label}")
    for key, expected in _counts(rows).items():
        actual = recorded.get(key)
        if expected is None:
            matches = key in recorded and actual is None
        elif type(expected) is int:
            matches = type(actual) is int and actual == expected
        else:
            matches = type(actual) in {int, float} and math.isclose(actual, expected, rel_tol=1e-12, abs_tol=1e-12)
        if not matches:
            raise ValueError(f"Summary mismatch at {label}.{key}: {actual!r} != {expected!r}")


@dataclass(frozen=True)
class CompletedEvaluation:
    directory: Path
    plan: dict[str, Any]
    cases: dict[str, dict[str, Any]]
    episodes: dict[tuple[str, str], dict[str, Any]]
    receipts: dict[str, str]


def load_completed_evaluation(
    directory: str | Path, *, expected_summary_sha256: str | None = None,
) -> CompletedEvaluation:
    """Validate the complete variant × case matrix, not just the chosen variant.

    The existing evaluator binds plan and episodes from summary.json. The summary
    has no self-hash; its computed receipt is externally verifiable, and callers
    can require a previously recorded hash with expected_summary_sha256.
    """
    directory = Path(directory)
    blobs = {name: (directory / name).read_bytes() for name in ("plan.json", "episodes.jsonl", "summary.json")}
    receipts = {name: _sha(data) for name, data in blobs.items()}
    if expected_summary_sha256 is not None:
        expected = _digest(expected_summary_sha256, "expected summary hash")
        if receipts["summary.json"] != expected:
            raise ValueError("Summary SHA256 does not match the caller-supplied receipt")
    plan = _parse(blobs["plan.json"], "plan.json")
    summary = _parse(blobs["summary.json"], "summary.json")
    if plan.get("schema") != "arena-evaluation-plan-v1" or summary.get("schema") != "arena-evaluation-report-v1":
        raise ValueError("Unsupported evaluation artifact schema")
    for name, field in (("plan.json", "plan_sha256"), ("episodes.jsonl", "episodes_sha256")):
        if _digest(summary.get(field), field) != receipts[name]:
            raise ValueError(f"{name} SHA256 does not match summary.json")
    required = {"cases", "variants", "environment_identity", "deterministic", "max_decisions", "max_wall_seconds",
                "trace_every", "trace_max_bytes", "score_scope", *_CHECKPOINT_FIELDS}
    if required - plan.keys():
        raise ValueError(f"Incomplete plan: missing {sorted(required - plan.keys())}")
    _digest(plan["checkpoint_sha256"], "checkpoint_sha256")
    if not isinstance(plan["checkpoint"], str) or not plan["checkpoint"]:
        raise ValueError("Plan checkpoint must be nonempty")
    if type(plan["deterministic"]) is not bool:
        raise ValueError("Plan deterministic decoder setting must be boolean")
    for key in ("max_decisions", "trace_every", "trace_max_bytes"):
        if type(plan[key]) is not int or plan[key] <= 0:
            raise ValueError(f"Plan {key} must be a positive integer")
    if type(plan["max_wall_seconds"]) not in {int, float} or plan["max_wall_seconds"] <= 0:
        raise ValueError("Plan max_wall_seconds must be positive")
    metadata = plan["checkpoint_metadata"]
    identity = plan["environment_identity"]
    if not isinstance(identity, dict) or not identity or not isinstance(metadata, dict):
        raise ValueError("Plan needs explicit environment and checkpoint metadata")
    if _canonical(metadata.get("environment_identity")) != _canonical(identity):
        raise ValueError("Checkpoint and plan environment_identity differ")
    variants = plan["variants"]
    if not isinstance(variants, list) or not variants or any(not isinstance(v, str) or not v for v in variants):
        raise ValueError("Plan variants must be a nonempty string list")
    if len(set(variants)) != len(variants):
        raise ValueError("Duplicate planned variant")
    if not isinstance(plan["cases"], list) or not plan["cases"]:
        raise ValueError("Plan must have nonempty cases")
    cases = {}
    case_inputs = set()
    for case in plan["cases"]:
        if not isinstance(case, dict) or set(case) != _CASE_FIELDS:
            raise ValueError("Case must explicitly contain case_id, seed, decks, side, opponent and reset_options")
        key = case["case_id"]
        if not isinstance(key, str) or not key or key in cases:
            raise ValueError("Empty or duplicate case_id in plan")
        if type(case["seed"]) is not int or type(case["candidate_side"]) is not int or case["candidate_side"] not in {0, 1}:
            raise ValueError(f"Invalid seed or candidate_side for case {key}")
        decks = case["decks"]
        if not isinstance(decks, list) or len(decks) != 2 or any(not isinstance(deck, list) or len(deck) != 8 for deck in decks):
            raise ValueError(f"Case {key} needs two explicit eight-card decks")
        if not isinstance(case["opponent"], str) or case["opponent"] not in {"wait", "random", "scripted"}:
            raise ValueError(f"Invalid opponent for case {key}")
        if case["reset_options"] is not None and not isinstance(case["reset_options"], dict):
            raise ValueError(f"Invalid reset_options for case {key}")
        signature = _canonical({field: value for field, value in case.items() if field != "case_id"})
        if signature in case_inputs:
            raise ValueError("Duplicate case inputs under different case IDs")
        case_inputs.add(signature)
        cases[key] = case
    episodes = {}
    for index, line in enumerate(blobs["episodes.jsonl"].splitlines(), 1):
        if not line.strip():
            raise ValueError(f"Blank episode line at {index}")
        row = _parse(line, f"episodes.jsonl:{index}")
        variant, case_id = row.get("variant"), row.get("case_id")
        if not isinstance(variant, str) or not isinstance(case_id, str) or variant not in variants or case_id not in cases:
            raise ValueError(f"Unplanned episode at line {index}")
        pair = (variant, case_id)
        if pair in episodes:
            raise ValueError(f"Duplicate episode: {pair}")
        if not _CASE_FIELDS <= row.keys() or _canonical({key: row[key] for key in _CASE_FIELDS}) != _canonical(cases[case_id]):
            raise ValueError(f"Episode inputs differ from plan: {pair}")
        if not {"status", "outcome", "winner"} <= row.keys():
            raise ValueError(f"Episode is missing status, outcome or winner: {pair}")
        status = row.get("status")
        if not isinstance(status, str) or status not in _STATUSES:
            raise ValueError(f"Invalid episode status: {pair}")
        if status == "natural_end":
            winner = row.get("winner")
            if type(winner) is not int or winner not in {0, 1, 2}:
                raise ValueError(f"Invalid natural winner: {pair}")
            outcome = "draw" if winner == 2 else "win" if winner == row["candidate_side"] else "loss"
            if row.get("outcome") != outcome:
                raise ValueError(f"Winner/outcome mismatch: {pair}")
        elif row.get("outcome") is not None or row.get("winner") is not None:
            raise ValueError(f"Non-natural episode cannot have an outcome or winner: {pair}")
        episodes[pair] = row
    expected_pairs = {(variant, case_id) for variant in variants for case_id in cases}
    if set(episodes) != expected_pairs:
        raise ValueError(f"Incomplete evaluation: missing {len(expected_pairs - episodes.keys())} episodes")
    rows = list(episodes.values())
    _check_counts(summary.get("total"), rows, "total")
    if not isinstance(summary.get("by_variant"), dict) or set(summary["by_variant"]) != set(variants):
        raise ValueError("Summary variants do not match plan")
    if not isinstance(summary.get("by_opponent"), dict) or set(summary["by_opponent"]) != set(variants):
        raise ValueError("Summary opponent variants do not match plan")
    opponents = {case["opponent"] for case in cases.values()}
    for variant in variants:
        selected = [row for row in rows if row["variant"] == variant]
        _check_counts(summary["by_variant"][variant], selected, f"by_variant.{variant}")
        grouped = summary["by_opponent"][variant]
        if not isinstance(grouped, dict) or set(grouped) != opponents:
            raise ValueError(f"Summary opponent groups do not match plan: {variant}")
        for opponent in opponents:
            _check_counts(grouped[opponent], [row for row in selected if row["opponent"] == opponent],
                          f"by_opponent.{variant}.{opponent}")
    if summary.get("scope") != plan["score_scope"]:
        raise ValueError("Summary score scope differs from plan")
    return CompletedEvaluation(directory, plan, cases, episodes, receipts)


def compare_evaluations(
    old_directory: str | Path, new_directory: str | Path, *, variant: str = "model",
    old_summary_sha256: str | None = None, new_summary_sha256: str | None = None,
) -> dict[str, Any]:
    """Return new-minus-old paired scores after strict artifact/input checks."""
    old = load_completed_evaluation(old_directory, expected_summary_sha256=old_summary_sha256)
    new = load_completed_evaluation(new_directory, expected_summary_sha256=new_summary_sha256)
    for evaluation in (old, new):
        if variant not in evaluation.plan["variants"]:
            raise ValueError(f"Variant {variant!r} is not declared by {evaluation.directory}")
    # Compare every current or future top-level setting, including decoder knobs.
    # Only weights/training provenance and the set of additionally evaluated
    # variants may differ. Case ordering must also match the frozen run plan.
    excluded = _CHECKPOINT_FIELDS | {"variants"}
    comparable = lambda plan: {key: value for key, value in plan.items() if key not in excluded}
    if _canonical(comparable(old.plan)) != _canonical(comparable(new.plan)):
        raise ValueError("Evaluation plans differ in cases, environment, decoder settings or budgets")
    old_decoder = {key: old.plan["checkpoint_metadata"][key] for key in _DECODER_METADATA_FIELDS
                   if key in old.plan["checkpoint_metadata"]}
    new_decoder = {key: new.plan["checkpoint_metadata"][key] for key in _DECODER_METADATA_FIELDS
                   if key in new.plan["checkpoint_metadata"]}
    if _canonical(old_decoder) != _canonical(new_decoder):
        raise ValueError("Checkpoint decoder settings differ")
    pairs = []
    old_natural, new_natural = [], []
    differences = []
    status_pairs: Counter[str] = Counter()
    for case_id in old.cases:
        previous, current = old.episodes[(variant, case_id)], new.episodes[(variant, case_id)]
        valid = previous["status"] == current["status"] == "natural_end"
        difference = _SCORES[current["outcome"]] - _SCORES[previous["outcome"]] if valid else None
        if valid:
            differences.append(difference)
            old_natural.append(previous)
            new_natural.append(current)
        status_pairs[f'{previous["status"]}->{current["status"]}'] += 1
        pairs.append({
            **old.cases[case_id], "both_natural": valid,
            "old_status": previous["status"], "new_status": current["status"],
            "old_outcome": previous["outcome"], "new_outcome": current["outcome"],
            "old_score": _SCORES[previous["outcome"]] if valid else None,
            "new_score": _SCORES[current["outcome"]] if valid else None,
            "score_difference_new_minus_old": difference,
            "old_termination_reason": previous.get("termination_reason"),
            "new_termination_reason": current.get("termination_reason"),
        })
    def receipt(evaluation: CompletedEvaluation) -> dict[str, Any]:
        selected = [row for (name, _), row in evaluation.episodes.items() if name == variant]
        counts = _counts(selected)
        # Per-run outcomes cannot accidentally be presented as paired win rates.
        return {
            "directory": str(evaluation.directory), "files_sha256": evaluation.receipts,
            "checkpoint": evaluation.plan["checkpoint"], "checkpoint_sha256": evaluation.plan["checkpoint_sha256"],
            "declared_variants": evaluation.plan["variants"],
            "attempted_games": counts["attempted_games"], "natural_games": counts["natural_games"],
            "truncated_games": counts["truncated_games"], "faulted_games": counts["faulted_games"],
        }
    return {
        "schema": "arena-evaluation-comparison-v1", "variant": variant,
        "direction": "new_minus_old", "old": receipt(old), "new": receipt(new),
        "planned_pairs": len(pairs), "both_natural_pairs": len(differences),
        "unscored_pairs": len(pairs) - len(differences), "status_pairs": dict(sorted(status_pairs.items())),
        "positive_pairs": sum(value > 0 for value in differences),
        "negative_pairs": sum(value < 0 for value in differences),
        "tied_pairs": sum(value == 0 for value in differences),
        "score_difference_sum": sum(differences) if differences else None,
        "mean_score_difference": sum(differences) / len(differences) if differences else None,
        "old_on_both_natural_pairs": _counts(old_natural), "new_on_both_natural_pairs": _counts(new_natural),
        "comparable_plan_sha256": _sha(_canonical(comparable(old.plan)).encode("utf-8")),
        "decoder": {"deterministic": old.plan["deterministic"], "checkpoint_settings": old_decoder},
        "cases": pairs, "scope": old.plan["score_scope"],
        "limitations": [
            "Only both-natural pairs enter scores or win rates; every planned case remains listed.",
            "Receipts verify recorded artifacts, not unrecorded evaluator code or external game equivalence.",
            "Summary SHA256 is computed here; external authenticity requires a caller-supplied trusted receipt.",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--reference", "--old", dest="old", required=True, help="Completed reference evaluation directory")
    parser.add_argument("--candidate", "--new", dest="new", required=True, help="Completed candidate evaluation directory")
    parser.add_argument("--variant", default="model")
    parser.add_argument("--reference-summary-sha256", "--old-summary-sha256", dest="old_summary_sha256")
    parser.add_argument("--candidate-summary-sha256", "--new-summary-sha256", dest="new_summary_sha256")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    report = compare_evaluations(args.old, args.new, variant=args.variant,
                                 old_summary_sha256=args.old_summary_sha256,
                                 new_summary_sha256=args.new_summary_sha256)
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    # Preserve prior comparisons and input evidence; do not overwrite a path.
    with output.open("x", encoding="utf-8") as stream:
        stream.write(json.dumps(report, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n")
    print(_canonical({key: report[key] for key in ("planned_pairs", "both_natural_pairs", "unscored_pairs",
                                                  "mean_score_difference", "positive_pairs", "negative_pairs", "tied_pairs")}))


if __name__ == "__main__":
    main()

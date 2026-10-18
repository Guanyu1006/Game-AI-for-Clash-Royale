"""Audit pinned source data; freeze formal/dev sets without engine-result filtering."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.dataset import (OUT, audit_rows, compare_action_table, export_frozen,
                                   load_source_shard, select_frozen, write_json)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-only", action="store_true")
    args = parser.parse_args()
    rows, source, schema = load_source_shard()
    episodes, groups, report = audit_rows(rows, source, schema)
    write_json(OUT / "source_schema_audit.json", report)
    comparison = compare_action_table(episodes)
    write_json(OUT / "source_action_join.json", comparison)
    summary = {"raw_rows": len(rows), "parsed_episodes": len(episodes), "parse_quality": report["counts"]["parse_quality"],
               "event_kinds": report["counts"]["event_kind"], "levels": report["counts"]["deck_level"],
               "hero_keys": report["observed_hero_keys"], "evolution_keys": report["observed_evolution_keys"],
               "duplicate_groups": len(report["duplicate_or_near_duplicate_groups"]), "action_table_join": comparison["status"]}
    if not args.audit_only:
        if report["parse_exceptions"] or report["duplicate_source_ids"] or comparison["status"] != "pass":
            raise ValueError("Resolve source audit failures before freezing; all denominator rows are in audit")
        result = export_frozen(select_frozen(episodes, groups), rows, source, report)
        summary["conversion"] = result
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

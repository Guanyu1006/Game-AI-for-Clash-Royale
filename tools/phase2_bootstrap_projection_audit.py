"""Read-only source evidence for a conservative actor projection; no actor code."""
from __future__ import annotations
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "outputs/phase2/assets"
JASON = ROOT / "third_party/phase2/jason"
FIRSTLIGHT = ROOT / "third_party/phase2/firstlight"


def evidence(path, fragments):
    lines = path.read_text(encoding="utf-8").splitlines()
    matches = []
    for fragment in fragments:
        found = [{"line": index + 1, "text": line.strip()} for index, line in enumerate(lines) if fragment in line]
        if not found:
            raise ValueError(f"Evidence missing: {path}: {fragment}")
        matches.append({"fragment": fragment, "matches": found})
    return {"path": path.relative_to(ROOT).as_posix(), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "matches": matches}


def main():
    minimal = JASON / "vendor/firstlight/probe/minimal_capture.inc"
    probe = JASON / "vendor/firstlight/probe/cr_replay_probe.cpp"
    records = [evidence(minimal, [
        'json.field("schema", "cr-minimal.v4")',
        'json.field("tick", tick)',
        'json.nullable("result", result.world_result_raw, result.finalized)',
        'json.field("elixir", player.elixir_raw)',
        'card.card_parameter, card.command_card_id',
        'json.begin_array("cycle")',
        'json.begin_array("abilities")',
        'json.begin_array("evolution")',
        'const char *entity_type = is_projectile ? "projectile" : card_id == -1 ? "building" : "troop"',
        'if (has_hp) json.append("%d,%d,", hp, max_hp)',
        'if (has_attack && target.has_target)',
    ]), evidence(probe, [
        'constexpr std::int32_t kKingX = 9000',
        'constexpr std::int32_t kLeftPrincessX = 3500',
        'constexpr std::int32_t kRightPrincessX = 14500',
        'constexpr std::int32_t kKingY[kPlayerCount] = {3000, 29000}',
        'constexpr std::int32_t kPrincessY[kPlayerCount] = {6500, 25500}',
        'if (object == nullptr || read_object_field<std::int32_t>(object, 0xac) != -1)',
        'if (recognized_towers == 0)',
        'crowns_raw[owner] = !king_alive[opponent] ? 3',
        'value.finalized = read_object_field<std::uint8_t>(world, 0x1e0) != 0',
        'value.world_result_raw = read_object_field<std::int32_t>(world, 0x1b8)',
        'if (battle_result.world_result_raw == 0 || battle_result.world_result_raw == 1)',
        'std::snprintf(winner_json, sizeof(winner_json), "%d", battle_result.world_result_raw)',
        'result->elixir_raw = read_object_field<std::int32_t>(result->player, 0x2f8)',
        'result->cost = static_cast<std::int32_t>(selection.packed >> 28)',
    ]), evidence(JASON / "engine.py", [
        'card: int  # -1 for towers.',
        'elixir: int  # Fixed-point native units: 10,000 = one elixir.',
        'parameter: int  # Native command encoding; never a policy feature.',
    ]), evidence(FIRSTLIGHT / "native_runner/training_capture.py", [
        'winner = world_result_raw if finalized and world_result_raw in (0, 1) else None',
    ])]
    report = {
        "status": "source_evidence_audit_complete_runtime_and_render_attestation_pending",
        "scope": "read-only source audit; no actor implementation; no engine or remote script executed",
        "source_commits": {"Jason": "70a08af6807fa4d59a11c7d52bf6272a96afe30c", "FirstLight": "71944e3993b594b3cc89b38768b5e817dfbdbe0e"},
        "minimal_wire_schema": "cr-minimal.v4",
        "entity_tuple": ["native_id", "native_owner", "card_id", "native_x", "native_y", "hp_or_null", "max_hp_or_null", "target_native_id_or_null", "attack_cooldown_ms_or_null", "entity_type", "deployment_remaining_ms_or_null"],
        "hand_tuple": ["hand_slot", "root_deck_card_id", "current_dynamic_cost", "packed_native_parameter", "effective_command_card_id"],
        "strict_actor_allowlist": {
            "native_tick": "current captured tick, finite integer >=0; fix time origin/version, no future event schedule",
            "own_current_hand": "exactly slots 0..3, copy only slot/card_id/current_cost from the requested native owner; reject duplicate/missing slots rather than fabricate",
            "crowns_self_other": "reorder current crowns[actor_owner],crowns[1-actor_owner], each integer0..3; note source derives them from fixed 1v1 tower graph",
            "public_tower_hp": "optional only for validated canonical towers; copy hp (and max_hp only if required/attested), label self/other+slot; no native IDs or arbitrary entity arrays",
        },
        "own_elixir_optional_not_in_requested_minimum": {
            "source": "players[actor_owner].elixir, one elixir=10000 native units", "public_status": "own resource is generally observable but exact fixed-point UI precision not independently checked here",
            "recommendation": "keep outside the minimum actor projection unless root explicitly enables own-resource field; self affordability mask may use only this player's validated resource, never enemy resource",
        },
        "always_exclude_from_actor": ["opponent hand", "opponent exact elixir", "full cycle for either owner", "seed/RNG/deal hypothesis", "future commands/events", "raw episode config/source final result", "all non-tower entities including invisible/hidden ones", "total entity counts", "target ids", "attack cooldowns", "deployment remaining", "native entity ids", "generation/state_epoch/transport cursor", "packed native card parameter", "effective command/form card id", "abilities/evolution runtime unless separately approved", "raw payload passthrough"],
        "tower_marker_limit": "minimal_capture has no isTower attestation. building is inferred from nonprojectile+HP+card_id==-1; engine.py's -1-for-towers comment is not independent runtime proof",
        "canonical_tower_cells": {
            "native_owner_0": {"king": [9000, 3000], "left_princess": [3500, 6500], "right_princess": [14500, 6500]},
            "native_owner_1": {"king": [9000, 29000], "left_princess": [3500, 25500], "right_princess": [14500, 25500]},
        },
        "tower_hp_acceptance_recommendation": [
            "require fixed supported 1v1 version/arena profile; do not reuse six-cell assumptions for unknown modes",
            "require card_id==-1, entity_type=='building', owner0/1, exact canonical owner/cell, finite 0<=hp<=max_hp and max_hp>0",
            "at verified reset require six unique owner/cell identities and record private native tower IDs internally; subsequent hp rows must match that roster",
            "do not expose roster native IDs to actor; expose only semantic own/opponent tower slots",
            "if tower identity/roster/source visibility is unverified, omit tower hp and record capability reason outside actor features",
            "a missing row is not automatically hp=0; mark unknown unless actual destruction/full-capture evidence establishes absence",
            "do not include raw targets/cooldowns as a side effect of taking a public tower row",
        ],
        "crowns_caveat": "read_native_crowns accepts recognized_towers>0, not an intact6-tower initialization attestation; an unsupported/incomplete tower graph could produce misleading crowns. Validate reset/arena and fail closed outside supported profile",
        "result_mapping": {
            "source_fields": "world+0x1e0 finalized; world+0x1b8 raw result", "minimal_preterminal": "result=null",
            "source_supported_mapping": "only finalized and raw result exactly0/1 => winner native owner0/1; reward own+1 if equal else-1",
            "other_raw_values": "unknown or draw not established; do not auto-label all non0/1 as draw or crash as loss",
            "actor_usage": "terminal result is evaluation/reward metadata after transition, never pre-action actor input",
            "native_to_source_owner": "use each episode's explicit data_i/FirstLight source_team_owner mapping, never assume team==native0",
            "evidence_grade": "consistent code paths in pinned source; local native winning0/winning1/draw runtime attestation not performed by this audit",
        },
        "coordinate_caveat": "Canonical cells prove native owner/cell associations only. Do not derive physical rendered top/bottom from y values without matching renderer anchors. Jason rotate() and textual bottom-side comments alone are insufficient visual attestation",
        "privacy_test_recommendations": [
            "two raw inputs differing only enemy hand/exact resource/cycles/seed/future events yield byte-identical actor projection",
            "poison ignored non-tower/hidden entity payloads and nested unknown keys; projection must not copy them or derive counts",
            "permutation of players array must not change actor owner selection; select by explicit owner field",
            "raw pre-action observation and post-action outcome stay in distinct records; no future result/mask injection",
            "wrong/duplicate tower marker or invalid HP cannot be silently reclassified into a public tower",
        ],
        "evidence": records,
    }
    output = OUT / "dex_actor_projection_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "sources": len(records), "report": output.relative_to(ROOT).as_posix(),
                      "result_mapping": report["result_mapping"]["source_supported_mapping"], "tower_marker_limit": report["tower_marker_limit"]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

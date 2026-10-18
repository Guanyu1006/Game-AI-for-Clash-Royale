"""Derive the byte-exact upstream fingerprint representation; preserve CDN raw.

Only a separate derived file is written. No runtime-update/full-update paths or
original assets are edited, and no remote endpoint is contacted.
"""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "third_party/phase2/runtime-assets/candidates/patch-cr.json"
DESTINATION = ROOT / "third_party/phase2/runtime-assets/derived/fingerprint.json"
EXPECTED_RAW = "3be42f6304f175eab5034f75047ff195e31827c32de576313cdecad0f41d46c0"
EXPECTED_DERIVED = "e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339"
CONTENT = "41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82"


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def reject_duplicate_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key: " + key)
        result[key] = value
    return result


def main():
    raw = SOURCE.read_bytes()
    if sha256(raw) != EXPECTED_RAW:
        raise ValueError("Original public CDN fingerprint differs from audited raw bytes")
    original = json.loads(raw, object_pairs_hook=reject_duplicate_keys)
    if original.get("version") != "15.535.86" or original.get("sha") != CONTENT:
        raise ValueError("Source content identity mismatch")
    if list(original) != ["files", "version", "sha"] or len(original["files"]) != 15456:
        raise ValueError("Source fingerprint structure changed")
    files = {record["file"]: record["sha"] for record in original["files"]}
    if len(files) != len(original["files"]):
        raise ValueError("Duplicate file path in source manifest")
    derived = json.dumps(original, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    reparsed = json.loads(derived, object_pairs_hook=reject_duplicate_keys)
    checks = {
        "json_structure_exact_equal": reparsed == original,
        "root_key_order_preserved": list(reparsed) == list(original),
        "file_array_and_entry_values_exact_equal": reparsed["files"] == original["files"],
        "all_file_path_sha1_pairs_exact_equal": {record["file"]: record["sha"] for record in reparsed["files"]} == files,
        "declared_version_and_content_sha1_unchanged": (reparsed["version"], reparsed["sha"]) == (original["version"], original["sha"]),
        "only_escaped_slash_bytes_removed": derived == raw.replace(b"\\/", b"/"),
        "exact_derived_sha256_matches_upstream": sha256(derived) == EXPECTED_DERIVED,
    }
    if not all(checks.values()):
        raise ValueError("Fingerprint representation derivation failed exact equivalence/hash checks")
    DESTINATION.resolve().relative_to(ROOT)
    DESTINATION.parent.mkdir(parents=True, exist_ok=True)
    if DESTINATION.exists() and DESTINATION.read_bytes() != derived:
        raise ValueError("Existing derived fingerprint has different bytes; refuse overwrite")
    temporary = DESTINATION.with_suffix(".json.partial")
    temporary.write_bytes(derived)
    temporary.replace(DESTINATION)
    checks["derived_file_reopened_exact_equal"] = DESTINATION.read_bytes() == derived
    checks["original_source_bytes_unchanged"] = SOURCE.read_bytes() == raw
    report = {
        "status": "passed_derived_representation_exact_hash_and_semantic_equivalence",
        "source": {"path": SOURCE.relative_to(ROOT).as_posix(), "bytes": len(raw), "sha256": sha256(raw),
                   "upstream_expected_sha256_matches": False, "escaped_slash_sequences": raw.count(b"\\/")},
        "derived": {"path": DESTINATION.relative_to(ROOT).as_posix(), "bytes": len(derived), "sha256": sha256(derived),
                    "upstream_expected_sha256_matches": True},
        "derivation": "parse JSON preserving object insertion order; compact UTF-8 JSON without forward-slash escaping or trailing newline",
        "equivalent_direct_byte_rule": "replace every JSON escaped slash \\/ with /; verified equal to full reserialization for this exact input",
        "byte_reduction": len(raw) - len(derived), "file_count": len(files), "content_version": original["version"],
        "declared_content_sha1": original["sha"], "content_sha1_computation": "declared identity retained, not recomputed or used to pretend original SHA256 matched",
        "checks": checks,
        "provenance_limit": "Exact target bytes and parsed content agree; this does not prove which historical client/writer produced the upstream local file",
        "upstream_wrapper_source": "offline_build.validate_update checks exact runtime_fingerprint_sha256; prepare copies update unchanged, it does not generate fingerprint JSON",
        "original_resources_modified": False, "runtime_update_or_full_update_written": False,
        "remote_requests": 0, "scripts_executed": False,
    }
    output = ROOT / "outputs/phase2/assets/dex_fingerprint_representation_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"status": report["status"], "source": report["source"], "derived": report["derived"], "checks": checks}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

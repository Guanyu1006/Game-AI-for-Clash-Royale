"""Pinned, bounded public-data acquisition and inert RoyaleAPI HTML parsing.

Raw receipts describe source facts, not reconstructed simulator observations.  In
particular, collection from a leaderboard-seeded crawler does not prove that every
encountered opponent is an expert.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal
import hashlib
from html.parser import HTMLParser
import json
from pathlib import Path, PurePosixPath
import re
import tarfile
from typing import Any, Iterator, Mapping
from urllib.parse import parse_qs, urlparse
from urllib.request import Request, urlopen

COCHON_REPOSITORY = "Cochon123/clash-royale-replays"
COCHON_REVISION = "10cca44e9d863d06cf078f142d3e744f78698b0a"
COCHON_LICENSE = "mit"
SOURCE_HZ = 20
MANIFEST_DIRECTORY = Path(__file__).with_name("manifests")


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def canonical_hash(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                     ensure_ascii=False).encode()).hexdigest()


def safe_source_id(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Unsafe or empty source replay identifier")
    return value


def fetch_immutable(url: str, target: str | Path, *, maximum_bytes: int,
                    expected_sha256: str | None = None) -> dict:
    """Fetch one HTTPS public object; never replace a different existing file.

    A caller must pin revisions in URLs. No credentials or environment tokens are
    read. The temporary file remains beside the destination after a failed fetch.
    """
    if urlparse(url).scheme != "https" or maximum_bytes < 1:
        raise ValueError("An HTTPS URL and positive byte budget are required")
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        digest = sha256_file(target)
        if expected_sha256 and digest != expected_sha256:
            raise ValueError(f"Existing raw asset hash mismatch: {target}")
        return {"url": url, "path": str(target), "bytes": target.stat().st_size,
                "sha256": digest, "reused": True}
    partial = target.with_name(target.name + ".download")
    if partial.exists():
        raise FileExistsError(f"Previous incomplete fetch retained: {partial}")
    size = 0
    with urlopen(Request(url, headers={"User-Agent": "RoyaleLab-public-data-audit/1"}), timeout=60) as response:
        length = response.headers.get("Content-Length")
        if length and int(length) > maximum_bytes:
            raise ValueError("Source exceeds requested download budget")
        with partial.open("xb") as handle:
            while block := response.read(min(1024 * 1024, maximum_bytes - size + 1)):
                size += len(block)
                if size > maximum_bytes:
                    raise ValueError("Source exceeded requested download budget")
                handle.write(block)
    digest = sha256_file(partial)
    if expected_sha256 and digest != expected_sha256:
        raise ValueError("Downloaded asset hash mismatch; partial file retained")
    partial.rename(target)
    return {"url": url, "path": str(target), "bytes": size, "sha256": digest,
            "reused": False}


def fetch_prefix(url: str, target: str | Path, *, prefix_bytes: int,
                 expected_sha256: str) -> dict:
    """Fetch exactly a locked compressed prefix and close the larger HTTP object."""
    if urlparse(url).scheme != "https" or prefix_bytes <= 0:
        raise ValueError("Pinned HTTPS source and positive prefix length required")
    target = Path(target)
    if target.exists():
        if target.stat().st_size != prefix_bytes or sha256_file(target) != expected_sha256:
            raise ValueError("Existing compressed prefix differs from acquisition lock")
        return {"path": str(target), "bytes": prefix_bytes, "sha256": expected_sha256, "reused": True}
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".download")
    if partial.exists():
        raise FileExistsError("Previous incomplete download retained: " + str(partial))
    headers = {"User-Agent": "RoyaleLab-public-data-audit/1", "Range": f"bytes=0-{prefix_bytes-1}"}
    received = 0
    with urlopen(Request(url, headers=headers), timeout=60) as response, partial.open("xb") as output:
        while received < prefix_bytes:
            block = response.read(min(1024 * 1024, prefix_bytes - received))
            if not block:
                raise ValueError("Source ended before locked prefix length")
            output.write(block)
            received += len(block)
    if sha256_file(partial) != expected_sha256:
        raise ValueError("Compressed prefix hash mismatch; downloaded prefix retained")
    partial.rename(target)
    return {"url": url, "path": str(target), "bytes": received, "sha256": expected_sha256,
            "is_complete_archive": False, "reused": False}


def prepare(output_dir: str | Path, *, lock_path: str | Path | None = None,
            full: bool = False) -> dict:
    """One reproducible fetch -> hash verification -> expert-tag selection entry."""
    default_lock = "cochon-historical-top100-full-v2.json" if full else "cochon-historical-top100-v1.json"
    lock_path = Path(lock_path) if lock_path else MANIFEST_DIRECTORY / default_lock
    lock = json.loads(lock_path.read_text())
    output_dir = Path(output_dir)
    raw = output_dir / "raw" / ("cochon-" + lock["revision"]) / "snapshots"
    archive = raw / ("data-2026-11-01.tar.gz" if full else "discovery-prefix.tar.gz.partial")
    manifest = raw / "data-2026-11-01.manifest.json"
    evidence = lock_path.parent / lock["expert_fact_table"]["path"]
    if sha256_file(evidence) != lock["expert_fact_table"]["sha256"]:
        raise ValueError("Historical expert fact table differs from committed lock")
    receipts = [fetch_immutable(lock["snapshot_manifest"]["url"], manifest,
                                maximum_bytes=lock["snapshot_manifest"]["bytes"],
                                expected_sha256=lock["snapshot_manifest"]["sha256"])]
    if full:
        receipts.append(fetch_immutable(lock["archive"]["url"], archive,
                                        maximum_bytes=lock["archive"]["full_bytes"],
                                        expected_sha256=lock["archive"]["full_sha256"]))
    else:
        receipts.append(fetch_prefix(lock["archive"]["url"], archive, prefix_bytes=lock["prefix"]["bytes"],
                                     expected_sha256=lock["prefix"]["sha256"]))
    destination = output_dir / ("cochon-historical-top100-full-v2" if full else "cochon-historical-top100-v1")
    result = select_snapshot(archive, manifest, evidence, destination, complete_archive=full)
    result["acquisition_receipts"] = receipts
    (output_dir / "acquisition-receipt.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def iter_raw_tar(path: str | Path, *, allow_partial: bool = False) -> Iterator[tuple[str, bytes]]:
    """Read raw JSON members without extracting paths or executing objects.

    An intentionally bounded gzip prefix is supported only when explicitly asked;
    yielded complete members are individual raw objects, never a verified archive.
    """
    try:
        with tarfile.open(path, "r|gz") as archive:
            for member in archive:
                pure = PurePosixPath(member.name)
                if (not member.isfile() or not member.name.startswith("data/raw/")
                        or pure.suffix != ".json" or ".." in pure.parts):
                    continue
                if member.size > 2 * 1024 * 1024:
                    raise ValueError("Unexpectedly large raw replay member")
                handle = archive.extractfile(member)
                if handle:
                    payload = handle.read()
                    if len(payload) == member.size:
                        yield member.name, payload
    except (EOFError, tarfile.ReadError):
        if not allow_partial:
            raise


def base_card(slug: str) -> tuple[str, int]:
    """Preserve explicit evolution/hero forms rather than conflating data-i with form."""
    if slug.endswith("-ev1"):
        return slug[:-4], 1
    if slug.endswith("-hero"):
        return slug[:-5], 2
    return slug, 0


class _ReplayHTML(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.timeline: list[dict] = []
        self.markers: list[dict] = []
        self.decks: dict[int, list[str]] = {0: [], 1: []}
        self.end_ticks: list[int] = []
        self.names: dict[int, str] = {}

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = dict(attrs)
        classes = (a.get("class") or "").split()
        if tag == "img" and "data-card" in a:
            side = {"blue": 0, "red": 1}.get(a.get("data-s"))
            if side is not None:
                if "data-t" in a:
                    self.timeline.append({"team": side, "tick": int(a["data-t"]),
                                          "card": a["data-card"],
                                          "ability": a.get("data-ability") == "1",
                                          "source_index": len(self.timeline)})
                else:
                    self.decks[side].append(a["data-card"])
        if tag == "div" and "marker" in classes and "data-t" in a and "data-c" in a:
            side = {"t": 0, "o": 1}.get(a.get("data-s"))
            if side is None:
                side = 0 if "blue" in classes else 1 if "red" in classes else None
            if side is not None:
                self.markers.append({"team": side, "tick": int(a["data-t"]),
                                     "card": a["data-c"],
                                     "x": int(a["data-x"]) if a.get("data-x") not in (None, "None") else None,
                                     "y": int(a["data-y"]) if a.get("data-y") not in (None, "None") else None,
                                     "data_i": a.get("data-i"),
                                     "source_index": len(self.markers)})
        if "end_time" in classes:
            match = re.search(r"left:\s*([0-9.]+)px", a.get("style") or "")
            if match:
                # HTML timeline uses ten pixels per second; 20 source ticks/sec.
                self.end_ticks.append(int(Decimal(match.group(1)) * 2))


def parse_cochon(raw: Mapping[str, Any], *, source_path: str = "",
                 source_sha256: str | None = None) -> dict:
    """Normalize a complete public command timeline without inventing hidden fields."""
    if not raw.get("payload", {}).get("success"):
        raise ValueError("Source replay request did not succeed")
    parser = _ReplayHTML()
    parser.feed(raw["payload"]["html"])
    query = parse_qs(urlparse(raw["request_url"]).query)
    tags = [query.get(key, []) for key in ("team_tags", "opponent_tags")]
    if any(len(row) != 1 for row in tags):
        raise ValueError("Only identifiable two-player 1v1 replays are supported")
    if any(len(parser.decks[seat]) != 8 or len(set(parser.decks[seat])) != 8 for seat in (0, 1)):
        raise ValueError("Source lacks two complete unique eight-card decks")
    if not parser.end_ticks or len(set(parser.end_ticks)) != 1:
        raise ValueError("Source has no unambiguous terminal timeline boundary")
    end_tick = parser.end_ticks[0]
    buckets: dict[tuple, list[dict]] = defaultdict(list)
    for marker in parser.markers:
        buckets[(marker["team"], marker["tick"], base_card(marker["card"])[0])].append(marker)
    events, unmatched, ambiguous = [], [], []
    for event in parser.timeline:
        key = (event["team"], event["tick"], base_card(event["card"])[0])
        candidates = buckets.get(key, [])
        if len(candidates) == 1:
            marker = candidates.pop()
            events.append({**event, "x": marker["x"], "y": marker["y"],
                           "data_i": marker["data_i"], "marker_index": marker["source_index"],
                           "form": base_card(event["card"])[1] if event["card"].endswith(("-ev1", "-hero")) else None})
        elif event["ability"]:
            # Some HTML versions omit the ability card identity/marker. Retain it.
            events.append({**event, "x": None, "y": None, "data_i": None,
                           "marker_index": None,
                           "form": base_card(event["card"])[1] if event["card"].endswith(("-ev1", "-hero")) else None})
        else:
            (ambiguous if len(candidates) > 1 else unmatched).append(event)
    if unmatched or ambiguous or any(buckets.values()):
        raise ValueError(f"Timeline/map mismatch: missing={len(unmatched)}, ambiguous={len(ambiguous)}, "
                         f"unused={sum(map(len, buckets.values()))}")
    if not events or any(e["tick"] < 0 or e["tick"] > end_tick for e in events):
        raise ValueError("Empty or out-of-terminal source timeline")
    events.sort(key=lambda e: (e["tick"], e["marker_index"] if e["marker_index"] is not None else 10**9,
                               e["source_index"]))
    crowns = [int(query[key][0]) for key in ("team_crowns", "opponent_crowns")]
    if any(c < 0 or c > 3 for c in crowns):
        raise ValueError("Invalid terminal crowns")
    game_tag = safe_source_id(query.get("tag", [""])[0])
    player_tags = [t[0].lstrip("#") for t in tags]
    # Viewer orientation, side order and coordinates must not change the split.
    # This intentionally over-groups identical command traces rather than leaking
    # a replay shown from the opposite seat into a second data partition.
    identity = {"players": sorted((player_tags[s], sorted(parser.decks[s]), crowns[s]) for s in (0, 1)),
                "end_tick": end_tick,
                "events": sorted((e["tick"], player_tags[e["team"]], e["card"], e["ability"])
                                 for e in events)}
    return {"schema_version": 1, "source": COCHON_REPOSITORY,
            "source_revision": COCHON_REVISION, "license": COCHON_LICENSE,
            "source_path": source_path, "source_sha256": source_sha256,
            "source_id": game_tag, "source_game_id": game_tag, "game_id": canonical_hash(identity),
            "split_group_semantics": "undirected player/deck/crowns and tick/player/card/ability trace; excludes viewing coordinates",
            "captured_at": raw.get("captured_at"), "request_url": raw["request_url"],
            "players": [{"tag": tags[s][0].lstrip("#"), "crowns": crowns[s],
                         "deck": parser.decks[s]} for s in (0, 1)],
            "events": events, "end_tick": end_tick, "source_hz": SOURCE_HZ,
            "winner": 0 if crowns[0] > crowns[1] else 1 if crowns[1] > crowns[0] else None,
            "expert_evidence": [],
            "source_quality": {"timeline_complete": True, "dual_decks": True,
                               "terminal_crowns": True, "exact_game_build": None,
                               "card_levels": None, "initial_hand": None, "rng_seed": None,
                               "unsuffixed_card_form": "unknown, not verified base",
                               "time_semantics": "RoyaleAPI HTML ticks; execution/input boundary unverified",
                               "coordinates": "RoyaleAPI millitiles; data-i retained as orientation metadata",
                               "state_truth": "commands only; no recorded battlefield entity states"}}


def whole_game_split(game_id: str, *, seed: str = "arena-lab-v1") -> str:
    bucket = int(hashlib.sha256((seed + ":" + game_id).encode()).hexdigest()[:8], 16) % 100
    return "train" if bucket < 80 else "validation" if bucket < 90 else "test"


def summarize_sources(games: list[dict], counts: Mapping[str, int]) -> dict:
    return {"schema_version": 1, "source_revision": COCHON_REVISION,
            "counts": dict(counts), "unique_games": len(games),
            "splits": dict(Counter(whole_game_split(g["game_id"]) for g in games)),
            "expert_games": sum(bool(g.get("expert_evidence")) for g in games),
            "evidence_tiers": dict(Counter(e["tier"] for g in games for e in g.get("expert_evidence", []))),
            "unknowns": ["exact client build", "original initial hand and RNG", "card/tower levels",
                         "official battlefield states", "input versus execution timestamp semantics"]}


def select_snapshot(prefix: str | Path, manifest_path: str | Path, evidence_path: str | Path,
                    destination: str | Path, *, complete_archive: bool = False) -> dict:
    """Retain every matching expert-account game in a fixed archive or prefix.

    The sampling frame is the complete archive when explicitly declared, otherwise
    the complete members of a saved compressed prefix. Neither is a random sample
    or every version of the live HF repository.
    """
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    raw_dir = destination / "raw-selected"
    normalized_dir = destination / "normalized"
    raw_dir.mkdir(exist_ok=True)
    normalized_dir.mkdir(exist_ok=True)
    evidence = json.loads(Path(evidence_path).read_text())
    by_tag = {e["tag"]: e for e in evidence if e.get("tag")}
    manifest = json.loads(Path(manifest_path).read_text())
    hashes = {f["path"]: f["sha256"] for f in manifest["files"]}
    counts: Counter = Counter()
    failures, games, seen = [], [], set()
    for member, payload in iter_raw_tar(prefix, allow_partial=not complete_archive):
        counts["raw_complete_members"] += 1
        is_candidate = False
        try:
            raw = json.loads(payload)
            query = parse_qs(urlparse(raw["request_url"]).query)
            tags = [query.get(k, [""])[0].lstrip("#") for k in ("team_tags", "opponent_tags")]
            if not any(tag in by_tag for tag in tags):
                counts["no_certified_teacher_account"] += 1
                continue
            counts["expert_account_candidate_members"] += 1
            is_candidate = True
            digest = hashlib.sha256(payload).hexdigest()
            if hashes.get(member) != digest:
                raise ValueError("Raw member differs from pinned upstream SHA256 manifest")
            name = PurePosixPath(member).name
            raw_path = raw_dir / name
            if raw_path.exists() and sha256_file(raw_path) != digest:
                raise ValueError("Refuse to replace different existing raw")
            if not raw_path.exists():
                raw_path.write_bytes(payload)
            game = parse_cochon(raw, source_path=str(raw_path), source_sha256=digest)
            game["archive_member"] = member
            game["expert_evidence"] = [{**by_tag[tag], "team": seat}
                                        for seat, tag in enumerate(tags) if tag in by_tag]
            game["teacher_seats"] = [e["team"] for e in game["expert_evidence"]]
            game["split"] = whole_game_split(game["game_id"])
            if game["game_id"] in seen:
                counts["duplicate_games_including_reverse_views"] += 1
                continue
            seen.add(game["game_id"])
            normalized_path = normalized_dir / (game["source_id"] + ".json")
            normalized_path.write_text(json.dumps(game, ensure_ascii=False, indent=2) + "\n")
            games.append({"game_id": game["game_id"], "source_id": game["source_id"],
                          "source_sha256": digest, "path": str(normalized_path),
                          "split": game["split"], "teacher_seats": game["teacher_seats"],
                          "events": len(game["events"]), "expert_evidence": game["expert_evidence"]})
            counts["normalized_unique_games"] += 1
        except (ValueError, KeyError, TypeError) as exc:
            counts["rejected_members"] += 1
            counts["rejected_expert_candidate_members" if is_candidate else "unreadable_or_rejected_non_candidate_members"] += 1
            failures.append({"archive_member": member, "reason": str(exc), "expert_candidate": is_candidate})
    result = summarize_sources(games, counts)
    result.update({"archive_path": str(prefix), "archive_sha256": sha256_file(prefix),
                   "archive_is_complete": complete_archive,
                   "manifest_path": str(manifest_path), "manifest_sha256": sha256_file(manifest_path),
                   "expert_evidence_path": str(evidence_path), "expert_evidence_sha256": sha256_file(evidence_path),
                   "sampling": ("all teacher-tag matches across the complete pinned archive; not random" if complete_archive else
                                "all teacher-tag matches among complete members in bounded archive prefix; not random"),
                   "games": games, "failures": failures})
    if not complete_archive:
        result.update({"prefix_path": str(prefix), "prefix_sha256": result["archive_sha256"],
                       "prefix_is_incomplete_archive": True})
    (destination / "source-index.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    return result


def main() -> None:
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    fetch = sub.add_parser("fetch")
    fetch.add_argument("--output-dir", default="data/arena-lab")
    fetch.add_argument("--lock")
    fetch.add_argument("--full", action="store_true", help="Fetch/verify complete fixed archive using independent full-v2 lock")
    select = sub.add_parser("select")
    for option in ("prefix", "manifest", "evidence", "destination"):
        select.add_argument("--" + option, required=True)
    args = parser.parse_args()
    result = (prepare(args.output_dir, lock_path=args.lock, full=args.full) if args.command == "fetch" else
              select_snapshot(args.prefix, args.manifest, args.evidence, args.destination))
    print(json.dumps({k: result[k] for k in ("counts", "unique_games", "splits", "evidence_tiers")}, indent=2))


if __name__ == "__main__":
    main()

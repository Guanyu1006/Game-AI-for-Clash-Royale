"""Same paused native scene, alternating fresh and persistent raw screenshots.

This is a small transport diagnostic, never a real-time/closed-loop acceptance.
It configures one fresh ordinary-card offline scene and sends no card requests.
"""
import argparse
import hashlib
from pathlib import Path
import statistics
import time

from .device import (PersistentRawAndroidCapture, RawAndroidCapture, append,
                     native_level_audit, write)


def benchmark(args):
    from royale_phase2.backend import NativeBattleBackend
    from royale_phase3.actions import match
    from royale_phase3.common import native_lock
    root = Path(args.output); root.mkdir(parents=True, exist_ok=False)
    report = {"schema": "arena-android-capture-benchmark.v1", "status": "running",
              "scope": "One paused offline native scene; alternating transports; small diagnostic sample",
              "real_time_verified": False, "closed_loop_verified": False,
              "render_freshness_verified": False, "physical_device": False,
              "official_live_client": False, "card_requests": 0, "samples": []}
    write(root/"report.json", report)
    engine = None; persistent = PersistentRawAndroidCapture(root/"persistent.stderr.log")
    transports = [("raw", RawAndroidCapture()), ("raw-persistent", persistent)]
    try:
        with native_lock():
            configuration = match(args.seed)
            configuration["battle"]["lvlcap"] = configuration["battle"]["cardlvlmin"] = args.level
            for seat in (0, 1):
                configuration["battle"][f"avatar{seat}"]["expLevel"] = args.level
                configuration["battle"]["hbd"][seat]["kt"] = args.level
            write(root/"initialization.json", configuration)
            engine = NativeBattleBackend(rendered=True, connection_config=args.connection,
                                        trace_path=root/"wire.jsonl")
            state = engine.reset(configuration)
            write(root/"level-audit.json", native_level_audit(configuration, state))
            version = engine.export_journal()["version_fingerprint"]
            for iteration in range(args.repetitions):
                # Alternate which transport goes first; do not discard cold samples.
                for name, capture in transports if iteration % 2 == 0 else reversed(transports):
                    before = engine._capture(state["generation"], state["next"])
                    started = time.monotonic_ns(); png = capture.capture(); finished = time.monotonic_ns()
                    state = engine._capture(before["generation"], before["next"])
                    if (before["tick"], before["generation"]) != (state["tick"], state["generation"]):
                        raise RuntimeError("Native state changed during transport benchmark")
                    index = len(report["samples"])
                    frame = root/"frames"/f"{index:06d}.png"; frame.parent.mkdir(exist_ok=True); frame.write_bytes(png)
                    state_path = root/"states"/f"{index:06d}.json"; write(state_path, before)
                    row = {"transport": name, "iteration": iteration,
                           "frame_path": frame.as_posix(), "frame_sha256": hashlib.sha256(png).hexdigest(),
                           "capture_started_ns": started, "capture_finished_ns": finished,
                           "capture_ms": (finished-started)/1e6, "transport_detail": capture.last_capture,
                           "state_tick_before": before["tick"], "state_tick_after": state["tick"],
                           "generation_before": before["generation"], "generation_after": state["generation"],
                           "state_path": state_path.as_posix(),
                           "state_sha256": hashlib.sha256(state_path.read_bytes()).hexdigest(),
                           "version_fingerprint": version, "rendered_state_tick": None,
                           "render_latency_bound_ms": None, "source": "android_framebuffer"}
                    append(root/"captures.jsonl", row); report["samples"].append(row); write(root/"report.json", report)
            report["summary"] = {}
            for name, _ in transports:
                values = [r["capture_ms"] for r in report["samples"] if r["transport"] == name]
                report["summary"][name] = {"count": len(values), "median_ms": statistics.median(values),
                                            "minimum_ms": min(values), "maximum_ms": max(values)}
            report.update(status="completed", version_fingerprint=version,
                          actual_levels_verified=False, natural_terminal=False)
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__+": "+str(exc)); raise
    finally:
        for name, closer in (("capture", persistent), ("native", engine)):
            if closer:
                try: closer.close()
                except Exception as exc: report[name+"_close_error"] = type(exc).__name__+": "+str(exc)
        write(root/"report.json", report)
    return report


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--connection", required=True); p.add_argument("--output", required=True)
    p.add_argument("--level", type=int, choices=range(1, 17), default=11)
    p.add_argument("--seed", type=int, default=20261129)
    p.add_argument("--repetitions", type=int, choices=range(1, 11), default=3)
    benchmark(p.parse_args())


if __name__ == "__main__": main()

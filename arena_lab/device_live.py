"""Fixed-layout ADB pixel controller for an already-open offline battle.

No application launch, login, matchmaking, native RPC, or game-memory reads.
After the first attempted card, capture its visual receipt and stop: neither a
successful shell command nor a changed HUD independently proves consumption.
"""
from __future__ import annotations

import argparse
from dataclasses import asdict
import hashlib
import math
from pathlib import Path
import re
import subprocess
import time

from .device import ActionLedger, AdbTapActuator, append, visual_consistency, write
from .vision import CARDS, HandRecognizer, PixelObserver, ScreenCalibration, UnitDetector


class AdbPngCapture:
    """No root requirement; foreground is checked before each capture."""
    def __init__(self, adb, serial, port=5038, timeout=10.):
        if not serial or serial.startswith("-") or any(c.isspace() for c in serial):
            raise ValueError("An explicit ADB serial is required")
        self.prefix = [str(adb), "-H", "127.0.0.1", "-P", str(port), "-s", serial]
        self.timeout = timeout

    def verify_foreground(self):
        started = time.monotonic_ns()
        focus = subprocess.run(self.prefix+["shell", "dumpsys", "window", "windows"],
                               capture_output=True, timeout=self.timeout, check=True)
        if not re.search(rb"mCurrentFocus=[^\n]*\snullsroyale\.rel\.free/", focus.stdout):
            raise RuntimeError("The supported offline application is not the focused window; no taps allowed")
        return {"package": "nullsroyale.rel.free", "started_ns": started, "finished_ns": time.monotonic_ns()}

    def capture(self):
        self.verify_foreground()
        result = subprocess.run(self.prefix+["exec-out", "screencap", "-p"],
                                capture_output=True, timeout=self.timeout, check=True)
        if not result.stdout.startswith(b"\x89PNG\r\n\x1a\n") or len(result.stdout) > 8*1024*1024:
            raise ValueError("Invalid or oversized Android PNG screenshot")
        return result.stdout


def tap_coordinates(action, observation, calibration):
    """Validate the visible slot and resource again at the actuator boundary."""
    if action.get("kind") != "play_card" or observation.get("scene_ready") is not True:
        raise ValueError("A ready pixel battlefield and play_card action are required")
    slot = action.get("slot")
    if type(slot) is not int or not 0 <= slot < 4:
        raise ValueError("Invalid hand slot")
    known = next((row for row in observation["hand"] if row["slot"] == slot), None)
    value = observation.get("elixir", {}).get("lower_bound")
    if (not known or not known.get("known") or not known.get("visually_ready")
            or known.get("card_id") != action.get("card") or action.get("card") not in CARDS
            or value is None or not math.isfinite(value) or value < CARDS[action["card"]][1]):
        raise ValueError("Action differs from the known, visible, affordable hand")
    x, y = action.get("x"), action.get("y")
    if (type(x) not in (int, float) or type(y) not in (int, float) or not math.isfinite(x+y)
            or not 0 <= x < 18000 or not 0 <= y < 32000):
        raise ValueError("Target is outside the calibrated world")
    left, top, right, bottom = calibration.arena
    hand = calibration.hand[slot]
    return ((hand[0]+hand[2])//2, (hand[1]+hand[3])//2), (
        round(left+x/18000*(right-left)), round(top+y/32000*(bottom-top)))


def verify_before_tap(capture, observed_ns, max_age_ms):
    foreground = capture.verify_foreground()
    now = time.monotonic_ns()
    if now < observed_ns or (now-observed_ns)/1e6 > max_age_ms:
        raise RuntimeError("Stale screenshot after foreground check; no tap allowed")
    return foreground


def run_live(args, *, observer=None, controller=None, capture=None, actuator=None, sleep=time.sleep):
    if not 1 <= args.frames <= 100 or not 0 <= args.interval <= 60 or not 0 <= args.receipt_delay <= 60:
        raise ValueError("Bounded frame count and delays required")
    if not math.isfinite(args.max_age_ms) or args.max_age_ms <= 0 or not 0 < args.timeout <= 90:
        raise ValueError("Positive bounded timeout and screenshot age required")
    root = Path(args.output); root.mkdir(parents=True, exist_ok=False)
    calibration = ScreenCalibration(); ledger = ActionLedger(timeout_seconds=args.timeout)
    report = {"schema": "arena-device-live.v1", "status": "initializing", "transport": "adb_input_tap",
              "source": "screenshot_pixels_only", "package": "nullsroyale.rel.free",
              "physical_device_verified": False, "physical_acceptance_passed": False,
              "full_match_verified": False, "elapsed_game_time_known": False,
              "render_freshness_verified": False, "real_time_verified": False,
              "decision_horizon_on_device_verified": False, "calibration": asdict(calibration),
              "scope": "Fixed 360x640 bottom-player layout; already-open offline battle; stop after one attempted card",
              "automatic_retry": False, "captures": 0, "card_attempts": 0, "game_consumed": None}
    report["connection"] = {"serial": getattr(args, "serial", None), "host": "127.0.0.1",
                            "port": getattr(args, "port", None), "adb": getattr(args, "adb", None)}
    write(root/"report.json", report)
    try:
        checkpoint = getattr(args, "checkpoint", None)
        if checkpoint:
            checkpoint = Path(checkpoint)
            report["checkpoint"] = {"path": str(checkpoint), "sha256": hashlib.sha256(checkpoint.read_bytes()).hexdigest()}
        if observer is None:
            detector = UnitDetector(args.assets, calibration, threads=1)
            hand = HandRecognizer({k: Path(args.assets)/"cards"/(v[0]+".jpg") for k,v in CARDS.items()}, calibration)
            observer = PixelObserver(detector, hand)
        if controller is None:
            from .pixel_policy import PixelModelController
            controller = PixelModelController(args.checkpoint)
        report["policy"] = controller.metadata
        capture = capture or AdbPngCapture(args.adb, args.serial, args.port, args.timeout)
        actuator = actuator or AdbTapActuator(args.adb, args.serial, args.port, args.timeout,
            before_tap=lambda: verify_before_tap(capture, observed_ns, args.max_age_ms))

        def observe():
            started = time.monotonic_ns(); png = capture.capture(); finished = time.monotonic_ns()
            index = report["captures"]; name = f"frame-{index:04d}.png"
            (root/name).write_bytes(png)
            append(root/"captures.jsonl", {"frame_path": name, "frame_sha256": hashlib.sha256(png).hexdigest(),
                "capture_started_ns": started, "capture_finished_ns": finished,
                "source": "adb_screencap_png", "rendered_state_tick": None,
                "render_latency_bound_ms": None, "capture_ms": (finished-started)/1e6})
            # Unknown logical time is intentional: observer and model reset their
            # recurrent histories instead of treating wall seconds as game ticks.
            observation = observer.observe(png, finished/1e9)
            report["captures"] += 1
            append(root/"observations.jsonl", {"frame": index, "observation": observation})
            return observation, started

        for index in range(args.frames):
            if index: sleep(args.interval)
            before, observed_ns = observe()
            start = time.monotonic_ns(); action = controller.decide(before); finish = time.monotonic_ns()
            append(root/"decisions.jsonl", {"frame": index, "started_ns": start, "finished_ns": finish,
                                           "decision": action})
            if action.get("kind") == "wait": continue
            hand_xy, target_xy = tap_coordinates(action, before, calibration)
            # Conservative age includes capture, foreground checking and inference.
            # No silent adjustment for slow transports or paused/offline operation.
            request = ledger.request(action, observed_ns, time.monotonic_ns(), args.max_age_ms)
            append(root/"requests.jsonl", request); report["card_attempts"] += 1
            receipt = actuator.send(hand_xy, target_xy)
            ledger.acknowledge(request["request_id"], time.monotonic_ns(), receipt["accepted"])
            append(root/"transport-receipts.jsonl", {"request_id": request["request_id"], **receipt})
            if not receipt["accepted"]:
                report["status"] = "stopped_transport_rejected_execution_unknown"
                break
            sleep(args.receipt_delay)
            after, _ = observe(); evidence = visual_consistency(before, after, action)
            evidence["reason"] = "Pixel HUD change is supporting evidence; no independent consumption receipt exists"
            ledger.visual(request["request_id"], time.monotonic_ns(), evidence)
            report["visual_support"] = evidence
            report["status"] = "stopped_execution_unknown"
            break
        else:
            report["status"] = "frame_budget_without_card_attempt"
    except Exception as error:
        report.update(status="failed", error=f"{type(error).__name__}: {error}")
    finally:
        report["actions"] = ledger.history
        report["outstanding_action"] = ledger.pending
        write(root/"report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--serial", required=True)
    parser.add_argument("--checkpoint", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--adb", default=".cache/native-tools/bin/adb")
    parser.add_argument("--port", type=int, default=5038)
    parser.add_argument("--assets", default=".cache/arena-lab/vision")
    parser.add_argument("--frames", type=int, default=30)
    parser.add_argument("--interval", type=float, default=.5)
    parser.add_argument("--receipt-delay", type=float, default=.5)
    parser.add_argument("--max-age-ms", type=float, default=2000)
    parser.add_argument("--timeout", type=float, default=10)
    result = run_live(parser.parse_args())
    print(result["status"])
    raise SystemExit(1 if result["status"] == "failed" else 0)


if __name__ == "__main__": main()

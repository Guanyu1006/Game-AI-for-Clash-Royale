import json
from pathlib import Path
import tempfile
import subprocess
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from arena_lab.device_live import AdbPngCapture, run_live, tap_coordinates, verify_before_tap
from arena_lab.device import AdbTapActuator
from arena_lab.device_diagnostics import collect, SCRIPT
from arena_lab.vision import ScreenCalibration


def observation(card=26000000, elixir=6):
    return {"source": "screenshot_pixels_only", "scene_ready": True,
            "hand": [{"slot": 0, "known": True, "visually_ready": True, "card_id": card}],
            "elixir": {"value": elixir, "lower_bound": elixir}, "tracks": [], "detections": []}


class LiveDeviceContractTests(unittest.TestCase):
    def run_fixture(self, *, receipt_error=False, stale=False):
        root = Path(__file__).resolve().parents[1]/".tmp"; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="live-contract-", dir=root) as temporary:
            args = SimpleNamespace(output=str(Path(temporary)/"out"), frames=10, interval=0,
                                   receipt_delay=0, max_age_ms=1e-9 if stale else 2000, timeout=1)
            calls = {"observe": 0, "decide": 0, "send": 0, "capture": 0}
            def capture():
                calls["capture"] += 1
                if receipt_error and calls["capture"] == 2: raise TimeoutError("receipt unavailable")
                return b"fixture PNG transport"
            def observe(png, timestamp, **kwargs):
                self.assertEqual(kwargs, {})  # No invented logical step receipt.
                calls["observe"] += 1
                return observation() if calls["observe"] == 1 else observation(26000001, 3)
            def decide(obs):
                calls["decide"] += 1
                return {"kind": "play_card", "slot": 0, "card": 26000000, "x": 9000, "y": 21000}
            def send(hand, target):
                calls["send"] += 1
                return {"accepted": True, "game_consumed": None, "receipts": []}
            report = run_live(args, capture=SimpleNamespace(capture=capture),
                observer=SimpleNamespace(observe=observe), actuator=SimpleNamespace(send=send),
                controller=SimpleNamespace(decide=decide, metadata={"fixture": True}), sleep=lambda _: None)
            persisted = json.loads((Path(args.output)/"report.json").read_text())
            self.assertEqual(json.loads(json.dumps(report)), persisted)
            return calls, report

    def test_full_chain_stops_after_visual_support_without_claiming_consumption(self):
        calls, report = self.run_fixture()
        self.assertEqual(calls, {"observe": 2, "decide": 1, "send": 1, "capture": 2})
        self.assertEqual(report["status"], "stopped_execution_unknown")
        self.assertTrue(report["visual_support"]["consistent"])
        self.assertFalse(report["visual_support"]["proof_of_consumption"])
        self.assertIsNone(report["game_consumed"])
        self.assertFalse(report["physical_acceptance_passed"])
        self.assertIsNone(report["actions"][0]["consumed"])

    def test_missing_post_action_capture_preserves_pending_and_never_retries(self):
        calls, report = self.run_fixture(receipt_error=True)
        self.assertEqual(calls["send"], 1)
        self.assertEqual(report["status"], "failed")
        self.assertIsNone(report["outstanding_action"]["consumed"])
        self.assertFalse(report["automatic_retry"])

    def test_stale_capture_cannot_reach_tap_actuator(self):
        calls, report = self.run_fixture(stale=True)
        self.assertEqual(calls["send"], 0)
        self.assertIn("Stale", report["error"])

    def test_tap_mapping_and_revalidation(self):
        action = {"kind": "play_card", "slot": 0, "card": 26000000, "x": 9000, "y": 16000}
        self.assertEqual(tap_coordinates(action, observation(), ScreenCalibration()), ((70, 585), (180, 339)))
        with self.assertRaises(ValueError): tap_coordinates(action, observation(26000001), ScreenCalibration())
        with self.assertRaises(ValueError): tap_coordinates(action, observation(elixir=2), ScreenCalibration())
        with self.assertRaises(ValueError): tap_coordinates({**action, "x": float("nan")}, observation(), ScreenCalibration())

    def test_nonfinite_age_rejected_before_creating_or_controlling_anything(self):
        for value in (float("nan"), float("inf"), float("-inf")):
            args = SimpleNamespace(frames=1, interval=0, receipt_delay=0, max_age_ms=value, timeout=1)
            with self.assertRaisesRegex(ValueError, "timeout and screenshot age"):
                run_live(args)

    def test_capture_never_screenshots_when_foreground_is_unrelated(self):
        capture = AdbPngCapture("adb", "explicit-serial")
        with patch("arena_lab.device_live.subprocess.run", return_value=SimpleNamespace(stdout=b"mCurrentFocus=other.app/Main")) as run:
            with self.assertRaisesRegex(RuntimeError, "focused"): capture.capture()
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args[0][0][-4:], ["shell", "dumpsys", "window", "windows"])

    def test_second_tap_timeout_preserves_first_transport_receipt(self):
        actuator = AdbTapActuator("adb", "explicit-serial")
        outcomes = [SimpleNamespace(returncode=0, stderr=b""), subprocess.TimeoutExpired("tap", 1)]
        with patch("arena_lab.device.subprocess.run", side_effect=outcomes) as run:
            receipt = actuator.send((70, 585), (180, 339))
        self.assertEqual(run.call_count, 2)
        self.assertFalse(receipt["accepted"])
        self.assertIsNone(receipt["game_consumed"])
        self.assertEqual(receipt["receipts"][0]["returncode"], 0)
        self.assertTrue(receipt["receipts"][1]["outcome_unknown"])

    def test_focus_change_between_taps_prevents_second_input(self):
        checks = iter([{"package": "nullsroyale.rel.free"}, RuntimeError("focus changed")])
        def guard():
            value = next(checks)
            if isinstance(value, Exception): raise value
            return value
        actuator = AdbTapActuator("adb", "explicit-serial", before_tap=guard)
        with patch("arena_lab.device.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr=b"")) as run:
            receipt = actuator.send((70, 585), (180, 339))
        self.assertEqual(run.call_count, 1)
        self.assertFalse(receipt["accepted"])
        self.assertEqual(receipt["receipts"][0]["foreground_check"]["package"], "nullsroyale.rel.free")
        self.assertIn("focus changed", receipt["receipts"][1]["error"])

    def test_slow_foreground_check_cannot_refresh_screenshot_age(self):
        capture = SimpleNamespace(verify_foreground=lambda: {"package": "nullsroyale.rel.free"})
        with patch("arena_lab.device_live.time.monotonic_ns", return_value=3_000_000_000):
            with self.assertRaisesRegex(RuntimeError, "Stale screenshot"):
                verify_before_tap(capture, 0, 2000)

    def test_failed_second_foreground_command_preserves_first_tap(self):
        count = 0
        def guard():
            nonlocal count
            count += 1
            if count == 2: raise subprocess.CalledProcessError(1, ["adb", "dumpsys"])
            return {"package": "nullsroyale.rel.free"}
        actuator = AdbTapActuator("adb", "explicit-serial", before_tap=guard)
        with patch("arena_lab.device.subprocess.run", return_value=SimpleNamespace(returncode=0, stderr=b"")) as run:
            receipt = actuator.send((70, 585), (180, 339))
        self.assertEqual(run.call_count, 1)
        self.assertEqual(receipt["receipts"][0]["returncode"], 0)
        self.assertIn("CalledProcessError", receipt["receipts"][1]["error"])
        self.assertIsNone(receipt["game_consumed"])

    def test_diagnostic_launch_error_is_preserved_in_report(self):
        root = Path(__file__).resolve().parents[1]/".tmp"; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="diagnostics-contract-", dir=root) as temporary:
            args = SimpleNamespace(output=str(Path(temporary)/"out"), serial="explicit-serial",
                                   adb="missing-adb", port=5038, samples=1, interval=0, timeout=1)
            with patch("arena_lab.device_diagnostics.subprocess.run", side_effect=FileNotFoundError("fixture")):
                report = collect(args)
            persisted = json.loads((Path(args.output)/"report.json").read_text())
            self.assertEqual(report, persisted)
            self.assertIn("FileNotFoundError", report["samples"][0]["error"])
            self.assertEqual(report["root_cause"], "unknown")

    @unittest.skipUnless(shutil.which("bash"), "Optional local shell unavailable")
    def test_diagnostic_retains_both_candidates_without_selecting_identity(self):
        prelude = ('pidof() { printf "998877 998878"; }\n'
                   'cat() { :; }; wc() { :; }; grep() { :; }; df() { :; }; dumpsys() { :; }\n')
        result = subprocess.run(["bash"], input=(prelude + SCRIPT).encode(), capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())
        self.assertIn(b"GAME_PID=998877\n", result.stdout)
        self.assertIn(b"GAME_PID=998878\n", result.stdout)
        self.assertIn(b"IDENTITY_SELECTION=none", result.stdout)
        self.assertEqual(result.stdout.count(b"PROCESS_IDENTITY_END\n"), 2)

    @unittest.skipUnless(shutil.which("bash"), "Optional local shell unavailable")
    def test_diagnostic_rejects_missing_invalid_or_excess_candidates_before_reads(self):
        for pids, code in [("", 21), ("   ", 21), ("0", 21), ("000", 21),
                           ("998877 0", 21), ("998877;invalid", 21), ("1 2 3 4 5", 22)]:
            with self.subTest(pids=pids):
                prelude = f'pidof() {{ printf "%s" "{pids}"; }}\ncat() {{ echo UNEXPECTED_READ; }}\n'
                result = subprocess.run(["bash"], input=(prelude + SCRIPT).encode(), capture_output=True)
                self.assertEqual(result.returncode, code)
                self.assertNotIn(b"UNEXPECTED_READ", result.stdout)


if __name__ == "__main__": unittest.main()

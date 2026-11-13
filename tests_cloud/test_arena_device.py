import importlib.util
import io
import struct
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace
import unittest

from arena_lab.device import (ActionLedger, PixelPolicy, RawAndroidCapture, PersistentRawAndroidCapture, native_level_audit,
                              validate_controller_timing, visual_consistency)
from arena_lab.vision import PixelObserver, ScreenCalibration, UnitTracker


def det(x=20, side="enemy", label="knight"):
    return {"label": label, "side": side, "bbox": [x, 20, x+10, 40],
            "xy_native_estimate": [5000, 20000]}


class DeviceSafetyTests(unittest.TestCase):
    def test_ack_is_not_execution_and_duplicate_cannot_resend(self):
        ledger = ActionLedger(1)
        row = ledger.request({"kind": "play_card"}, 0, 20)
        ledger.acknowledge(row["request_id"], 30, True)
        self.assertIsNone(ledger.pending["consumed"])
        with self.assertRaises(RuntimeError): ledger.request({}, 30, 40)
        self.assertTrue(ledger.expire(1_000_000_020))
        self.assertEqual(ledger.history[0]["status"], "timed_out_execution_unknown")
        self.assertFalse(ledger.history[0]["automatic_retry"])
        with self.assertRaises(ValueError): ledger.consume(row["request_id"], 1_100_000_000, True, {})

    def test_stale_future_out_of_order_and_wrong_receipt_rejected(self):
        ledger = ActionLedger()
        for bad_age in (float("nan"), float("inf"), float("-inf"), 0, -1):
            with self.assertRaisesRegex(ValueError, "finite and positive"):
                ledger.request({}, 20, 30, bad_age)
        with self.assertRaises(ValueError): ledger.request({}, 0, 2_000_000_000)
        with self.assertRaises(ValueError): ledger.request({}, 20, 10)
        row = ledger.request({}, 20, 30)
        with self.assertRaises(ValueError): ledger.acknowledge("wrong", 40, True)
        ledger.acknowledge(row["request_id"], 40, True)
        with self.assertRaises(ValueError): ledger.consume(row["request_id"], 39, True, {})

    def test_consumption_and_visual_support_remain_distinct(self):
        ledger = ActionLedger(); row = ledger.request({}, 1, 2)
        ledger.acknowledge(row["request_id"], 3, True)
        ledger.consume(row["request_id"], 4, True, {"native_event": 123})
        ledger.visual(row["request_id"], 5, {"consistent": False})
        self.assertTrue(ledger.history[0]["consumed"])
        self.assertFalse(ledger.history[0]["visual_consistency"]["consistent"])

    def test_missing_consumption_receipt_stays_unknown(self):
        ledger = ActionLedger(); row = ledger.request({}, 1, 2)
        ledger.acknowledge(row["request_id"], 3, True)
        ledger.consume(row["request_id"], 4, None, [])
        self.assertIsNone(ledger.pending["consumed"])
        self.assertEqual(ledger.pending["status"], "native_execution_unknown")
        with self.assertRaises(ValueError): ledger.consume(row["request_id"], 5, 1, [])
        with self.assertRaises(ValueError): ledger.consume(row["request_id"], 5, True, [])
        with self.assertRaises(ValueError): ledger.acknowledge(row["request_id"], 5, True)

    def test_requested_level_is_not_claimed_as_actual_level(self):
        config = {"battle": {"lvlcap": 11, "cardlvlmin": 11, "hbd": [{"kt": 11}, {"kt": 11}]}}
        state = {"tick": 90, "players": [{"owner": 0, "hand": [[0, 26000000, 3, 123, 26000000]]}],
                 "entities": [[1, 0, 1, 5000, 6000, 4824, 4824]]}
        audit = native_level_audit(config, state)
        self.assertTrue(audit["configuration_accepted"])
        self.assertFalse(audit["actual_levels_verified"])
        self.assertIsNone(audit["actual_levels"])
        self.assertEqual(audit["native_entity_hp_rows"][0]["max_hp"], 4824)

    def test_checkpoint_horizon_cannot_be_changed_by_elapsed_time_feedback(self):
        timing = SimpleNamespace(tick_hz=20., decision_ticks=10)
        controller = SimpleNamespace(model=SimpleNamespace(timing_config=timing))
        validate_controller_timing(controller, 10)
        with self.assertRaisesRegex(ValueError, "decision_ticks"):
            validate_controller_timing(controller, 100)
        timing.tick_hz = 60.
        with self.assertRaisesRegex(ValueError, "tick_hz"):
            validate_controller_timing(controller, 10)
        with self.assertRaises(ValueError): validate_controller_timing(PixelPolicy(), 10)

    def test_track_expiry_uses_time_and_side_class_gates(self):
        tracker = UnitTracker(max_age_seconds=1.)
        one = tracker.update([det()], 1.)[0]
        moved = tracker.update([det(23)], 1.2)[0]
        self.assertEqual(one["track_id"], moved["track_id"])
        swap = tracker.update([det(24, side="ally")], 1.3)
        self.assertEqual(len(swap), 2)
        self.assertEqual(sum(x["visible"] for x in swap), 1)
        gone = tracker.update([], 3.)
        self.assertEqual(gone, [])
        with self.assertRaises(ValueError): tracker.update([], 2.)
        tracker.reset(); self.assertEqual(tracker.update([det()], 1.)[0]["track_id"], 1)

    def test_unknown_hand_and_resource_never_become_legal(self):
        p = PixelPolicy()
        unknown = {"source": "screenshot_pixels_only", "elixir": {"lower_bound": None},
                   "hand": [{"known": False}], "tracks": []}
        self.assertEqual(p.decide(unknown)["kind"], "wait")
        with self.assertRaises(ValueError): p.decide({**unknown, "source": "native_oracle"})

    def test_only_visible_tracks_influence_pixel_controller(self):
        p = PixelPolicy()
        obs = {"source": "screenshot_pixels_only", "elixir": {"lower_bound": 3},
            "hand": [{"known": True, "visually_ready": True, "cost": 3, "card_id": 26000000, "slot": 0}],
            "tracks": [{**det(), "visible": False}]}
        self.assertEqual(p.decide(obs)["kind"], "wait")
        obs["tracks"][0]["visible"] = True
        self.assertEqual(p.decide(obs)["kind"], "play_card")
        obs["scene_ready"] = False
        self.assertEqual(p.decide(obs)["kind"], "wait")

    def test_visual_unknown_cannot_confirm_execution(self):
        obs = {"hand": [{"slot": 0, "known": False}], "elixir": {"value": None}}
        e = visual_consistency(obs, obs, {"kind": "play_card", "slot": 0})
        self.assertIsNone(e["hand_slot_changed"])
        self.assertFalse(e["consistent"])
        self.assertFalse(e["proof_of_consumption"])

    def test_calibration_must_describe_supported_layout(self):
        with self.assertRaises(ValueError): ScreenCalibration(owner=0)
        with self.assertRaises(ValueError): ScreenCalibration(arena=(0, 0, 400, 640))

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Optional Pillow not installed")
    def test_persistent_capture_preserves_frame_boundaries_and_never_restarts_poisoned_stream(self):
        from PIL import Image
        root = Path(__file__).resolve().parents[1]/".tmp"; root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="pixel-pipe-", dir=root) as folder:
            script = """import sys, struct
for i, request in enumerate(sys.stdin.buffer):
 assert request == b'screencap\\n'
 header=struct.pack('<IIII',360,640,1,1)
 sys.stdout.buffer.write(header[:3]); sys.stdout.buffer.flush()
 sys.stdout.buffer.write(header[3:])
 sys.stdout.buffer.write(bytes([i,17,33,255])*(360*640)); sys.stdout.buffer.flush()
"""
            capture = PersistentRawAndroidCapture(Path(folder)/"good.stderr", timeout=2)
            self.assertEqual(capture.command[-6:], ["shell", "-T", "su", "0", "sh", "-s"])
            self.assertNotIn("exec-out", capture.command)
            capture.command = [sys.executable, "-u", "-c", script]
            try:
                one = capture.capture(); pid = capture.process.pid; two = capture.capture()
                self.assertEqual(capture.process.pid, pid)
                self.assertEqual(Image.open(io.BytesIO(one)).getpixel((0, 0)), (0, 17, 33, 255))
                self.assertEqual(Image.open(io.BytesIO(two)).getpixel((0, 0)), (1, 17, 33, 255))
            finally: capture.close()
            with self.assertRaisesRegex(RuntimeError, "poisoned"): capture.capture()

            broken = PersistentRawAndroidCapture(Path(folder)/"broken.stderr", timeout=2)
            broken.command = [sys.executable, "-u", "-c", "import sys,struct; sys.stdin.readline(); "
                              "sys.stdout.buffer.write(struct.pack('<IIII',360,640,1,1)+b'bad')"]
            with self.assertRaisesRegex(RuntimeError, "mid-frame"): broken.capture()
            with self.assertRaisesRegex(RuntimeError, "poisoned"): broken.capture()

            stalled = PersistentRawAndroidCapture(Path(folder)/"stalled.stderr", timeout=.02)
            stalled.command = [sys.executable, "-u", "-c", "import sys,time; sys.stdin.readline(); time.sleep(.1)"]
            with self.assertRaises(TimeoutError): stalled.capture()
            self.assertIsNotNone(stalled.process.poll())

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Optional Pillow not installed")
    def test_confirmed_game_clock_tracks_through_slow_capture_and_rejects_double_application(self):
        from PIL import Image
        image = Image.new("RGB", (360, 640), (200, 0, 200)); buffer = io.BytesIO()
        image.save(buffer, format="PNG"); png = buffer.getvalue()
        detector = SimpleNamespace(calibration=ScreenCalibration(), detect=lambda _: [det()])
        hand = SimpleNamespace(recognize=lambda _: [{"known": True}])
        observer = PixelObserver(detector, hand)
        feedback = lambda n: {"elapsed_ticks": n, "tick_hz": 20, "provenance": "confirmed_local_step"}
        first = observer.observe(png, 1., control_feedback=feedback(0))
        slow = observer.observe(png, 101., control_feedback=feedback(10))
        self.assertEqual(first["tracks"][0]["track_id"], slow["tracks"][0]["track_id"])
        self.assertEqual(slow["tracks"][0]["observations"], 2)
        self.assertEqual(slow["tracking_timestamp"], .5)
        with self.assertRaises(ValueError): observer.observe(png, 101., control_feedback=feedback(10))
        self.assertEqual(observer.control_seconds, .5)
        expired = observer.observe(png, 201., control_feedback=feedback(30))
        self.assertNotEqual(slow["tracks"][0]["track_id"], expired["tracks"][0]["track_id"])
        unknown = observer.observe(png, 202.)
        self.assertEqual(unknown["tracks"][0]["observations"], 1)
        again = observer.observe(png, 203.)
        self.assertEqual(again["tracking_reset_reason"], "no_confirmed_step_receipt")
        self.assertEqual(again["tracks"][0]["observations"], 1)
        self.assertFalse(again["render_freshness_verified"])

    @unittest.skipUnless(importlib.util.find_spec("PIL"), "Optional Pillow not installed")
    def test_raw_capture_roundtrip_preserves_pixels_and_refuses_bad_header(self):
        from PIL import Image
        pixels = bytes([255, 0, 0, 255, 0, 255, 0, 255])
        png, info = RawAndroidCapture.decode(struct.pack("<IIII", 2, 1, 1, 1)+pixels)
        self.assertEqual(Image.open(io.BytesIO(png)).tobytes(), pixels)
        self.assertEqual(info["header_bytes"], 16)
        with self.assertRaises(ValueError): RawAndroidCapture.decode(struct.pack("<III", 9000, 9000, 1))
        with self.assertRaises(ValueError): RawAndroidCapture.decode(struct.pack("<III", 2, 1, 5)+pixels)
        with self.assertRaises(ValueError): RawAndroidCapture.decode(struct.pack("<III", 2, 1, 1)+pixels[:4])


if __name__ == "__main__": unittest.main()

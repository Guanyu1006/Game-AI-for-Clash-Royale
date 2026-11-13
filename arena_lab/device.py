"""Measured Android capture -> pixels -> action -> acknowledgement pipeline.

Native RPC is an explicitly named offline actuator. Its debug state is written
only to the evaluation archive and never passed to PixelPolicy. ADB tap mode
is a separate transport: transport success alone cannot confirm game execution.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from dataclasses import asdict
import hashlib
import json
import io
import os
from pathlib import Path
import selectors
import struct
import subprocess
import time
import uuid

from .vision import CARDS, HandRecognizer, PixelObserver, ScreenCalibration, UnitDetector


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False)+"\n")


def append(path, value):
    with Path(path).open("a") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, allow_nan=False)+"\n"); stream.flush()


class ActionLedger:
    """At most one outstanding action; timeout is ambiguous, never retry-safe."""
    def __init__(self, timeout_seconds=5.):
        if timeout_seconds <= 0:
            raise ValueError("Positive timeout required")
        self.timeout_ns = int(timeout_seconds*1e9); self.pending = None; self.history = []

    def request(self, action, observed_ns, now_ns, max_age_ms=1000):
        if self.pending is not None:
            raise RuntimeError("Outstanding action must be resolved; never resend automatically")
        if now_ns < observed_ns or (now_ns-observed_ns)/1e6 > max_age_ms:
            raise ValueError("Stale or future screenshot; reject action")
        row = {"request_id": str(uuid.uuid4()), "action": deepcopy(action),
            "observed_ns": observed_ns, "request_ns": now_ns, "transport_ack_ns": None,
            "consumed_ns": None, "visual_evidence_ns": None, "status": "requested",
            "consumed": None, "visual_consistency": None, "automatic_retry": False}
        self.pending = row
        return deepcopy(row)

    def acknowledge(self, request_id, now_ns, accepted):
        row = self._row(request_id, now_ns)
        if row["transport_ack_ns"] is not None:
            raise ValueError("Duplicate transport acknowledgement")
        row.update(transport_ack_ns=now_ns, transport_accepted=bool(accepted))
        row["status"] = "acknowledged_execution_unknown" if accepted else "transport_rejected"
        if not accepted: self._finish()

    def consume(self, request_id, now_ns, consumed, receipt):
        if consumed is not None and type(consumed) is not bool:
            raise ValueError("Consumption must be true, false, or explicitly unknown")
        row = self._row(request_id, now_ns)
        if row["consumed_ns"] is not None:
            raise ValueError("Duplicate native execution receipt")
        row.update(consumed_ns=now_ns, consumed=consumed, consumption_receipt=deepcopy(receipt),
                   status="native_execution_unknown" if consumed is None else
                   "native_consumed" if consumed else "native_not_consumed")

    def visual(self, request_id, now_ns, evidence):
        row = self._row(request_id, now_ns)
        row.update(visual_evidence_ns=now_ns, visual_consistency=deepcopy(evidence))
        self._finish()

    def expire(self, now_ns):
        if self.pending and now_ns-self.pending["request_ns"] >= self.timeout_ns:
            self.pending.update(status="timed_out_execution_unknown", timeout_ns=now_ns)
            self._finish(); return True
        return False

    def _row(self, request_id, now_ns):
        if self.pending is None or self.pending["request_id"] != request_id:
            raise ValueError("Unexpected, duplicate or already settled receipt")
        if now_ns < max(self.pending[k] or 0 for k in
                        ("request_ns", "transport_ack_ns", "consumed_ns", "visual_evidence_ns")):
            raise ValueError("Non-monotonic action timestamp")
        return self.pending

    def _finish(self):
        self.history.append(deepcopy(self.pending)); self.pending = None


class AdbTapActuator:
    """Two UI taps; success means shell transport only, no hidden state access."""
    def __init__(self, adb, serial, port=5038, timeout=5.):
        self.adb, self.serial, self.port, self.timeout = str(adb), serial, port, timeout
        if not serial or serial.startswith("-") or any(c.isspace() for c in serial):
            raise ValueError("Explicit ADB serial required")

    def send(self, hand_xy, target_xy):
        for point in (hand_xy, target_xy):
            if len(point) != 2 or any(type(v) is not int or not 0 <= v < 8192 for v in point):
                raise ValueError("Invalid screen coordinates")
        prefix = [self.adb, "-H", "127.0.0.1", "-P", str(self.port), "-s", self.serial, "shell", "input", "tap"]
        receipts = []
        for point in (hand_xy, target_xy):
            start = time.monotonic_ns()
            result = subprocess.run(prefix+list(map(str, point)), capture_output=True, timeout=self.timeout)
            receipts.append({"started_ns": start, "finished_ns": time.monotonic_ns(),
                             "returncode": result.returncode, "stderr": result.stderr.decode(errors="replace")[:1000]})
            if result.returncode: break
        return {"transport": "adb_input_tap", "receipts": receipts,
                "accepted": len(receipts) == 2 and all(r["returncode"] == 0 for r in receipts),
                "game_consumed": None, "automatic_retry": False}


class RawAndroidCapture:
    """Move lossless PNG encoding to host CPU; preserve captured RGBA pixels."""
    def __init__(self, adb=".cache/native-tools/bin/adb", serial="127.0.0.1:15555", port=5038, timeout=90):
        self.command = [str(Path(adb).resolve()), "-H", "127.0.0.1", "-P", str(port), "-s", serial,
                        "exec-out", "su", "0", "screencap"]
        self.timeout = timeout; self.last_capture = None

    @staticmethod
    def decode(raw):
        from PIL import Image
        if len(raw) < 12: raise ValueError("Truncated Android framebuffer header")
        width, height, pixel_format = struct.unpack_from("<III", raw)
        if not (0 < width <= 2048 and 0 < height <= 2048 and pixel_format == 1):
            raise ValueError("Unsupported Android raw framebuffer; require bounded RGBA8888")
        header_size = len(raw)-width*height*4
        if header_size not in (12, 16): raise ValueError("Unexpected framebuffer stride/header length")
        pixels = raw[header_size:]
        image = Image.frombytes("RGBA", (width, height), pixels)
        out = io.BytesIO(); image.save(out, format="PNG", compress_level=1)
        return out.getvalue(), {"width": width, "height": height, "header_bytes": header_size,
            "raw_sha256": hashlib.sha256(raw).hexdigest(), "rgba_sha256": hashlib.sha256(pixels).hexdigest(),
            "dataspace": struct.unpack_from("<I", raw, 12)[0] if header_size == 16 else None}

    def capture(self):
        started = time.monotonic_ns()
        result = subprocess.run(self.command, capture_output=True, timeout=self.timeout)
        finished = time.monotonic_ns()
        if result.returncode: raise RuntimeError("Raw screencap failed: "+result.stderr.decode(errors="replace")[:1000])
        png, info = self.decode(result.stdout)
        self.last_capture = {"transport_started_ns": started, "transport_finished_ns": finished,
            "host_encoded_ns": time.monotonic_ns(), "info": info,
            "source": "Android RGBA framebuffer; lossless host PNG encoding"}
        return png


class PersistentRawAndroidCapture(RawAndroidCapture):
    """One ADB/guest shell session, one exact fixed-layout RGBA frame per request.

    This saves connection setup only; screencap still executes for every frame.
    The 16-byte Android 12 header is required. A partial/invalid frame poisons
    the session, with no hidden retry or stream resynchronization.
    """
    def __init__(self, stderr_path, **kwargs):
        super().__init__(**kwargs)
        # adb exec-out only copies device stdout, so it cannot forward our next
        # request. shell -T is the bidirectional no-PTY byte-preserving protocol.
        offset = self.command.index("exec-out")
        self.command = self.command[:offset]+["shell", "-T", "su", "0", "sh", "-s"]
        self.stderr_path = Path(stderr_path)
        self.process = None; self.error_stream = None
        self.poisoned = False; self.session_started_ns = None

    def _start(self):
        self.stderr_path.parent.mkdir(parents=True, exist_ok=True)
        self.error_stream = self.stderr_path.open("xb")
        self.session_started_ns = time.monotonic_ns()
        self.process = subprocess.Popen(self.command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                        stderr=self.error_stream, bufsize=0)

    def _read_exact(self, size, deadline):
        chunks = []; remaining = size
        with selectors.DefaultSelector() as selector:
            selector.register(self.process.stdout, selectors.EVENT_READ)
            while remaining:
                budget = deadline-time.monotonic()
                if budget <= 0 or not selector.select(budget):
                    raise TimeoutError("Persistent Android screenshot incomplete; stream is poisoned")
                chunk = os.read(self.process.stdout.fileno(), min(remaining, 65536))
                if not chunk:
                    raise RuntimeError("Persistent Android screenshot pipe closed mid-frame")
                chunks.append(chunk); remaining -= len(chunk)
        return b"".join(chunks)

    def capture(self):
        if self.poisoned:
            raise RuntimeError("Capture session poisoned; explicit new session required, never retry automatically")
        started = time.monotonic_ns()
        try:
            if self.process is None: self._start()
            deadline = time.monotonic()+self.timeout
            sent = time.monotonic_ns()
            self.process.stdin.write(b"screencap\n"); self.process.stdin.flush()
            header = self._read_exact(16, deadline)
            width, height, pixel_format, _ = struct.unpack("<IIII", header)
            if (width, height, pixel_format) != (360, 640, 1):
                raise ValueError("Persistent capture requires calibrated Android12 360x640 RGBA8888 layout")
            raw = header+self._read_exact(width*height*4, deadline)
            finished = time.monotonic_ns(); png, info = self.decode(raw)
            self.last_capture = {"transport_started_ns": started, "request_sent_ns": sent,
                "transport_finished_ns": finished, "host_encoded_ns": time.monotonic_ns(),
                "session_started_ns": self.session_started_ns, "info": info,
                "source": "persistent Android shell, one raw screencap per request; lossless host PNG",
                "stderr_path": self.stderr_path.as_posix()}
            return png
        except BaseException:
            self.poisoned = True; self.close(); raise

    def close(self):
        self.poisoned = True
        if self.process is not None:
            if self.process.stdin and not self.process.stdin.closed:
                try: self.process.stdin.close()
                except BrokenPipeError: pass
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self.process.terminate()
                try: self.process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.process.kill(); self.process.wait(timeout=3)
            if self.process.stdout: self.process.stdout.close()
        if self.error_stream: self.error_stream.close()


class PixelPolicy:
    """Conservative diagnostic controller; explicitly not a trained imitator."""
    def decide(self, observation):
        if observation.get("source") != "screenshot_pixels_only":
            raise ValueError("PixelPolicy accepts screenshot observations only")
        if observation.get("scene_ready") is False:
            return {"kind": "wait", "reason": "battle_hud_not_confirmed"}
        elixir = observation["elixir"].get("lower_bound")
        cards = [c for c in observation["hand"] if c["known"] and c["visually_ready"]
                 and elixir is not None and c["cost"] <= elixir]
        threats = [t for t in observation["tracks"] if t["visible"] and t["side"] == "enemy"
                   and t["xy_native_estimate"][1] > 14000]
        if not cards:
            return {"kind": "wait", "reason": "hand_or_elixir_unknown_or_unaffordable"}
        if not threats and elixir < 7:
            return {"kind": "wait", "reason": "reserve_from_visible_hud"}
        candidates = [c for c in cards if c["card_id"] < 28000000]
        if not candidates:
            return {"kind": "wait", "reason": "no_confident_troop_or_building"}
        card = min(candidates, key=lambda c: (c["cost"], c["slot"]))
        x = threats[0]["xy_native_estimate"][0] if threats else 3500
        x = int(max(1500, min(16500, x)))
        return {"kind": "play_card", "slot": card["slot"], "card": card["card_id"],
                "x": 8500 if card["card_id"] == 27000000 else x, "y": 21000,
                "reason": "visible_intrusion" if threats else "visible_elixir_pressure"}


def visual_consistency(before, after, action):
    if action["kind"] != "play_card": return None
    slot = action["slot"]
    old = next((c for c in before["hand"] if c["slot"] == slot), None)
    new = next((c for c in after["hand"] if c["slot"] == slot), None)
    changed = (old["card_id"] != new["card_id"]) if old and new and old["known"] and new["known"] else None
    a, b = before["elixir"].get("value"), after["elixir"].get("value")
    spent = (a-b > .5) if a is not None and b is not None else None
    return {"hand_slot_changed": changed, "elixir_decreased": spent,
            "consistent": changed is True and spent is True,
            "proof_of_consumption": False, "reason": "pixels are supporting evidence; independent native receipt retained"}


def native_level_audit(configuration, state):
    """Record actual native values without inventing a level decoder.

    cr-minimal.v4 does not export card/entity levels. HP and the packed card
    parameter remain raw evidence, never a claimed independent level readback.
    This archive-only function is not part of the screenshot actor interface.
    """
    battle = configuration["battle"]
    return {"requested_card_level": battle["lvlcap"], "requested_minimum_card_level": battle["cardlvlmin"],
        "requested_king_levels": [x["kt"] for x in battle["hbd"]],
        "configuration_accepted": True, "actual_levels_verified": False, "actual_levels": None,
        "reason": "cr-minimal.v4 has no independently validated level field/decoder",
        "state_tick": state["tick"], "native_hand_rows": [
            {"owner": p["owner"], "hand": deepcopy(p["hand"])} for p in state["players"]],
        "native_entity_hp_rows": [{"id": e[0], "owner": e[1], "card_id": e[2], "hp": e[5], "max_hp": e[6]}
                                  for e in state["entities"]]}


def validate_controller_timing(controller, step_ticks):
    """A recurrent elapsed-time input does not change its trained action horizon."""
    timing = getattr(getattr(controller, "model", None), "timing_config", None)
    if timing is None or timing.tick_hz != 20:
        raise ValueError("Native checkpoint deployment requires trained tick_hz=20")
    if type(step_ticks) is not int or step_ticks != timing.decision_ticks:
        raise ValueError(f"Device step_ticks={step_ticks} differs from trained decision_ticks={timing.decision_ticks}; "
                         "explicitly use the checkpoint horizon; variable-horizon deployment is unverified")


def run_native(args, policy=None):
    from royale_phase2.backend import NativeBattleBackend
    from royale_phase2.cloud_frames import CloudAndroidBridge
    from royale_phase3.actions import PublicTeacher, match
    from royale_phase3.common import native_lock
    root = Path(args.output); root.mkdir(parents=True, exist_ok=False)
    calibration = ScreenCalibration()
    detector = UnitDetector(args.assets, calibration, threads=1)
    hand = HandRecognizer({k: Path(args.assets)/"cards"/(v[0]+".jpg") for k, v in CARDS.items()}, calibration)
    if policy is None and getattr(args, "checkpoint", None):
        from .pixel_policy import PixelModelController
        policy = PixelModelController(args.checkpoint)
    if getattr(args, "checkpoint", None) or hasattr(policy, "model"):
        validate_controller_timing(policy, args.step_ticks)
    observer = PixelObserver(detector, hand); policy = policy or PixelPolicy(); ledger = ActionLedger(120)
    if args.capture_format == "raw-persistent":
        bridge = PersistentRawAndroidCapture(root/"capture-session.stderr.log")
    else:
        bridge = RawAndroidCapture() if args.capture_format == "raw" else CloudAndroidBridge(timeout=90)
    report = {"schema": "arena-device-run.v1", "status": "running", "actuator": "offline_native_rpc",
        "official_live_client": False, "physical_device": False, "runtime": "Android12_QEMU_TCG",
        "policy": type(policy).__name__, "policy_metadata": getattr(policy, "metadata", {}),
        "trained_imitation": bool(getattr(policy, "metadata", {}).get("trained", False)),
        "render_freshness_verified": False, "real_time_closed_loop_passed": False,
        "model_license": detector.license, "frames": 0, "requests": 0, "consumed": 0,
        "configuration": vars(args), "calibration": asdict(calibration),
        "actual_levels_verified": False, "state_render_alignment_verified": False}
    write(root/"report.json", report)
    started = time.monotonic(); captures = []; observations = []; requests = []; engine = None
    try:
        with native_lock():
            engine = NativeBattleBackend(rendered=True, connection_config=args.connection,
                                        trace_path=root/"wire.jsonl", timeout=120)
            configuration = match(args.seed)
            configuration["battle"]["lvlcap"] = args.level
            configuration["battle"]["cardlvlmin"] = args.level
            for seat in (0, 1):
                configuration["battle"][f"avatar{seat}"]["expLevel"] = args.level
                configuration["battle"]["hbd"][seat]["kt"] = args.level
            write(root/"initialization.json", configuration)
            state = engine.reset(configuration)
            write(root/"level-audit.json", native_level_audit(configuration, state))
            opponent = PublicTeacher("cycle", args.seed+1)
            version = engine.export_journal()["version_fingerprint"]

            def capture(elapsed_control_ticks=0):
                nonlocal state
                if args.frame_wait: time.sleep(args.frame_wait)
                before = deepcopy(engine.debug_state())
                begin = time.monotonic_ns(); png = bridge.capture(); finish = time.monotonic_ns()
                after = engine._capture(before["generation"], before["next"])
                if (before["tick"], before["generation"]) != (after["tick"], after["generation"]):
                    raise RuntimeError("State changed during capture; reject pairing")
                i = len(captures); fp = root/"frames"/f"{i:06d}.png"; fp.parent.mkdir(exist_ok=True); fp.write_bytes(png)
                sp = root/"states"/f"{i:06d}.json"; write(sp, before)
                row = {"frame_path": fp.as_posix(), "frame_sha256": hashlib.sha256(png).hexdigest(),
                    "capture_started_ns": begin, "capture_finished_ns": finish,
                    "state_tick_before": before["tick"], "state_tick_after": after["tick"],
                    "generation_before": before["generation"], "generation_after": after["generation"],
                    "state_path": sp.as_posix(), "state_sha256": hashlib.sha256(sp.read_bytes()).hexdigest(),
                    "version_fingerprint": version, "rendered_state_tick": None, "render_latency_bound_ms": None,
                    "source": "android_framebuffer", "capture_ms": (finish-begin)/1e6,
                    "raw_transport": deepcopy(getattr(bridge, "last_capture", None))}
                append(root/"captures.jsonl", row); captures.append(row)
                # Keep measured wall latency separate from our confirmed game
                # advance. A paused TCG screenshot can take many wall seconds.
                observe_start = time.monotonic_ns()
                feedback = {"elapsed_ticks": elapsed_control_ticks, "tick_hz": 20,
                            "provenance": "confirmed_local_step"}
                obs = observer.observe(png, finish/1e9, control_feedback=feedback)
                obs["capture_finished_ns"] = finish
                obs.update(observe_started_ns=observe_start, observe_finished_ns=time.monotonic_ns())
                append(root/"observations.jsonl", obs); observations.append(obs)
                state = after; return obs

            observation = capture()
            for iteration in range(args.decisions):
                if state["finalized"]: break
                decide_start = time.monotonic_ns(); action = policy.decide(observation)
                decide_finish = time.monotonic_ns(); request = None
                if action["kind"] == "play_card":
                    request = ledger.request(action, observation["capture_finished_ns"], time.monotonic_ns(),
                                             max_age_ms=args.max_age_ms)
                    append(root/"requests.jsonl", request); requests.append(request)
                    receipt = engine.schedule_card(1, action["card"], action["x"], action["y"], state["tick"]+1,
                                                   request_id=request["request_id"])
                    ledger.acknowledge(request["request_id"], time.monotonic_ns(), True)
                    append(root/"acknowledgements.jsonl", {**deepcopy(ledger.pending), "schedule_receipt": receipt})
                # Opponent's public-state controller is separate from screenshot actor.
                other = opponent.decide(engine.observe_visible(0))
                if other["kind"] == "play_card":
                    engine.schedule_card(0, other["card"], other["x"], other["y"], state["tick"]+1,
                                         request_id="opponent:"+str(uuid.uuid4()))
                before_tick = state["tick"]; advance_start = time.monotonic_ns()
                state = engine.advance_exact_ticks(1)
                if request:
                    feedback = engine.execution_feedback([request["request_id"]])
                    ledger.consume(request["request_id"], time.monotonic_ns(),
                                   feedback[0].get("card_consumed") if feedback else None, feedback)
                    append(root/"consumption.jsonl", deepcopy(ledger.pending))
                if not state["finalized"] and args.step_ticks > 1:
                    state = engine.advance_exact_ticks(args.step_ticks-1)
                advance_finish = time.monotonic_ns()
                # This is the receipt for this controller's own exact advance,
                # not a hidden world clock input. Terminal may shorten a step.
                completed_control_ticks = state["tick"]-before_tick
                if not 0 <= completed_control_ticks <= args.step_ticks:
                    raise RuntimeError("Native step receipt outside requested advance budget")
                next_observation = capture(completed_control_ticks)
                if request:
                    ledger.visual(request["request_id"], time.monotonic_ns(), visual_consistency(observation, next_observation, action))
                    append(root/"actions.jsonl", ledger.history[-1])
                append(root/"decisions.jsonl", {"iteration": iteration, "before_tick": before_tick,
                    "after_tick": state["tick"], "action": action, "observation_frame": observation["frame_sha256"],
                    "decide_started_ns": decide_start, "decide_finished_ns": decide_finish,
                    "decision_ms": (decide_finish-decide_start)/1e6,
                    "advance_started_ns": advance_start, "advance_finished_ns": advance_finish,
                    "advance_ms": (advance_finish-advance_start)/1e6})
                observation = next_observation
                report.update(frames=len(captures), requests=len(requests),
                              consumed=sum(r["consumed"] is True for r in ledger.history), end_tick=state["tick"])
                write(root/"report.json", report)
                print(json.dumps({"iteration": iteration, "tick": state["tick"], "action": action,
                                  "frames": len(captures), "consumed": report["consumed"]}), flush=True)
            write(root/"journal.json", engine.export_journal())
            write(root/"result.json", engine.result())
            report.update(status="completed", natural_terminal=bool(state["finalized"]),
                          result=state["result"], version_fingerprint=version)
    except Exception as exc:
        report.update(status="failed", error=type(exc).__name__+": "+str(exc))
        if ledger.pending: write(root/"unresolved-action.json", ledger.pending)
        raise
    finally:
        if engine:
            try: engine.close()
            except Exception as exc:
                report.update(close_error=type(exc).__name__+": "+str(exc))
        if hasattr(bridge, "close"):
            try: bridge.close()
            except Exception as exc: report.update(capture_close_error=type(exc).__name__+": "+str(exc))
        report.update(elapsed_seconds=time.monotonic()-started, frames=len(captures),
                      requests=len(requests), consumed=sum(r["consumed"] is True for r in ledger.history)
                      + int(bool(ledger.pending and ledger.pending["consumed"] is True)),
                      outstanding_action=ledger.pending is not None,
                      capture_ms=[r["capture_ms"] for r in captures], inference_ms=[o["inference_ms"] for o in observations])
        write(root/"report.json", report)


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--connection", required=True); p.add_argument("--output", required=True)
    p.add_argument("--assets", default=".cache/arena-lab/vision")
    p.add_argument("--seed", type=int, default=20261121); p.add_argument("--decisions", type=int, default=12)
    p.add_argument("--level", type=int, choices=range(1, 17), default=16)
    p.add_argument("--step-ticks", type=int, default=100); p.add_argument("--frame-wait", type=float, default=1.1)
    p.add_argument("--max-age-ms", type=float, default=2000)
    p.add_argument("--capture-format", choices=["png", "raw", "raw-persistent"], default="png")
    p.add_argument("--checkpoint", help="Strict masked-pixel-v3 trained checkpoint; omission uses diagnostic rule")
    args = p.parse_args()
    if not 1 <= args.step_ticks <= 200 or not 1 <= args.decisions <= 2000 or not 0 <= args.frame_wait <= 10:
        p.error("Invalid bounded experiment configuration")
    run_native(args)


if __name__ == "__main__": main()

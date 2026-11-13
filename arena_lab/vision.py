"""Pixel-only perception; native state is deliberately not an accepted input.

The optional ONNX models are separately downloaded upstream assets. Their AGPL
model metadata does not inherit this repository's license. Detection confidence
is not a calibrated probability and unknown class/side/HP remains unknown.
"""
from __future__ import annotations

import ast
from dataclasses import asdict, dataclass
import hashlib
import io
import math
from pathlib import Path
import time

import numpy as np

MODEL_SHA = "4cf422370650b1d18647cffc05e02bf75fdc2fbdb1f8ef983f4b8d6f8ea908b3"
SIDE_SHA = "686bc75eae2ddc872b556534aeac8b4f5c6e891df76eef12e9fc91d406ab2583"
CARDS = {26000000: ("knight", 3), 26000001: ("archers", 3),
         26000003: ("giant", 5), 26000014: ("musketeer", 4),
         26000018: ("minipekka", 4), 27000000: ("cannon", 3),
         28000000: ("fireball", 4), 28000001: ("arrows", 3)}


@dataclass(frozen=True)
class ScreenCalibration:
    """Device-specific measured screen coordinates, never silently rescaled."""
    width: int = 360
    height: int = 640
    arena: tuple = (36, 139, 324, 539)
    hand: tuple = ((48, 556, 92, 614), (96, 556, 140, 614),
                   (144, 556, 188, 614), (192, 556, 236, 614))
    elixir: tuple = (29, 618, 345, 626)
    owner: int = 1
    coordinate_error_bound_tiles: float | None = None

    def __post_init__(self):
        if self.owner != 1:
            raise ValueError("This calibrated spectator layout controls bottom/owner 1 only")
        for box in (self.arena, *self.hand, self.elixir):
            if not (0 <= box[0] < box[2] <= self.width and 0 <= box[1] < box[3] <= self.height):
                raise ValueError("Calibration box outside screen")

    def native_xy(self, x, y):
        left, top, right, bottom = self.arena
        return [(x-left)/(right-left)*18000, (y-top)/(bottom-top)*32000]


def _checked(path, expected):
    path = Path(path)
    if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
        raise ValueError("Vision model SHA256 mismatch: " + str(path))
    return path


class UnitDetector:
    def __init__(self, asset_dir, calibration=None, threshold=.3, threads=1):
        import onnxruntime as ort
        self.calibration = calibration or ScreenCalibration()
        self.threshold = float(threshold)
        if not 0 < self.threshold < 1 or threads not in (1, 2):
            raise ValueError("Invalid detector threshold/threads")
        options = ort.SessionOptions()
        options.intra_op_num_threads, options.inter_op_num_threads = threads, 1
        self.units = ort.InferenceSession(str(_checked(Path(asset_dir)/"units_M_480x352.onnx", MODEL_SHA)),
            sess_options=options, providers=["CPUExecutionProvider"])
        self.side = ort.InferenceSession(str(_checked(Path(asset_dir)/"side.onnx", SIDE_SHA)),
            sess_options=options, providers=["CPUExecutionProvider"])
        if self.units.get_inputs()[0].shape != [1, 3, 480, 352]:
            raise ValueError("Unexpected model input interface")
        self.labels = ast.literal_eval(self.units.get_modelmeta().custom_metadata_map["names"])
        self.license = self.units.get_modelmeta().custom_metadata_map.get("license")

    def detect(self, image):
        from PIL import Image
        if image.size != (self.calibration.width, self.calibration.height):
            raise ValueError("Screen resolution changed: explicit recalibration required")
        # Reproduce upstream numerical transforms, then exclude HUD objects.
        crop = image.crop((0, .05*image.height, image.width, .80*image.height))
        ratio = crop.height/crop.width
        h, w = (480, int(480/ratio)) if ratio > 480/352 else (int(352*ratio), 352)
        resized = np.array(crop.resize((w, h)), dtype=np.float16)
        dx, dy = 352-w, 480-h
        left, top = dx-dx//2, dy-dy//2
        a = np.pad(resized, ((top, dy//2), (left, dx//2), (0, 0)), constant_values=114)
        a = a.transpose(2, 0, 1); a /= 255
        pred = self.units.run(None, {self.units.get_inputs()[0].name: a[None]})[0]
        if pred.shape != (1, 300, 6) or not np.isfinite(pred).all():
            raise ValueError("Invalid unit detector output")
        result = []
        for row in pred[0]:
            if row[4] <= self.threshold:
                continue
            box = row[:4].astype(float)
            box[[0, 2]] = (box[[0, 2]]-left)*image.width/w
            box[[1, 3]] = (box[[1, 3]]-top)*image.height/h*.75+.05*image.height
            x, y = (box[0]+box[2])/2, box[3]
            al, at, ar, ab = self.calibration.arena
            if not (al <= x <= ar and at <= y <= ab):
                continue
            box[[0, 2]] = np.clip(box[[0, 2]], 0, image.width)
            box[[1, 3]] = np.clip(box[[1, 3]], 0, image.height)
            if box[2] <= box[0] or box[3] <= box[1]:
                continue
            side_image = image.crop(tuple(box)).resize((16, 16), Image.Resampling.BICUBIC)
            side_tensor = np.array(side_image, dtype=np.float32)[None]/255
            logits = self.side.run(None, {self.side.get_inputs()[0].name: side_tensor})[0].reshape(-1)
            if logits.shape != (2,) or not np.isfinite(logits).all():
                raise ValueError("Invalid side detector output")
            # Upstream outputs are not assumed calibrated; color evidence can
            # independently expose contradictory team assignments.
            side_index = int(np.argmax(logits))
            model_side = ("ally", "enemy")[side_index]
            # Native spectator artwork differs from the model's screenshot
            # domain. Use the visible HP/level marker above the sprite as the
            # primary team evidence and retain NN disagreement for auditing.
            marker_box = (max(0, box[0]-3), max(0, box[1]-16),
                          min(image.width, box[2]+3), min(image.height, box[1]+5))
            marker = np.asarray(image.crop(marker_box), dtype=np.float32)
            r, g, b = marker[:, :, 0], marker[:, :, 1], marker[:, :, 2]
            blue = int(((b > 130) & (b > r*1.25) & (g > r*1.1)).sum())
            red = int(((r > 140) & (r > g*1.35) & (r > b*1.05)).sum())
            side_name = "ally" if blue >= 5 and blue > 1.5*red else "enemy" if red >= 5 and red > 1.5*blue else "unknown"
            result.append({"label": self.labels.get(int(row[5]), "unknown"),
                "confidence": float(row[4]), "bbox": box.tolist(), "side": side_name,
                "side_raw_scores": logits.tolist(), "side_model": model_side,
                "side_color_evidence": {"blue_pixels": blue, "red_pixels": red}, "side_calibrated": False,
                "xy_native_estimate": self.calibration.native_xy(x, y),
                "coordinate_error_bound_tiles": self.calibration.coordinate_error_bound_tiles,
                "hp": None, "hp_known": False})
        return result


def _feature(image):
    from PIL import Image
    a = np.asarray(image.resize((12, 16), Image.Resampling.BILINEAR).convert("L"), dtype=np.float32)
    a = a-a.mean()
    return a / max(1., float(np.linalg.norm(a)))


class HandRecognizer:
    """Template NCC with absolute and nearest-alternative abstention gates."""
    def __init__(self, templates, calibration=None, minimum_similarity=.65, minimum_margin=.06):
        from PIL import Image
        self.calibration = calibration or ScreenCalibration()
        self.minimum_similarity, self.minimum_margin = minimum_similarity, minimum_margin
        self.templates = [(int(card), _feature(Image.open(path).convert("RGB")))
                          for card, path in templates.items()]
        if len(self.templates) < 2 or any(card not in CARDS for card, _ in self.templates):
            raise ValueError("Need at least two supported card templates")

    def recognize(self, image):
        if image.size != (self.calibration.width, self.calibration.height):
            raise ValueError("Explicit screen recalibration required")
        rows = []
        for slot, box in enumerate(self.calibration.hand):
            crop = image.crop(box)
            feature = _feature(crop)
            ranking = sorted([(float(np.sum(feature*v)), card) for card, v in self.templates], reverse=True)
            score, card = ranking[0]; margin = score-ranking[1][0]
            known = score >= self.minimum_similarity and margin >= self.minimum_margin
            rgb = np.asarray(crop, dtype=np.float32)
            ready = float(rgb.std(axis=2).mean()) > 8
            rows.append({"slot": slot, "card_id": card if known else None,
                "similarity": score, "margin": margin, "known": known,
                "visually_ready": ready, "cost": CARDS[card][1] if known else None})
        return rows


def read_elixir(image, calibration):
    """Spectator purple fill, with a conservative lower bound and unknown HUD."""
    a = np.asarray(image.crop(calibration.elixir).convert("RGB"), dtype=np.int16)
    purple = (a[:, :, 0] > 110) & (a[:, :, 2] > 90) & (a[:, :, 0] > a[:, :, 1]*1.25)
    columns = purple.mean(axis=0) > .4
    if not columns.any():
        return {"value": None, "lower_bound": None, "known": False}
    # Require a contiguous filled prefix; disconnected effects are not elixir.
    first = int(np.flatnonzero(columns)[0])
    if first > 4:
        return {"value": None, "lower_bound": None, "known": False}
    # A card UI can overlay a one-pixel separator inside the bar. Require a
    # sustained four-column empty run instead of truncating on that separator.
    empty_run = np.convolve((~columns).astype(np.int8), np.ones(4, dtype=np.int8), mode="valid")
    gaps = np.flatnonzero(empty_run[first:] == 4)
    end = first+int(gaps[0]) if len(gaps) else len(columns)
    value = 10*end/len(columns)
    return {"value": value, "lower_bound": max(0, math.floor(value-.25)), "known": True}


class UnitTracker:
    """Class/side gated global nearest pairs; age is elapsed seconds, not frames.

    Occluded tracks remain predictions and are never silently relabeled visible.
    The association is greedy and reported as such, not claimed identity truth.
    """
    def __init__(self, max_age_seconds=1., gate_pixels=45.):
        if max_age_seconds <= 0 or gate_pixels <= 0:
            raise ValueError("Positive tracking limits required")
        self.max_age, self.gate = max_age_seconds, gate_pixels
        self.reset()

    def reset(self):
        self.tracks = {}; self.next_id = 1; self.last_time = None

    def update(self, detections, timestamp):
        if not math.isfinite(timestamp) or self.last_time is not None and timestamp < self.last_time:
            raise ValueError("Tracker timestamps must not decrease")
        self.last_time = timestamp
        self.tracks = {k: v for k, v in self.tracks.items() if timestamp-v["last_seen"] <= self.max_age}
        candidates = []
        for ident, old in self.tracks.items():
            for i, detection in enumerate(detections):
                if (old["label"], old["side"]) != (detection["label"], detection["side"]):
                    continue
                box = detection["bbox"]; center = np.array([(box[0]+box[2])/2, box[3]])
                dt = timestamp-old["last_seen"]
                distance = float(np.linalg.norm(center-(np.array(old["center"])+np.array(old["velocity"])*dt)))
                if distance <= self.gate:
                    candidates.append((distance, ident, i))
        assigned, used = {}, set()
        for _, ident, i in sorted(candidates):
            if ident not in used and i not in assigned:
                assigned[i] = ident; used.add(ident)
        for i, detection in enumerate(detections):
            ident = assigned.get(i)
            if ident is None:
                ident = self.next_id; self.next_id += 1
            box = detection["bbox"]; center = [(box[0]+box[2])/2, box[3]]
            old = self.tracks.get(ident)
            dt = timestamp-old["last_seen"] if old else 0.
            velocity = ((np.array(center)-old["center"])/dt).tolist() if dt > 0 else old["velocity"] if old else [0., 0.]
            self.tracks[ident] = {**detection, "track_id": ident, "last_seen": timestamp,
                "center": center, "velocity": velocity, "observations": old["observations"]+1 if old else 1}
        return [{**v, "visible": v["last_seen"] == timestamp,
                 "age_seconds": timestamp-v["last_seen"], "association": "class_side_gated_greedy"}
                for _, v in sorted(self.tracks.items())]


class PixelObserver:
    def __init__(self, detector, hand=None):
        self.detector, self.hand = detector, hand
        self.tracker = UnitTracker()
        self.tracking_mode = None
        self.control_seconds = 0.
        self.last_observation_timestamp = None

    def observe(self, png, timestamp, *, control_feedback=None):
        from PIL import Image
        if (not math.isfinite(timestamp) or self.last_observation_timestamp is not None
                and timestamp <= self.last_observation_timestamp):
            raise ValueError("Capture timestamp must strictly increase; never apply the same control feedback twice")
        if control_feedback is not None and not (
                control_feedback.get("provenance") == "confirmed_local_step"
                and control_feedback.get("tick_hz") == 20
                and type(control_feedback.get("elapsed_ticks")) is int
                and 0 <= control_feedback["elapsed_ticks"] <= 200):
            raise ValueError("Tracking requires this controller's bounded confirmed 20 Hz step receipt")
        started = time.monotonic_ns()
        with Image.open(io.BytesIO(png)) as im:
            im = im.convert("RGB")
            detections = self.detector.detect(im)
            hand = self.hand.recognize(im) if self.hand else []
            elixir = read_elixir(im, self.detector.calibration)
        scene_ready = elixir["known"] and any(c["known"] for c in hand)
        self.last_observation_timestamp = timestamp
        mode = "confirmed_local_step" if control_feedback is not None else "wall_time_unconfirmed_game_clock"
        reset_reason = None
        if mode != self.tracking_mode or control_feedback is None:
            reset_reason = "time_source_changed" if mode != self.tracking_mode else "no_confirmed_step_receipt"
            self.tracker.reset(); self.control_seconds = 0.
        self.tracking_mode = mode
        if control_feedback is not None:
            self.control_seconds += control_feedback["elapsed_ticks"]/20
        tracking_timestamp = self.control_seconds if control_feedback is not None else timestamp
        return {"schema": "arena-pixel-observation.v1", "source": "screenshot_pixels_only",
            "frame_sha256": hashlib.sha256(png).hexdigest(), "observation_timestamp": timestamp,
            "control_feedback": dict(control_feedback) if control_feedback is not None else {},
            "tracking_clock": mode, "tracking_timestamp": tracking_timestamp,
            "tracking_reset_reason": reset_reason, "render_freshness_verified": False,
            "scene_ready": scene_ready, "raw_detections": detections,
            "detections": detections if scene_ready else [],
            "tracks": self.tracker.update(detections if scene_ready else [], tracking_timestamp),
            "hand": hand, "elixir": elixir, "tower_hp": None, "opponent_hand": None,
            "opponent_elixir": None, "inference_ms": (time.monotonic_ns()-started)/1e6,
            "calibration": asdict(self.detector.calibration)}

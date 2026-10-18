"""Calibrated UI templates + digit OCR + pluggable unit detector.

Synthetic marker detection is a test fixture only. Real screenshots require a
locally supplied Clash Royale YOLO model and explicit class/team mapping.
"""
from dataclasses import replace
import json
import math
import os
from pathlib import Path
from .cards import CARDS
from .controller import ScreenLayout
from .paths import ROOT, inside
from .simulator import Arena
from .state import Entity, Observation

class PerceptionError(ValueError):
    pass

def vision_libs():
    try:
        import cv2
        import numpy as np
        return cv2, np
    except ImportError as error:
        raise PerceptionError("Vision needs local .venv NumPy and OpenCV; see README") from error

def read_image(path):
    cv2,np = vision_libs()
    # imread on Windows can fail on non-ASCII paths. Decode bytes instead.
    image = cv2.imdecode(np.frombuffer(inside(path).read_bytes(), dtype=np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise PerceptionError(f"Cannot decode {path}")
    return image

def write_image(path, image):
    cv2,_ = vision_libs()
    from .paths import output_path
    ok, blob = cv2.imencode(".png", image)
    if not ok:
        raise PerceptionError("Cannot encode PNG")
    output_path(path).write_bytes(blob.tobytes())

def crop(image, roi):
    x1,y1,x2,y2 = roi
    if not (0 <= x1 < x2 <= image.shape[1] and 0 <= y1 < y2 <= image.shape[0]):
        raise PerceptionError("ROI outside image")
    return image[y1:y2, x1:x2]

def glyphs(image):
    cv2,np = vision_libs()
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if len(image.shape) == 3 else image
    _, binary = cv2.threshold(gray, 180, 255, cv2.THRESH_BINARY)
    count, labels, stats, _ = cv2.connectedComponentsWithStats(binary)
    boxes = sorted([s for s in stats[1:] if s[4] >= 6 and s[3] >= 5], key=lambda s: s[0])
    return [cv2.resize(binary[y:y+h,x:x+w], (20,32), interpolation=cv2.INTER_NEAREST)
            for x,y,w,h,area in boxes]

class DigitReader:
    def __init__(self, directory):
        self.templates = {}
        for digit in range(10):
            gs = glyphs(read_image(inside(directory)/f"{digit}.png"))
            if len(gs) != 1:
                raise PerceptionError("Digit templates need one isolated white digit on dark background")
            self.templates[digit] = gs[0]

    def read(self, image):
        _,np = vision_libs()
        gs = glyphs(image)
        if not gs or len(gs) > 5:
            raise PerceptionError("Numeric ROI has no readable digits")
        text, scores = "", []
        for g in gs:
            errors = [(float(np.mean(g != t)), d) for d,t in self.templates.items()]
            errors.sort()
            error,d = errors[0]
            confidence = 1-error
            if confidence < .80 or errors[1][0]-error < .015:
                raise PerceptionError("Ambiguous numeric glyph; recalibrate font/ROI")
            text += str(d)
            scores.append(confidence)
        return int(text), min(scores)

class UnitTracker:
    def __init__(self):
        self.previous = []
        self.next_id = 1
        self.time = 0

    def update(self, detections, time):
        units, events = [], []
        unused = self.previous.copy()
        for detection in sorted(detections, key=lambda u: (u.card,u.team,u.x,u.y)):
            matches = [p for p in unused if p.card == detection.card and p.team == detection.team
                       and math.hypot(p.x-detection.x,p.y-detection.y) < max(2.5, (time-self.time)*2)]
            if matches:
                old = min(matches, key=lambda p: math.hypot(p.x-detection.x,p.y-detection.y))
                unused.remove(old)
                detection = replace(detection, id=old.id)
            else:
                detection = replace(detection, id=self.next_id)
                self.next_id += 1
                if detection.team == 1:
                    # New track may just be an old unit becoming visible: weak evidence.
                    events.append({"id":f"track_{detection.id}","kind":"seen","team":1,
                                   "card":detection.card,"time":time,"evidence":"inferred"})
            units.append(detection)
        self.previous, self.time = units, time
        return units, events

class ScreenObserver:
    def __init__(self, config_path):
        self.config = json.loads(inside(config_path).read_text(encoding="utf-8"))
        self.layout = ScreenLayout.load(config_path)
        self.digits = DigitReader(self.config["digit_templates"])
        self.card_templates = {c:read_image(path) for c,path in self.config["card_templates"].items()}
        self.anchor = read_image(self.config["battle_anchor"]["template"])
        self.tracker = UnitTracker()
        self.model = None
        backend = self.config["units"]["backend"]
        if backend == "yolo":
            model_path = inside(self.config["units"]["model"])
            if not model_path.is_file():
                raise PerceptionError("Local game-specific YOLO weights required")
            os.environ["YOLO_CONFIG_DIR"] = str(ROOT / ".cache" / "ultralytics")
            os.environ["TORCH_HOME"] = str(ROOT / ".cache" / "torch")
            try:
                from ultralytics import YOLO
            except ImportError as error:
                raise PerceptionError("YOLO backend needs a compatible PyTorch/Ultralytics environment") from error
            self.model = YOLO(str(model_path))
        elif backend != "synthetic":
            raise PerceptionError("Unknown unit detector backend")

    def _match(self, sample, template):
        cv2,_ = vision_libs()
        template = cv2.resize(template, (sample.shape[1],sample.shape[0]))
        return float(cv2.matchTemplate(sample,template,cv2.TM_CCOEFF_NORMED)[0,0])

    def _units(self, image):
        cv2,np = vision_libs()
        x1,y1,x2,y2 = self.layout.arena
        detected, confidence = [], 1.0
        if self.model:
            result = self.model.predict(image, conf=self.config["units"].get("confidence",.5), verbose=False)[0]
            mapping = self.config["units"]["classes"]
            for box in result.boxes:
                name = result.names[int(box.cls.item())]
                if name not in mapping:
                    # Unknown class is uncertainty, not silently an empty board.
                    confidence = min(confidence, .5)
                    continue
                spec = mapping[name]
                bx1,by1,bx2,by2 = box.xyxy[0].tolist()
                px,py = (bx1+bx2)/2, by2  # Ground contact point; flying units need calibration.
                if not x1 <= px <= x2 or not y1 <= py <= y2:
                    continue
                cid,team = spec["card"],spec["team"]
                if cid not in CARDS or team not in (0,1):
                    raise PerceptionError("Invalid YOLO class/team mapping")
                detected.append(Entity(0,cid,team,(px-x1)/(x2-x1)*18,(py-y1)/(y2-y1)*32,CARDS[cid].hp))
                confidence = min(confidence,float(box.conf.item()))
        else:
            region = image[y1:y2,x1:x2]
            for marker in self.config["units"]["markers"]:
                color = np.array(marker["bgr"])
                mask = (np.max(np.abs(region.astype(np.int16)-color),axis=2) < 8).astype(np.uint8)*255
                contours,_ = cv2.findContours(mask,cv2.RETR_EXTERNAL,cv2.CHAIN_APPROX_SIMPLE)
                for contour in contours:
                    if cv2.contourArea(contour) < 15:
                        continue
                    x,y,w,h = cv2.boundingRect(contour)
                    cid = marker["card"]
                    detected.append(Entity(0,cid,marker["team"],(x+w/2)/(x2-x1)*18,
                                           (y+h/2)/(y2-y1)*32,CARDS[cid].hp))
        return detected, confidence

    def observe(self, image_path, time):
        image = read_image(image_path)
        if image.shape[:2] != (self.layout.height,self.layout.width):
            raise PerceptionError("Image resolution differs from calibration")
        anchor_score = self._match(crop(image,self.config["battle_anchor"]["roi"]),self.anchor)
        if anchor_score < self.config.get("template_threshold",.85):
            raise PerceptionError("Battle anchor absent; no game actions allowed")
        hand, scores = [], [anchor_score]
        for roi in self.layout.slots:
            ranked = sorted([(self._match(crop(image,roi),template),cid)
                             for cid,template in self.card_templates.items()], reverse=True)
            value,cid = ranked[0]
            if value < self.config.get("template_threshold",.85) or (len(ranked)>1 and value-ranked[1][0]<.02):
                raise PerceptionError("Unrecognized/ambiguous hand card")
            hand.append(cid)
            scores.append(value)
        elixir,score = self.digits.read(crop(image,self.config["elixir_roi"]))
        scores.append(score)
        towers = Arena(shuffle=False).towers
        for tower in towers:
            spec = self.config.get("towers",{}).get(tower.id,{})
            tower.max_hp = spec.get("max_hp",tower.max_hp)
            tower.x = spec.get("x",tower.x)
            tower.y = spec.get("y",tower.y)
            hp,score = self.digits.read(crop(image,self.config["tower_hp_rois"][tower.id]))
            tower.hp = hp
            scores.append(score)
        for tower in towers:
            if tower.king:
                tower.active = tower.hp < tower.max_hp or any(
                    t.team == tower.team and not t.king and t.hp == 0 for t in towers)
        detections, score = self._units(image)
        scores.append(score)
        units,events = self.tracker.update(detections,time)
        obs = Observation(time,float(elixir),hand,None,units,towers,events,
                          confidence=max(0,min(1,min(scores))), source="screen")
        obs.validate()
        return obs

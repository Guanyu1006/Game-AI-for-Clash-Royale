"""ADB transport, calibration and dry-run action rendering."""
from dataclasses import dataclass
import json
import os
from pathlib import Path
import struct
import subprocess
import time
from .paths import ROOT, inside, output_path
from .state import Action, Observation, legal_action

@dataclass
class ScreenLayout:
    width: int
    height: int
    arena: list[int]
    slots: list[list[int]]

    @classmethod
    def load(cls, path):
        obj = json.loads(inside(path).read_text(encoding="utf-8"))
        return cls(*obj["image_size"], obj["arena"], obj["slots"])

    def __post_init__(self):
        if self.width <= 0 or self.height <= 0 or len(self.slots) != 4:
            raise ValueError("Invalid screen geometry")
        for roi in [self.arena, *self.slots]:
            if len(roi) != 4:
                raise ValueError("ROIs must be [left,top,right,bottom]")
            x1,y1,x2,y2 = roi
            if not (0 <= x1 < x2 <= self.width and 0 <= y1 < y2 <= self.height):
                raise ValueError("ROI outside screenshot")

    def points(self, action: Action):
        if action.is_wait:
            return []
        if not 0 <= action.slot < 4 or not 0 <= action.x <= 18 or not 0 <= action.y <= 32:
            raise ValueError("Invalid action coordinates")
        x1,y1,x2,y2 = self.slots[action.slot]
        ax1,ay1,ax2,ay2 = self.arena
        return [(round((x1+x2)/2), round((y1+y2)/2)),
                (round(ax1+action.x/18*(ax2-ax1)), round(ay1+action.y/32*(ay2-ay1)))]

class ADBController:
    def __init__(self, executable="adb", serial=None, layout=None, execute=False):
        self.executable = executable
        self.serial = serial
        self.layout = layout
        self.execute = execute
        self.last_action = -float("inf")
        self.env = {**os.environ, "ANDROID_USER_HOME":str(ROOT/".cache"/"android"),
                    "ANDROID_SDK_HOME":str(ROOT/".cache"/"android_sdk"),
                    "TEMP":str(ROOT/".tmp"),"TMP":str(ROOT/".tmp")}

    @property
    def base(self):
        return [self.executable] + (["-s", self.serial] if self.serial else [])

    def run(self, args, timeout=10):
        return subprocess.run(self.base+args, env=self.env,capture_output=True,check=True,timeout=timeout).stdout

    def capture(self, path):
        blob = self.run(["exec-out", "screencap", "-p"])
        if not blob.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("ADB did not return PNG; select one online device with --serial")
        if self.layout:
            dims = struct.unpack(">II", blob[16:24])
            if dims != (self.layout.width, self.layout.height):
                raise ValueError(f"Screenshot resolution {dims} differs from calibration")
        output_path(path).write_bytes(blob)
        return inside(path)

    def dispatch(self, obs: Observation, action: Action, observed_at=None):
        if not legal_action(obs, action) or obs.confidence < .75 or not obs.in_battle:
            return {"status": "rejected", "reason": "illegal or uncertain observation"}
        if action.is_wait:
            return {"status": "wait", "commands": []}
        if not self.layout:
            raise ValueError("Calibration required")
        commands = [self.base+["shell", "input", "tap", str(x), str(y)] for x,y in self.layout.points(action)]
        if not self.execute:
            return {"status": "dry_run", "commands": commands}
        now = time.monotonic()
        if observed_at is None or now-observed_at > 1.5:
            return {"status": "rejected", "reason": "observation older than 1.5 seconds"}
        if now-self.last_action < 1.0:
            return {"status": "rejected", "reason": "action cooldown"}
        for command in commands:
            subprocess.run(command, env=self.env,capture_output=True,check=True,timeout=10)
            time.sleep(.1)
        self.last_action = time.monotonic()
        return {"status": "executed", "commands": commands}

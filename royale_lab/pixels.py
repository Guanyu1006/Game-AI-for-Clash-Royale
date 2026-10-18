"""Optional real-frame adapter contract. Missing game HUD fields fail explicitly.

The baseline's synthetic detector is intentionally not imported here. A detector
can wrap downloaded ONNX or KataCR models; its boxes do not establish a full game
observation without calibrated coordinates, card HUD, OCR and temporal tracking.
"""
from typing import Protocol
from .contracts import validate_observation

class GameFrameDetector(Protocol):
    def detect(self,frame):
        """Return detections with card_id/team/x/y/hp/confidence; coordinates calibrated to arena."""
        ...

class PixelAdapter:
    def __init__(self,detector:GameFrameDetector,confidence_threshold=.65):
        self.detector=detector;self.threshold=confidence_threshold;self.events=[]
    def observation(self,frame,hud,geometry):
        required={'time','tick','hand','elixir','own_deck','towers','max_ticks'}
        missing=required-hud.keys()
        if missing:raise ValueError('Real-frame HUD adapter is missing: '+', '.join(sorted(missing)))
        units=[]
        for item in self.detector.detect(frame):
            if item.get('confidence',0)<self.threshold:continue
            fields={'id','card_id','team','x','y','hp'}
            if not fields<=item.keys():raise ValueError('Detector requires tracking, team, coordinate and HP adapters')
            units.append(item)
        observation={**hud,**geometry,'units':units,'events':list(self.events),'source':'screen',
                     'history_complete':False,'confidence':self.threshold}
        validate_observation(observation)
        return observation

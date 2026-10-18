"""Probe PPO resume edge in a separate output sandbox; never replace main checkpoint."""
from __future__ import annotations
import json
from pathlib import Path
import shutil
import sys
import traceback

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
import royale_lab.learning as learning

OUT=ROOT/'outputs/audit_candidates/ppo_audit'
SANDBOX=OUT/'resume_probe'
copied=SANDBOX/'outputs/training/ppo/resume.pt'
copied.parent.mkdir(parents=True,exist_ok=True)
shutil.copy2(ROOT/'outputs/training/ppo/resume.pt',copied)

def isolated(path):
    result=SANDBOX/path
    result.parent.mkdir(parents=True,exist_ok=True)
    return result

learning.target=isolated
learning.local=isolated
state=learning.torch.load(copied,map_location='cpu',weights_only=True)
row={'scope':'isolated output-directory probe; main model and training output unchanged',
     'checkpoint_steps':state['steps'],'requested_total_steps':state['steps'],
     'checkpoint_keys':list(state),'expected':'resume with no additional budget should return valid existing status or explicit explanatory result'}
try:
    result=learning.train_ppo(total_steps=state['steps'],seed=71,resume=True,max_ticks=100)
    row.update(status='passed',result=result)
except Exception as e:
    row.update(status='failed',error=repr(e),traceback=traceback.format_exc())
(OUT/'resume_probe_report.json').write_text(json.dumps(row,indent=2),encoding='utf-8')
print(json.dumps(row,indent=2))

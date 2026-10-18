"""All phase-two mutable paths are contained in the project."""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

def local(path):
    p = (ROOT/Path(path)).resolve()
    try:p.relative_to(ROOT)
    except ValueError:raise ValueError(f'Path escapes project: {path}') from None
    return p

def target(path):
    p = local(path);p.parent.mkdir(parents=True,exist_ok=True)
    return p

def configure():
    paths = {'TEMP':'.tmp/phase2','TMP':'.tmp/phase2','PIP_CACHE_DIR':'.cache/phase2/pip',
             'HF_HOME':'.cache/phase2/hf','HF_HUB_CACHE':'.cache/phase2/hf/hub',
             'TORCH_HOME':'.cache/phase2/torch','XDG_CACHE_HOME':'.cache/phase2/xdg',
             'GRADLE_USER_HOME':'.cache/phase2/gradle',
             'ANDROID_USER_HOME':'.cache/phase2/android/user',
             'ANDROID_EMULATOR_HOME':'.cache/phase2/android/emulator',
             'ANDROID_AVD_HOME':'.cache/phase2/android/avd'}
    for key,rel in paths.items():
        p=local(rel);p.mkdir(parents=True,exist_ok=True);os.environ[key]=str(p)
    os.environ['PYTHONUTF8']='1'
    os.environ['PYTHONDONTWRITEBYTECODE']='1'
    sys.dont_write_bytecode = True

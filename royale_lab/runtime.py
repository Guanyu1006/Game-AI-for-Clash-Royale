from pathlib import Path
import os

ROOT = Path(__file__).resolve().parent.parent

def local(path: str | Path) -> Path:
    p = Path(path)
    p = (ROOT/p).resolve() if not p.is_absolute() else p.resolve()
    if not p.is_relative_to(ROOT):
        raise ValueError(f"Project files must stay in {ROOT}: {p}")
    return p

def target(path: str | Path) -> Path:
    p = local(path)
    p.parent.mkdir(parents=True,exist_ok=True)
    return p

def configure():
    for key,sub in {"TEMP":".tmp","TMP":".tmp","PIP_CACHE_DIR":".cache/pip",
                    "TORCH_HOME":".cache/torch","HF_HOME":".cache/huggingface",
                    "YOLO_CONFIG_DIR":".cache/ultralytics","MPLCONFIGDIR":".cache/matplotlib"}.items():
        folder=local(sub)
        folder.mkdir(parents=True,exist_ok=True)
        os.environ[key]=str(folder)

configure()

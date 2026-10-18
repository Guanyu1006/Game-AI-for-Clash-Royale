"""Constrain project-generated files to the user's requested workspace."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

def inside(path: str | Path) -> Path:
    path = Path(path)
    resolved = (ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not resolved.is_relative_to(ROOT):
        raise ValueError(f"Path must stay inside project: {resolved}")
    return resolved

def output_path(path: str | Path) -> Path:
    path = inside(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return path

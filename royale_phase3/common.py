"""Project containment, immutable experiment identities, atomic recoverable records."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def local(path):
    value = (ROOT / path).resolve()
    value.relative_to(ROOT)
    return value

def configure():
    sys.dont_write_bytecode = True
    for name, value in {'TEMP': '.tmp/phase3', 'TMP': '.tmp/phase3',
        'PIP_CACHE_DIR': '.cache/phase3/pip', 'HF_HOME': '.cache/phase3/hf',
        'HF_HUB_CACHE': '.cache/phase3/hf/hub', 'TORCH_HOME': '.cache/phase3/torch',
        'XDG_CACHE_HOME': '.cache/phase3/xdg', 'MPLCONFIGDIR': '.cache/phase3/matplotlib',
        'NUMBA_CACHE_DIR': '.cache/phase3/numba'}.items():
        folder = local(value); folder.mkdir(parents=True, exist_ok=True)
        os.environ[name] = str(folder)
    os.environ['PYTHONUTF8'] = '1'; os.environ['PYTHONDONTWRITEBYTECODE'] = '1'

def now(): return datetime.now(timezone.utc).isoformat()
def canonical(value): return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False).encode('utf-8')
def digest(value): return hashlib.sha256(canonical(value)).hexdigest()
def sha(path):
    h = hashlib.sha256()
    with local(path).open('rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()
def read(path): return json.loads(local(path).read_text(encoding='utf-8-sig'))
def write(path, value):
    path = local(path); path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as f:
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False); f.flush(); os.fsync(f.fileno())
    os.replace(temporary, path)
def append(path, value):
    path = local(path); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('ab') as f: f.write(canonical(value) + b'\n'); f.flush()
def rows(path):
    with local(path).open(encoding='utf-8') as f:
        for line in f:
            if line.strip(): yield json.loads(line)
def reference(path): return {'path': local(path).relative_to(ROOT).as_posix(), 'sha256': sha(path)}

@contextmanager
def native_lock():
    """Cross-process OS lock; survives process death without stale lock deletion."""
    path = local('.tmp/phase3/native.lock'); path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        if os.name == 'nt':
            import msvcrt
            acquire = lambda: msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            release = lambda: msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            acquire = lambda: fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            release = lambda: fcntl.flock(f.fileno(), fcntl.LOCK_UN)
        try: acquire()
        except OSError: raise RuntimeError('Another phase3 controller owns native RPC') from None
        try: yield
        finally: f.seek(0); release()

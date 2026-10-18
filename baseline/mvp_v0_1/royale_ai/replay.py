import json
from pathlib import Path
from .paths import inside,output_path

def render_replay(log_path, output="outputs/replay.html"):
    rows=[json.loads(line) for line in inside(log_path).read_text(encoding="utf-8").splitlines() if line.strip()]
    template=(Path(__file__).parent/"viewer.html").read_text(encoding="utf-8")
    # Escape script terminators so log text cannot execute markup.
    payload=json.dumps(rows,ensure_ascii=False).replace("<","\\u003c")
    dest=output_path(output)
    dest.write_text(template.replace("__REPLAY_DATA__",payload),encoding="utf-8")
    return dest

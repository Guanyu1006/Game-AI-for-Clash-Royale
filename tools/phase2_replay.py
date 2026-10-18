"""Run one immutable real source episode on the actual native backend."""
import argparse
import json
from pathlib import Path
import sys
from datetime import datetime, timezone

ROOT = Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from royale_phase2.reconstruction import reconstruct


def main():
    parser = argparse.ArgumentParser(); parser.add_argument('source_id'); parser.add_argument('--output')
    parser.add_argument('--port', type=int, default=26789); parser.add_argument('--grid-ticks', type=int, default=10)
    args = parser.parse_args()
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    output = args.output or f'outputs/phase2/replays/development/{args.source_id}/{stamp}'
    report = reconstruct(args.source_id, output, args.port, args.grid_ticks)
    print(json.dumps({'output': output, 'quality': report['quality'], 'counts': report['counts'],
                      'failures': report['failures'], 'wall_seconds': report['wall_seconds']}, ensure_ascii=False, indent=2))
    if report['quality']['runtime_replayable'] != 'pass': raise SystemExit(2)


if __name__ == '__main__': main()

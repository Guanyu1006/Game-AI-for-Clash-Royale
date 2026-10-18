"""Drive the actual stock native ReplayBattleController, preserving wire proof."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path
import sys
import time
import traceback

from phase2_native_match_probe import Wire, ROOT


def main():
    p = argparse.ArgumentParser(); p.add_argument('--port', type=int, default=26789)
    p.add_argument('--timeout', type=float, default=20)
    p.add_argument('--output', default='outputs/phase2/native/render/runtime86_first')
    args = p.parse_args(); folder = (ROOT / args.output).resolve()
    if not folder.is_relative_to(ROOT): raise ValueError('Output outside project')
    folder.mkdir(parents=True, exist_ok=True)
    report = {'schema': 'phase2-native-render-probe.v1', 'rendered_match_created': False,
              'actual_play_consumed': False, 'frame_visual_verified': False}
    sys.path.insert(0, str(ROOT / 'third_party/phase2'))
    from jason.engine import RenderedEngine, Play
    wire = None
    try:
        with (folder / 'wire.jsonl').open('w', encoding='utf-8') as log:
            wire = Wire('127.0.0.1', args.port, args.timeout, log)
            engine = RenderedEngine(timeout=args.timeout); engine.request = wire.request
            before = engine.reset(seed=1); report['reset_state'] = asdict(before)
            report['rendered_match_created'] = True
            player = before.players[0]
            hand = next(h for h in player.hand if h.cost * 10000 <= player.elixir)
            action = Play(0, hand.slot, 3000, 12000)
            after = engine.step([action], ticks=30).state
            report.update(actual_play_consumed=any(e.owner == 0 and e.tick == 91 for e in after.plays),
                          state=asdict(after), status=wire.request('status'))
            (folder / 'paused_state.json').write_text(json.dumps(asdict(after), indent=2), encoding='utf-8')
            # Leave the real native scene paused for independent framebuffer inspection.
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        (folder / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        if wire is not None: wire.close()
        (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'rendered_match_created': report['rendered_match_created'],
                          'actual_play_consumed': report['actual_play_consumed'], 'error': report.get('error'),
                          'report': str((folder / 'report.json').relative_to(ROOT))}), flush=True)
    if not report['actual_play_consumed']: raise SystemExit(1)


if __name__ == '__main__': main()

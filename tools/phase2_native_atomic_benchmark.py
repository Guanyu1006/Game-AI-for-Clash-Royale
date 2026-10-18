"""Compare actual two-RPC and atomic native advances on the same scheduled journal."""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT)); sys.path.insert(0, str(ROOT / 'third_party/phase2'))
from royale_phase2.backend import NativeBattleBackend
from jason.engine import standard_match


def physical(state):
    result = deepcopy(state)
    for key in ('generation', 'next'): result.pop(key, None)
    result['plays'] = [event[1:] for event in result['plays']]
    return result


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def main():
    p = argparse.ArgumentParser(); p.add_argument('--steps', type=int, default=120)
    p.add_argument('--ticks', type=int, default=10); p.add_argument('--port', type=int, default=26789)
    p.add_argument('--output', default='outputs/phase2/native/atomic_benchmark')
    args = p.parse_args(); folder = (ROOT / args.output).resolve()
    if not folder.is_relative_to(ROOT) or args.steps < 1 or args.ticks < 1: raise ValueError('Invalid bounds')
    folder.mkdir(parents=True, exist_ok=True); results = {}; traces = {}
    for mode in ('two_rpc', 'atomic'):
        with NativeBattleBackend(port=args.port, advance_mode=mode, trace_path=folder / f'{mode}.wire.jsonl') as native:
            initial = native.reset(standard_match(1)); states = [initial]
            for owner, card, tick, x, y in [(0, 28000001, 91, 1000, 1000), (1, 28000000, 91, 17000, 31000),
                                          (0, 26000000, 291, 3500, 12000), (1, 26000001, 291, 3500, 20000)]:
                native.schedule_card(owner, card, x, y, tick, request_id=f'{owner}:{card}:{tick}')
            request_start = len(native._wire); started = time.perf_counter()
            for _ in range(args.steps):
                state = native.advance_exact_ticks(args.ticks); states.append(state)
                if state['finalized']: break
            elapsed = time.perf_counter() - started
            results[mode] = {'elapsed_seconds': elapsed, 'advances': len(states)-1,
                             'advancing_rpc_count': len(native._wire)-request_start,
                             'native_ticks_per_second': (states[-1]['tick']-states[0]['tick'])/elapsed,
                             'terminal': states[-1]['finalized'], 'physical_sha256': [digest(physical(s)) for s in states]}
            traces[mode] = states
            (folder / f'{mode}.states.jsonl').write_text(''.join(json.dumps(s,separators=(',',':'))+'\n' for s in states), encoding='utf-8')
    equal = results['two_rpc']['physical_sha256'] == results['atomic']['physical_sha256']
    report = {'schema': 'phase2-native-atomic-equivalence.v1', 'passed': equal, 'actual_native_execution': True,
              'scheduled_cards': 4, 'comparison': 'Every cr-minimal.v4 field; generation/next/global event sequence excluded',
              'modes': results, 'speedup': results['two_rpc']['elapsed_seconds']/results['atomic']['elapsed_seconds'],
              'scope': 'One deterministic short native fixture, not a full-game population throughput claim'}
    (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'passed': equal, 'state_points_per_mode': len(traces['atomic']), 'speedup': report['speedup'],
                      'times': {m: r['elapsed_seconds'] for m,r in results.items()}}), flush=True)
    if not equal: raise SystemExit(1)


if __name__ == '__main__': main()

"""Actual native adapter regression; requires the isolated .86 Android runtime."""
import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.backend import NativeBattleBackend, NativeCapabilityUnsupported, NativeStateInvalid


def comparable(state):
    state = deepcopy(state)
    for key in ('generation', 'next'): state.pop(key, None)
    state['plays'] = [play[1:] for play in state['plays']]
    return state


def main():
    p = argparse.ArgumentParser(); p.add_argument('--manifest', default='data/phase2/version_manifest.json')
    p.add_argument('--port', type=int, default=26789)
    p.add_argument('--output', default='outputs/phase2/native/backend_smoke')
    args = p.parse_args(); folder = (ROOT / args.output).resolve()
    if not folder.is_relative_to(ROOT): raise ValueError('Output outside project')
    folder.mkdir(parents=True, exist_ok=True)
    sys.path.insert(0, str(ROOT / 'third_party/phase2'))
    from jason.engine import standard_match
    report = {'schema': 'phase2-native-backend-smoke.v1', 'checks': [], 'passed': False}
    backend = None
    try:
        backend = NativeBattleBackend(args.manifest, port=args.port, trace_path=folder / 'wire.jsonl')
        first = backend.reset(standard_match(1)); assert first['tick'] == 90
        report['checks'].append('actual native reset and warmup90')
        observation = backend.observe_visible(0)
        assert len(observation['towers']) == 6 and 'entities' not in observation and 'opponent_elixir' not in observation
        report['checks'].append('conservative actor own HUD plus six attested public towers')
        hand = next(row for row in first['players'][0]['hand'] if row[2] * 10000 <= first['players'][0]['elixir'])
        backend.schedule_card(0, hand[1], 3000, 12000, 91, request_id='valid-card')
        backend.schedule_card(1, 26999999, 3000, 20000, 91, request_id='not-in-deck')
        backend.schedule_ability(1, ['definitely_absent'], 92, request_id='absent-ability')
        final = backend.advance_exact_ticks(20); assert final['tick'] == 110
        feedback = {row['request_id']: row for row in backend.execution_feedback()}
        assert feedback['valid-card']['card_consumed'] and feedback['valid-card']['actual_execution_tick'] == 91
        assert not feedback['not-in-deck']['native_injected'] and not feedback['not-in-deck']['card_consumed']
        assert feedback['not-in-deck']['scheduler_feedback']['error'] == 'card-unavailable'
        assert not feedback['absent-ability']['native_injected']
        assert feedback['absent-ability']['consumption_status'] == 'ability_consumption_unavailable_in_minimal'
        report['checks'].append('actual native card consumption91; unavailable card and ability rejected at native boundary')
        journal = backend.export_journal(); replay = backend.replay_from_start(journal, end_tick=110)
        assert replay['generation'] != first['generation'] and comparable(replay) == comparable(final)
        report['checks'].append('reset and journal replay reproduce full minimal state excluding generation/event sequence metadata')
        try: backend.schedule_card(0, hand[1], 18000, 12000, 111)
        except ValueError: pass
        else: raise AssertionError('Out-of-bounds x18000 was not rejected locally')
        assert backend.debug_state()['tick'] == 110
        report['checks'].append('exact native coordinate upper bound is exclusive, without advancing time')
        try: backend.snapshot()
        except NativeCapabilityUnsupported: pass
        else: raise AssertionError('Minimal snapshot capability incorrectly advertised')
        report['checks'].append('unsupported native snapshot fails explicitly')
        try: backend._request('a-deliberately-invalid-command')
        except Exception: pass
        else: raise AssertionError('Native invalid command unexpectedly accepted')
        try: backend.advance_exact_ticks(1)
        except NativeStateInvalid: pass
        else: raise AssertionError('Failed native command did not invalidate the episode')
        backend.reset(standard_match(1))
        report['checks'].append('native rejection invalidates episode; explicit reset recovers')
        report.update(passed=True, first_state=first, advanced_state=final, feedback=list(feedback.values()),
                      replay_state=replay, observation=observation, version_fingerprint=backend.version['fingerprint'])
    except Exception as exc:
        report['error'] = f'{type(exc).__name__}: {exc}'
        (folder / 'error.log').write_text(traceback.format_exc(), encoding='utf-8')
    finally:
        if backend is not None: backend.close()
        (folder / 'report.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
        print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']),
                          'error': report.get('error'), 'report': str((folder / 'report.json').relative_to(ROOT))}), flush=True)
    if not report['passed']: raise SystemExit(1)


if __name__ == '__main__': main()

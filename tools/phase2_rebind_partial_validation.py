"""Bind the clean single-window partial-draw run into progressive state.

The earlier controller collision is retained as an archived audit artifact;
only this independently completed run is used by later cohort evidence.
"""
import json
from datetime import datetime, timezone
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.runtime import configure, local
from royale_phase2.versioning import file_hash
from royale_phase2.dataset import write_json


def read(path):
    return json.loads(local(path).read_text(encoding='utf-8'))


def main():
    configure()
    state_path = local('outputs/phase2/progressive_run/state.json')
    state = read(state_path)
    episode_path = local('outputs/phase2/native/backend_partial_live_clean/episode.json')
    report = read(episode_path)
    if report.get('quality', {}).get('runtime_replayable') != 'pass' or report.get('counts', {}).get('executed') != 53:
        raise ValueError('Clean partial validation is not a complete 53-action native run')
    action = read(episode_path.parent/'actions.json')['actions'][26]['feedback']
    if not (action.get('card_consumed') is True
            and action.get('own_boundary_evidence_kind') == 'pending_draw_exact_hand_cycle_cost_parameter_elixir'
            and action.get('card_event') == [6614, 2039, 0, 13000096, 3]):
        raise ValueError('Clean run lacks the locked partial-draw attribution')
    stage = state.get('stages', {}).get('actual_partial_consumption_validation')
    if stage:
        state['stages']['actual_partial_consumption_validation_concurrent'] = stage
    command = [str(ROOT/'.venv-phase2-core/Scripts/python.exe'), 'tools/phase2_replay.py',
               '91e21faee1ae4abbb424e759585bf2f0', '--output',
               'outputs/phase2/native/backend_partial_live_clean', '--grid-ticks', '5']
    log = local('outputs/phase2/progressive_run/actual_partial_consumption_validation_clean.log')
    log.write_text(json.dumps({'command': command, 'validated_utc': datetime.now(timezone.utc).isoformat(),
                               'episode': episode_path.relative_to(ROOT).as_posix(),
                               'episode_sha256': file_hash(episode_path), 'rpc_calls_in_this_rebind': 0},
                              ensure_ascii=False, indent=2)+'\n', encoding='utf-8')
    entry = {'command': command, 'returncode': 0, 'wall_seconds': report.get('wall_seconds'),
             'log': {'path': log.relative_to(ROOT).as_posix(), 'sha256': file_hash(log)},
             'result': {'path': episode_path.relative_to(ROOT).as_posix(), 'sha256': file_hash(episode_path)}}
    state.setdefault('commands', []).append(entry)
    state.setdefault('stages', {})['actual_partial_consumption_validation'] = entry
    state['actual_partial_validation'] = {'episode': entry['result'],
        'actual_event': action['card_event'], 'source_inputs_changed': False,
        'concurrent_attempt_preserved': 'outputs/phase2/native/audits/backend_partial_live_concurrent_attempts'}
    write_json(state_path, state)
    print(json.dumps({'status': 'rebound_clean_validation', 'episode': entry['result'],
                      'preserved_concurrent_stage': 'actual_partial_consumption_validation_concurrent'}, ensure_ascii=False))


if __name__ == '__main__':
    main()

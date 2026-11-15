"""Verify the committed interrupted-device evidence without Android or ML deps."""
from pathlib import Path
import hashlib
import json

root = Path(__file__).resolve().parent
load = lambda name: json.loads((root/name).read_text())
rows = lambda name: [json.loads(line) for line in (root/name).read_text().splitlines()]
package = load('package.json')
for item in package['files']:
    raw = (root/item['path']).read_bytes()
    assert len(raw) == item['bytes'], item['path']
    assert hashlib.sha256(raw).hexdigest() == item['sha256'], item['path']
summary = load('summary.json')['quarter_attempt']
captures, observations, decisions = [rows(name+'.jsonl') for name in ('captures', 'observations', 'decisions')]
assert len(captures) == len(observations) == summary['frames'] == 13
assert len(decisions) == summary['durably_completed_decisions'] == 12 < summary['planned_decisions']
for i, (capture, observation) in enumerate(zip(captures, observations)):
    assert capture['frame_sha256'] == observation['frame_sha256']
    assert capture['state_tick_before'] == capture['state_tick_after'] == 90+10*i
    assert capture['generation_before'] == capture['generation_after'] == 2
    assert observation['control_feedback'] == {'elapsed_ticks': 0 if i == 0 else 10,
        'tick_hz': 20, 'provenance': 'confirmed_local_step'}
    assert capture['rendered_state_tick'] is None and capture['render_latency_bound_ms'] is None
for i, decision in enumerate(decisions):
    assert decision['observation_frame'] == captures[i]['frame_sha256']
    assert (decision['before_tick'], decision['after_tick']) == (90+10*i, 100+10*i)
assert sum(item['scene_ready'] for item in observations) == 7
requests, acknowledgements, consumptions, actions = [rows(name+'.jsonl') for name in (
    'requests', 'acknowledgements', 'consumption', 'actions')]
assert len(requests) == len(acknowledgements) == len(consumptions) == len(actions) == 2
request_ids = {item['request_id'] for item in requests}
assert len(request_ids) == 2
for collection in (acknowledgements, consumptions, actions):
    assert {item['request_id'] for item in collection} == request_ids
for consumption in consumptions:
    assert consumption['consumed'] is True
    receipt, = consumption['consumption_receipt']
    assert receipt['card_consumed'] is True
    assert receipt['matched_by'] == 'unique_native_event_exact_card_id'
    assert receipt['card_event'][1:4] == [receipt['actual_execution_tick'], 1, receipt['card_id']]
    i = (receipt['actual_execution_tick']-91)//10
    assert receipt['actual_execution_tick'] == 91+10*i
    action = decisions[i]['action']
    assert action['kind'] == 'play_card'
    assert observations[i]['hand'][action['slot']]['known'] is True
    assert observations[i]['hand'][action['slot']]['card_id'] == receipt['card_id']
assert sum(item['visual_consistency']['consistent'] for item in actions) == 1
for item in load('summary.json')['manual_image_review']['unmodified_png_copies']:
    assert item['sha256'] == captures[item['frame']]['frame_sha256']
interruption = load('interruption.json')
assert interruption['original_report_status_preserved'] == 'running'
assert interruption['durably_completed_decisions'] < interruption['planned_decisions']
assert summary['completed_24_step_budget'] is False
assert summary['natural_terminal_verified'] is False
assert summary['independent_retest_executed'] is False
assert summary['real_time_verified'] is False
print(f"Verified {len(package['files'])} bound files, 13 frames / 12 of 24 decisions, 2 native consumptions; interrupted scope preserved.")

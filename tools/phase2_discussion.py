"""Read saved project evidence for a beginner's project discussion.

No RPC, VM, training, download, or historical artifact mutation. Optional JSON
output is constrained to this project. Winner totals use agent_env's documented
native result convention: result 0 or 1 is the winning native owner.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def local(value):
    path = (ROOT / value).resolve()
    path.relative_to(ROOT)
    return path


def read(value):
    return json.loads(local(value).read_text(encoding='utf-8-sig'))


def jsonl(value):
    with local(value).open(encoding='utf-8-sig') as stream:
        for line in stream:
            if line.strip():
                yield json.loads(line)


def reference(value):
    path = local(value)
    return {'path': path.relative_to(ROOT).as_posix(),
            'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}


def example(folder):
    decisions = folder + '/game-000/decisions.jsonl'
    feedback = folder + '/game-000/execution_feedback.json'
    states = folder + '/game-000/states.jsonl'
    decision = next(row for row in jsonl(decisions)
                    if row['model_prediction']['kind'] == 'play_card')
    request = next(row['registration']['request_id'] for row in decision['registrations']
                   if row['controller'] == 'model')
    receipt = next(row for row in read(feedback)['actions'] if row['request_id'] == request)
    native_event_found = any(receipt['card_event'] in row['state']['plays'] for row in jsonl(states))
    if not receipt['card_consumed'] or not native_event_found:
        raise ValueError('Example has no confirmed native event in saved raw states')
    return {'actor_before': decision['model_actor'],
            'legal_mask': decision['model_legal_mask']['values'],
            'model_prediction': decision['model_prediction'],
            'registered_at_tick': receipt['registration_receipt']['registeredAtTick'],
            'actual_execution_tick': receipt['actual_execution_tick'],
            'native_injected': receipt['native_injected'],
            'card_consumed': receipt['card_consumed'],
            'native_event': receipt['card_event'],
            'native_event_found_in_raw_states': native_event_found,
            'own_elixir_raw_before': receipt['before_registration']['elixir'],
            'own_elixir_raw_after': receipt['after_execution']['elixir'],
            'after_capture_tick': receipt['after_execution']['tick'],
            'note': 'Elixir difference includes regeneration between capture ticks; event cost is 3.',
            'evidence': [reference(value) for value in (decisions, feedback, states)]}


def summary():
    acceptance = read('outputs/phase2/acceptance.json')
    export = read('outputs/phase2/learning_pipeline/export/export_manifest.json')
    checkpoint = read('outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json')
    game_folder = 'outputs/phase2/native/agent_eval/acceptance-008'
    agent = read(game_folder + '/report.json')
    heldout = read('outputs/phase2/learning_pipeline/heldout_evaluation.json')
    games = agent['games']
    wins = sum(type(game['result']['result']) is int and game['result']['result'] == game['owner'] for game in games)
    losses = sum(type(game['result']['result']) is int and game['result']['result'] in (0, 1)
                 and game['result']['result'] != game['owner'] for game in games)
    distribution = export['before_after_distributions']['hard_e_decisions']
    return {'schema': 'royale-phase2-discussion-summary.v1',
            'read_utc': datetime.now(timezone.utc).isoformat(),
            'scope': 'Readback of existing evidence; not a rerun of A-I or the native game.',
            'acceptance_snapshot': {'generated': acceptance['generated_utc'],
                                    'all_required_passed': acceptance['all_required_passed'],
                                    'gates': {key: value['status'] for key, value in acceptance['checks'].items()}},
            'formal': {'recorded_denominator': acceptance['checks']['C']['formal_denominator'],
                       'quality': acceptance['checks']['C']['quality_dimensions']},
            'learning': {'diagnostic_and_eligible_rows': export['rows'],
                         'hard_e_rows': export['eligible_hard_e'],
                         'source_episodes': checkpoint['training_data']['source_episodes'],
                         'by_split': {split: {'samples': metric['samples'],
                                              'source_episodes': metric['whole_source_episodes'],
                                              'plays': metric['play_labels']}
                                      for split, metric in checkpoint['metrics'].items()},
                         'action_distribution': distribution,
                         'sampled_wait_fraction': distribution['wait'] / export['eligible_hard_e'],
                         'original_checkpoint_heldout': heldout['metrics'],
                         'heldout_scope': 'heldout_evaluation.json evaluates the original model, not the bias-adjusted variant.',
                         'variant_inference_play_bias': checkpoint.get('inference_play_bias'),
                         'variant_metric_scope': 'checkpoint metrics are tensor forward/argmax metrics; metrics() does not apply inference_play_bias.'},
            'native_agent': {'games': len(games), 'natural_terminal': sum(game['natural_terminal'] for game in games),
                             'model_card_consumptions': sum(game['model_card_consumptions'] for game in games),
                             'wins_from_native_owner_result': wins, 'losses_from_native_owner_result': losses,
                             'other_results': len(games)-wins-losses,
                             'counts_toward_G': agent['counts_toward_G'],
                             'fixture': {'spell_first_deck': agent['configuration']['spell_first_deck'],
                                         'selected_seeds': agent.get('selected_seeds')},
                             'claim': 'Engineering closed-loop success; selected fixture, heuristic opponent; not a strength benchmark.'},
            'viewer': {'native_frames': acceptance['checks']['F']['actual_native_frames'],
                       'anchors': len(acceptance['checks']['F']['semantic_anchors'])},
            'native_rpc_connections_by_this_tool': 0,
            'evidence': [reference(value) for value in (
                'outputs/phase2/acceptance.json',
                'outputs/phase2/learning_pipeline/export/export_manifest.json',
                'outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json',
                game_folder + '/report.json',
                'outputs/phase2/learning_pipeline/heldout_evaluation.json')],
            'one_real_action': example(game_folder)}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--example', action='store_true', help='Show one actual saved model action and native receipt')
    parser.add_argument('--write-report', help='Optional project-relative JSON report path')
    args = parser.parse_args()
    report = summary()
    if args.write_report:
        path = local(args.write_report)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    if args.example:
        output = report['one_real_action']
    else:
        output = {key: value for key, value in report.items() if key not in ('one_real_action', 'evidence')}
    print(json.dumps(output, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()

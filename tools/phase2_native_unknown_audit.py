"""Read-only native consumption audit; never constructs a client or sends RPC.

The candidate below is an audit proposal, not a production feedback rewrite.
It writes a separate report and never updates the original replay artifacts.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import difflib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.backend import NativeBattleBackend
from phase2_native_feedback_candidate import _read_locked_elixir_rates, _pending_draw_boundary


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def reference(path):
    return {'path': path.relative_to(ROOT).as_posix(), 'sha256': sha(path)}


def load_lines(path):
    return [json.loads(line) for line in path.read_text(encoding='utf-8').splitlines()]


def partial_slot_proposal(action, candidates, competing, embedded, expected_regen_raw=178):
    """Strict new branch for consumption pending a draw; no event ID guessing."""
    before = action['before_registration']; after = action['after_execution']
    receipt = action['scheduler_feedback']
    selected = [h for h in before['hand'] if h[1] == action['card_id']]
    if len(selected) != 1:
        return False
    selected = selected[0]
    remaining = [h for h in before['hand'] if h[0] != selected[0]]
    return bool(
        action['kind'] == 'play_card' and receipt['state'] == 'succeeded'
        and not embedded and len(competing) == 1 and len(candidates) == 1
        and before['tick'] == action['tick'] - 1 and after['tick'] == action['tick']
        and receipt.get('resolvedCardParameter') == selected[3]
        and receipt.get('resolvedCommandCardId') == action['card_id']
        and candidates[0][4] == selected[2]
        and len(before['hand']) == 4 and len(after['hand']) == 3
        and len({h[0] for h in before['hand']}) == 4
        and len({h[0] for h in after['hand']}) == 3
        and sorted(after['hand']) == sorted(remaining)
        and after['cycle'] == before['cycle'] + [action['card_id']]
        and type(expected_regen_raw) is int and expected_regen_raw >= 0
        and before['elixir'] >= selected[2] * 10000
        and after['elixir'] == min(100000, before['elixir'] - selected[2] * 10000 + expected_regen_raw))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--episode', default='outputs/phase2/batches/formal100-v1/91e21faee1ae4abbb424e759585bf2f0/attempt-01')
    parser.add_argument('--output', default='outputs/phase2/native/audits/91e21fae_partial_consumption.json')
    args = parser.parse_args()
    folder = (ROOT / args.episode).resolve(); output = (ROOT / args.output).resolve()
    if not folder.is_relative_to(ROOT) or not output.is_relative_to(ROOT):
        raise ValueError('All paths must remain in project')
    names = ['actions.json', 'journal.json', 'states.jsonl', 'wire.jsonl', 'episode.json']
    hashes_before = {name: sha(folder / name) for name in names}
    actions = json.loads((folder/'actions.json').read_text(encoding='utf-8'))['actions']
    journal = json.loads((folder/'journal.json').read_text(encoding='utf-8'))
    states = load_lines(folder/'states.jsonl'); wire = load_lines(folder/'wire.jsonl')
    unknown = [row for row in actions if not row['feedback']['card_consumed']]
    if len(unknown) != 1:
        raise ValueError(f'Expected one unknown for this focused audit, got {len(unknown)}')
    row = unknown[0]; action = row['feedback']; owner = action['owner']; tick = action['tick']
    captures = {r['raw_capture']['tick']: r for r in states}
    before_raw = captures[tick-1]['raw_capture']; after_raw = captures[tick]['raw_capture']
    player = lambda c: next(p for p in c['players'] if p['owner'] == owner)
    candidates = [event for c in [r['raw_capture'] for r in states] for event in c['plays']
                  if event[1] == tick and event[2] == owner]
    candidates = list({event[0]: event for event in candidates}.values())
    competing = [r for r in actions if r['kind'] == 'play_card' and r['tick'] == tick and r['owner'] == owner]
    manifest = json.loads((ROOT/'data/phase2/version_manifest.json').read_text(encoding='utf-8'))
    aliases = NativeBattleBackend._read_evolution_aliases(manifest)
    schedules = _read_locked_elixir_rates(manifest)
    alias = aliases.get(action['card_id'])
    embedded = bool(journal['match'].get('cmd'))
    proposal = partial_slot_proposal(action, candidates, competing, embedded)
    candidate_proposal = _pending_draw_boundary(action,action['scheduler_feedback'],candidates,competing,embedded,schedules,journal['match'])
    identity_matches = bool(alias and len(candidates) == 1 and candidates[0][3] == alias['event_card_id'])
    old_other_selected = [h for h in action['before_registration']['hand'] if h[1] == action['card_id']][0]
    replacements = [h for h in action['after_execution']['hand'] if h[0] == old_other_selected[0]]
    native_wire = [dict(x, jsonl_line=i+1) for i, x in enumerate(wire)
                   if x['command'] == 'replay-schedule-status '+str(action['registration_receipt']['sequence'])
                   or (x.get('response',{}).get('schema') == 'cr-minimal.v4'
                       and x['response'].get('tick') in (tick-1, tick, tick+1, 2050, 2060))
                   or x['command'] == f"replay-schedule-card {owner} {action['card_id']} {action['x']} {action['y']} {tick}"]
    final_draw = next((r for r in states if r['raw_capture']['tick'] > tick
                       and len(player(r['raw_capture'])['hand']) == 4), None)
    controls = []
    def negative(name, mutate=None, events=None, competition=None, has_embedded=False):
        copy = deepcopy(action)
        if mutate:
            mutate(copy)
        accepted = partial_slot_proposal(copy, candidates if events is None else events,
                                        competing if competition is None else competition, has_embedded)
        candidate_accepted = _pending_draw_boundary(copy,copy['scheduler_feedback'],candidates if events is None else events,
                            competing if competition is None else competition,has_embedded,schedules,journal['match'])
        controls.append({'name': name, 'independent_case_predicate_accepted': accepted,
                         'candidate_branch_accepted': candidate_accepted, 'status': 'pass' if not accepted and not candidate_accepted else 'fail'})
    negative('scheduler_failure', lambda a: a['scheduler_feedback'].update(state='failed'))
    negative('wrong_packed_parameter', lambda a: a['scheduler_feedback'].update(resolvedCardParameter=0))
    negative('wrong_resolved_root', lambda a: a['scheduler_feedback'].update(resolvedCommandCardId=26000000))
    # These receipt-specific checks are additional to the independent minimal
    # case predicate; validate them directly against the proposed helper.
    negative('wrong_cost', events=[[candidates[0][0], tick, owner, candidates[0][3], 4]])
    negative('one_raw_wrong_elixir', lambda a: a['after_execution'].update(elixir=a['after_execution']['elixir']+1))
    negative('missing_native_card_event', events=[])
    negative('duplicate_native_card_event', events=candidates+candidates)
    negative('two_same_owner_tick_source_commands', competition=competing+competing)
    negative('unrelated_remaining_hand_mutation', lambda a: a['after_execution']['hand'][0].__setitem__(1,26000000))
    negative('wrong_cycle_order', lambda a: a['after_execution']['cycle'].reverse())
    negative('selected_card_not_appended', lambda a: a['after_execution']['cycle'].pop())
    negative('not_exact_before_boundary', lambda a: a['before_registration'].update(tick=tick-2))
    negative('not_exact_after_boundary', lambda a: a['after_execution'].update(tick=tick+1))
    negative('embedded_commands_unattributable', has_embedded=True)
    for field,value in [('owner',1-owner),('cardId',26000000),('executeTick',tick+1),
                        ('registeredAtTick',tick-2),('sequence',-1),('generation',-1)]:
        altered=deepcopy(action);altered['scheduler_feedback'][field]=value
        accepted=_pending_draw_boundary(altered,altered['scheduler_feedback'],candidates,competing,embedded,schedules,journal['match'])
        controls.append({'name':'wrong_receipt_'+field,'candidate_branch_accepted':accepted,'status':'pass' if not accepted else 'fail'})
    for mode in [None,72000000,72000007]:
        altered=deepcopy(journal['match']);altered['battle']['gamemode']=mode
        accepted=_pending_draw_boundary(action,action['scheduler_feedback'],candidates,competing,embedded,schedules,altered)
        controls.append({'name':'unmeasured_mode_'+str(mode),'candidate_branch_accepted':accepted,'status':'pass' if not accepted else 'fail'})
    for boundary in [2400,4800]:
        altered=deepcopy(action);altered['tick']=boundary
        altered['before_registration']['tick']=boundary-1;altered['after_execution']['tick']=boundary
        altered['registration_receipt'].update(registeredAtTick=boundary-1,executeTick=boundary)
        altered['scheduler_feedback'].update(registeredAtTick=boundary-1,queuedAtTick=boundary-1,executeTick=boundary)
        event=deepcopy(candidates);event[0][1]=boundary
        accepted=_pending_draw_boundary(altered,altered['scheduler_feedback'],event,[altered],embedded,schedules,journal['match'])
        controls.append({'name':'unmeasured_rate_switch_tick_'+str(boundary),'candidate_branch_accepted':accepted,'status':'pass' if not accepted else 'fail'})
    assert len(candidates) == 1 and proposal and candidate_proposal and identity_matches
    assert all(c['status'] == 'pass' for c in controls)
    assert hashes_before == {name: sha(folder/name) for name in names}
    backend_path = ROOT/'royale_phase2/backend.py'
    original_backend = backend_path.read_text(encoding='utf-8')
    helper_path = ROOT/'tools/phase2_native_feedback_candidate.py'
    helper_text = helper_path.read_text(encoding='utf-8')
    helpers = helper_text[helper_text.index('def _read_locked_elixir_rates'):]
    candidate_backend = original_backend.replace('class NativeBattleBackend:',helpers+'\n\nclass NativeBattleBackend:',1)
    candidate_backend = candidate_backend.replace('        self._form_aliases = self._read_evolution_aliases(manifest)',
                          '        self._form_aliases = self._read_evolution_aliases(manifest)\n        self._elixir_rates = _read_locked_elixir_rates(manifest)',1)
    candidate_backend = candidate_backend.replace('            alias_match = (len(candidates) == 1',
                          '            pending_draw_evidence = _pending_draw_boundary(\n'
                          '                action, receipt, candidates, competing, embedded_ambiguous, self._elixir_rates, self._match)\n'
                          '            own_boundary_evidence = own_boundary_evidence or pending_draw_evidence\n'
                          '            alias_match = (len(candidates) == 1',1)
    candidate_backend = candidate_backend.replace("                         'own_boundary_consumption_evidence': own_boundary_evidence,",
                          "                         'own_boundary_consumption_evidence': own_boundary_evidence,\n"
                          "                         'own_boundary_evidence_kind': ('pending_draw_exact_hand_cycle_cost_parameter_elixir' if pending_draw_evidence else\n"
                          "                             'same_slot_replacement' if own_boundary_evidence else None),\n"
                          "                         'own_boundary_evidence_version': 'strict-partial-draw.v1',",1)
    compile(candidate_backend,str(output.parent/'backend.partial-draw.candidate.py'),'exec')
    patch_path = output.parent/'backend.partial-draw.candidate.patch'
    patch_path.parent.mkdir(parents=True,exist_ok=True)
    patch_path.write_text(''.join(difflib.unified_diff(original_backend.splitlines(keepends=True),candidate_backend.splitlines(keepends=True),
                          fromfile='a/royale_phase2/backend.py',tofile='b/royale_phase2/backend.py')),encoding='utf-8')
    candidate_path = output.parent/'backend.partial-draw.candidate.py'
    candidate_path.write_text(candidate_backend,encoding='utf-8')
    namespace={'__name__':'royale_phase2._uninstalled_feedback_candidate','__package__':'royale_phase2'}
    exec(compile(candidate_backend,str(candidate_path),'exec'),namespace)
    def offline_feedback(cls):
        # __new__ skips __init__, ProbeClient and runtime. _request reads only
        # the already frozen wire receipts, and fails for any other command.
        obj=object.__new__(cls);obj._valid=True;obj._state=states[-1]['raw_capture']
        obj._match=deepcopy(journal['match']);obj._journal=[deepcopy(r['feedback']) for r in actions]
        obj._plays={event[0]:event for r in states for event in r['raw_capture']['plays']}
        obj._form_aliases=aliases;obj._elixir_rates=schedules
        responses={r['command']:r['response'] for r in wire if r['command'].startswith('replay-schedule-status ')}
        obj._request=lambda command: deepcopy(responses[command])
        return obj.execution_feedback()
    old_feedback=offline_feedback(NativeBattleBackend)
    candidate_feedback=offline_feedback(namespace['NativeBattleBackend'])
    assert [r['card_consumed']for r in old_feedback]==[r['feedback']['card_consumed']for r in actions]
    assert sum(bool(r['card_consumed'])for r in old_feedback)==52
    assert sum(bool(r['card_consumed'])for r in candidate_feedback)==53
    changed=[{'request_id':new['request_id'],'old_card_consumed':old['card_consumed'],
              'new_card_consumed':new['card_consumed'],'matched_by':new['matched_by'],
              'effective_form_identity':new['effective_form_identity'],
              'own_boundary_evidence_kind':new['own_boundary_evidence_kind']}
             for old,new in zip(old_feedback,candidate_feedback) if old['card_consumed']!=new['card_consumed']]
    report = {
        'schema': 'royale-phase2-native-consumption-readonly-audit.v1',
        'rpc_calls': 0, 'backend_modified': False, 'original_episode_modified': False,
        'source_episode_id': row['request_id'].rsplit(':',1)[0],
        'original_result_preserved': {'source_commands': len(actions), 'confirmed': sum(bool(a['feedback']['card_consumed']) for a in actions),
                                      'unknown': len(unknown), 'quality': json.loads((folder/'episode.json').read_text(encoding='utf-8'))['quality']},
        'evidence_files': [reference(folder/name) for name in names],
        'backend_reference': reference(ROOT/'royale_phase2/backend.py'),
        'audit_tool_reference': reference(Path(__file__)),
        'candidate_helper_reference': reference(helper_path),
        'candidate_patch_reference': reference(patch_path),
        'candidate_backend_reference': reference(candidate_path),
        'timeline_sources': [reference(ROOT/'third_party/phase2/runtime-assets/full-update/csv_logic'/name)
                             for name in ('game_modes.csv','battle_timelines.csv')],
        'feedback_field_contract': {'existing_fields_preserved':True,
            'own_boundary_evidence_kind': ['pending_draw_exact_hand_cycle_cost_parameter_elixir','same_slot_replacement',None],
            'own_boundary_evidence_version':'strict-partial-draw.v1',
            'independent_verification_inputs':'original before_registration / after_execution / scheduler_feedback / actual CardPlay and pinned sources; never rely solely on the boolean'},
        'frozen_trace_only_candidate_execution': {'network_client_constructed':False,'rpc_calls':0,
            'old_backend_reproduces_original_52_of_53':True,'candidate_53_of_53':True,'changed':changed,
            'fresh_native_execution':False},
        'action': row, 'unique_actual_card_event': candidates[0], 'evolution_alias': alias,
        'wire_evidence': native_wire,
        'checks': {
            'single_same_owner_tick_source_command': len(competing)==1,
            'single_same_owner_tick_native_card_play': len(candidates)==1,
            'no_embedded_commands': not embedded,
            'exact_receipt_parameter': action['scheduler_feedback']['resolvedCardParameter']==old_other_selected[3],
            'exact_native_event_cost': candidates[0][4]==old_other_selected[2],
            'explicit_name_join_evolution_identity': identity_matches,
            'raw_before_matches_recorded': NativeBattleBackend._own_capture(before_raw,owner)==action['before_registration'],
            'raw_after_matches_recorded': NativeBattleBackend._own_capture(after_raw,owner)==action['after_execution'],
            'selected_slot_missing': not replacements,
            'strict_partial_branch': proposal,
            'candidate_source_timeline_partial_branch': candidate_proposal,
            'source_timeline_segments_for_mode': schedules[journal['match']['battle']['gamemode']],
            'other_owner_one_tick_regeneration': after_raw['players'][1-owner]['elixir']-before_raw['players'][1-owner]['elixir'],
            'elixir_exact_deduction_plus_one_tick_regeneration': player(after_raw)['elixir']==player(before_raw)['elixir']-30000+178,
            'evolution_runtime_cycle_before': [e for e in player(before_raw)['evolution'] if e[1]==action['card_id']],
            'evolution_runtime_cycle_after': [e for e in player(after_raw)['evolution'] if e[1]==action['card_id']],
        },
        'failure_location': 'backend.py execution_feedback: len(replacement)==1 requires immediate draw; actual replacement count is zero',
        'first_observed_hand_replenishment': final_draw,
        'candidate_patch': {
            'status': 'proposal_only_not_installed',
            'preserve_existing_complete_replacement_branch': True,
            'add_branch': 'at exact t-1/t: before has 4 unique slots; after has exactly the unchanged other 3 slots; after.cycle==before.cycle+[root]; exact selected packed parameter and cost; exact after_elixir==min(100000,before_elixir-cost*10000+source_timeline_integer_regen); one native CardPlay, one scheduled command, succeeded receipt, no embedded commands',
            'identity_rule': 'retain exact ID / explicit source-name evolution alias; event 0 root consumption alone must retain unknown effective form',
            'required_next_validation': 'new native run of unchanged source after formal backend-lock release, preserve original attempt and previous backend hash',
            'phase_boundary_policy': 'exact rate switch ticks or missing/unknown timeline -> partial-slot branch rejects; no guessed tolerance',
            'negative_controls': controls,
        },
        'conclusion': {'root_card_consumption_supplementable_from_existing_raw_evidence': True,
                       'effective_form_supplementable': 'Cannon_EV1, explicit pinned-name alias to actual event13000096',
                       'original_formal_quality_rewritten': False,
                       'fresh_native_run_tested_with_candidate_patch': False,
                       'official_trajectory_ground_truth': False,
                       'uncertainty': ['CardPlay and hand/cycle/cost prove this native execution, not external replay trajectory fidelity.',
                                       'The replenishment appears between sampled ticks2050 and2060; no exact draw tick claimed.',
                                       'No production backend change or fresh native validation has been performed.']}}
    output.parent.mkdir(parents=True,exist_ok=True)
    output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({'report': reference(output), 'root_consumption_supplementable': True,
                      'event': candidates[0], 'negative_controls':len(controls), 'rpc_calls':0},ensure_ascii=False))


if __name__ == '__main__':
    main()

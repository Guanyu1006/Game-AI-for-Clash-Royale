"""Replay controller contract tests using synthetic clock/transport only.

No Android, ADB, native RPC or game physics is exercised here. These tests must
never be cited as native-renderer or native determinism acceptance.
"""
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch

from royale_phase2.cloud_replay import CloudNativeReplay, NativeReplaySource
from royale_phase2.runtime import ROOT
from royale_phase2.versioning import VersionMismatch


class SyntheticReplayTransport:
    """A tiny monotonic clock with scheduler receipts, deliberately not physics."""
    def __init__(self):
        self.version = {'fingerprint': 'synthetic-fixed-version'}
        self.resets = 0
        self.closed = False
        self.mutations = []
        self.fail_advance = False
        self.tamper_tick = None
        self.interference = False

    def reset(self, match, warmup_ticks=0):
        self.resets += 1
        self.origin = 1000 * self.resets
        self.actions = []
        self.events = []
        self.state = {'schema': 'cr-minimal.v4', 'ok': True, 'generation': self.resets,
                      'tick': warmup_ticks, 'next': self.origin + 1, 'plays': [],
                      'players': [{'owner': 0, 'elixir': 100000, 'hand': [], 'cycle': []},
                                  {'owner': 1, 'elixir': 100000, 'hand': [], 'cycle': []}],
                      'entities': [[42, 0, 1, 5000, 5000, 100, 100, None, 0, 'troop', 0]],
                      'crowns': [0, 0], 'result': None, 'finalized': False}
        self.mutations.append(('reset', warmup_ticks))
        return deepcopy(self.state)

    def schedule_card(self, owner, card, x, y, tick, request_id):
        receipt = {'request_id': request_id, 'kind': 'play_card', 'owner': owner,
                   'card_id': card, 'x': x, 'y': y, 'tick': tick,
                   'registration_receipt': {'registeredAtTick': self.state['tick'],
                                            'sequence': self.origin + len(self.actions) + 1},
                   'before_registration': {'tick': self.state['tick']}}
        self.actions.append(receipt)
        self.mutations.append(('register', request_id, self.state['tick'], tick))
        return deepcopy(receipt)

    def schedule_ability(self, owner, hints, tick, request_id):
        result = self.schedule_card(owner, 1, 0, 0, tick, request_id)
        self.actions[-1].update(kind='activate_ability', hints=deepcopy(hints))
        result.update(kind='activate_ability', hints=deepcopy(hints))
        return result

    def advance_exact_ticks(self, ticks):
        self.mutations.append(('advance', ticks))
        if self.fail_advance:
            raise TimeoutError('Synthetic unknown mutation outcome')
        before = self.state['tick']
        tick = min(before + ticks, 12)
        new_events = []
        for action in self.actions:
            if action['kind'] == 'play_card' and before < action['tick'] <= tick:
                new_events.append([self.origin + len(self.events) + len(new_events) + 1,
                                   action['tick'], action['owner'], action['card_id'], 3])
        self.events.extend(new_events)
        self.state.update(tick=tick, plays=deepcopy(new_events), next=self.origin + len(self.events) + 1)
        self.state['entities'][0][5] = 100 - tick
        if self.tamper_tick == tick:
            self.state['entities'][0][5] += 1
        if self.interference:
            self.state['generation'] += 1
        if tick == 12:
            self.state.update(finalized=True, result=0, crowns=[1, 0])
        return deepcopy(self.state)

    def execution_feedback(self, request_ids):
        return [{**deepcopy(a), 'native_injected': True,
                 'card_consumed': a['kind'] == 'play_card',
                 'actual_execution_tick': a['tick'] if a['kind'] == 'play_card' else None,
                 'consumption_status': 'confirmed_native_card_event' if a['kind'] == 'play_card'
                 else 'ability_consumption_unavailable_in_minimal'}
                for a in self.actions if a['request_id'] in request_ids]

    def observe_visible(self, owner):
        return {'tick': self.state['tick'], 'owner': owner, 'hand': [], 'history':
                [event[1:] for event in self.events if event[1] <= self.state['tick']]}

    def close(self):
        self.closed = True


def fixture():
    engine = SyntheticReplayTransport()
    states = [engine.reset({'battle': {'seed': 17}}, warmup_ticks=0)]
    engine.schedule_card(0, 26000001, 3500, 9000, 3, request_id='early')
    states.append(engine.advance_exact_ticks(3))
    states.append(engine.advance_exact_ticks(1))
    # Deliberately register a later execution four ticks ahead; replay must
    # retain this registration tick, never infer executeTick - 1.
    engine.schedule_card(1, 26000002, 9000, 20000, 8, request_id='later')
    states.append(engine.advance_exact_ticks(4))
    states.append(engine.advance_exact_ticks(4))
    journal = {'schema': 'royale-phase2-native-journal.v1',
               'version_fingerprint': engine.version['fingerprint'],
               'identity_receipt_sha256': 'synthetic-not-identity-proof',
               'match': {'battle': {'seed': 17}}, 'initialization_tick': 0,
               'actions': deepcopy(engine.actions), 'actual_card_events': deepcopy(engine.events)}
    terminal = {k: engine.state[k] for k in ('tick', 'generation', 'finalized', 'result', 'crowns')}
    feedback = engine.execution_feedback(['early', 'later'])
    return journal, states, feedback, terminal


class CloudNativeReplayContract(unittest.TestCase):
    def source(self):
        journal, states, feedback, terminal = fixture()
        return NativeReplaySource.from_journal(journal, states=states, feedback=feedback, terminal=terminal)

    def test_forward_seek_preserves_registration_boundaries_and_never_preregisters_future(self):
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            replay.seek(3)
            self.assertEqual([a['request_id'] for a in engine.actions], ['early'])
            replay.seek(4)
            self.assertEqual([a['request_id'] for a in engine.actions], ['early'])
            replay.seek(8)
            self.assertEqual([(a['request_id'], a['registration_receipt']['registeredAtTick'])
                              for a in engine.actions], [('early', 0), ('later', 4)])
            self.assertNotIn(('register', 'later', 7, 8), engine.mutations)
            actor = replay.observe_actor(0)
            self.assertEqual(actor['tick'], 8)
            self.assertNotIn('actions', actor)
            self.assertNotIn('source_action', actor)
            self.assertNotIn('labels', actor)

    def test_backward_seek_explicitly_resets_and_clears_future_history(self):
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            replay.seek(12)
            generation = replay.state['generation']
            replay.seek(3)
            self.assertEqual(engine.resets, 2)
            self.assertNotEqual(replay.state['generation'], generation)
            self.assertEqual([a['request_id'] for a in engine.actions], ['early'])
            self.assertEqual(len(replay.observe_actor(0)['history']), 1)
            self.assertTrue(replay.seek_history[-1]['explicit_reset'])
            replay.seek(12)
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])
        self.assertFalse(engine.closed)

    def test_full_verification_normalizes_only_generation_and_event_counter_origin(self):
        engine = SyntheticReplayTransport()
        engine.resets = 4
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            report = replay.verify_all()
        self.assertEqual(report['status'], 'pass')
        self.assertEqual(report['compared_saved_states'], 5)
        self.assertTrue(report['all_registration_ticks_equal'])
        self.assertTrue(report['available_consumption_feedback_equal'])
        self.assertTrue(report['terminal_equal'])
        self.assertTrue(report['natural_terminal_verified'])
        self.assertFalse(report['native_execution_verified'])
        self.assertEqual(len(report['run']['feedback']), 2)
        self.assertEqual(report['run']['registrations'][1]['original_registration_tick'], 4)
        self.assertEqual(report['run']['registrations'][1]['replayed_registration']
                         ['registration_receipt']['registeredAtTick'], 4)

    def test_progress_reports_each_boundary_and_duplicate_tick_incremental_events_are_equal(self):
        journal, states, feedback, terminal = fixture()
        duplicate = deepcopy(states[1])
        duplicate['plays'] = []
        source = NativeReplaySource.from_journal(journal, states=[states[0], states[1], duplicate] + states[2:],
                                                feedback=feedback, terminal=terminal)
        progress = []
        def capture(state):
            progress.append(state['tick'])
            state['tick'] = -999  # Callbacks cannot mutate controller state.
        with CloudNativeReplay(SyntheticReplayTransport(), source, lease_external=True,
                               progress_callback=capture) as replay:
            report = replay.verify_all()
            self.assertEqual(replay.state['tick'], 12)
        self.assertEqual(progress, [0, 3, 4, 8, 12])
        self.assertTrue(report['saved_state_trajectory_verified'])

    def test_stop_flag_never_cancels_explicit_start_and_paused_prefix_can_continue(self):
        stop = threading.Event()
        stop.set()
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True, stop_requested=stop.is_set) as replay:
            replay.start()
            self.assertEqual(engine.resets, 1)
            self.assertEqual(replay.state['tick'], 0)
            reached = replay.seek(12)
            self.assertEqual(reached['tick'], 0)
            self.assertEqual(engine.mutations, [('reset', 0)])
            self.assertTrue(replay.status()['last_seek_interrupted'])
            self.assertEqual(replay.status()['requested_seek_tick'], 12)
            self.assertFalse(replay.seek_history[-1]['target_reached'])
            self.assertTrue(replay.seek_history[-1]['interrupted'])
            self.assertTrue(replay.status()['valid'])
            self.assertIsNone(replay.status()['error'])
            stop.clear()
            replay.seek(12)
            self.assertEqual(engine.resets, 1)
            self.assertFalse(replay.status()['last_seek_interrupted'])
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])

    def test_stop_after_registration_keeps_receipt_and_never_resubmits_on_continue(self):
        stop = threading.Event()
        class StoppedAfterRegistration(SyntheticReplayTransport):
            def schedule_card(self, *args, **kwargs):
                result = super().schedule_card(*args, **kwargs)
                if result['request_id'] == 'early':
                    stop.set()
                return result
        engine = StoppedAfterRegistration()
        with CloudNativeReplay(engine, self.source(), lease_external=True, stop_requested=stop.is_set) as replay:
            replay.start()
            replay.seek(12)
            self.assertEqual(replay.state['tick'], 0)
            self.assertEqual(replay.status()['registered_actions'], 1)
            self.assertEqual(engine.mutations, [('reset', 0), ('register', 'early', 0, 3)])
            self.assertTrue(replay.status()['valid'])
            self.assertTrue(replay.status()['last_seek_interrupted'])
            stop.clear()
            replay.seek(12)
            self.assertEqual([m for m in engine.mutations if m[0] == 'register'],
                             [('register', 'early', 0, 3), ('register', 'later', 4, 8)])
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])

    def test_stop_after_advance_keeps_pending_feedback_incomplete_then_backfills_same_tick(self):
        stop = threading.Event()
        class StoppedAfterAdvance(SyntheticReplayTransport):
            def advance_exact_ticks(self, ticks):
                result = super().advance_exact_ticks(ticks)
                if result['tick'] == 8:
                    stop.set()
                return result
        journal, states, feedback, _ = fixture()
        source = NativeReplaySource.from_journal(journal, states=states[:-1], feedback=feedback)
        engine = StoppedAfterAdvance()
        with CloudNativeReplay(engine, source, lease_external=True, stop_requested=stop.is_set) as replay:
            replay.start()
            replay.seek(8)
            report = replay.report()
            self.assertTrue(replay.status()['valid'])
            self.assertTrue(report['all_saved_states_equal'])
            self.assertFalse(report['executed_feedback_complete'])
            self.assertFalse(report['saved_state_trajectory_verified'])
            self.assertEqual(report['status'], 'incomplete')
            self.assertTrue(replay.seek_history[-1]['interrupted'])
            self.assertTrue(replay.seek_history[-1]['target_reached'])
            self.assertEqual(len(report['run']['feedback']), 1)
            mutations = deepcopy(engine.mutations)
            stop.clear()
            replay.seek(8)
            self.assertEqual(engine.mutations, mutations, 'Same-tick continue must only drain feedback, never advance or register again')
            self.assertTrue(replay.report()['executed_feedback_complete'])
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])
            self.assertFalse(replay.status()['last_seek_interrupted'])

    def test_stop_after_feedback_finishes_current_receipt_and_issues_no_next_mutation(self):
        stop = threading.Event()
        class StoppedAfterFeedback(SyntheticReplayTransport):
            def __init__(self):
                super().__init__()
                self.feedback_calls = []
            def execution_feedback(self, request_ids):
                self.feedback_calls.append(deepcopy(request_ids))
                result = super().execution_feedback(request_ids)
                if request_ids == ['early']:
                    stop.set()
                return result
        engine = StoppedAfterFeedback()
        with CloudNativeReplay(engine, self.source(), lease_external=True, stop_requested=stop.is_set) as replay:
            replay.start()
            replay.seek(12)
            self.assertEqual(replay.state['tick'], 3)
            self.assertEqual(engine.feedback_calls, [['early']])
            self.assertEqual(engine.mutations[-1], ('advance', 3))
            self.assertTrue(replay.report()['executed_feedback_complete'])
            self.assertTrue(replay.status()['last_seek_interrupted'])
            stop.clear()
            replay.seek(12)
            self.assertEqual(engine.feedback_calls, [['early'], ['later']])
            self.assertEqual(engine.resets, 1)
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])

    def test_stop_between_same_tick_feedback_rpcs_preserves_unread_receipt_for_continue(self):
        source_engine = SyntheticReplayTransport()
        states = [source_engine.reset({'battle': {}}, warmup_ticks=0)]
        source_engine.schedule_card(0, 26000001, 3500, 9000, 3, request_id='early')
        source_engine.schedule_card(1, 26000002, 9000, 20000, 3, request_id='peer')
        states.append(source_engine.advance_exact_ticks(3))
        journal = {'schema': 'royale-phase2-native-journal.v1',
            'version_fingerprint': source_engine.version['fingerprint'], 'match': {'battle': {}},
            'initialization_tick': 0, 'actions': source_engine.actions,
            'actual_card_events': source_engine.events}
        source = NativeReplaySource.from_journal(journal, states=states,
            feedback=source_engine.execution_feedback(['early', 'peer']))
        stop = threading.Event()
        class StoppedBetweenReceipts(SyntheticReplayTransport):
            def __init__(self):
                super().__init__()
                self.feedback_calls = []
            def execution_feedback(self, request_ids):
                self.feedback_calls.append(deepcopy(request_ids))
                result = super().execution_feedback(request_ids)
                if request_ids == ['early']:
                    stop.set()
                return result
        engine = StoppedBetweenReceipts()
        with CloudNativeReplay(engine, source, lease_external=True, stop_requested=stop.is_set) as replay:
            replay.start()
            replay.seek(3)
            self.assertEqual(engine.feedback_calls, [['early']])
            self.assertFalse(replay.report()['executed_feedback_complete'])
            self.assertTrue(replay.status()['last_seek_interrupted'])
            self.assertTrue(replay.status()['valid'])
            mutations = deepcopy(engine.mutations)
            stop.clear()
            replay.seek(3)
            self.assertEqual(engine.feedback_calls, [['early'], ['peer']])
            self.assertEqual(engine.mutations, mutations)
            self.assertTrue(replay.report()['saved_state_trajectory_verified'])

    def test_actual_hp_difference_cannot_be_hidden_by_normalization(self):
        engine = SyntheticReplayTransport()
        engine.tamper_tick = 8
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            report = replay.verify_all()
        self.assertEqual(report['status'], 'fail')
        self.assertFalse(report['saved_state_trajectory_verified'])
        mismatches = [c for c in report['run']['comparisons'] if not c['equal']]
        self.assertEqual([row['tick'] for row in mismatches], [8])
        self.assertIn('entities', mismatches[0]['differences'][0]['path'])

    def test_unknown_mutation_outcome_requires_explicit_reset_and_is_never_retried(self):
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            engine.fail_advance = True
            with self.assertRaises(TimeoutError):
                replay.seek(3)
            advances = [m for m in engine.mutations if m[0] == 'advance']
            self.assertEqual(advances, [('advance', 3)])
            with self.assertRaisesRegex(RuntimeError, 'explicitly start/reset'):
                replay.seek(3)
            self.assertEqual([m for m in engine.mutations if m[0] == 'advance'], advances)
            engine.fail_advance = False
            replay.start()
            replay.seek(3)
            self.assertTrue(replay.status()['valid'])

    def test_external_reset_invalidates_generation_without_automatic_recovery(self):
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            engine.interference = True
            with self.assertRaisesRegex(RuntimeError, 'Another controller'):
                replay.seek(3)
            self.assertFalse(replay.status()['valid'])
            self.assertEqual(engine.resets, 1)

    def test_different_thread_cannot_mutate_native_replay(self):
        engine = SyntheticReplayTransport()
        errors = []
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            def other_thread():
                try:
                    replay.seek(3)
                except Exception as exc:
                    errors.append(exc)
            worker = threading.Thread(target=other_thread)
            worker.start()
            worker.join()
            self.assertEqual(len(errors), 1)
            self.assertIn('lease-owning worker', str(errors[0]))
            self.assertFalse(any(m[0] == 'advance' for m in engine.mutations))

    def test_shared_native_lock_blocks_existing_training_controller(self):
        from royale_phase3.common import native_lock
        engine = SyntheticReplayTransport()
        (ROOT / '.tmp').mkdir(exist_ok=True)
        # Exercise the real OS locking implementation on an isolated test
        # path, without contending with a user's running native controller.
        with tempfile.TemporaryDirectory(prefix='cloud-replay-lock-', dir=ROOT / '.tmp') as temporary:
            lock_path = Path(temporary) / 'native.lock'
            with patch('royale_phase3.common.local', return_value=lock_path):
                with native_lock():
                    with self.assertRaisesRegex(RuntimeError, 'owns native RPC'):
                        with CloudNativeReplay(engine, self.source()):
                            self.fail('Acquiring another controller must fail')
                self.assertEqual(engine.mutations, [])
                with CloudNativeReplay(engine, self.source(), close_backend=True) as replay:
                    replay.start()
        self.assertTrue(engine.closed)

    def test_version_and_source_timing_rejected_before_any_reset(self):
        engine = SyntheticReplayTransport()
        engine.version['fingerprint'] = 'different'
        with self.assertRaises(VersionMismatch):
            with CloudNativeReplay(engine, self.source(), lease_external=True):
                pass
        self.assertEqual(engine.mutations, [])
        for invalid in ('missing', 'conflicting', 'too_late', 'wrong_order'):
            journal, states, feedback, terminal = fixture()
            if invalid == 'missing':
                journal['actions'][0].pop('registration_receipt')
                journal['actions'][0].pop('before_registration')
            elif invalid == 'conflicting':
                journal['actions'][0]['before_registration']['tick'] = 1
            elif invalid == 'too_late':
                journal['actions'][0]['registration_receipt']['registeredAtTick'] = 3
                journal['actions'][0]['before_registration']['tick'] = 3
            else:
                journal['actions'].reverse()
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                NativeReplaySource.from_journal(journal, states=states, feedback=feedback, terminal=terminal)

    def test_source_integrity_checks_initialization_events_and_final_result(self):
        journal, states, feedback, terminal = fixture()
        for field in ('event', 'terminal', 'state'):
            changed_journal, changed_states, changed_terminal = deepcopy(journal), deepcopy(states), deepcopy(terminal)
            if field == 'event':
                changed_journal['actual_card_events'][0][3] += 1
            elif field == 'terminal':
                changed_terminal['crowns'] = [0, 1]
            else:
                changed_states[2]['tick'] = 1
            with self.subTest(field=field), self.assertRaises(ValueError):
                NativeReplaySource.from_journal(changed_journal, states=changed_states,
                    feedback=feedback, terminal=changed_terminal)

    def test_load_existing_raw_native_folder_and_records_hashes(self):
        import json
        journal, states, feedback, terminal = fixture()
        (ROOT / '.tmp').mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='cloud-replay-', dir=ROOT / '.tmp') as temporary:
            native = Path(temporary) / 'native'
            native.mkdir()
            for name, value in (('journal.json', journal), ('initialization.json', journal['match']),
                                ('execution_feedback.json', feedback), ('native_result.json', terminal)):
                (native / name).write_text(json.dumps(value))
            (native / 'states.jsonl').write_text(''.join(json.dumps(s) + '\n' for s in states))
            source = NativeReplaySource.load(Path(temporary))
            self.assertEqual(source.end_tick, 12)
            self.assertEqual(len(source.summary()['files']), 5)
            self.assertTrue(all(len(row['sha256']) == 64 for row in source.summary()['files']))
            (native / 'initialization.json').write_text('{"battle":{"seed":19}}')
            with self.assertRaisesRegex(ValueError, 'initialization differs'):
                NativeReplaySource.load(native / 'journal.json')

    def test_prefix_and_absent_state_evidence_do_not_claim_full_replay(self):
        engine = SyntheticReplayTransport()
        with CloudNativeReplay(engine, self.source(), lease_external=True) as replay:
            replay.start()
            replay.seek(8)
            self.assertEqual(replay.report()['status'], 'incomplete')
        journal, states, feedback, terminal = fixture()
        source = NativeReplaySource.from_journal(journal)
        with CloudNativeReplay(SyntheticReplayTransport(), source, lease_external=True) as replay:
            report = replay.verify_all()
            self.assertFalse(report['saved_state_trajectory_verified'])
            self.assertEqual(report['source_saved_states'], 0)


if __name__ == '__main__':
    unittest.main()

"""Public JSON -> native-session control contracts using a toy transport.

The parser is real; the engine and PNG are synthetic. These checks do not
certify Android rendering or the original match's trajectory.
"""
from contextlib import contextmanager
from copy import deepcopy
from pathlib import Path
import tempfile
import threading
import time
import unittest

from royale_phase2.cloud_native import CloudNativeSession
from royale_phase2.public_replay import PublicReplayPlan
from royale_phase3.common import ROOT, read, rows, write
from tests_cloud.test_cloud_native_session import SyntheticBridge, SyntheticSessionEngine


def public_fixture():
    keys = ('knight', 'archers', 'giant', 'goblins', 'mini-pekka', 'cannon', 'fireball', 'arrows')
    deck = [{'card_key': key, 'level': 16} for key in keys]
    return {'source': {'replay_tag': '0123456789abcdef0123456789abcdef', 'anonymized': True},
        'battle': {'game_mode': 'Ladder', 'result': 'victory',
            'team': {'players': [{'deck': deepcopy(deck)}]},
            'opponent': {'players': [{'deck': deepcopy(deck)}]}},
        'events': [{'kind': 'play_card', 'side': side, 'source_index': i,
            'card_key': card, 'replay_tick_20hz': tick, 'source_fields': {'data_i': 1},
            'coordinates': {'native_world_units': {'x': x, 'y': y}}}
            for i, (side, card, tick, x, y) in enumerate((
                ('team', 'knight', 92, 3500, 6500),
                ('opponent', 'archers', 92, 14500, 25500),
                ('team', 'fireball', 96, 9000, 22000)))]}


class PublicSourceTransport(SyntheticSessionEngine):
    """Honor each configured deck order so the real deal aligner can run."""
    def reset(self, match, warmup_ticks=90):
        super().reset(match, warmup_ticks)
        for player in self.state['players']:
            deck = [c['d'] for c in match['battle'][f'deck{player["owner"]}']['sp']]
            player['hand'] = [[slot, card, 3, card, card] for slot, card in enumerate(deck[:4])]
            player['cycle'] = deck[4:]
        return deepcopy(self.state)


class PublicSourceSessionContracts(unittest.TestCase):
    def setUp(self):
        (ROOT/'.tmp').mkdir(exist_ok=True)
        self.temporary = tempfile.TemporaryDirectory(prefix='public-source-', dir=ROOT/'.tmp')
        self.addCleanup(self.temporary.cleanup)
        self.folder = Path(self.temporary.name)
        self.engines = []
        self.leases = []
        self.session = None
        self.addCleanup(self._close)
        self.plan = PublicReplayPlan.from_mapping(public_fixture())

    def _close(self):
        if self.session:
            self.session.close(timeout=8)

    def wait(self, predicate):
        deadline = time.monotonic()+8
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(.01)
        self.fail('Public source worker timeout: '+str(self.session.public()))

    def start(self):
        @contextmanager
        def lease():
            self.leases.append(('enter', threading.get_ident()))
            try:
                yield
            finally:
                self.leases.append(('exit', threading.get_ident()))
        def factory(**kwargs):
            engine = PublicSourceTransport(**kwargs)
            self.engines.append(engine)
            return engine
        self.session = CloudNativeSession(self.folder/'session', archive_root=self.folder/'archives',
            engine_factory=factory, bridge=SyntheticBridge(self.engines), lease_factory=lease,
            frame_wait=0, capture_ticks=5, max_tick=120)
        self.wait(lambda: self.session.public()['ready'] and not self.session.public()['busy'])

    def command(self, name, data=None, *, success=True):
        job = self.session.submit(name, data)
        self.wait(lambda: self.session.public()['jobs'].get(job, {}).get('status') in ('done', 'failed'))
        answer = self.session.public()['jobs'][job]
        self.assertEqual(answer['status'], 'done' if success else 'failed', answer)
        return answer

    def open(self):
        path = self.folder/'plan.json'
        write(path, self.plan.to_dict())
        self.command('source_open', {'plan_path': str(path), 'source_id': 'public-test'})

    def test_real_import_executes_both_sides_and_archives_local_receipts(self):
        self.start(); self.open()
        self.assertEqual(self.session.public()['mode'], 'source')
        self.assertIsNone(self.session.opponent)
        initial_generation = self.session.public()['generation']
        self.command('step', {'ticks': 10})
        status = self.session.public()['source_replay']
        self.assertEqual((status['registered_actions'], status['executed_actions'], status['card_consumed']), (3, 3, 3))
        self.assertTrue(status['source_actions_finished'])
        self.assertFalse(status['natural_terminal'])
        actions = self.engines[0].actions
        self.assertEqual([{k: a[k] for k in ('owner', 'card_id', 'x', 'y', 'tick', 'request_id')}
                          for a in actions],
                         [{k: a[k] for k in ('owner', 'card_id', 'x', 'y', 'tick', 'request_id')}
                          for a in self.plan.commands])
        self.assertEqual([a['registration_receipt']['registeredAtTick'] for a in actions], [92, 92, 96])
        self.command('run', {'ticks_per_step': 10})
        self.wait(lambda: self.session.public()['terminal'] and not self.session.public()['running'])
        self.command('save')
        self.assertEqual(self.session.public()['generation'], initial_generation)
        archive = self.session.active_dir
        report = read(archive/'source-execution.json')
        self.assertFalse(report['original_source_registration_ticks_known'])
        self.assertFalse(report['official_fidelity_verified'])
        self.assertTrue(report['current']['natural_terminal'])
        self.assertTrue(all(r['original_source_registration_tick'] is None for r in report['feedback']))
        self.assertEqual(len(read(archive/'native/journal.json')['actions']), 3)
        self.assertEqual(read(archive/'native/native_result.json')['tick'], 110)
        self.assertTrue(rows(archive/'frames/index.jsonl'))
        self.assertEqual({r['generation'] for r in rows(archive/'native/states.jsonl')}, {initial_generation})
        manifest = read(archive/'manifest.json')
        self.assertTrue(manifest['public_source_reconstruction'])
        self.assertTrue(manifest['natural_terminal'])
        worker_threads = {thread for _, thread in self.engines[0].calls}
        self.assertEqual(worker_threads, {self.leases[0][1]})

    def test_backward_seek_creates_fresh_archive_and_generated_journal_is_replayable(self):
        from royale_phase2.cloud_replay import NativeReplaySource
        self.start(); self.open()
        self.command('source_seek', {'tick': 104})
        old_archive = self.session.archive_id
        old_folder = self.session.active_dir
        old_frames = (old_folder/'frames/index.jsonl').read_bytes()
        old_generation = self.session.native_state['generation']
        self.command('source_seek', {'tick': 94})
        self.assertNotEqual(self.session.archive_id, old_archive)
        self.assertGreater(self.session.native_state['generation'], old_generation)
        self.assertEqual(self.session.public()['source_replay']['executed_actions'], 2)
        self.assertEqual((old_folder/'frames/index.jsonl').read_bytes(), old_frames)
        self.command('source_seek', {'tick': 110})
        saved = NativeReplaySource.load(self.session.active_dir/'native')
        self.assertEqual(len(saved.journal['actions']), 3)
        self.assertTrue(saved.terminal['finalized'])
        source_archive = self.session.archive_id
        self.command('replay_open', {'archive_id': source_archive})
        self.assertIsNone(self.session.public()['source_replay'])
        self.command('replay_seek', {'tick': 110})
        self.assertTrue(self.session.public()['replay']['verified'])

    def test_pause_during_seek_keeps_prefix_and_resume_executes_once(self):
        self.start(); self.open()
        entered, release = threading.Event(), threading.Event()
        self.engines[0].block_next_advance = entered, release
        job = self.session.submit('source_seek', {'tick': 108})
        self.assertTrue(entered.wait(2))
        pause_job = self.session.submit('pause')
        release.set()
        self.wait(lambda: self.session.public()['jobs'][pause_job]['status'] == 'done')
        self.assertEqual(self.session.public()['jobs'][job]['status'], 'done')
        self.assertLess(self.session.public()['tick'], 108)
        self.command('source_seek', {'tick': 108})
        self.assertEqual(len(self.engines[0].actions), 3)
        self.assertEqual(self.session.public()['source_replay']['executed_actions'], 3)
        self.assertEqual(len({a['request_id'] for a in self.engines[0].actions}), 3)

    def test_unknown_mutation_is_not_retried_and_source_blocks_manual_play(self):
        self.start(); self.open()
        self.command('play', {}, success=False)
        self.assertEqual(len(self.engines[0].actions), 0)
        self.engines[0].fail_next_advance = True
        answer = self.command('source_seek', {'tick': 104}, success=False)
        self.assertIn('NativeStateInvalid', answer['error'])
        reset_count = self.engines[0].resets
        self.assertFalse(self.session.public()['source_replay']['valid'])
        failure = read(self.session.active_dir/'source-failure.json')
        self.assertFalse(failure['execution']['current']['valid'])
        self.assertFalse(failure['retry'])
        self.command('source_seek', {'tick': 104}, success=False)
        self.assertEqual(self.engines[0].resets, reset_count)
        self.assertEqual(len(self.engines[0].actions), 0)
        self.open()
        self.command('source_seek', {'tick': 110})
        self.assertEqual(self.session.public()['source_replay']['card_consumed'], 3)

    def test_unconfirmed_card_is_reported_without_replacing_or_retrying_source(self):
        self.start(); self.open()
        engine = self.engines[0]
        original_feedback = engine.execution_feedback
        unconfirmed_id = self.plan.commands[1]['request_id']
        def feedback(request_ids=None):
            replies = original_feedback(request_ids)
            for reply in replies:
                if reply['request_id'] == unconfirmed_id:
                    reply.update(card_consumed=False, actual_execution_tick=None,
                                 consumption_status='not_observed')
            return replies
        engine.execution_feedback = feedback
        self.command('source_seek', {'tick': 110})
        status = self.session.public()['source_replay']
        self.assertTrue(status['natural_terminal'])
        self.assertEqual((status['native_injected'], status['card_consumed'], status['not_confirmed']), (3, 2, 1))
        self.assertEqual([a['request_id'] for a in engine.actions],
                         [a['request_id'] for a in self.plan.commands])
        saved = read(self.session.active_dir/'native/execution_feedback.json')
        self.assertFalse(saved[1]['card_consumed'])
        self.assertTrue(saved[1]['native_injected'])
        self.assertEqual(saved[1]['source_event_index'], 1)

    def test_initialization_reports_running_message_without_inventing_a_tick(self):
        self.start()
        engine = self.engines[0]
        reset = engine.reset
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        def delayed_reset(match, warmup_ticks=90):
            entered.set()
            if not release.wait(5):
                raise RuntimeError('Synthetic reset coordination timed out')
            return reset(match, warmup_ticks)
        engine.reset = delayed_reset
        job = self.session.submit('source_open', {'plan': self.plan.to_dict()})
        self.assertTrue(entered.wait(2))
        state = self.session.public()
        self.assertEqual(state['jobs'][job]['status'], 'running')
        self.assertIsNone(state['tick'])
        self.assertIsNone(state['source_replay']['tick'])
        self.assertEqual(state['source_replay']['phase'], 'initializing')
        self.assertIn('等待场景加载', state['status_message'])
        release.set()
        self.wait(lambda: self.session.public()['jobs'][job]['status'] == 'done')
        self.assertEqual(self.session.public()['tick'], 90)


if __name__ == '__main__':
    unittest.main()

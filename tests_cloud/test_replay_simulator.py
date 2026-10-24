"""Actual saved-data replay boundaries and pure Python combat lifecycle."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from tools.cloud.replay import ROOT, ReplaySession
from tools.cloud.simulator import SimulatorSession


class SavedReplay(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = ReplaySession()

    def setUp(self):
        self.session.reset()

    def test_real_dataset_towers_unknown_entities_and_provenance(self):
        data = self.session.state(); obs = data['observation']
        self.assertEqual(len(obs['towers']), 6)
        self.assertTrue(all(t['hp'] > 0 for t in obs['towers']))
        self.assertEqual(obs['hand'][0]['card_id'], data['label']['card_id'])
        self.assertIsNone(obs['entities'])
        self.assertFalse(data['provenance']['raw_states_available'])
        self.assertEqual(data['provenance']['rows_sha256'], 'a87ccc48f89cc7c121d81a4b4dec22f5aceb28f6644b379b492b3c6943111012')

    def test_seek_back_has_no_future_or_stale_history(self):
        first = self.session.public()
        later = self.session.seek(40)
        self.assertGreater(len(later['observation']['history']), len(first['observation']['history']))
        self.assertTrue(all(h['tick'] <= later['observation']['tick'] for h in later['observation']['history']))
        self.session.seek(0)
        self.assertEqual(self.session.public(), first)
        for field in ('label', 'transition_label', 'targets', 'terminated', 'actual_execution_tick'):
            self.assertNotIn(field, first)
        self.assertGreater(self.session.state()['label']['actual_execution_tick'], first['observation']['tick'])

    def test_owner_episode_reset_and_terminal_semantics(self):
        self.session.seek(30)
        current = self.session.reset('native-330001', 1)
        self.assertEqual((current['episode_id'], current['owner'], current['index']), ('native-330001', 1, 0))
        self.assertEqual(current['observation']['history'], [])
        last = self.session.seek(current['frames']-1)
        self.assertTrue(last['at_last_saved_frame'])
        self.assertTrue(last['transition_label']['terminated'])
        self.assertGreater(last['transition_label']['next_tick'], last['observation']['tick'])
        self.assertEqual(self.session.step()['index'], last['index'])
        reset = self.session.reset('native-330001', 1)
        self.assertFalse(reset['at_last_saved_frame'])

    def test_public_results_cannot_mutate_saved_source(self):
        original = self.session.public(); modified = self.session.public()
        modified['observation']['hand'].clear()
        self.assertEqual(self.session.public(), original)
        for value in (-1, 10000, 1.5, True):
            with self.assertRaises(ValueError):
                self.session.seek(value)

    def test_single_controller_owner_one_export_can_open_and_seek(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/cloud') as directory:
            folder=Path(directory)
            metadata=json.loads(json.dumps(self.session.metadata))
            sample=self.session._groups[('native-330001',1)][:2]
            payload='\n'.join(json.dumps(row) for row in sample)+'\n'
            row_path=folder/'rows.jsonl';row_path.write_text(payload,encoding='utf-8')
            metadata['rows']={'path':row_path.relative_to(ROOT).as_posix(),
                'sha256':hashlib.sha256(row_path.read_bytes()).hexdigest()}
            meta_path=folder/'dataset.json';meta_path.write_text(json.dumps(metadata),encoding='utf-8')
            replay=ReplaySession(metadata_path=meta_path.relative_to(ROOT).as_posix())
            self.assertEqual(replay.key,('native-330001',1))
            self.assertEqual(replay.seek(1)['owner'],1)
            result=subprocess.run([sys.executable,'-B','tools/cloud/workspace.py','replay',
                '--metadata',str(meta_path),'--index','1'],cwd=ROOT,capture_output=True,text=True,timeout=15)
            self.assertEqual(result.returncode,0,result.stderr)
            self.assertEqual(json.loads(result.stdout)['owner'],1)
            with self.assertRaises(ValueError):replay.reset(owner=0)


class ApproximateSimulator(unittest.TestCase):
    def test_actual_play_cost_movement_and_illegal_region(self):
        sim = SimulatorSession(seed=3)
        initial = sim.state(); slot = next(h['slot'] for h in initial['hand'] if h['kind'] == 'troop' and h['cost'] <= 5)
        cost = initial['hand'][slot]['cost']; before = initial['observation']['elixir']
        invalid = sim.play(slot, 4, 10, seconds=0)
        self.assertFalse(invalid['feedback']['legal'])
        self.assertEqual(invalid['observation']['elixir'], before)
        valid = sim.play(slot, 4, 25, seconds=0)
        self.assertTrue(valid['feedback']['consumed'])
        self.assertEqual(valid['observation']['elixir'], before-cost)
        self.assertGreater(len(valid['observation']['units']), 0)
        units = {u['id']: u for u in valid['observation']['units']}
        advanced = sim.step(3)
        self.assertEqual(advanced['observation']['time'], 3)
        self.assertTrue(any((u['x'], u['y']) != (units[u['id']]['x'], units[u['id']]['y'])
            for u in advanced['observation']['units'] if u['id'] in units))

    def test_reset_terminal_and_determinism(self):
        sim = SimulatorSession(seed=3, max_time=4)
        first = sim.state()['observation']
        end = sim.step(10, autoplay=True)
        self.assertTrue(end['done']); self.assertEqual(end['termination_reason'], 'time_limit')
        self.assertEqual(end['observation']['time'], 4)
        rejected = sim.play(0, 4, 18)
        self.assertFalse(rejected['feedback']['consumed'])
        restarted = sim.reset(seed=3, max_time=4)
        self.assertEqual(restarted['observation'], first)
        self.assertFalse(restarted['done']); self.assertIsNone(restarted['feedback'])
        self.assertEqual(restarted['snapshots'], 1)
        rerun = sim.step(10, autoplay=True)
        self.assertEqual(rerun['observation'], end['observation'])

    def test_king_destroyed_by_actual_spell_reports_success_and_terminal(self):
        sim = SimulatorSession(seed=7)
        king = next(t for t in sim.arena.towers if t.team == 1 and t.king)
        king.hp = 1
        slot = next(h['slot'] for h in sim.state()['hand'] if h['kind'] == 'spell')
        finished = sim.play(slot, king.x, king.y, seconds=0)
        self.assertTrue(finished['feedback']['consumed'])
        self.assertEqual(finished['feedback']['reason'], 'sandbox_play_applied')
        self.assertTrue(finished['done'])
        self.assertEqual(finished['winner'], 0)
        self.assertEqual(finished['termination_reason'], 'king_destroyed')

    def test_save_snapshot_replay_keeps_active_state_and_reset_isolated(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/cloud') as folder:
            sim = SimulatorSession(root=folder, seed=9)
            sim.step(5, autoplay=True)
            before = sim.state()
            old = sim.replay(0)
            self.assertEqual(old['state']['observation']['time'], 0)
            self.assertEqual(old['state']['observation']['events'], [])
            self.assertEqual(sim.state(), before)
            report = sim.save(); target = Path(folder)/report['snapshots']
            self.assertEqual(report['sha256'], hashlib.sha256(target.read_bytes()).hexdigest())
            snapshots = [json.loads(line) for line in target.read_text().splitlines()]
            self.assertEqual(len(snapshots), before['snapshots'])
            self.assertEqual(snapshots[-1]['state']['observation'], before['observation'])
            self.assertEqual(report['native_rpc_calls'], 0)
            self.assertNotEqual(sim.save()['snapshots'], report['snapshots'])
            sim.reset(seed=9)
            self.assertNotEqual(sim.state()['session_id'], before['session_id'])
            self.assertEqual(len(sim.snapshots), 1)

    def test_bad_step_does_not_mutate_match(self):
        sim = SimulatorSession()
        initial = sim.state()
        for seconds in (-1, float('nan'), 31, True):
            with self.assertRaises(ValueError): sim.step(seconds)
        self.assertEqual(initial, sim.state())

    def test_saved_run_survives_new_session_and_rejects_escape_or_corruption(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'.tmp/cloud') as folder:
            sim = SimulatorSession(root=folder, seed=2)
            sim.step(3, autoplay=True)
            expected = sim.state()['observation']
            report = sim.save()
            restarted = SimulatorSession(root=folder, seed=100)
            active = restarted.state()
            restored = restarted.replay(report['frames']-1, run_id=report['run_id'])
            self.assertEqual(restored['state']['observation'], expected)
            self.assertEqual(restarted.state(), active)
            for run_id in ('../escape', '/absolute', '.', '..', 'x/y'):
                with self.assertRaises((ValueError, FileNotFoundError)):
                    restarted.replay(0, run_id=run_id)
            target = Path(folder)/report['snapshots']
            target.write_bytes(target.read_bytes()+b'\n')
            with self.assertRaisesRegex(ValueError, 'SHA256'):
                restarted.replay(0, run_id=report['run_id'])


if __name__ == '__main__':
    unittest.main()

"""Independent pure Python Arena sandbox with small, saved snapshot traces."""
import copy
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import re
import threading
import uuid

from royale_ai.cards import CARDS
from royale_ai.policy import GreedyPolicy
from royale_ai.simulator import Arena
from royale_ai.state import Action, legal_action

ROOT = Path(__file__).resolve().parents[2]


class SimulatorSession:
    def __init__(self, root=ROOT, seed=0, max_time=180):
        self.root = Path(root).resolve()
        self.lock = threading.RLock()
        self.reset(seed, max_time)

    def reset(self, seed=0, max_time=180):
        if type(seed) is not int or not 0 <= seed < 2**32:
            raise ValueError('seed 必须为 0..2^32-1 整数')
        if type(max_time) not in (int, float) or not math.isfinite(max_time) or not 1 <= max_time <= 600:
            raise ValueError('max_time 必须为 1..600 秒')
        with self.lock:
            self.seed = seed; self.max_time = max_time
            self.session_id = uuid.uuid4().hex
            self.arena = Arena(seed=seed, max_time=max_time)
            self.policy = GreedyPolicy(); self.feedback = None
            self.snapshots = []
            self._capture('reset')
            return self.state()

    def _state(self):
        obs = self.arena.observation().to_dict()
        hand = [{**vars(CARDS[card]), 'slot': slot,
            'affordable': self.arena.players[0].elixir + 1e-6 >= CARDS[card].cost}
            for slot, card in enumerate(self.arena.players[0].hand)]
        return {'schema': 'cloud-simulator-state.v1', 'session_id': self.session_id,
            'mode': 'approximate_python_sandbox', 'engine': 'royale_ai.simulator.Arena',
            'scope': '12 卡 MVP 近似教学沙盒；不是阶段二原生引擎，也不是官方物理',
            'coordinate_system': '18×32 tile；蓝方在下，普通部队 y>=17；与原生 18000×32000 坐标分开',
            'seed': self.seed, 'max_time': self.max_time, 'observation': obs,
            'hand': hand, 'done': self.arena.done, 'winner': self.arena.winner(),
            'termination_reason': ('king_destroyed' if any(t.king and t.hp <= 0 for t in self.arena.towers)
                else 'time_limit') if self.arena.done else None,
            'feedback': copy.deepcopy(self.feedback), 'native_rpc_calls': 0,
            'snapshots': len(self.snapshots)}

    def state(self):
        with self.lock:
            return self._state()

    def _capture(self, command, detail=None):
        state = self._state()
        state['snapshots'] = len(self.snapshots)+1
        self.snapshots.append({'index': len(self.snapshots), 'command': command,
            'detail': copy.deepcopy(detail), 'state': state})

    def _advance(self, seconds, autoplay):
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 30:
            raise ValueError('seconds 必须为 0..30，使用多次 step 推进更长时间')
        if type(autoplay) is not bool:
            raise ValueError('autoplay 必须为布尔值')
        remaining = float(seconds)
        while remaining > 1e-8 and not self.arena.done:
            if autoplay:
                self.arena.play(0, self.policy.choose(self.arena.observation(0)))
            self.arena.play(1, self.policy.choose(self.arena.observation(1)))
            delta = min(1., remaining)
            self.arena.advance(delta)
            remaining -= delta
            self._capture('tick', {'seconds': delta, 'autoplay': autoplay})

    def step(self, seconds=1, autoplay=False):
        with self.lock:
            self._advance(seconds, autoplay)
            return self.state()

    def play(self, slot, x, y, seconds=1):
        if type(slot) is not int or not -1 <= slot < 4:
            raise ValueError('slot 必须为 -1（等待）或 0..3')
        if any(type(v) not in (int, float) or not math.isfinite(v) for v in (x, y)):
            raise ValueError('x/y 必须为有限 tile 坐标')
        if type(seconds) not in (int, float) or not math.isfinite(seconds) or not 0 <= seconds <= 30:
            raise ValueError('seconds 必须为 0..30')
        with self.lock:
            action = Action(slot, x, y); observation = self.arena.observation()
            card = CARDS[observation.hand[slot]] if slot >= 0 else None
            affordable = card is None or observation.elixir + 1e-6 >= card.cost
            was_done = self.arena.done
            legal = not was_done and legal_action(observation, action)
            before = observation.elixir
            consumed = bool(legal and self.arena.play(0, action))
            self.feedback = {'action': action.to_dict(), 'card': card.id if card else None,
                'affordable': affordable, 'legal': legal, 'accepted': consumed,
                'consumed': consumed and slot >= 0, 'time': self.arena.time,
                'elixir_before': before, 'elixir_after': self.arena.players[0].elixir,
                'reason': ('session_finished' if was_done else 'insufficient_elixir' if not affordable
                    else 'outside_deployment_region' if not legal else 'wait' if slot < 0 else 'sandbox_play_applied'),
                'scope': '仅近似 Arena 执行反馈'}
            self._capture('play', self.feedback)
            if consumed:
                self._advance(seconds, False)
            return self.state()

    def save(self):
        """Save one immutable run with command trace and random-access snapshots."""
        with self.lock:
            run_id = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')+'-sandbox-'+uuid.uuid4().hex[:8]
            base = (self.root/'outputs/cloud/runs').resolve()
            base.relative_to(self.root)
            path = base/run_id
            path.mkdir(parents=True, exist_ok=False)
            target = path/'snapshots.jsonl'
            with target.open('w', encoding='utf-8') as file:
                for snapshot in self.snapshots:
                    file.write(json.dumps(snapshot, ensure_ascii=False, allow_nan=False)+'\n')
            report = {'schema': 'cloud-sandbox-run.v1', 'run_id': run_id, 'session_id': self.session_id,
                'mode': 'approximate_python_sandbox', 'seed': self.seed, 'max_time': self.max_time,
                'frames': len(self.snapshots), 'done': self.arena.done, 'winner': self.arena.winner(),
                'snapshots': str(target.relative_to(self.root)),
                'sha256': hashlib.sha256(target.read_bytes()).hexdigest(),
                'native_rpc_calls': 0, 'official_fidelity_claim': False}
            (path/'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
            return report

    def replay(self, index, run_id=None):
        """Read current or hash-verified saved snapshots without changing Arena."""
        with self.lock:
            snapshots = self.snapshots
            if run_id is not None:
                if not isinstance(run_id, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}', run_id):
                    raise ValueError('Invalid saved run_id')
                base = (self.root/'outputs/cloud/runs').resolve()
                base.relative_to(self.root)
                run = base/run_id
                run.resolve().relative_to(base)
                if run.is_symlink():
                    raise ValueError('Refusing saved run symlink')
                report_path = run/'report.json'; target = run/'snapshots.jsonl'
                if report_path.is_symlink() or target.is_symlink():
                    raise ValueError('Refusing saved replay symlink')
                report = json.loads(report_path.read_text(encoding='utf-8'))
                if report.get('schema') != 'cloud-sandbox-run.v1' or report.get('run_id') != run_id:
                    raise ValueError('Unsupported saved sandbox report')
                if report.get('snapshots') != target.relative_to(self.root).as_posix():
                    raise ValueError('Saved replay path does not match its run')
                payload = target.read_bytes()
                if hashlib.sha256(payload).hexdigest() != report.get('sha256'):
                    raise ValueError('Saved replay SHA256 mismatch')
                snapshots = [json.loads(line) for line in payload.splitlines()]
                if len(snapshots) != report.get('frames') or any(s.get('index') != i for i, s in enumerate(snapshots)):
                    raise ValueError('Saved replay frame sequence mismatch')
            if type(index) is not int or not 0 <= index < len(snapshots):
                raise ValueError('Unknown sandbox snapshot')
            snapshot = copy.deepcopy(snapshots[index])
            snapshot['replay'] = {'run_id': run_id, 'frames': len(snapshots), 'active_match_changed': False}
            return snapshot

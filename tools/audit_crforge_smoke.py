"""Run actual Python-to-Java ZMQ complete episodes in an isolated audit process."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import time
import numpy as np
from crforge_gym import CRForgeEnv

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/audit_candidates/crforge_build'
JAVA = Path(os.environ.get('JAVA_HOME', r'C:\Users\63047\AppData\Local\Programs\Microsoft\jdk-21.0.7.6-hotspot')) / 'bin/java.exe'
port = 29876
cp = (OUT / 'classpath.txt').read_text(encoding='utf-8')
rows = []
server_log = (OUT / 'bridge_server.log').open('wb')
server = subprocess.Popen([str(JAVA), '-Duser.home='+str(ROOT / '.tmp'), '-cp', cp,
                           'org.crforge.bridge.BridgeServer', str(port)], cwd=ROOT, stdout=server_log, stderr=subprocess.STDOUT)
try:
    # Process output rather than a fixed long delay determines readiness.
    deadline = time.monotonic()+20
    while time.monotonic() < deadline:
        if server.poll() is not None:
            raise RuntimeError('bridge stopped before ready')
        if 'Waiting for client' in (OUT / 'bridge_server.log').read_text(errors='replace'):
            break
        time.sleep(0.05)
    else:
        raise TimeoutError('bridge did not become ready')
    for idx, opponent in enumerate(['random', 'rule_based', 'noop']):
        env = CRForgeEnv(endpoint=f'tcp://127.0.0.1:{port}', ticks_per_step=20, opponent=opponent)
        env.action_space.seed(700+idx)
        obs, info = env.reset(seed=700+idx)
        initial = obs.copy()
        replay_obs, _ = env.reset(seed=700+idx)
        assert np.array_equal(initial, replay_obs), 'same-seed reset mismatch'
        start = time.perf_counter()
        attempts = rejected = steps = executed_from_cycle = incorrect_failure_flags = 0
        reward_sum = 0.0
        maximum_entities = 0
        accepted_recorded = False
        trace_path = OUT / f'episode_{opponent}.jsonl'
        with trace_path.open('w', encoding='utf-8') as trace:
            for steps in range(1, 351):
                action = env.action_space.sample()
                attempts += int(action[0] != 0)
                previous = obs
                obs, reward, terminated, truncated, info = env.step(action)
                rejected += int(info.get('action_failed', False))
                # The upstream failure flag is evaluated before its queued deployment executes.
                # For this unique-card deck, a changed hand card-index vector is execution evidence.
                cycled = bool(action[0] and not np.array_equal(previous[15:19], obs[15:19]))
                executed_from_cycle += int(cycled)
                incorrect_failure_flags += int(cycled and info.get('action_failed', False))
                reward_sum += reward
                # Entity records: all-zero padding after the 55-value global prefix.
                maximum_entities = max(maximum_entities, int(np.count_nonzero(obs[55:].reshape(64, 16)[:, 0])))
                accepted_recorded |= cycled
                trace.write(json.dumps({'step':steps, 'action':action.tolist(), 'feedback':info,
                    'reward':reward, 'hand_cycle_execution_evidence':cycled,
                    'terminated':terminated, 'truncated':truncated,
                    'observation':obs.tolist()})+'\n')
                if terminated or truncated:
                    break
            else:
                raise AssertionError('episode did not reach terminal within 350 seconds')
        elapsed = time.perf_counter()-start
        row = {'opponent':opponent, 'seed':700+idx, 'steps':steps, 'ticks_per_step':20,
               'wall_seconds':elapsed, 'steps_per_second':steps/elapsed,
               'attempted_plays':attempts, 'rejected_plays':rejected,
               'accepted_plays_as_upstream_reports':attempts-rejected,
               'executed_plays_verified_by_hand_cycle':executed_from_cycle,
               'false_failure_flags_on_executed_plays':incorrect_failure_flags,
               'reward_sum':reward_sum,
               'terminated':bool(terminated), 'truncated':bool(truncated),
               'same_seed_reset_equal':True, 'observations_changed':bool(not np.array_equal(initial,obs)),
               'max_nonzero_entity_records':maximum_entities,
               'trace_path':str(trace_path.relative_to(ROOT)),
               'obs_shape':list(obs.shape), 'scope':'oracle upstream observation; no official-game agreement implied'}
        assert accepted_recorded and row['observations_changed']
        rows.append(row)
        print(json.dumps(row), flush=True)
        env.close()
finally:
    server.terminate()
    try:
        server.wait(timeout=5)
    except subprocess.TimeoutExpired:
        server.kill()
        server.wait(timeout=5)
    server_log.close()
    (OUT / 'smoke_results.json').write_text(json.dumps(rows, indent=2), encoding='utf-8')

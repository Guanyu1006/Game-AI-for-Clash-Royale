"""Measure candidate spell/economy/card-cycle behavior through its actual Java bridge."""
from __future__ import annotations
import json
import os
from pathlib import Path
import subprocess
import time
import zmq
from crforge_gym.bridge import BridgeClient

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'outputs/audit_candidates/crforge_build'
JAVA = Path(os.environ.get('JAVA_HOME', r'C:\Users\63047\AppData\Local\Programs\Microsoft\jdk-21.0.7.6-hotspot')) / 'bin/java.exe'
SPELLS = ['arrows','fireball','zap','rocket','lightning','poison']
BASE = ['knight','archer','giant','musketeer','minions','valkyrie','skeletons']

def play(c, idx, x, y):
    return c.step({'handIndex':idx, 'x':x, 'y':y}, None)

if __name__ == '__main__':
    log = (OUT / 'mechanics_bridge.log').open('wb')
    cp = (OUT / 'classpath.txt').read_text(encoding='utf-8')
    server = subprocess.Popen([str(JAVA), '-cp', cp, 'org.crforge.bridge.BridgeServer', '29877'],
                              cwd=ROOT, stdout=log, stderr=subprocess.STDOUT)
    results = {'scope':'independent community simulator comparison; neither engine proves official equivalence',
               'crforge_commit':'b41cf9b6276945152ec4c869ce56f98a5fa3f513', 'level':11, 'spells':[]}
    c = BridgeClient('tcp://127.0.0.1:29877', binary_obs=False)
    c.socket.setsockopt(zmq.RCVTIMEO, 15000)
    c.socket.setsockopt(zmq.SNDTIMEO, 15000)
    c.connect()
    try:
        # Readiness follows socket queueing; the transport has a bounded receive timeout.
        for spell in SPELLS:
            c.init([spell]+BASE, ['arrows']+BASE, ticks_per_step=240, seed=7)
            for seed in range(7,30):
                obs = c.reset(seed=seed)
                hand = obs['bluePlayer']['hand']
                slots = [i for i,x in enumerate(hand) if x['id'] == spell]
                if slots:
                    break
            else:
                raise AssertionError('spell absent across deterministic reset seeds')
            # Twelve idle seconds supplies enough elixir for every six-elixir spell.
            obs = c.step()['observation']
            tower = next(t for t in obs['redPlayer']['towers'] if t['type']=='princess')
            before = tower['hp']
            tower_id = tower['id']
            result = play(c, slots[0], tower['x'], tower['y'])
            after = next(t for t in result['observation']['redPlayer']['towers'] if t['id']==tower_id)['hp']
            entry = {'card':spell, 'seed':seed, 'tower_hp_before':before, 'tower_hp_after':after,
                     'tower_damage':before-after, 'cast_position':[tower['x'],tower['y']],
                     'upstream_action_failed':result['blueActionFailed'],
                     'hand_cycled':hand != result['observation']['bluePlayer']['hand']}
            assert entry['tower_damage'] > 0 and entry['hand_cycled']
            results['spells'].append(entry)
        c.init(['arrows']+BASE, ['arrows']+BASE, ticks_per_step=1, seed=7)
        obs = c.reset(seed=7)
        phase_samples = []
        for frame in range(1, 4803):
            obs = c.step()['observation']
            if frame in [1,20,100,2399,2400,2401,3600,3601,4800,4801]:
                phase_samples.append({'frame':obs['frame'], 'seconds':obs['gameTimeSeconds'],
                  'elixir':obs['bluePlayer']['elixir'], 'multiplier':obs['elixirMultiplier'], 'overtime':obs['isOvertime']})
        results['economy'] = phase_samples
        assert phase_samples[0]['elixir'] > 5 and phase_samples[2]['elixir'] < 10
        assert all(s['elixir'] <= 10 for s in phase_samples)
        assert next(s for s in phase_samples if s['frame']==2401)['multiplier'] == 2
        assert next(s for s in phase_samples if s['frame']==4801)['multiplier'] == 3
        c.init(['arrows','fireball','zap','rocket','lightning','poison','freeze','rage'],
               ['arrows']+BASE, ticks_per_step=400, seed=7)
        obs = c.reset(seed=7)
        initial_hand = [x['id'] for x in obs['bluePlayer']['hand']]
        initial_next = obs['bluePlayer']['nextCard']['id']
        obs = c.step()['observation']
        played = initial_hand[0]
        sequence = []
        for turn in range(1,6):
            before_hand = [x['id'] for x in obs['bluePlayer']['hand']]
            before_next = obs['bluePlayer']['nextCard']['id']
            response = play(c, 0, 9.0, 5.0)
            obs = response['observation']
            after_hand = [x['id'] for x in obs['bluePlayer']['hand']]
            sequence.append({'turn':turn, 'played':before_hand[0], 'hand_before':before_hand,
                             'next_before':before_next, 'hand_after':after_hand,
                             'next_after':obs['bluePlayer']['nextCard']['id']})
            assert after_hand[0] == before_next and before_hand[1:] == after_hand[1:]
        assert sequence[3]['next_after'] == played and sequence[4]['hand_after'][0] == played
        results['card_cycle'] = {'initial_hand':initial_hand, 'initial_next':initial_next, 'sequence':sequence}
        print(json.dumps(results, indent=2))
    finally:
        c.close()
        server.terminate()
        server.wait(timeout=5)
        log.close()
        (OUT / 'mechanics_results.json').write_text(json.dumps(results, indent=2), encoding='utf-8')

"""Scan legal native reset seeds for a restricted two-spell agent fixture.

The scanner performs reset/capture only and never submits a card or advances
after the warmup tick. It is useful for selecting an explicit, reproducible
evaluation seed list when a restricted deck's initial hand is randomized.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase2.agent_env import NativeAgentEnv

ALLOWED = {28000000, 28000001}


def fixture(env, seed, level, spell_first):
    match = env.default_match(seed, level)
    if spell_first:
        for owner in (0, 1):
            deck = match['battle'][f'deck{owner}']['sp']
            spells = [card for card in deck if card['d'] in ALLOWED]
            rest = [card for card in deck if card['d'] not in ALLOWED]
            match['battle'][f'deck{owner}']['sp'] = spells + rest
    return match


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--start', type=int, default=10000)
    parser.add_argument('--count', type=int, default=100)
    parser.add_argument('--level', type=int, default=16)
    parser.add_argument('--spell-first-deck', action='store_true')
    parser.add_argument('--output', default='outputs/phase2/native/seed_scan/latest')
    args = parser.parse_args()
    if args.count < 1 or args.count > 10000:
        raise ValueError('count must be in [1,10000]')
    folder = (ROOT / args.output).resolve()
    folder.relative_to(ROOT)
    folder.mkdir(parents=True, exist_ok=True)
    env = NativeAgentEnv(owner=0, trace_path=folder / 'wire.jsonl')
    rows = []
    try:
        for offset, seed in enumerate(range(args.start, args.start + args.count)):
            owner = offset % 2
            env.owner = owner
            env.reset(seed=seed, options={'level': args.level,
                                           'match': fixture(env, seed, args.level, args.spell_first_deck)})
            actor = env.actor_observation()
            cards = [card['card'] for card in actor['hand']]
            rows.append({'index': offset, 'seed': seed, 'owner': owner, 'hand': cards,
                         'allowed_spells': sorted(ALLOWED.intersection(cards)),
                         'eligible': bool(ALLOWED.intersection(cards))})
    finally:
        env.close()
    report = {'schema': 'royale-phase2-seed-scan.v1', 'status': 'pass',
              'configuration': vars(args), 'rows': rows,
              'eligible_seeds': [row['seed'] for row in rows if row['eligible']],
              'eligible_count': sum(row['eligible'] for row in rows),
              'scope': 'Reset/capture only; no card submission, no training or model selection'}
    (folder / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'report': str((folder / 'report.json').relative_to(ROOT)),
                      'eligible_count': report['eligible_count'],
                      'eligible_seeds': report['eligible_seeds']}, ensure_ascii=False))


if __name__ == '__main__':
    main()

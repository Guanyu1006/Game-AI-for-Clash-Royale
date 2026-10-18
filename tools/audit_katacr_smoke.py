"""Dependency/utility smoke only: no device taps or remote account actions."""
import ast
import importlib
import json
from pathlib import Path
import sys
import traceback

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'third_party/audit_candidates/KataCR'
sys.path.insert(0, str(SOURCE))
row = {'repo':'https://github.com/wty-yy/KataCR', 'commit':'36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8',
       'scope':'utility import and dependency probe, no game/perception reproduction', 'ast_parsed':0, 'ast_failures':[]}
for path in (SOURCE / 'katacr').rglob('*.py'):
    try:
        ast.parse(path.read_text(encoding='utf-8'), filename=str(path))
        row['ast_parsed'] += 1
    except Exception as e:
        row['ast_failures'].append({'path':str(path.relative_to(ROOT)), 'error':repr(e)})
try:
    m = importlib.import_module('katacr.constants.card_list')
    row['constants_import'] = 'passed'
    row['card2elixir_count'] = len(m.card2elixir)
    row['sample_knight_elixir'] = m.card2elixir.get('knight')
except Exception as e:
    row['constants_import'] = repr(e)
try:
    importlib.import_module('katacr.policy.env.interact_env')
    row['interact_env_import'] = 'passed'
except Exception as e:
    row['interact_env_import'] = repr(e)
    row['interact_env_traceback'] = traceback.format_exc()
row['full_game_loop'] = 'unverified: no dedicated phone stream, perception weights, policy checkpoint or Linux CUDA stack'
out = ROOT / 'outputs/audit_candidates/KataCR_smoke.json'
out.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(row, ensure_ascii=False, indent=2))

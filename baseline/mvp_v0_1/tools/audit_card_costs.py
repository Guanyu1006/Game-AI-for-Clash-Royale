"""Read the pinned MIT reference as syntax; never execute downloaded code."""
import ast
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent.parent
tree=ast.parse((ROOT/'references/upstream/KataCR/katacr/constants/card_list.py').read_text(encoding='utf-8'))
groups=None
for node in tree.body:
    if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='elixir2card' for t in node.targets):
        groups=ast.literal_eval(node.value)
if groups is None:raise ValueError('Upstream cost table missing')
costs={name.replace('-','_'):cost for cost,names in groups.items() for name in names}
cards=json.loads((ROOT/'royale_ai/data/cards.json').read_text(encoding='utf-8'))['cards']
bad=[{'card':c['id'],'ours':c['cost'],'reference':costs.get(c['id'])} for c in cards if costs.get(c['id'])!=c['cost']]
report={'checked_cards':len(cards),'mismatches':bad,'scope':'matches pinned upstream table, not latest balance verification'}
(ROOT/'outputs').mkdir(exist_ok=True)
(ROOT/'outputs/card_cost_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
print(json.dumps(report))
if bad:raise SystemExit(1)

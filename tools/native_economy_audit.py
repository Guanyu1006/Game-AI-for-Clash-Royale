"""Record current rate/phase/legality implementation and preserved baseline."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'third_party/ClashRoyaleAi/python_ai'))
import clash_royale_env as n
BASE=[0,1,2,3,4,5,6,7]
e=n.ClashRoyaleEnv(BASE,BASE,3000);e.seed(7)
rows=[]
for tick in [1,10,28,50,1199,1200,1201,1799,1800,1801,2399,2400,2401]:
    e.advance_ticks(tick-e.get_current_tick())
    rows.append({'tick':tick,'seconds':tick/10,'elixir':e.get_elixir_for_team(0),'multiplier':e.get_elixir_multiplier(),'done':e.is_game_over()})
assert n.elixir_multiplier_at_tick(1800)==2
assert n.elixir_multiplier_at_tick(2400)==3
assert abs(rows[1]['elixir']-(5+10/28))<.00001
assert abs(rows[2]['elixir']-6)<.00001
probe=n.ClashRoyaleEnv(BASE,BASE,3000);probe.seed(7);probe.set_hand_for_team(0,[0,1,2,3])
before=probe.state_dict();assert probe.can_play(0,0,3,10);assert before==probe.state_dict()
out={'version':'ClashRoyaleAi@2f172925a15b+lab-calibrated-v2','tick_seconds':.1,'base_elixir_per_tick':1/28,
     'phase_samples':rows,'can_play_no_mutation':True,'source':'crforge decoded standard-1v1 rules; official timing not certified'}
(ROOT/'outputs/native_engine_economy_patched.json').write_text(json.dumps(out,indent=2),encoding='utf-8')
source=ROOT/'outputs/audit_candidates/backend_differential.json'
if source.exists():
    data=json.loads(source.read_text(encoding='utf-8'))
    (ROOT/'outputs/native_engine_economy_baseline.json').write_text(json.dumps({'native_phase_samples':data['native_phase_samples'],'source':str(source.relative_to(ROOT)),'version':'pre-economy-crown-v1'},indent=2),encoding='utf-8')
print(json.dumps(out,indent=2))

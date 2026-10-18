"""Platform policy contract: deliberately simple, intended for replacement.

run_platform.ps1 -Mode Agent -PolicyFile examples/platform_policy.py
Optional -InformationMode oracle passes all *captured* probe fields. The same
module supports player-only input. General placement rules are not proven.
No replay future commands or future observations are passed to this function.
"""
def decide(observation):
    if observation.get('information_mode')=='researcher_oracle':
        player=next(p for p in observation['players'] if p['owner']==0)
        cards=[{'slot':c['slot'],'card':c['card_id'],'cost':c['cost_elixir']} for c in player['hand'] or []]
        elixir=player['elixir_raw']; tick=observation['tick']
    else:
        cards=observation['hand'];elixir=observation['own_elixir_raw'];tick=observation['tick']
    affordable=[c for c in cards if c['cost']*10000<=elixir]
    if not affordable:return {'kind':'wait'}
    card=min(affordable,key=lambda c:(-c['cost'],c['slot']))
    # Native owner0 starts at low y; alternate lanes. Coordinates are native
    # world units, not browser pixels. This toy rule makes no strength claim.
    x=3500 if (tick//20)%2==0 else 14500
    y=25500 if card['card']>=28000000 else 11000
    return {'kind':'play_card','slot':card['slot'],'x':x,'y':y}

"""Explicit utility and training reward. Own view has team 0 at y=0."""
def utility(obs,catalog):
    score=0
    for t in obs['towers']:
        sign=1 if t['team']==0 else -1
        weight=14 if t.get('king') else 8
        score+=sign*weight*t['hp']/max(1,t['max_hp'])
        if t['hp']<=0:score-=sign*(150 if t.get('king') else 22)
    for u in obs['units']:
        c=catalog.get(u['card_id'],{})
        ratio=max(0,min(1,u['hp']/max(1,u.get('max_hp',u['hp']))))
        value=c.get('cost',3)/max(1,c.get('count',1))*ratio
        height=obs.get('height',34)
        progress=u['y']/height if u['team']==0 else (height-u['y'])/height
        score+=(1 if u['team']==0 else -1)*value*(.3+.2*progress)
    score+=.2*obs['elixir']
    return score

def transition_reward(before,after,catalog,result=None):
    value=.1*(utility(after,catalog)-utility(before,catalog))
    if result is not None:value+=10 if result=='win' else -10 if result=='loss' else 0
    return value

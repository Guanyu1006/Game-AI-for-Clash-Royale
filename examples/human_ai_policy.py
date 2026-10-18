"""这里就是人机演示的 AI。修改 decide() 后重新启动 run_play.ps1。

输入：己方手牌/圣水、塔、非塔实体、已经发生的双方下牌历史。
输出：wait 或 play_card(slot,x,y)，reason 是实际规则分支的说明。
这是可解释的规则基线，不是训练好的强化学习模型，也不是语言模型。
"""


def decide(observation):
    if observation.get('information_mode')!='battlefield':
        raise ValueError('这个演示策略需要 battlefield 输入')
    owner=observation['native_owner']; tick=observation['tick']
    history=observation['history']
    own=[play for play in history if play['owner']==owner]
    if own and tick-own[-1]['tick']<40:
        return {'kind':'wait','reason':'刚刚已出牌；规则要求间隔至少2秒，保留圣水观察。'}
    cards=[card for card in observation['hand'] if card['cost']*10000<=observation['own_elixir_raw']]
    if not cards: return {'kind':'wait','reason':'当前手牌都不够圣水，等待恢复。'}
    enemies=[entity for entity in observation['entities'] if entity['owner']==1-owner and (entity['hp'] or 0)>0]
    enemies.sort(key=lambda entity:entity['y_raw'] if owner==0 else -entity['y_raw'])
    spells=[card for card in cards if card['card'] in (28000000,28000001)]
    if enemies and spells:
        target=enemies[0]; card=min(spells,key=lambda item:(item['cost'],item['slot']))
        x,y=target['x_raw'],target['y_raw']
        reason=f"观察到对方{len(enemies)}个非塔对象，向最靠近己方的对象位置({x},{y})施放法术。"
    else:
        units=[card for card in cards if 26000000<=card['card']<28000000]
        if not units: return {'kind':'wait','reason':'当前没有合适部队，且未观察到可选法术目标。'}
        card=min(units,key=lambda item:(item['cost'],item['slot']))
        x=enemies[0]['x_raw'] if enemies else (3500 if len(own)%2==0 else 14500)
        y=11000 if owner==0 else 21000
        reason=f"观察到对方{len(enemies)}个非塔对象、{len(history)}次双方下牌；选择当前最便宜部队，在对应路线部署。"
    return {'kind':'play_card','slot':card['slot'],'x':max(0,min(17999,int(x))),
            'y':max(0,min(31999,int(y))),'reason':reason}

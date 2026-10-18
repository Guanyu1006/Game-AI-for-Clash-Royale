# 实际实验教程

先按START_HERE启动。以下接口/命令均对应实现，具体验收结果看DELIVERY_REPORT。

## 真正执行与反馈

```powershell
.\.venv\Scripts\python.exe -m royale_lab demo --seed 7 --policies random greedy --max-ticks 600
```

输出有final、replay路径与accepted数量。600tick是一分钟课程，默认3000tick研究局。不是dry-run：动作进入C++战斗，日志记录扣费和轮转。

```python
from royale_lab.backend import NativeBackend
from royale_lab.contracts import Action
b=NativeBackend(seed=7)
a=next(Action(**r) for r in b.observe()['legal_actions'] if r['kind']=='card')
print(b.apply(0,a,command_id='first'))
b.advance(10)
print(b.observe()['tick'])
```

重复command_id不再次执行；delay_ticks指定未来tick执行，但原卡槽那时可能已改变，仍重新验合法性。

## 改搜索深度

```python
from royale_lab.backend import NativeBackend
from royale_lab.policies import SearchPolicy
b=NativeBackend(seed=7)
p=SearchPolicy(b.catalog,horizon_ticks=80,budget_ms=40,max_candidates=12)
d=p.decide(b.observe());print(d.to_dict());print(b.apply(0,d.action))
```

预算在候选间检查，是软墙钟预算；一次完整前推可能超时。正式评估40tick/6候选/15ms，UI默认60tick/10候选/35ms，不能当作同配置。

## 修改奖励与训练

编辑rewards.py的终局分数/塔权重，先跑tests，再重训；旧模型没有自动学习新奖励。查是否靠WAIT囤水或无效法术投机，终局胜负比训练回报更重要。

```powershell
.\.venv\Scripts\python.exe -m royale_lab train-ppo --steps 8192 --seed 72
.\.venv\Scripts\python.exe -m royale_lab evaluate --policies ppo greedy --games 100 --output outputs/evaluation/reward-experiment
```

`--resume --steps 16384`恢复optimizer/model到目标累计步数，但从新的环境局势开始，未恢复中途rollout，不能声称位级连续。BC的--resume复用整局数据重新训练，不是optimizer续训。

## 替换对手估计/网络

保留Belief.update/to_dict/available接口，使用public events，不读oracle.players。测试事件重复不会多扣费、回到旧局势重建memory、不完整历史保持宽区间。换CandidateNet或encode时同步修改ENCODER_VERSION并重训，旧schema明确拒绝。保留mask，不合法候选的logit置极小值；最终通过Session实际完整对局。

## 消融与失败重演

```powershell
.\.venv\Scripts\python.exe -m royale_lab evaluate --policies search search_no_response --games 100 --output outputs/evaluation/response-ablation
.\.venv\Scripts\python.exe -m royale_lab replay outputs/evaluation/main/search/000.replay.json
```

两策略使用相同seed/deck/side/opponent。report含配对差，summary含胜平负/塔伤/拒绝/延迟。每局都有replay和decisions.json.gz；输局/拒绝另外保存failure.json。用这些判断问题出在候选、预测、奖励还是执行。

## HTTP加载局势

```powershell
$game=Invoke-RestMethod http://127.0.0.1:18765/api/new -Method Post -ContentType application/json -Body '{"controllers":["human","greedy"]}'
$body=@{session=$game.session;path='outputs/evaluation/main/search/000.replay.json'} | ConvertTo-Json
Invoke-RestMethod http://127.0.0.1:18765/api/load -Method Post -ContentType application/json -Body $body
```

界面也可保存/恢复/分叉。只接受当前engine版本的项目内JSON，服务最多12活动session，close旧局。

## 校准引擎

受支持数值优先按BALANCE_CONFIG.md使用真实JSON载入，无需改策略或重新编译。改变行为/效果仍按ENGINE_GUIDE编译。不同数值模型用独立输出；旧默认模型在自定义数据下拒绝。

从mechanic_coverage选一条近似/未验证机制，构造场景、保存轨迹，再对照独立引擎或合法参考录像。新增自写测试只证明代码行为，不能自动改成官方一致。卡牌/原生测试流程见ENGINE_GUIDE。

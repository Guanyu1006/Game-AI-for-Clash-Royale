# 实体、空间与时序模仿策略

本目录策略独立于历史 `royale_*` 模型。`model.py` 只消费环境适配器给出的公开观察，不读取引擎内部状态。测试中的合成样本只验证接口、梯度和遮罩正确性，不属于高手数据或棋力证据。

## 输入与动作契约

| 字段 | 单帧形状 | 含义 |
| --- | --- | --- |
| `global_features` | `G` | 环境定义的公开全局特征 |
| `entities` | `N × E` | 可见实体的类型、位置、血量等公开特征 |
| `entity_mask` | `N` | 实体存在且允许策略观察；填充行不参与运算 |
| `hand_ids` | `4` | 己方手牌的词表编号 |
| `legal_cards` | `8` | `0=WAIT`，`1..4=当前手牌槽`，`5..7=技能按钮` |
| `legal_positions` | `4 × (H×W)` | 各手牌槽允许部署的位置 |

离线张量增加 `[batch,time]` 两个前导维度。实体的 `x/y` 默认位于特征 `0/1`，且必须是玩家视角 `[0,1]` 坐标；可通过 `PolicySpec` 指定其它索引。特征顺序、卡牌词表与环境版本属于观察契约，训练和推理必须一致。

模型输出 `card` 与 `position`，位置是环境定义的行优先网格编号。模型不再转换朝向或坐标。WAIT 和技能动作的 `position=0` 且不计位置概率；只有手牌部署计算落点损失。WAIT 必须始终合法；缺少任何合法部署格的牌自动禁止出牌。被禁止的动作概率严格为零。

## 网络

1. 实体 MLP 编码保留每个实体的公开特征；由全局状态和手牌产生查询，做掩码注意力汇聚。
2. 同一批实体按公开位置投影到默认 `8×6` 空间网格，保留单元格内实体特征和占用计数，再经两层卷积编码。
3. 全局特征、实体注意力、空间编码、手牌身份嵌入和可见实体数融合后输入 GRU。
4. WAIT 与每张当前手牌分别打分；位置分布依赖所选卡牌身份和循环状态。

隐藏维度、空间大小与分支开关可配置。默认模型适合 CPU 验证，参数总量由最终环境维度决定；规模不等于策略强度。空间网格是表达方式之一，没有替代原始实体输入。

`PolicySpec(use_entities=False)` 可以训练无实体对照；`use_spatial=False` 和 `use_recurrent=False` 分别用于空间与记忆分支对照。`forward(..., ablate_entities=True)` 或 `PolicyAgent(..., ablate_entities=True)` 在相同权重下清除全部实体输入，适合诊断模型是否使用战场信息；这类消融不能单独证明实体改善了泛化胜率。

## 使用

`EntityGRUPolicy.forward()` 接收张量序列，返回卡牌与条件位置 logits、下一循环状态、实体注意力和有效动作 mask。训练应对所有有效行计算动作损失，只对 `1..4` 手牌部署行计算所选槽位的位置损失。落在非法动作上的标签应由数据检查拒绝并计数，不得通过训练时解除合法 mask 强行接受。

`decode_actions()` 是统一解码函数，离线评估和 `model.act()` 共同使用。`PolicyAgent` 保存每个独立玩家的循环状态，每局必须 `reset()`；不能在不同玩家或对局之间共享记忆。序列训练的 `episode_starts` 可在指定时间行前重置状态。

`save_policy()` 原子写入规格、纯张量权重和 JSON 元数据，默认拒绝覆盖。`load_policy()` 使用 `weights_only=True`，检查格式、精确规格字段、严格权重键与形状、有限值，支持强制校验规格和 SHA256。训练产物还应在元数据中记录环境、观察词表、数据分组与来源身份。

```bash
.venv-cloud/bin/python -m unittest tests_cloud.test_arena_model -v
.venv-cloud/bin/python -m unittest tests_cloud.test_arena_evaluation -v
```

测试覆盖序列与逐帧解码一致、每局重置、填充/不可见实体不影响输出、可见实体及位置能影响输出、非法动作概率为零、卡牌条件位置、消融完整性、严格保存加载与真实 CPU 梯度更新。实际高手模仿和闭环对局的质量应查看本次训练与评估报告。

## 独立闭环评估

`evaluation.py` 不更新或挑选模型。运行前冻结卡组、形式/等级配置、种子、换边和对手清单；每个模型版本与相同权重的无实体版本使用同一清单。默认提供 WAIT 和合法随机基线；只有环境明确实现脚本策略时才允许声明使用脚本对手。

评估拒绝未记录正数训练步数的 checkpoint，以及环境身份或观察规格不匹配的 checkpoint。输出逐局 JSONL、摘要和带体积上限的 gzip 公开观察 trace。完整自然终局、引擎/评估预算截断、异常分别计数；只有自然终局参与胜负比例，并同时展示失败与截断分母。模型耗时、环境耗时、重置、端到端实时倍率、决策吞吐和自然完成局数每小时分别报告。

```bash
.venv-cloud/bin/python -m arena_lab.evaluation \
  --checkpoint <训练目录>/best.pt \
  --cases <冻结评估清单.json> \
  --output <新的评估目录>
```

清单为 `EvaluationCase` 对象列表，必须明确 `case_id`、`seed`、两套 `decks`、`candidate_side`、`opponent`。`reset_options` 可记录 `forms`、`levels`、`tower_levels`、`shuffle`、`elixir_milli`；改变双方时，相关双侧配置也必须一起交换。`paired_cases()` 可以为普通卡组自动生成同种子双侧清单。原始引擎状态不会写入公开 trace；实体特征采用可重建的稀疏表示，丢帧间隔及体积限制明确记录。

`paired_cases()` 也接受 `{"decks": [己方八牌, 对方八牌], "reset_options": {...}}`，自动将双方的 forms、levels、tower_levels、elixir_milli 随座位一起交换。

将一次评估生成单文件离线图形回放：

```bash
.venv-cloud/bin/python -m arena_lab.replay_viewer \
  --evaluation <评估目录> --variant model --episode 0 \
  --output <新文件.html>
```

这个文件包含全部所需脚本和采样公开状态，不依赖外部网页、图像或 CDN；可播放、拖动、调速并查看己方手牌、圣水、动作和可见实体血量。它绘制的是模拟器公开状态，不是官方画面，不能补出没有保存的中间过程。当前图形渲染适配 Royale public-v2；crforge 的额外 known 位及原生类型词表尚未完成图形适配，入口明确拒绝该布局，避免把未知血量显示成测得的零血量。第二后端仍可使用原始公开 trace 和逐局 JSON 报告。

安装了仓库可选 Playwright/Chromium 依赖时，可独立复现真实浏览器验收；默认要求页面来自已训练检查点：

```bash
.venv-cloud/bin/python -m arena_lab.replay_browser_check \
  --html outputs/arena-lab-20261113/trained-viewer-v1/replay.html \
  --output outputs/arena-lab-20261113/my-replay-browser-check
```

它检查单步、前后帧、拖动、20 倍播放、暂停、四手牌、实体点击及画布，保存截图和报告，并验证没有 JavaScript 错误和外部网络请求。正式回放固定选取 `evaluation-v1` 的 `model` 第 0 局，未按胜负筛选；该局保存 90 个公开采样帧，每 4 次联合决策采一次。

分别测量已训练策略的 batch=1 在线式递归推理，以及 batch=32 的离线序列推理：

```bash
.venv-cloud/bin/python -m arena_lab.policy_benchmark \
  --checkpoint <训练目录>/best.pt --output <新推理基准.json>
```

该脚本先在真实模拟器采集观察，再对缓存观察推理；预热与计时分开。它报告 CPU 线程数、参数、哈希、形状、内存大小、延迟与决策吞吐。这些数值不包含对局推进、反向传播、优化器或截图识别；完整闭环吞吐应查看 `evaluation.py` 的逐局报告。

正式 243,045 参数检查点的 256 帧观察、三轮逐帧推理测得：单线程 622.7 决策/秒、p95 1.786 ms；双线程 647.2 决策/秒、p95 2.292 ms。离线 `B=32,T=16`、20 次推理分别为 1075.4 和 1811.7 帧/秒。原始结果是 `trained-policy-benchmark-1threads.json` 和 `trained-policy-benchmark-2threads.json`；同一权重 SHA256、1008 训练步均记录在报告中。批量吞吐不能替代在线延迟或棋力结论。

## 历史卡组补证与独立假设审计

`manifests/leaderboard-decks-202607.json` 保存公开榜单中 100 个账号的卡组超链接事实，包括账号、八张带形态后缀的牌、来源和日期、事实行 SHA-256。它表示 2026 年 7 月赛季结算的公开历史快照，不证明下载回放当局的进化／英雄配置。目标卡组页返回 403 时也只记录博客已公开的超链接，不声称读取目标页正文。

从固定源索引重现账号和全部八张基础牌的匹配：

```bash
.venv-cloud/bin/python -m arena_lab.enrichment \
  --source-index data/arena-lab/cochon-historical-top100-v1/source-index.json \
  --destination outputs/arena-lab-20261113/my-historical-deck-hypotheses.json
```

212 局中有 136 个席位满足精确账号及八张牌匹配，17 局双方均匹配。同牌型但不同账号的证据单列，不能自动补到原局。原始 forms、能力身份仍保持 unknown。

固定取训练分割按 game_id 排序的前五局，比较全基础牌、历史形态、历史形态加唯一能力假设三组：

```bash
.venv-cloud/bin/python -m arena_lab.hypothesis_audit \
  --source-index data/arena-lab/cochon-historical-top100-v1/source-index.json \
  --hypotheses outputs/arena-lab-20261113/my-historical-deck-hypotheses.json \
  --output outputs/arena-lab-20261113/my-historical-hypothesis-audit \
  --split train --limit 5
```

能力推断必须同时满足：卡组历史候选允许该英雄或卡牌本来是 champion、己方战场中存在对应存活实体、己方公开按钮可用、合法 mask 允许，并且只有一个符合条件的按钮。没有候选或存在歧义时继续标未知；即使唯一也只称推断，不认证源能力身份。审计保留每次按钮证据、原生反馈、首次分歧、终局、覆盖分母和环境哈希，不导出训练标签，不修改已有重建数据。

本次五局实际结果保存在 `outputs/arena-lab-20261113/historical-hypothesis-train5-v1/`：历史形态加能力推断使首次命令分歧前的合法出牌窗口从 167 增至 406，并得到 15 个能力窗口；15 次推断执行均被原生接受，另 2 次仍未知。但 44 个源事件因模拟器提前结束未到达，结束时间也不等于源记录。因此它展示了补证方向的价值，不能证明复原了官方高手战场；`strong_imitation_delivery_gate_passed` 始终为 false。

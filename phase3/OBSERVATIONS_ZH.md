# 第三阶段：观察、历史与公开 belief

本页说明 `royale_phase3/observation.py`、`belief.py` 的实际信息边界。它们复用第二阶段 `battlefield.py` 和 `visibility.py`，没有复制战斗引擎，也不会连接原生 RPC。

当前最重要的限制是：**公平观察仍不能看见非塔对象。** 原生探针虽然记录普通单位、建筑、投射物等对象，但没有通过验证的隐身、地下、生成中与瞬态可见性标志。因此不能把探针发现的对象自动称为玩家可见。当前实现选择保守排除所有非塔对象，包括己方对象；这比玩家实际所见更少。研究模式保留对象，但必须分开保存数据、模型和结果。

## 两种观察

| 模式 | 实际内容 | 使用口径 |
|---|---|---|
| `fair` | 己方手牌、费用、圣水、时钟、皇冠、经过 reset 身份核验的塔、双方已经发生的原生消费事件 | 保守部署输入；非塔实体槽全部为空 |
| `research_battlefield` | 前述内容，加已采集非塔对象的位置、HP、原生类别、同局匿名轨迹 | 可见性未验证的研究观察，不能称公平策略 |

调用者写入 `visibility="visible"` 不能解锁公平实体；提供 `visibility_evidence` 也会直接拒绝。需要先实现新的、可核查的可见性证据适配器，再改变这一边界。

敌方精确圣水、手牌、私有轮转、隐藏冷却、攻击目标、未来回放命令、文件路径、质量标签、重建来源和“排除了多少个对象”等诊断信息，都不进入公平 actor 或 affordability mask。诊断只保存在 `projector.diagnostics`，不能再拼回策略特征。

## 字段与缺失值

玩家观察 schema 是 `royale-phase3-player-observation.v1`。

| 字段 | 含义与限制 |
|---|---|
| `tick`、`dt_seconds` | 固定版本 20 Hz；相邻实际观察的时间差为 tick 差除以 20。首条为 `None`。tick 原点是原生初始化约定，不自动等于外部视频或 source 时间原点 |
| `native_owner` | 当前视角 0 或 1；同一 episode 不允许中途换边 |
| `hand`、`hand_complete` | 只有己方当下手牌。未就绪的缺失槽保留为空，不填一张费用为零的假牌 |
| `own_elixir_raw` | 己方当前观测，10000 原始单位等于 1 圣水；缺失时拒绝投影，不把未知填零 |
| `towers` | 只有通过 reset 六塔身份核验的塔；消失与 HP 未知分别保留 `present` 与 `None` |
| `history` | 已发生且不晚于当前 tick 的原生消费事件。记录相对阵营、时间、事件卡牌 ID 与公开费用；不是计划动作或排队回执 |
| `entities` | fair 为空；研究模式包含同局匿名 track、相对阵营、原生类别、卡牌 ID、位置、HP、max HP 与 present |
| `disappeared_tracks` | 研究对象在相邻采集中消失；这不证明死亡，也不能推断完整击杀因果 |

历史位置 `x/y` 当前一律为 `None`：控制器请求的落点是意图，不能冒充公开看到的最终落点。原生事件卡牌 ID 为 0 时，卡牌和费用都保持未知；不能用费用或先验反向替换为已知卡牌。事件身份也不自动完成进化牌、特殊形态与根卡牌别名解析。

研究对象的位置与动作落点是两个契约。原生对象可能越过 18×32 的合法部署矩形；研究观察保留有符号原生位置，不截成边界值。动作模块仍单独验证可下牌区域。

连续观察必须包含完整、只追加的当前局历史。历史删改、未来事件、重复序号、时间倒退或未 reset 的视角切换都会失败。换 generation 自动清空 projector；新建后端如果重新用了相同 generation，必须显式调用 `reset()`。

## 固定向量与坐标

编码 schema 是 `royale-phase3-player-vector.v1`。默认卡牌词表 8 张、实体上限 16、最近历史 16 条：

| 部分 | 宽度 |
|---|---:|
| 时钟、己方资源、完整性、皇冠、视角、时间差及 mask | 8 |
| 4 个手牌槽，每槽 13 | 52 |
| 6 个塔，每塔 6 | 36 |
| 16 个实体槽，每槽 23 | 368 |
| 16 条历史，每条 14 | 224 |
| 总计 | **688** |

关闭实体和历史时为 96 维。所有配置必须与 checkpoint 一起冻结，不应给已训练权重静默更换维度。词表外卡牌有独立 unknown 指示，不会映射成某一张已知牌。

可缺失的数值采用 `[value, known_mask]`：未知为 `[0,0]`，已测量的零为 `[0,1]`。不存在的实体或手牌槽使用全零槽；不能把它当作血量为零的已知对象。原生 ID、匿名 track 和 generation 都不进入向量。研究实体按可见特征排序，原生 ID 重编号和同位置对象行序不会改变向量。

原始世界单位为横向约 18000、纵向约 32000，每 tile 对应 1000 单位。观察 JSON 保留原生坐标；**只在编码一次**把 owner 1 变为己方在上方的视角：`x'=17999-x`、`y'=31999-y`，再分别除以 18000、32000。屏幕像素不进入这个编码，动作反变换由动作模块承担。研究对象越界时归一化数值可超出 `[0,1]`，这是实际测量而不是下牌许可。

`affordability_mask()` 返回 `[WAIT, slot0, slot1, slot2, slot3]`。它只核对当下手牌槽与预算，**不等于完整合法性**，没有使用隐藏就绪状态、敌方信息或最终部署结果。

## 公开 belief

`PublicBelief` 只读上述观察与当前锁定版本 CSV；默认对手圣水区间保持 `[0,10]`。这是未知资源机制下的保守边界，不是有预测力的准确估计。

当前版本的 `game_modes.csv` 和 `battle_timelines.csv` 经 manifest 文件 hash 核对后，Ladder/Default 模式 `72000006` 对应：

| 原生 tick 区间 | 每 tick 再生原始单位 |
|---|---:|
| 0–2400 | 178 |
| 2400–4800 | 357 |
| 4800–6000 | 537 |

这些数值来自当前版本，不来自第一阶段近似引擎。阶段切换 tick 尚缺精确先后顺序证明，传播时使用相邻速率的上下界。其他模式、超出时间表、未知费用或资源机制不明时会扩大不确定性。

只有显式设置 `ordinary_resource_only=True`，并在实验中另附固定普通卡组、无资源改变机制的证据，才启用较窄区间。这个选项不是自动证书；不能因为代码能计算区间就声称已经校准。观察到与资源模型不一致的真实消费事件时，会扩大区间并在诊断中记录不一致，不删除事件来提高成绩。

当前没有通过核验的“四张其他牌后回手”等轮转规则证据。因此敌方手牌分布、下张牌分布、回手所需最少其他出牌数都保持 `None`，未知卡组保持未知。已展示卡牌的次数、观察频率和上次出现时距可以使用；观察频率不是回手概率或下一张牌的预测概率。

默认 `encode_belief()` 是 15 维。相同当前状态但不同过去事件会得到不同历史/频率特征；它们是否帮助策略，需要训练与共同条件下的评估，不能由接口测试断言。

## 开发时怎样接入

```python
from royale_phase3.observation import ObservationProjector, encode_observation
from royale_phase3.belief import PublicBelief, encode_belief

projector = ObservationProjector(profile="fair")
belief = PublicBelief()  # 默认保持保守资源区间

# 每个独立 episode 开始都执行，哪怕后端重复使用同一 generation。
projector.reset()
belief.reset()

# battlefield 由当前局实际 capture + 已发生事件构建。
observation = projector.project(battlefield)
vector = encode_observation(observation)
belief_vector = encode_belief(belief.update(observation))
```

这段是接入示意，`battlefield` 需要由平台提供。循环策略的隐状态仍应由调用方管理：重复读取同一份缓存观察不能重复更新 GRU；每局清零，训练片段不能跨局。projector 返回相同输入的 detached 缓存副本，belief 在同一输入下幂等，但它们不会替策略模型清零隐状态。

fair 实体全部为空，所以“在单帧基础上只加入 fair 实体槽”的 B 对照没有新增战场信息。若用研究实体训练，实验应命名为 `research_battlefield` 并独立报告；不能据此宣称公平 B 获得改善。

## 已保存证据与复查

当前模块证据入口为 `outputs/phase3/observation/run-003/report.json`，包含源代码与 CSV hash、真实命令、36 项单元检查、两个已保存完整原生对局的投影核查和 3 张历史原生 PNG 的绑定关系。

- 两局共有 1133 条保存状态，双视角合计 2266 个输入。逐条改变敌方手牌、精确圣水、轮转、未知实体数量、未来 journal 后缀及质量路径，公平观察、向量和预算 mask 均保持不变。
- 正控改变已发生公开事件，确认历史与 belief 输入确实变化。负控包括未来事件、历史删改、reset、视角切换、缺失与已知零、CSV pin 篡改、未知机制和超出时间表。
- `samples/collection-*` 是实际采集 decision tick 的动作前观察；headless 采集没有配套 PNG，已明确标注。
- `samples/historical-render-*` 引用既有第二阶段原生截图，核对 PNG hash 与相同 tick 的 native semantic hash。tick 90 另核对人工动作前 owner 1 的真实输入；tick 110/194 只是绑定的暂停状态，不能冒充一 tick 后 AI 输入。
- 历史截图显示双方 HUD，属于研究审计材料；截图像素不作为 actor 输入。单张普通单位可见的画面不足以认证隐身、地下和瞬态机制。
- `run-001/failure.json` 保留首次真实记录检查中“实体位置被错误限制为部署矩形”的失败；修正与回归后分别保存 run-002、run-003，没有覆盖旧结果。

运行单元检查：

```powershell
.\.venv-phase2-core\Scripts\python.exe -B -m unittest tests_phase3.test_observation tests_phase3.test_belief -v
```

重新核查已保存原生数据并生成独立证据目录：

```powershell
.\.venv-phase2-core\Scripts\python.exe -B outputs/phase3/observation/build_evidence.py --run-id run-004
```

该命令不启动 VM、不接触 RPC/ADB/SSH；输出目录已存在时拒绝覆盖。原生数据后来扩展时需使用新 run ID，并在报告中核对新的分母。

**尚未由本模块验收的部分：**普通非塔可见性证书、已知特殊机制范围、可信轮转概率、资源区间校准、真正使用新输入的训练—保存—加载—原生闭环，以及策略强度改善。只有集成训练与原生评估的对应证据完成后，才能更新这些状态。两个真实整局与接口检查也不满足任务书的 50 局/2000 次出牌覆盖门槛。

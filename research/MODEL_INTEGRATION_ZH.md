# 在当前系统中加入自己的算法

当前可直接使用的是 **GRU C 序列行为克隆（BC）**：保存数据 → 监督训练 → 保存权重 → 新进程 validation。训练不启动 Android。工作台尚未提供任意模型插件接口；换成 Transformer、世界模型或 RL，需要下面列出的接入改动。

## 1. 先从配置和数据开始

日常入口：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py research
```

编辑 [`system.json`](system.json) 后重启工作台。它集中登记当前运行目录：

| 字段 | 研究者可调整的内容 |
|---|---|
| `experiment_defaults` | `epochs`、`hidden`、`seed`、`play_weight`；`games`、`max_tick`、`wall_seconds` 和预先固定的录像局号 |
| `datasets` | 新数据集 ID、metadata 路径、名称与 `offline_only`；原生版本不匹配的数据只能离线使用 |
| `reference_checkpoints` | 页面可选参考模型的 JSON 文件；每个 JSON 还引用经过校验的 NPZ 权重 |
| `paths` | 新实验为 `research/runs`；原生清单为 `research/runtime/version.json`；现有 Android 安装缓存保持独立 |

`model` 目前只接受 `gru-c`；修改这个字符串不能接入新模型。当前工作台固定 10 tick 控制、序列长 32、burn-in 8、batch 8、学习率 0.001。学习率和序列参数尚未作为 `system.json` 字段开放；未知字段会报错。高级现有 A–E 消融参数见 [`tools/phase3_train.py`](../tools/phase3_train.py)，它仍使用同一 `SmallPolicy`，不是任意模型加载器。

新增数据应保存新的 metadata 和 rows，不覆盖冻结数据。metadata 必须记录 rows 路径及 SHA256、原生版本、特征维度、卡牌词表、控制频率和整局划分；修改 rows 后必须重新导出这些派生信息。登记路径只登记资源，不负责把任意外部 JSON 转成合格训练数据。缺文件、哈希变化或 schema 不兼容会明确失败，不会转读历史目录。

当前主样本是 **3 个训练整局，1113 行、85 次出牌；1 个 validation 整局，352 行、27 次出牌；无 test**。它用于验证工程链路，不足以建立棋力结论，也不是专家示范。`portable-pilot` 原生版本不同，保留为仅离线的数据集。

## 2. 当前模型实际看到了什么

GRU C 输入为 `history 688 + belief 15 + 上一动作 one-hot 5 + 执行反馈 4 + dt 1 = 713` 维。`history` 已包含当前观察与公开事件历史。10 tick 对应 **0.5 秒游戏时间**；32 行是 16 秒游戏时间，burn-in 8 行是 4 秒。墙钟耗时另外记录，不作为游戏 dt。

当前 `fair` 排除**所有非塔实体，包括己方单位**；画面里看得见部队，不代表 actor 已获得它们。输入保留己方手牌、圣水、塔、皇冠、已实际消费的公开出牌历史和保守 belief。未知值保留 mask；敌方真实手牌、精确圣水、未来动作和原生隐藏字段不能直接进入 actor。

动作是 `0=WAIT，1..4=当前四个手牌槽位`，出牌还需原生坐标。动作 mask 主要表达支持牌与圣水可负担性；落点 mask 是当前普通场地的提案范围，二者都不证明最终合法。**只有原生消费回执能确认实际出牌。** 当前支持固定八卡；换卡组、技能、进化或特殊部署规则必须单独扩展和验证。

## 3. 按研究问题定位代码

| 研究改动 | 当前文件与函数 | 同步约束 |
|---|---|---|
| 网络与循环结构 | [`learning.py`](../royale_phase3/learning.py)：`architecture`、`SmallPolicy.__init__/forward` | 输出动作/位置头与 decoder 保持兼容；改变 hidden 结构还需改 `_initial_hidden` 和运行策略 |
| loss、优化器、模型选择 | 同文件：`loss`、`train` | 记录权重与训练配置；仅 train 优化、validation 选模型 |
| actor 拼接 | 同文件：`actor_vector`、`_tensor_batch` | 训练与部署必须使用同一维度、单位与字段 |
| 公开观察、编码、缺失值 | [`observation.py`](../royale_phase3/observation.py)：`ObservationProjector`、`feature_spec`、`encode_observation` | 新特征需要可见性依据、schema/维度版本与重导出 |
| 显式历史推断 | [`belief.py`](../royale_phase3/belief.py)：`PublicBelief`、`encode_belief` | 使用当前锁定版本机制；未知对手手牌不能填成已知 |
| 部署观察与运行反馈 | [`native_env.py`](../royale_phase3/native_env.py)：`_observe`、`reset`、`step` | reset 清空状态；只向 actor 供给动作前因果信息 |
| 数据导出、整局划分 | [`native_loop.py`](../royale_phase3/native_loop.py)：`export_trajectories`、`_learning_row`、`train_validation_view` | 同局双方视角/派生片段必须同一 split；物理隔离 test |
| 数据入口检查 | [`data_contract.py`](../royale_phase3/data_contract.py)：`load_dataset` | 校验 rows 哈希、频率、维度、mask、tick 顺序及整局泄漏 |
| 动作、坐标与卡组 | [`actions.py`](../royale_phase3/actions.py)：`DECK`、`mask`、`position_mask`、`validate`、`match` | 训练标签与在线逆转换一致；消费反馈不由请求成功代替 |
| 保存与部署 | `learning.py`：`save_checkpoint`、`read_checkpoint`、`StatefulPolicy.load/predict`、`decode` | JSON+NPZ、严格权重加载、原生版本/观察 profile 相符 |
| 独立验证与工作台 | [`research_worker.py`](../tools/cloud/research_worker.py)：`validate`；[`research.py`](../royale_phase3/research.py)：`_checkpoint`、`catalog`、`_resolve`、`_execute` | 当前只允许固定 fair C，不能绕过检查伪装兼容 |

## 4. 换成自己的网络，需要接哪些地方

1. **分配新模型 ID 和架构版本。** 保留当前 C 作为对照。只改变 loss 的实验也记录算法配置；改变 forward 语义时不要继续把旧权重描述成同一种模型。
2. **增加模型构造工厂及训练入口。** 当前 `train` 和 `StatefulPolicy.load` 直接构造 `SmallPolicy`，没有 `load_model` 注册器。新模型需实现兼容的 forward/hidden/output，或增加明确的策略适配器；Transformer 记忆不能直接套用 GRU hidden 逻辑。
3. **扩展 checkpoint 契约。** 保存模型类别、完整架构配置、特征/动作版本与权重指纹；更新 schema/读取分派，继续严格加载权重。不要用改 JSON 标签的方式跳过不兼容检查。
4. **统一推理。** `decode` 与 `StatefulPolicy` 供离线和部署共用；新 episode/owner 必须 reset，同 tick 重复读取不能重复推进记忆，未来事件不能进入输入。
5. **接入独立验证进程。** 更新 `research_worker.validate` 的模型检查与构造路径，证明保存重载后的预测与部署一致、test 未参与选择。
6. **登记工作台。** 同步更新配置 loader 的模型允许项、`research._checkpoint`、`catalog`、`_resolve`、训练命令路由及需要展示的参数。页面模型列表来自 catalog；只添加下拉名称仍会被后端拒绝。
7. **先离线验收，再原生评估。** 使用新输出目录保存每次实验；通过下面的验收链路后，才比较棋力。

若只改输入，还要同步数据导出和 `NativePhase3Env._observe`。旧 rows 不会因为改了 encoder 自动更新；旧 checkpoint 也不会因维度凑巧相同就语义兼容。改变公开特征与动作空间应建立独立数据/模型版本，保留原对照组。不能把 `research_battlefield` 中未经证明可见的对象静默混入 fair。

## 5. 世界环境与世界模型怎样研究

**环境规则配置**在 `actions.match`、`NativePhase3Env` 和固定版本后端之间；**学习出来的世界模型**需要独立网络、动作条件、预测目标、训练循环和验证协议。改地图/卡组或接一个预测 head，并没有自动实现世界模型训练。

当前 `SmallPolicy.world_head` 是可选的 9 维一步辅助监督，当前 C 关闭它；它不是完整动作条件动力学模型。独立的 [`world.py`](../royale_phase3/world.py) 有 `ShortModel`、`train`、`measure`，研究圣水、六塔 HP 和皇冠的聚合预测，并比较 0.5/1/2 秒 open-loop 与简单基线。它没有普通实体、死亡/生成模型，也未接入工作台或正式想象训练；代码明确记录 `formal_imagination_authorized=False`。

自己的世界模型至少需要明确：部署可得状态与缺失 mask；已执行或候选动作及 dt；预测的实体/资源/奖励/真实终止；一步损失与不读未来观察的多步误差；对手假设和分布外动作处理。现有离线多步实验条件于真实后续己方动作，这是声明过的预测条件，不能当成自主规划已成功。先冻结 validation 预测门槛，再做原生候选动作排序核验，最后才接想象 actor–critic。当前没有已注册的 Dreamer/RSSM/PPO 在线训练循环。

## 6. 从训练到实战的验收顺序

| 阶段 | 必须看到的证据 |
|---|---|
| 离线训练 | 实际优化、曲线、整局 train/validation 统计、配置与数据指纹；test 不调参 |
| 保存重载 | 新进程加载同一模型；共享 decoder 的部署预测一致；reset 和重复 tick 行为一致 |
| 新原生局 | 版本/profile/动作契约一致；同 seed 换边配对；记录 requested/consumed/rejected/unknown |
| 结果 | 所有请求局保留分母；自然终局与预算截断、超时、故障分别统计；小样本不宣称提升 |
| 原生录像 | 启动前固定录制局，从 reset 前覆盖自然终局；画面真实、可解码、终局画面可核验 |
| 回看与事件定位 | Android 停止后仍播放、倍速、前后 seek；动作按实测锚点给视频区间，未知误差明确保留 |

**当前最新录制评估在第一局原生预热超时：请求 2 局、尝试 1 局、模型 0 决策/0 出牌、0 自然终局。** 这次失败发生在模型开始决策前，无法据此评价模型。已保存真实初始化片段，关闭 Android 后浏览器播放/seek 通过；完整模型对战视频和动作视频对齐仍未通过。源帧没有捕获 PTS，主机收帧时间不能冒充精确画面时刻，也不能直接用 tick/20 定位视频。

慢原生运行不会拖慢保存数据的每次 BC 梯度更新，但会拖慢采样、在线 RL 和新局验证，并让墙钟预算更容易截断实验。当前环境还不能作为高吞吐在线训练或流畅人机对战运行端；应先验证更快且协议/版本兼容的端，不能仅放大超时便宣布问题解决。

未来 Windows 部署继续使用同一 checkpoint、公开观察编码、解码与消费反馈；模型 reset/记忆、坐标、固定版本必须一致。一个调度器持续推进游戏并接收双方动作，人类鼠标拖牌与 AI 不应分别拥有世界时钟。还需测有效画面更新、观察到消费的延迟与超时处理。**当前 Windows 连续人机对战尚未实现，镜像窗口本身也不会让模拟器加速。**

当前运行与媒体证据见[工作台指南](../docs/RESEARCH_WORKBENCH_ZH.md)及[分项验收记录](../docs/audits/research_workbench_implementation_20261107.json)。此前 [model-map.json](../outputs/cloud/system-consolidation-20261107/model-map.json) 是重组前审查快照，其中“历史路径耦合”记录前态；当前活动入口以 `research/system.json` 为准。原生清单内部受保护资产仍有独立依赖，不能据此删掉活跃 `royale_phase2` 后端或素材。

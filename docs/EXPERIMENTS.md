# 平台实验与验收记录

本报告对应 `ClashRoyaleAi@2f172925a15b+lab-calibrated-v2` 的冻结实验。训练、最终 700 局评估、统计绘图与数值接口复查均完成于 **2026-10-14（Asia/Shanghai）**；旧 MVP 的 2026-10-13 记录单独保留。七种策略各 100 局，共 **700 局真实闭环执行**；搜索策略在本测试集胜率 63%，行为克隆 48%，PPO 概率采样 37%。这些数字衡量本地社区近似引擎上的研究平台，不代表官方游戏胜率或全卡牌交互的正确性。

旧 12 卡 MVP 的代码、模型和实验继续作为基线保留。原实验与路线说明另存 [MVP 实验](../outputs/archive/mvp_experiment_docs/EXPERIMENTS.md)、[MVP 路线](../outputs/archive/mvp_experiment_docs/ROADMAP.md)，旧数据为 `outputs/evaluation.json`；不可与下列 700 局混算。圣水和塔伤校准前的首次平台实验位于 `outputs/archive/pre-economy-v1`，也不进入最终统计。

## 数据与冻结条件

原始证据是 [summary.csv](../outputs/evaluation/main/summary.csv)、[records.jsonl](../outputs/evaluation/main/records.jsonl)、[report.json](../outputs/evaluation/main/report.json) 和 [scenarios.json](../outputs/evaluation/main/scenarios.json)。每局保存动作反馈、压缩决策日志及可重建 JSON 回放；失败对局另存最后可见观测。图表脚本核对了 700 个唯一 `policy/index`、各策略的出牌/等待数和汇总延迟，输入 SHA256 记录在 [绘图 manifest](../outputs/platform/figures/manifest.json)。

| 项目 | 实际配置 |
| --- | --- |
| 引擎 | 固定社区源码 + 本地 `lab-calibrated-v2` 补丁；原生 C++，Python 3.14.5 绑定 |
| 场景 | 相同 100 个 seed/我方卡组/对手卡组/座位/对手策略；50 组相邻换边场景 |
| 场景 SHA256 | `5e98df85c75a0d21cf183b2061c531cd33d28bb7b73ed8e680ee0141291490a9` |
| seed 生成 | `10000 + (pair % 5) * 101 + pair // 5`，`pair=index//2` |
| 我方卡组 | Hog Cycle 26 局、Giant Control 26 局、Air Control 24 局、Bridge Pressure 24 局 |
| 对手策略 | random 34 局、greedy 34 局、search_no_response 32 局 |
| 时钟/决策 | 引擎 10 tick/s；每 10 tick 同时取得双方观测、做决策并执行；单局上限 3000 tick（300 秒模拟时间） |
| 信息 | 策略只收到过滤后的可见观测；对手手牌/next/真实圣水不可读取，搜索使用自身信念和重建局面 |
| 搜索 | 40 tick（4 秒）窗口、最多 6 候选、15 ms 软墙钟预算；响应消融仅关闭响应建模 |
| 执行 | 4 个评估 worker；动作合法性检查、command ID 去重、实际执行反馈；每局墙钟保护 180 秒 |
| 模型模式 | learned=BC argmax；ppo=原 PPO argmax 诊断；ppo_sample=同一 PPO 权重、实例私有种子的 categorical 采样 |

软墙钟预算会随机器负载改变实际评估候选数。记录了 seed 并不意味着并行搜索轨迹能逐步字节相同；追求严格策略复现时，应另做固定候选/固定展开数实验。

## 700 局结果与动作行为

全部 700 局 **0 崩溃、0 墙钟超时异常、0 被拒绝动作、0 平局**。下表的 PLAY 是策略提交的非 WAIT 帧，本实验均对应成功出牌；没有把 dry-run 建议计为执行。

| 策略 | 胜/负 | 胜率 | Wilson 95% 区间 | WAIT 帧 | PLAY 帧 | P50 / P95 决策延迟 ms |
| --- | --- | --- | --- | --- | --- | --- |
| random | 34/66 | 34% | 25.5–43.7% | 12,753 | 2,706 | 0.013 / 0.026 |
| greedy | 50/50 | 50% | 40.4–59.6% | 13,692 | 2,753 | 0.021 / 0.095 |
| search | 63/37 | 63% | 53.2–71.8% | 13,559 | 3,033 | 4.360 / 16.504 |
| learned（BC） | 48/52 | 48% | 38.5–57.7% | 11,889 | 2,215 | 0.296 / 0.719 |
| ppo（argmax） | 0/100 | 0% | 0.0–3.7% | 7,226 | 1 | 0.804 / 1.506 |
| search_no_response | 60/40 | 60% | 50.2–69.1% | 13,546 | 2,925 | 1.780 / 6.283 |
| ppo_sample | 37/63 | 37% | 28.2–46.8% | 13,322 | 2,886 | 1.070 / 1.972 |

![胜率与 Wilson 区间](../outputs/platform/figures/wins.png)

Wilson 区间采用二项比例的 score interval，而非零胜时退化的正态近似；计算方式见 [NIST 说明](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm)。它是描述性区间：换边的两局相关、场景卡组重复，因此不应把它当作跨任意对手总体的严格独立抽样结论。

## 配对消融与不确定性

同一场景的结果映射为胜=1、平=.5、负=0，相减得到得分差。以相同 seed 的两个换边座位为一组，保留组内相关性，对 **50 组**做 20,000 次聚类 bootstrap，seed=20261014；以下为 percentile 95% 区间。计算和原始不一致局数在 [statistics.json](../outputs/platform/figures/statistics.json)。没有做多重比较校正；场景间共享卡组/对手的更高层相关性仍未消除。

| 比较（后者减前者） | 平均得分差 | 聚类 bootstrap 95% 区间 | 后者更好/前者更好/相同局数 |
| --- | --- | --- | --- |
| greedy → search | +13 个百分点 | +6 至 +20 个百分点 | 18 / 5 / 77 |
| search_no_response → search | +3 个百分点 | −6 至 +13 个百分点 | 12 / 9 / 79 |
| learned → ppo argmax | −48 个百分点 | −60 至 −36 个百分点 | 0 / 48 / 52 |
| ppo argmax → ppo_sample | +37 个百分点 | +26 至 +49 个百分点 | 37 / 0 / 63 |

本样本支持搜索相对 greedy 的优势；响应建模的 +3 个百分点区间跨零，尚不足以证明它稳定提升棋力。它将 P95 从 6.283 ms 提高到 16.504 ms，下一轮应扩大样本并比较固定算力预算。PPO 的两个模式使用同一权重，差异主要证明部署动作选择方式影响闭环行为，不是第二个训练模型更强。

## 行为克隆训练

证据：[BC manifest](../outputs/training/bc/manifest.json)、[20 轮曲线](../outputs/training/bc/curve.csv)、`trajectories.npz`、12 个实际教师 episode 回放。seed=23–34，教师最多 1200 tick/局，对手 greedy，教师前瞻 40 tick、20 ms 软预算、最多 8 候选。共有 **1344** 决策样本，按整局分割：episode 0–8 训练，9–11 验证；训练 1017 帧，验证 327 帧。验证 episode 的外部 seed 不在评估 seed 集合中。

候选编码器有 34 个基础特征 + 173 个引擎卡牌身份特征，最多 128 候选，合法 mask 屏蔽填充动作。逐候选网络 `207→96→48→1` 使用 Tanh；Adam 学习率 8e-4，batch 64，20 epoch，梯度范数上限 1。当前报告来自本次运行参数和源码；数值接口已完成默认等价回归，实际更改数值后的新实验必须使用独立配置指纹，不能追溯改变已有样本配置。

最终 loss=0.32734，总验证一致率 **88.07%**；WAIT 为 **272/272=100%**，PLAY 为 **16/55=29.09%**。始终等待已经能达到 272/327=83.18%，因此总准确率掩盖了出牌错误。第 6 个 epoch 的 PLAY 一致率短暂高于最后一轮，当前保存最后一轮模型，未以最终评估挑选最佳 epoch。后续应使用验证集选择模型并独立报告 PLAY、卡牌、落点与胜率。

![BC 等待和出牌一致率](../outputs/platform/figures/BC_wait_play_accuracy.png)

模型为 `models/platform/learned.pt`，本次 SHA256 为 `4e36a20eec84f7422d34fcb03f37e3e5fe78d62cd40a5327238df2a8084d1afa`。最终 48/100 局只能证明它能执行真实候选并具有有限策略能力，尚未达到搜索教师的胜率。

## PPO 训练与实际缺陷诊断

证据：[PPO manifest](../outputs/training/ppo/manifest.json)、[16 个 rollout 曲线点](../outputs/training/ppo/curve.csv)、[27 个完成 episode](../outputs/training/ppo/episodes.json) 与 [独立行为审计](audits/PPO_BEHAVIOR.md)。本轮 CPU 单进程、seed=71、4096 环境步、rollout 256、每轮更新 4 epoch、batch 64、Adam 3e-4；γ=.99、GAE λ=.95、clip ratio [.8,1.2]、entropy 系数 .01、梯度范数上限 .5。value_loss 先乘 .5、总 loss 再乘 .5，实际 MSE 系数 .25；该细节已记录，未将其认定为不下牌的原因。

训练单局最多 1800 tick，对手 greedy；测试单局最多 3000 tick且含其他对手，存在时长和对手分布变化。训练完成的 27 局共有 **731 次成功出牌，10 胜、17 负**。训练采用 categorical 采样；PPO checkpoint 的 SHA256 为 `5f9dfa36109f49a78f5fa8a8afc3e44ea2c53c82b96ec2c00f59ca1d3a682142`。

![PPO 训练曲线](../outputs/platform/figures/ppo_training_curves.png)

独立的 20 对诊断未并入主 700 局：argmax 20 局 0 胜、0 出牌；采样 20 局 9 胜、530 次出牌、0 拒绝。第一帧有 20 个合法候选时，P(WAIT)=.05866，所有出牌候选概率之和=.94134，但最强单个出牌只有 .05252。对平铺的多个落点逐动作取最大值，会持续选择 WAIT，即使总出牌概率很高。主评估因此保留原 `ppo` 作为失败实验，增加带私有 seed 的 `ppo_sample` 并用同一冻结 100 场复测；结果为 37 胜、2886 次出牌。

平均训练 entropy 同时包含大量只允许 WAIT 的状态，不能单凭它很低断言策略塌缩。另有实际编码器限制：改变 next card 不改变特征；critic 只读 WAIT 行，无法看到手牌身份。需要全局可见状态表示，而不是仅增加训练步数。原 resume 已完成目标步数时曾触发空曲线 IndexError，隔离复现和修复后 no-op 重测证据保存于 `outputs/audit_candidates/ppo_audit`。当前续训保存权重和 optimizer，尚无 RNG/环境恢复，不能宣称逐 step 完全相同续训。

## 硬件与吞吐

本机 [environment.json](../outputs/platform/environment.json)：Intel Core i9-14900HX，24 核/32 线程，物理内存约 31.7 GiB，Windows。系统枚举到 RTX 4060 Laptop GPU，但本次 torch 未使用 CUDA；不以 Windows `AdapterRAM` 字段估计显存或 GPU 性能。主运行环境为 Python 3.14.5、torch 2.14.1、NumPy 2.5.4、Gymnasium 1.4.0、OpenCV 4.14.0.94。

| 策略 | 局/worker 秒 | 模拟秒/worker 秒 | 各局 wall_seconds 之和 |
| --- | --- | --- | --- |
| random | 4.981 | 768.5 | 20.08 秒 |
| greedy | 4.356 | 714.7 | 22.96 秒 |
| search | 0.770 | 127.5 | 129.92 秒 |
| learned | 2.953 | 415.2 | 33.86 秒 |
| ppo argmax | 4.348 | 312.3 | 23.00 秒 |
| search_no_response | 1.542 | 253.6 | 64.84 秒 |
| ppo_sample | 1.195 | 193.3 | 83.67 秒 |

吞吐定义为完成局数除以各局已记录 wall_seconds 之和，包含对手决策；是 worker 时间效率，不能乘 4 就当实际整批吞吐。它未包含全部进程启动和回放写盘开销，早败的 PPO argmax 也使局/秒偏高。最终 `report.json` 的 `elapsed_this_run_seconds` 是完成场景全部已有记录后重建汇总的用时，**不是 700 局耗时**；续建汇总时该字段可变化，不能当作冻结的比赛测量。

![决策 P95 延迟](../outputs/platform/figures/latency_p95.png)

P50/P95 直接汇集本策略全部决策帧，包括 WAIT；未把“各局 P95 的均值”混作总体 P95。search 的 16.504 ms 超过 15 ms 软预算，说明预算不是可中断的硬截止；0 timeout 只指没有触发每局 180 秒墙钟保护。

## 分层测试与独立引擎证据

| 层次 | 实际结果 | 证据与范围 |
| --- | --- | --- |
| C++ 引擎 | 740 cases：739 通过，1 个预期失败；8413 assertions：8412 通过，1 个预期失败 | [最终原生测试 XML](../outputs/native_engine_tests.xml)，已知 movement wedge 阻塞保留；不能写成 740 全通过 |
| Java crforge | Windows 原始 971/974；构建目录内修复路径扫描后 974/974 | [独立审计](audits/CRFORGE.md)、`outputs/audit_candidates/crforge_build/junit.log`；原始失败日志保留 |
| 主平台 | 21 个测试方法通过 | [最终交付测试日志](../outputs/platform-tests-delivery.log)：可见边界、动作执行、delay/去重、恢复/fork、Gym mask、训练模型与采样种子、配置版本契约等 |
| 原 MVP | 26 个测试通过，无跳过 | [原始日志](../outputs/verify_0.txt)；单独验证旧简化引擎和合成视觉 |
| 干净 Python 环境 | exact 依赖安装、pip check、info、600 tick 双学习模型 demo；最终 21 平台测试通过 | [repro 审计](audits/REPRO.md)、[原复现 manifest](../outputs/audit_candidates/repro/manifest.json)、[最终复现测试日志](../outputs/repro-tests-delivery.log)；原 manifest 保留较早的 20 项运行记录，最终重测为 21 项，复用了本机已编译的 3.14 `.pyd` |

不同层的测试不相加成为“全游戏覆盖率”。crforge 的自身 fidelity ledger 有 138 类：0 TRACED、1 PARTIAL、137 GUESS；974 tests 通过仍不能证明与官方一致。Java/Python ZMQ 桥真实跑完 3 个完整 episode，共 93 个已核对手牌轮转的出牌；审计发现其 `action_failed` 在真正执行前判定，错误地把这些出牌全部报失败，已单独记录。

随后完成真正参与结算的 `data/platform/balance_v2.json` 数值外置：25 类主工厂字段按卡牌/分支适用性导出，173 个 registry 行中 171 行可修改，41 个进化行的普通/进化数值分开；Mirror164 和 Spirit Empress165 的特殊路径拒绝修改。34 项 JSON→工厂→实际攻击/法术/费用/寿命回归通过，见 [numeric_stats.json](../outputs/native_engine_numeric_stats.json)。独立审查发现并修复“滚动/buff/clone 分支接受却不使用”的字段，8 个拒绝条件、4 个合法控制及非法批次/锁重测通过，见 [数值接口审计](audits/NUMERIC_CONFIG_REVIEW.md)。死亡/复合子体与 effect 内部数字仍明确排除，不会随主牌 HP/damage 自动变化。

[unsupported_numeric_parameters.json](../data/platform/unsupported_numeric_parameters.json) 列出 **68 个尚未外置的 CardStats 数值成员**和 **36 个 effect 类的内部参数清单**，另列塔兵种、子实体、特殊路径等排除项。这是源码成员/类库存，不是 68 类已缺失机制、36 张不支持卡或所有内部常量的穷尽列表；并非每个成员被每张牌使用。下一步是扩展这些具体剩余数值与版本/等级校准，而不是再次把已完成的主工厂字段外置作为未完成项。

最终 numeric 模块 SHA256 为 `6160b968f9dfde6ab9b047627596c5d50383b0af8728ed5036fc49239edccd53`。对 100 局既有评估回放，旧 v2、新模块隐式默认、新模块加载完整默认 JSON 的 5314 个接受动作逐 tick/逐动作复放；两个新变体各有 **169,994 个完整 state_dict 比较点，零差异**，证据 [numeric_replay_equivalence.json](../outputs/native_engine_numeric_replay_equivalence.json)。这支持默认动力学保持本地 v2 原样，既有 700 局结果未被重新运行或覆盖；不是官方正确性证明。

删除原先被接受却无效的字段后，默认配置的 schema 数值哈希变为 `9095f8eb1cd2fafa6115afb342b4888c6ebe23436e97e908919a1b8cee21129e`。已依据上述回放比较迁移 `experiment_config` 与 `report.json` metadata，原哈希和迁移理由保存在 [numeric_schema_migration.json](../outputs/platform/numeric_schema_migration.json)；700 条比赛记录和模型权重保持原样。

当前 [双引擎差分](../outputs/audit_candidates/backend_differential.json) 中，校准后五种法术塔伤与 Java 参考一致：Arrows 90、Fireball 206、Zap 57、Rocket 371、Lightning 285；这些比例来自 Java 参考后再回归核验，不构成独立官方证明。Poison 为 C++ 168 / Java 160；棋盘 18×34 / 18×32，公主塔 HP 2534 / 3052，阶段切换边界仍差一个 Java tick（.05 秒）。双方圣水 1/2.8 秒、120 秒 2×、240 秒 3×与四次抽牌后回到已出卡已实际验证。详细限制见 [服务器选择](SERVER_SELECTION.md)、[C++ 审计](audits/ClashRoyaleAi.md) 与逐卡/机制 CSV。

## 重建图表与继续评估

图表使用独立 `.venv-audit` 的 Python 3.11.0、matplotlib 3.11.2、NumPy 2.4.6；实际包版本见 [requirements-plot.txt](../outputs/platform/figures/requirements-plot.txt)，未更改主环境。四张图均提供 PNG 与可编辑 SVG，输入来自已保存 CSV/JSON。

```powershell
$env:PIP_CACHE_DIR = Join-Path (Get-Location) '.cache/pip'
$env:TEMP = Join-Path (Get-Location) '.tmp'
$env:TMP = $env:TEMP
$env:MPLCONFIGDIR = Join-Path (Get-Location) '.cache/matplotlib'
.\.venv-audit\Scripts\python.exe tools/plot_platform_experiments.py
.\.venv-repro\Scripts\python.exe -m unittest discover -s tests_platform -v
```

重建主汇总会复用完整的已有 records，不会重新运行 700 局。要获得新实验，使用新的输出目录和版本标记；继续沿用当前目录但改 seed/场景会被冻结检查拒绝。若改变引擎参数、候选生成器或编码器，需重新采样训练与测试并保存各版本数据。具体下一步见 [路线与优化定位](ROADMAP.md)。

尚未完成官方客户端逐卡轨迹对照、全组合穷举、真实设备视觉动作闭环或长期多种子自博弈。当前已经跑通可复现的本地服务器—观测—信念—策略—执行反馈—回放—训练—评估闭环；其保真度与学习棋力仍需逐项推进。

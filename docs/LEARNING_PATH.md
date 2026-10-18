# 从零掌握这个皇室战争 AI 研究平台

按下面顺序学习，每章先运行一个小实验，再读对应代码和论文。先把一次观测如何变成实际下牌说清楚，再研究如何提高棋力。路径均位于 `D:\研究生\Clash Royal`；旧 `royale_ai` 是保留的 MVP，新平台为 `royale_lab` 与原生 C++ 后端。

## 第 1 章：建立一个能解释的完整对局

先按根 README 完成项目内安装。下面命令查看引擎/卡牌信息并运行无界面对局；`serve` 开启交互界面，进程用 Ctrl+C 停止。

```powershell
Set-Location -LiteralPath 'D:\研究生\Clash Royal'
.\.venv\Scripts\python.exe -m royale_lab.cli info
.\.venv\Scripts\python.exe -m royale_lab.cli demo --seed 7 --policies greedy random
.\.venv\Scripts\python.exe -m royale_lab.cli serve --port 18765
```

从浏览器查看自己的四张手牌、圣水、塔血和单位，手动下牌并查看动作反馈；再选 AI 对 AI。界面取自实际后端。理解 `tick` 是引擎固定时间步、`decision_interval` 是代理多久决策一次，二者不是同一概念。暂停和逐帧可以把本来难以观察的卡牌交互拆开看。

读 [Session](<D:/研究生/Clash Royal/royale_lab/session.py>) 的 `Session.advance/act/state/export`，再读 [原生后端](<D:/研究生/Clash Royal/royale_lab/backend.py>) 的 `NativeBackend.observe/apply/advance/result`。练习：在一局 JSON 日志中找出一条下牌，指出计划动作、接受结果、圣水与手牌变化、单位何时出现。不能只展示打印的建议。

入门读物：[SEAT 本地 PDF](<D:/研究生/Clash Royal/references/library/seat/seat.pdf>) 的 §2/3。暂不追求理解神经网络公式，先对应游戏中的状态、动作与奖励。

## 第 2 章：分清能看到的信息和隐藏的信息

一个完整状态包括双方手牌、精确圣水、部署中的未来事件等；玩家观测只包含该玩家能知道的字段。UI 的调试完整状态不应直接喂给主策略。把历史观察和自己行为记下来，才有机会推断敌方手牌/轮转。

读 [契约](<D:/研究生/Clash Royal/royale_lab/contracts.py>) 的 `Action`、`Decision`、`BattleBackend`、`Policy` 和 `validate_observation`；读 `NativeBackend.observe` 的视角映射与过滤。练习：列表写下“自己手牌可见”“敌方精确圣水不可见”“敌方刚出的卡可见”等字段，逐一对应到观测结构。

论文：[POMCP](<D:/研究生/Clash Royal/references/library/pomcp/pomcp.pdf>) 的 §2/3、[DRQN](<D:/研究生/Clash Royal/references/library/drqn/drqn.pdf>) 的方法章节。两条路线分别是显式概率信念和从历史学习记忆。本平台已有简单信念，尚不是完整 POMCP 或循环 RL 实现。

练习：构造两份玩家观测相同、隐藏敌手不同的后端状态，主策略不能因为偷偷读取隐藏字段而输出不同动作。可用 oracle 做研究对照，但要明确标签并独立报告。

## 第 3 章：理解动作空间和合法动作

下牌动作至少含卡牌、落点和时机，英雄技能另有合法条件。WAIT 也是动作，它允许积累圣水或等敌方暴露信息。合法动作不等于高质量动作；低质量但合法的法术空放仍然会执行。

读 `policies.py::raw_candidates/candidates`，了解法术聚类点、建筑点、部队点和技能候选；读 `backend.py` 的动作合法性与接受反馈，再看 `learning.py::encode` 如何构造固定大小 candidate matrix 与 mask。

论文：[动作屏蔽](<D:/研究生/Clash Royal/references/library/action_masking/action_masking.pdf>) 的 Proposition 1 和实验、[P-DQN](<D:/研究生/Clash Royal/references/library/pdqn/pdqn.pdf>) 的方法。不要一开始把全部连续落点离散成几十万个动作；先确认候选不会遗漏最关键的反制位置。

练习：在 `raw_candidates` 增加一个可解释的建筑拉扯点，验证它通过后端合法性、出现在 UI 候选中、被搜索评价。随后比较加入前后的同种子胜率、法术空放与 p95 延迟。每次修改动作编码都要考虑旧 checkpoint 的兼容性。

## 第 4 章：理解引擎，并知道自测的边界

从 [原生引擎目录](<D:/研究生/Clash Royal/third_party/ClashRoyaleAi>) 阅读 GameManager 的固定步推进、CardRegistry 的数据/类型分配和单位/法术实现；用机制 coverage 与 interaction_tests 定位真实支持和误差。卡牌目录有一行只说明存在记录，不能证明特殊效果正确。

先读 [引擎审计导航](<D:/研究生/Clash Royal/docs/audits/INDEX.md>)。比较 crforge 的实际战斗实现、MSU gym 的 `pass` 骨架和 KataCR 的真实屏幕代理，理解为什么“私服能登录”与“服务器能计算战斗”是不同验收。

可迁移论文：[动力学随机化](<D:/研究生/Clash Royal/references/library/dynamics_randomization/dynamics_randomization.pdf>)。练习：选火球塔伤、桥上路径或首击延迟中的一项，用固定场景记录单位/血量时间序列。内部测试通过说明代码符合当前规则；独立引擎比较只是参考一致性；官方一致仍需要真实录像或可审计轨迹。

此章先解决机制偏差，因为强搜索可能更容易利用模拟错误。不要用网络变大掩盖塔伤、碰撞或时间结算错误。

## 第 5 章：先读懂透明规则与评价函数

读 [策略](<D:/研究生/Clash Royal/royale_lab/policies.py>) 的 `tactical_score`、`RandomPolicy.decide`、`GreedyPolicy.decide`；读 [奖励](<D:/研究生/Clash Royal/royale_lab/rewards.py>) 的 `utility/transition_reward`。前者是短期候选启发式，后者用于状态评价和训练回报。它们都属于可修改的研究假设。

观察一个规则策略失败局势，写出它为何偏好这张卡：例如费用惩罚、空中可攻击性、单位血量估计或兵种名启发式失准。练习：只调整一个评分系数，保留原配置，配对重跑，记录收益和副作用。奖励增高但胜率下降时，应以最终任务指标重新审视奖励。

论文：[SEAT](<D:/研究生/Clash Royal/references/library/seat/seat.pdf>) 的 reward 部分；更完整的批判性解读在 [DEEP_READS](<D:/研究生/Clash Royal/docs/research/DEEP_READS.md>) 第 1 节。终局胜负是主要目标，但资源和塔伤能提供密集学习信号；这些信号可能产生钻空子的行为。

## 第 6 章：对手信念不是精确真相

读 [Belief](<D:/研究生/Clash Royal/royale_lab/memory.py>) 的 `update/available/to_dict` 与 `regeneration`。它记录见过的卡、确认出牌、圣水区间、轮转近似和矛盾次数。当前四卡轮转近似对英雄等特殊规则有边界；真实屏幕漏事件时不能仍假装精确计数。

论文：[POMCP](<D:/研究生/Clash Royal/references/library/pomcp/pomcp.pdf>) 的粒子更新、[Opponent Modeling](<D:/研究生/Clash Royal/references/library/opponent_modeling/opponent_modeling.pdf>) 的表示设计。练习：在一组已知真值的模拟日志中检查精确圣水是否落入估计区间；故意删除一个可见出牌事件，再测区间覆盖与决策变化。

下一步可将单一区间改为多个卡组/轮转粒子，增加观测噪声模型和恢复机制。先量化信念误差，再用胜率判断它是否真正帮助搜索。

## 第 7 章：搜索为何强、为何慢、为何仍非最优

读 `SearchPolicy.__init__/decide` 与 `NativeBackend.from_observation/snapshot/restore`。当前搜索从可见观测重建假设，考虑敌方圣水区间与规则响应；按启发式预筛候选，用有限前瞻状态价值打分。它并非穷举连续时序，也不是完整 MCTS。

可调参数有 `horizon_ticks`、`budget_ms`、`max_candidates`、`responses`。预算是实现中的软预算，模型构造/首个候选及正在执行的 rollout 可能让墙钟超过设置，应看真实 p95。候选数与搜索深度之间存在折中；同等延迟下比较才公平。

论文：[POMCP](<D:/研究生/Clash Royal/references/library/pomcp/pomcp.pdf>)、[AlphaZero](<D:/研究生/Clash Royal/references/library/alphazero/alphazero.pdf>)、[MuZero](<D:/研究生/Clash Royal/references/library/muzero/muzero.pdf>)。练习：同一快照将 horizon 从 60 改到 100 tick，再观察搜索排名和完成时间；然后批量配对比较，判断更多计算是否带来稳定收益。对手假设错误时更深搜索也可能更自信地出错。

## 第 8 章：从搜索轨迹学习一个更快的策略

读 [学习模块](<D:/研究生/Clash Royal/royale_lab/learning.py>) 的 `encode`、`CandidateNet.forward`、`train_bc`、`save_model`、`LearnedPolicy.decide`。目前模型给候选打分，而非从空白网络掌握全卡交互。checkpoint 保存 card_ids 与 encoder 版本，加载时验证接口一致。

```powershell
.\.venv\Scripts\python.exe -m royale_lab.cli train-bc --episodes 12 --epochs 20 --seed 23
.\.venv\Scripts\python.exe -m royale_lab.cli demo --policies learned greedy --seed 101
```

论文：[DAgger](<D:/研究生/Clash Royal/references/library/dagger/dagger.pdf>) 的算法 3.1、[Decision Transformer](<D:/研究生/Clash Royal/references/library/decision_transformer/decision_transformer.pdf>) 的序列编码、[KataCR](<D:/研究生/Clash Royal/references/library/katacr/katacr.pdf>) 的 delay 与重采样。

练习：打开 BC 报告，分别看总体、WAIT 和出牌帧准确率；总体准确率高可能只是等待很多。将一个学习者失败局势重新交给搜索教师标签，说明 DAgger 与重复训练原数据有什么差别。所有拆分必须按整局，不能把相邻帧分到训练和验证。

## 第 9 章：启动 RL，并建立可靠训练诊断

读 `CandidateGym.reset/step`、`train_ppo` 和 `transition_reward`。环境负责推进实际战斗、运行对手、提供 mask 与反馈；PPO 负责从 on-policy 轨迹更新。短程训练只证明收集、优化、保存、加载和执行链路可运行，不能证明策略已收敛。

```powershell
.\.venv\Scripts\python.exe -m royale_lab.cli train-ppo --steps 4096 --seed 71
.\.venv\Scripts\python.exe -m royale_lab.cli demo --policies ppo greedy --seed 101
```

论文：[PPO](<D:/研究生/Clash Royal/references/library/ppo/ppo.pdf>) 的式 7/算法 1、[GAE](<D:/研究生/Clash Royal/references/library/gae/gae.pdf>) 的优势估计、[IQL](<D:/研究生/Clash Royal/references/library/iql/iql.pdf>) 的离线价值/策略提取。练习：把 reward 分解为终局和势差两部分，观察平均回报、熵、KL、接受动作数量及独立胜率；只做一项改动，保留基线 checkpoint。

本平台的短程 PPO 是小规模实现。若换 SB3/CleanRL，先保持观测、候选 mask、终局语义和对手设置一致，再比较框架性能。循环 PPO、CQL/IQL、DT 在资料库中是后续方案，不因为有论文 PDF 就已集成。

## 第 10 章：用评价证明改进，而非挑一局展示

读 [评价模块](<D:/研究生/Clash Royal/royale_lab/evaluation.py>) 的 `scenarios/configured_policy/play_game/evaluate`。了解 deck/seed 的冻结、换边、对手选择、延迟统计和可恢复日志。日志里既要有成功对局，也要有崩溃、超时和拒绝动作。

```powershell
.\.venv\Scripts\python.exe -m royale_lab.cli evaluate --games 100 --workers 4 --budget-ms 15 --policies random greedy search search_no_response learned ppo --output outputs/evaluation/lesson
```

论文：[PSRO](<D:/研究生/Clash Royal/references/library/psro/psro.pdf>) 与 [NFSP](<D:/研究生/Clash Royal/references/library/nfsp/nfsp.pdf>)。练习：从同一 seed/deck 下的策略结果算配对差，再看去掉敌方响应的消融；检查最差对手表现，避免总体平均掩盖克制。训练/调参用验证局，冻结测试局只用来最后报告。

真实报告应包含局数、胜/平/负、塔伤、动作拒绝、决策 p50/p95、吞吐和失败局势。模拟器胜率仅支持同版本近似环境内的结论。

## 第 11 章：真实像素路径，从检测到完整观测

读 [旧感知适配](<D:/研究生/Clash Royal/royale_ai/perception.py>) 与项目新的 PixelAdapter 文档。模型检测单位还不够：主策略另需自身手牌/圣水、塔血、阵营、时间与确认出牌事件；缺字段应明确拒绝闭环，不能偷偷填 oracle 数据。

资料库已保存一个 97 类游戏单位 ONNX，输入为 **float16 `[1,3,480,352]`**、输出 `[1,300,6]`。这是模型接口 smoke，真实游戏准确度仍未测。文件名中的 480×352 要按实际 H/W 解释；模型内嵌 class46 名字 `hungry_dragon` 与源码 `baby_dragon` 存在别名差异。模型 metadata 许可是 AGPL-3.0，与周围 MIT 源码分开记录。

论文：[KataCR](<D:/研究生/Clash Royal/references/library/katacr/katacr.pdf>)、[DBNet](<D:/研究生/Clash Royal/references/library/dbnet/dbnet.pdf>)、[ByteTrack](<D:/研究生/Clash Royal/references/library/bytetrack/bytetrack.pdf>)；资产与验证见 [DATASETS](<D:/研究生/Clash Royal/docs/research/DATASETS.md>)。练习：对独立真实帧做手工标注，分别计算单位检测、数字 OCR、阵营和出牌事件误差。不能只拿零张量或合成截图的正常输出说明感知成功。

## 第 12 章：形成你自己的优化课题

先从失败回放挑一个可解释问题，例如“建筑落点不能拉扯”“缺失出牌后圣水估计过窄”“BC 在进攻后过度等待”。写下假设、要改的函数、独立测试、冻结对照和主要指标，实施一个最小改动再评估。只有在同预算、同种子、同卡组的证据支持下才扩大规模。

按 [方法比较](<D:/研究生/Clash Royal/docs/research/METHOD_COMPARISON.md>) 和 [研究缺口](<D:/研究生/Clash Royal/docs/research/GAPS.md>) 选题；按 [资料导航](<D:/研究生/Clash Royal/docs/research/INDEX.md>) 继续读论文。先收集多样整局数据和校准引擎，再考虑大世界模型、复杂自博弈或全卡泛化。

一个合格的小实验包应包含配置/commit/data hash、数据分割、命令、原始结果、失败日志和可恢复局势。最后能清楚回答三个问题：改进发生在哪个模块，证据支持多大范围，哪项误差仍可能改变结论。

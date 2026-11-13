# 历史对手、自博弈与晋升

本模块把“收集训练数据、更新参数、保存历史、证明提高棋力”分成可审计步骤。权重进入对手池不会自动成为冠军，也不会因为训练完成而扩大实验规模。

## 对手池

`league.py` 保存不可变 SHA256 权重与固定规则代码指纹。规则、随机策略、模仿模型和每轮 PPO 快照都可成为对手。模型必须实际训练过，并与运行环境的二进制、数值表、观察版本和配置模板一致；每场卡组、种子、等级等覆盖参数完整写入计划。

训练抽样使用 25% 均匀覆盖与 75% loss² 优先历史对手抽样，基础胜率先验为 Beta(1,1)。新快照保留不可变父成员链接；每个对手取祖先最近 32 场自然对局，折算为至多 4 场的先验，当前快照自己的结果按完整权重更新。这样换快照不会清空已知弱项，旧策略的结果也不会压过当前策略。只有训练对局更新抽样统计；晋升套件保持独立。故障和截断保留记录，不伪装成胜负。

## 真正更新参数的自博弈

`selfplay.py` 执行 recurrent PPO。策略与价值网络只接收公开观察；对手独立维护自身循环状态，两方每个控制步共同推进。策略概率使用同一 WAIT／出牌、条件卡槽、条件位置分布，熵包含按卡槽概率加权的位置熵。

默认控制间隔 0.5 秒，折扣明确按秒配置：`gamma(dt)=0.997**dt`，`lambda(dt)=0.95**dt`。自然终局清零 bootstrap，预算截断使用实际尾状态价值。奖励为终局 ±1 加公开塔血 potential 差，终局 potential 归零；这是声明过的塑形目标，不是官方积分。

每个 PPO epoch 从整局起点用当前权重重新计算历史，32 帧截断梯度。所有小段累积梯度后才更新一次参数，避免同一遍历史中的隐状态来自不断变化的权重。每轮保存策略、critic、优化器、两个随机数状态、对手池快照和完整对局回执。

`training_steps` 统一表示优化器更新次数，`selfplay_environment_steps` 单独表示环境控制步数，不能把两者相加来夸大训练量。

中断后从最后完整轮次恢复。未完成轮次的对手池记录先另存为 `interrupted-pool-*.json`，重新执行该轮；已有权重与失败现场保留。每次重试使用新的 `round-XXX-attempt-XXX` 目录。与训练无关的对手池编辑会拒绝恢复。

## 晋升规则

冻结计划在 `configs/arena-lab/promotion-next-v1.json`：3 类对手，每类 25 个独立种子、双方换边，共 150 场。来源数据的整局测试、训练种子、自博弈种子和晋升种子分开。

候选和现任模型必须执行完全相同的案例矩阵、对手权重与解码规则。晋升同时要求：

- 每类对手至少 50 场、25 个种子簇，覆盖双方；全部自然终局。
- 每类对手得分至少 55%，种子簇平均配对得分提高至少 5 个百分点。
- 同种子换边作为一簇，单侧精确符号检验按对手数进行 Bonferroni 校正，总显著性水平 0.05。
- 实际出牌消费已知，拒绝率不高于 1%。

`promote()`重新计算这些指标，核对计划、完整回执和文件哈希；不能传一个 `passed=true` 跳过验收。统计要求较保守，因此小规模自博弈可能没有任何晋升，这是正确结果。固定套件也不能无限用于开发选择；连续多代研究需要新的封存验收种子与多重检验预算。

## 运行

```bash
.venv-cloud/bin/python -m arena_lab selfplay \
  --checkpoint outputs/arena-next-20261113/timing-v1/best.pt \
  --plan configs/arena-lab/selfplay-next-v1.json \
  --output outputs/my-selfplay

# 中断恢复使用同一计划；已完成轮次不会重新训练
.venv-cloud/bin/python -m arena_lab selfplay \
  --checkpoint outputs/arena-next-20261113/timing-v1/best.pt \
  --plan configs/arena-lab/selfplay-next-v1.json \
  --output outputs/my-selfplay --resume outputs/my-selfplay/resume.pt

.venv-cloud/bin/python -m arena_lab league --pool outputs/my-selfplay/pool \
  --candidate POLICY_ID --cases outputs/resolved-frozen-cases.json \
  --output outputs/candidate-matches

.venv-cloud/bin/python -m arena_lab league --pool outputs/my-selfplay/pool \
  --candidate POLICY_ID --candidate-results outputs/candidate-matches \
  --incumbent-results outputs/incumbent-matches --output outputs/promotion
```

首次模仿模型是 foundation，不等于经过晋升的强模型。套件中的 `imitation-foundation` 在任何评估开始前解析为该模型不可变 ID，并保存解析后的文件。评估不修改 PFSP 训练统计。

## 已实际验证的恢复范围

`outputs/arena-next-20261113/selfplay-resume-audit.json` 比较了旧已训练模型上的真实模拟器实验：各 4 场自然终局、1,893 个联合控制步，2 轮实际 PPO；一条在首轮停止并恢复，另一条连续运行。最终 actor、critic、历史对手池、RNG、动作数量、终局哈希及胜负均一致。这验证实现与恢复，不证明棋力提高。

设计参考原始 [PPO 论文](https://arxiv.org/abs/1707.06347)与 [AlphaStar 联赛论文](https://storage.googleapis.com/deepmind-media/research/alphastar/AlphaStar_unformatted.pdf)。本实现是资源可复现的历史对手训练起点，没有宣称实现 AlphaStar 的规模或全部联赛角色。

## 正式训练与独立晋升验收

2026-11-13 的 `selfplay-timing-v1` 按预定计划完成 3 轮、每轮 8 局和 2 次 PPO 更新：24 局全部自然终局，11,761 个环境控制步，6 次新优化器更新，1,510 次确认消费、0 拒绝，训练进程实测 88.435 秒。三轮历史快照都保留，最终候选为 `policy-77de9550d937f4d56348`，此时训练池仍以原 timing 为 foundation。

随后在独立验收池里让 timing、joint、最终 PPO 各执行相同的 150 个新案例。全部 450 局自然终局、31,916 次确认消费、0 拒绝；timing 为 86 胜 1 平 63 负，joint 为 84 胜 0 平 66 负，PPO 为 85 胜 1 平 64 负。完整分对手表、统计口径与恢复过程见 [ACCEPTANCE_ZH.md](ACCEPTANCE_ZH.md)。

PPO 对随机／规则／固定 timing 的得分分别为 90%／30%／51%，相对 foundation 配对提升 +2／−4／0 个百分点，三组均未达到预定改善与显著性门槛，**不晋升**。joint 只作为预声明诊断，同样没有通过门槛，也未被事后选为新 foundation。模型已实际训练、能完成对局，与“已证明棋力提升”是不同结论。

正式 450 局累计 236,366 个控制步，逐局累计 1,004.215 秒，带同机负载的实测吞吐 235.37 控制步／秒。环境中断留下的额外 100 局 PPO 回执被完整保留；冻结恢复后其全部已记录字段（除耗时）一致，但不作为额外独立样本。PPO 训练池、恢复文件和原报告在验收后保持原 SHA。[可移植审计包](../docs/audits/arena_next_20261113/league/README.md) 包含全部逐局证据及无需 Torch 的验证命令。

# PPO 失败实验的独立诊断：解码模式与动作分解

日期：2026-10-14。未修改 `royale_lab` 核心代码。保留最初 calibrated-v2 PPO checkpoint 的独立副本 `outputs/audit_candidates/ppo_audit/original_ppo.pt`，SHA256 为 `5f9dfa36109f49a78f5fa8a8afc3e44ea2c53c82b96ec2c00f59ca1d3a682142`。这是 4096 个训练 step 的模型，不是后续 warm-start 模型。

主平台原 deterministic PPO 的 100 局结果仍应保留。独立诊断发现：**模型的随机策略没有全 WAIT；将所有候选的单项 argmax 用于部署，才导致几乎永远 WAIT。**

## 实际配对对局证据

使用同一份 checkpoint、20 个相同 seed/卡组/对手配对，比较两种选择方式。对手为规则或随机，完整上限 3000 tick；此诊断场景与主平台冻结 100 局不同，不能将胜率直接混在主表。

| 执行方式 | 胜/负 | 接受出牌 | 拒绝 | 所有决策帧 | 有至少一张合法牌的帧 |
|---|---:|---:|---:|---:|---:|
| 单项 argmax | 0 / 20 | 0 | 0 | 1326 | 1326 |
| Categorical sampling | 9 / 11 | 530 | 0 | 3021 | 649 |

argmax 的 1326 个有可下牌动作的帧，每个都是“WAIT 的单项概率最大，但所有出牌候选的概率总和大于 WAIT”。第一个实测局势有 20 个候选，WAIT 概率 0.05866，全部下牌总概率 0.94134，单个最可能下牌落点概率 0.05252。取单项 argmax 因而选择 WAIT，尽管训练策略有约 94% 概率出牌。

这来自一个 WAIT 对多个“卡牌×落点”候选的动作表示。大量相近落点分割了出牌概率；一份接近均匀、稍偏 WAIT 的网络在单项 argmax 下会成为永不下牌的 deterministic 策略。训练使用 `Categorical(logits).sample()`，`LearnedPolicy.decide` 却无条件 `argmax`；这两种行为不是同一个被评估的策略。

训练实际日志也反驳“训练期间已停止下牌”：27 个完整训练局中共有 731 次接受出牌、10 胜、17 负。训练 curve 的平均 entropy 约 0.30–0.58 也不足以证明塌缩，因为大量圣水不足的状态只有一个合法 WAIT，其 entropy 必然为 0。在样例有牌状态中 entropy 约 2.99–3.13，接近 20–23 个候选的均匀上限。后续应按“至少两个合法动作”的帧单独汇报 entropy 和 WAIT 概率。

## 源码与表示检查

`royale_lab/learning.py::train_ppo` 的 GAE 使用 done 清除 bootstrap 和未来优势，PPO ratio 与 clip 的结构符合通常算法，未发现终局穿越或 old log probability 与 action 排列明显错位。没有保存原训练 rollout 的全部 logits，故不能宣称逐 tensor 复算了 4096 step 更新。

实际 encoder 探针发现两个可以优化的缺口：

1. 仅改变 `obs['next_card']`，feature matrix 完全不变。主观测提供了下一张，但当前 encode 没使用。
2. 固定 legal action 表、替换自方四张 hand ID 后，critic 使用的 WAIT 行完全不变，173 维卡牌 identity 区域始终为零。`CandidateNet.forward` 的 value 只读 `features[:,0]`，所以 critic 不知道自方手牌身份/轮转；WAIT 的局部单位统计还围绕 (0,0)，不能完整编码远处威胁。

这属于状态表示受限，并非此次不出牌的主要触发器；不应只用更多训练 step 掩盖。另有 `value_loss=.5*MSE` 后总 loss 又乘 `.5`，有效系数为 .25，若目标是常见的 .5 coefficient 可去掉一处；它不解释 argmax 与采样的剧烈差异。

另一个实际实现缺陷与 WAIT 无关：最初 `train_ppo --steps 4096 --resume` 在现有 checkpoint 已完成 4096 step 时不进入训练循环，`curve` 为空，随后 `curve[0]` 引发 IndexError。`tools/audit_ppo_resume.py` 将所有训练输出路径隔离到 audit sandbox 后实际复现，原主 checkpoint 未改；原始失败已归档为 `resume_probe_before_fix.json`。主平台随后修复为无新增预算时返回明确 no-op 状态，继续训练时读取旧 curve/episode 记录；隔离脚本重新执行通过，见当前 `resume_probe_report.json`。初始 resume.pt 只保存 model、optimizer、steps、seed，没有 RNG 和环境状态，属于参数/optimizer 续训，不能宣称逐 step 完全相同的恢复。测试了无新增预算分支，没有把短检查说成逐 tensor 连续训练的等价证明。

## 最小修改建议

1. 增加显式 `ppo_sample` 执行模式，使用策略实例自己的 seed/RNG 从 masked categorical 分布抽样；保留 `ppo` deterministic 结果和原 checkpoint。按原冻结场景执行至少 100 局，用零拒绝与 replay 验证实际动作。BC 模型仍可使用 argmax。
2. 在 UI/日志同时展示 `P(WAIT)`、`P(play)` 总和、最佳单项概率与可用动作数；不要把低平均 entropy 自动称为模型塌缩。
3. 若需要 deterministic 决策，进一步分层成 WAIT/play → 卡牌 → 位置，使是否出牌的决策不会依赖落点被离散成几个副本。单纯对概率做 group sum 也需重新验证具体动作采样/训练关系。
4. 给 value 一个独立的全局可见 state encoder，加入手牌、next card、敌我分路压力和塔信息；actor 可继续逐候选评分。这时必须更新 encoder 版本并重新训练。

独立诊断命令：

```powershell
.venv\Scripts\python.exe tools\audit_ppo_behavior.py --games-per-mode 20
```

结果在 `outputs/audit_candidates/ppo_audit/behavior_report.json`，每局有 `.replay.json` 和压缩 `.trace.json.gz`，包括每帧概率和动作反馈。最初训练 episodes/curve/manifest 也已复制。原始 100 局评估、这 40 局诊断、后续 sampling 100 局应分别保存，并标注模型和选择方式。

主平台已加入 `make_policy('ppo_sample', catalog, seed)`，使用实例私有 `torch.Generator` 和 masked multinomial 抽样。新增采样种子一致性/有牌行为测试，项目内干净 `.venv-repro` 的 20 个测试全部通过。独立 100 局 sampling 评估由主实验报告保存，本报告的 20 对诊断不替代它。

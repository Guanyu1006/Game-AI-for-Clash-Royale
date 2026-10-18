# 第三阶段小模型：训练、加载与结果口径

本阶段的可运行入口是根目录 `.venv/Scripts/python.exe` 与 `tools/phase3_train.py`。
它只处理项目内的已冻结 JSONL 数据，不连接原生 RPC，也不启动第二个控制者。
训练成功、预测任务达标与原生胜率改善必须分开看。

## 输入与模型

同一份元数据指定数据 SHA256、版本指纹、玩家观察 profile、10 tick 节拍和分组。
当前版本是 20Hz，所以每次策略观测间隔通常为 0.5 秒。
`dt_ticks` 只能表示本次观察与过去观察的间隔；未来转移长度留在独立字段。

| 变体 | 实际 actor 输入与结构 | 位置输出 |
|---|---|---|
| A | HUD 的单帧 MLP | 两个 sigmoid 经公开部署区域变换 |
| B | 当前战场向量 MLP | 同 A |
| C | 当前与历史向量、belief、自己上一动作、实际反馈、过去 dt，GRU | 同 A |
| D | 与 C 相同 | 4 个卡槽分别具有 18×32 概率图 |
| E | D 加下一状态九维监督头 | 同 D；辅助目标从不进入 actor 输入 |

当前 fair profile 排除全部未获独立可见性证据的非塔实体。
因此 B 增加的是空实体位置，不能宣称它看到了额外可信战场信息。
历史与 belief 仍然可以提供过去已实际消费的公开出牌事件。
对手预测头和一间隔出牌 hazard 头是独立的可选实验，默认关闭。
hazard 头目前只增加辅助训练损失，部署依旧每 10 tick 决策；它不等于已经实现可变等待控制。

部署位置由**预测卡槽当前公开手牌**决定限制，不读取监督标签的 `card_id`。
普通牌仅使用己方半场的保守提案区域，普通法术使用全场。
网格位置以 `y//1000*18+x//1000` 编码，解码使用格心。
这些规则只限定提案范围，完整合法性和是否真正消费依赖原生反馈。

## 可复制的训练命令

```powershell
.\.venv\Scripts\python.exe -B tools/phase3_train.py train `
  --metadata data/phase3/native/pilot-003/metadata.json `
  --output models/phase3/my-new-D `
  --variant D --epochs 2 --seed 31001 --play-weight 8
```

输出目录必须是新目录。`configuration.json` 冻结输入与训练配置。
每轮保存 `epoch-NNNN.json`、安全 NPZ 数值文件、验证曲线及 `latest.json`。
JSON checkpoint 包含优化器状态、RNG 状态与验证集最优模型；部署加载的是保存的验证最优权重。
没有调用 `torch.load`、pickle 或不可信对象数组。

`--play-weight` 是**训练**出牌类别权重，不是部署 play bias。
其默认值为 1；若设为 8，应在所有主要同预算对照中共同使用。
验证指标始终在自然时间分布上计算，不重复出牌帧，不给指标额外加权。
当前选择标准是验证动作 NLL；没有用 test 选阈值、轮数或模型。

## 中断与恢复

```powershell
# 第一进程运行一轮后主动退出；总预算仍声明为两轮。
.\.venv\Scripts\python.exe -B tools/phase3_train.py train `
  --metadata data/phase3/native/pilot-003/metadata.json `
  --output models/phase3/my-resume-D --variant D --epochs 2 `
  --seed 31001 --play-weight 8 --stop-after-epochs 1

# 新进程读取 latest.json，继续第二轮；其他配置必须完全相同。
.\.venv\Scripts\python.exe -B tools/phase3_train.py train `
  --metadata data/phase3/native/pilot-003/metadata.json `
  --output models/phase3/my-resume-D --variant D --epochs 2 `
  --seed 31001 --play-weight 8 --resume
```

真实 pilot003 已实际进行这两次独立进程运行，并与连续训练比较。
所有模型张量逐位相同，验证预测 SHA256 相同。
证据：`outputs/phase3/real-resume-verification.json`。
这里验证的是按 epoch 边界恢复；非正常断电后回到最后完整 epoch，不能宣称恢复半个 batch 的状态。

## 如何接入原生环境

```python
from royale_phase3.learning import StatefulPolicy

policy = StatefulPolicy.load(
    checkpoint_path,
    version=expected_version_sha256,
    profile="fair",
)
policy.reset()  # 每次原生 reset / 新局都调用
decision = policy.predict(current_public_row)
```

`current_public_row` 的字段与训练 JSONL actor 字段相同。
`action_index=0` 是 WAIT，1..4 为卡槽 0..3；`xy_native` 为原生绝对坐标。
必须再经公开动作校验并提交给唯一的原生控制者，记录 consumed/rejected/unknown。
反馈与上一动作在下一次实际观察构造时进入 actor，不能把计划动作当作消费成功。
同一局同一 tick 重复读取是幂等的；改变同 tick 输入或 tick 回退会拒绝，避免偷偷重复更新 GRU。
新局和换边清空隐状态；匿名实体身份、文件路径、质量标签、未来监督不会编码进网络输入。

训练把每局每侧分成最长 32 步（16 秒）学习片段，最多前置 8 步（4 秒）burn-in。
burn-in 只重建隐状态，不计算其损失，隐状态随后 detach。
尾部 padding、未知监督和无位置的 WAIT 均有 loss mask。
验证与在线运行按完整真实观察序列逐步更新，使用同一个 decoder。
训练有限 burn-in 与部署整局记忆的差别是已知限制。

## 已经跑出的 pilot 结果与限制

`pilot-003` 只有 5 个独立整局：3 train、1 validation、1 test。
这远低于任务书的数据门槛，不能用它宣布最终消融或策略强度提升。
test 只被载入做 schema/分组校验，没有计算模型得分。

同 seed、同 2 epoch 的 A—E、默认权重 1 均在验证集预测全 WAIT。
低动作 NLL 并不能说明策略能玩；验证集中 17 次真实出牌全部漏掉。
同预算 D、固定训练权重 8 的演示学生预测 58 次出牌，时间匹配 15/17，
事件 precision 0.2586、recall 0.8824，时间匹配卡槽准确率 0.3333。
这是低覆盖规则 teacher 的离线蒸馏接口实验，不是原生胜率结论。
事件容差在训练前固定为 ±10 tick（0.5 秒），没有为了结果扩大容差。

对应 checkpoint 为：
`models/phase3/pilot-003-D-distill-weight8-seed31001/epoch-0002.json`。
它保存了验证最优轮的 actor，实际加载和新进程恢复均已测试。
更多整局数据的冻结主实验、原生完整闭环、预测辅助与最终 test 应独立保存新报告，不能覆盖 pilot 失败。

历史基线的全表结构计数是 26,068 行，hard-E 18,440 行（18,388 WAIT、52 出牌），12 局。
`outputs/phase3/baseline/bounded-v1/report.json` 对 109 条分层抽样做了严格证据和预测路径核对：
批量、逐条部署、历史 decoder 无差异；随机基线 RNG 可复现。
它不是完整全表逐行证据验收，也不报告抽样准确率。

随后完整严格审计已经完成：
`outputs/phase3/baseline/aligned-resume-v1/report.json`。
18,440 条合格行的证据校验及部署/离线预测逐条一致，差异为 0，耗时约 557 秒。
冻结配置后原历史 test 仅报告一次：该 test 只有 1 局、9 个真实出牌。
旧 variant 的固定 play bias 4 使 947 个可出牌观察都返回 PLAY，
事件 recall 为 1，但 precision 仅约 0.00950，平均位置距离约 8,013 原生单位。
这暴露了旧离线评估没有走实际带偏置推理的问题，也说明单看召回会误导。
本次校正没有证明策略改善；新 A—E 必须用自己的冻结数据和配对原生评估另行比较。

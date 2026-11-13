# 冻结的三模型完整对局验收

`league_acceptance.py` 将时序模仿 foundation、同数据 joint 基线、按预定计划完成自博弈的 PPO 模型放入独立验收对手池。它不修改 PPO 训练池、恢复状态或训练统计；开始前和结束后都验证这些文件的 SHA。三个模型分别从自身 checkpoint 的原始 `environment_identity.config` 建立模拟器，完整环境身份必须一致。

本轮对局在训练完成前已声明：25 个新种子、随机／规则／固定 foundation 三类对手、双方座位，共 150 个配置。三模型逐一执行相同配置，总计 450 局。随机对手的种子也相同；时序模型采用冻结事件阈值后的条件解码，joint 使用原联合 argmax。对局失败、截断、拒绝和最终状态哈希都保留，不因某组胜率偏低而提前停止。

```bash
.venv-cloud/bin/python -m arena_lab.league_acceptance prepare \
  --output outputs/arena-next-20261113/promotion-three-arm-v1 \
  --template configs/arena-lab/promotion-next-v1.json \
  --cases outputs/arena-next-20261113/promotion-cases-v1.json \
  --foundation outputs/arena-next-20261113/timing-v1/best.pt \
  --joint outputs/arena-next-20261113/timing-joint-baseline-v1/best.pt \
  --ppo outputs/arena-next-20261113/selfplay-timing-v1/round-002-attempt-000/policy.pt \
  --training-run outputs/arena-next-20261113/selfplay-timing-v1 --threads 1

.venv-cloud/bin/python -m arena_lab.league_acceptance run \
  --output outputs/arena-next-20261113/promotion-three-arm-v1
```

`prepare` 只验证模型、解析预先声明的 foundation ID、建立新池并保存 `execution-plan.json`，不运行游戏。新机器复现应先准备相应数据与权重，并选用新的输出目录；准备阶段拒绝覆盖已有目录。

每组 `foundation/`、`joint/`、`ppo/` 包含冻结 `plan.json`、逐局刷新保存的 `episodes.jsonl` 和绑定前两者 SHA 的 `summary.json`。顶层 `report.json` 从完整逐局回执重新统计每种对手的胜／平／负、故障、截断、请求、确认消费、拒绝、决策吞吐和模拟时间倍率。读取已有分组时会重新核对预算、对手身份、完整对局矩阵和每条回执的配置。已经完成且哈希一致的分组可以在恢复时跳过；半完成目录保留，命令拒绝自动覆盖它重跑。

PPO 的实际晋升由 `OpponentPool.promote` 从已保存证据重新判定，门槛来自预先冻结配置：每种对手至少 50 局和 25 个完整种子簇、双方座位、无缺失或故障、得分至少 0.55、平均配对提升至少 0.05；双座位按种子聚合后做单侧精确符号检验，对三类对手进行 Bonferroni 校正。确认消费必须大于零且拒绝比例不超过 1%。任一对手组不达标即不晋升，不以保存新 checkpoint 代表能力提升。

joint 对 timing 的比较使用相同门槛生成诊断报告，但不改变预先选择的 foundation，也不会因为看到了这次结果而重选 PPO 起点。所有晋升结论仅适用于这套固定版本模拟对手，真实设备与国际服排名有独立验收。

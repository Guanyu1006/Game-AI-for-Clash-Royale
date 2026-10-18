# 当前可执行入口

工作目录为项目根目录。先读 [TUTORIAL.md](TUTORIAL.md) 理解平台，再读 [STATUS.md](STATUS.md) 和实际 `outputs/phase2/acceptance.json` 判断验收状态。当前阶段 A–I 总验收已经全部通过；源码、资源或模型变化后仍必须重新执行受影响门，不能只看旧快照。

```powershell
.\phase2.ps1 doctor
.\phase2.ps1 verify-version
.\phase2.ps1 verify-data
.\phase2.ps1 baseline-check
```

已验证的阶段二闭环证据：`outputs/phase2/acceptance.json`；严格轨迹导出在 `outputs/phase2/learning_pipeline/export`，最终 G checkpoint variant 在 `outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json`，10 局原生 agent 在 `outputs/phase2/native/agent_eval/acceptance-008`，冷启动离线 B 在 `outputs/phase2/offline/acceptance-003`，固定画面 F 在 `outputs/phase2/viewer/acceptance-001`。

虚拟机关闭时按 [SECONDARY_AUDIT.md](SECONDARY_AUDIT.md) 恢复 isolated/restrict=on 运行时、Android包和本机SSH/ADB转发，再执行 `probe-status`。当前一个原生实例只能有一个控制者，不要同时开viewer、batch、机制或agent评估。

实际重建及可恢复队列：

```powershell
.\phase2.ps1 replay 85c0a48b6a1d47cc8057710e05bf7773 --output outputs/phase2/manual/example-01 --grid-ticks 10
.\phase2.ps1 batch-replay --name formal100-v1 --split formal --grid-ticks 10
.\phase2.ps1 cohort --help
.\phase2.ps1 repeatability --help
```

同名队列从未完成episode续跑，已完成结果先校SHA；单局完成目录不可覆盖。完整失败保留在正式100分母。学习扩展与重复采集在独立清单/队列，三次重复只选预声明r1用于学习，不复制样本。

配套原生画面与旁路HUD：

```powershell
.\phase2.ps1 view plan
.\phase2.ps1 view serve --exclusive-runtime --restore-headless --record
```

打开命令实际打印的本机URL，用页面开始、暂停、继续、单步和定量推进。主画面来自Android原生framebuffer，旁路显示手牌、圣水、塔血与事件。恢复headless需重新reset，不能保存完整渲染场景快照。说明见 [VIEWER.md](VIEWER.md)。

数据/学习入口：

```powershell
.\phase2.ps1 export --help
.\phase2.ps1 train-bc --help
.\phase2.ps1 evaluate-model --help
.\phase2.ps1 evaluate --help
.\phase2.ps1 benchmark --help
```

复核本轮总验收（使用 data 环境以读取 PyArrow/NumPy）：

```powershell
.\.venv-phase2-data\Scripts\python.exe -B tools/phase2_acceptance.py `
  --formal-queue outputs/phase2/batches/formal100-v1/queue.json `
  --repeatability outputs/phase2/repeatability/learning16/report.json `
  --repeatability outputs/phase2/repeatability/formal2/report.json `
  --export outputs/phase2/learning_pipeline/export `
  --checkpoint outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json `
  --agent outputs/phase2/native/agent_eval/acceptance-008 `
  --offline outputs/phase2/offline/acceptance-003 `
  --viewer outputs/phase2/viewer/acceptance-001 `
  --benchmark outputs/phase2/performance/whole-pipeline/report.json `
  --output outputs/phase2/acceptance.json
```

G 的受限研究夹具使用 `tools/phase2_seed_scan.py` 选择含普通法术的合法初始牌序，并用 `tools/phase2_evaluate_native.py --spell-first-deck --seed-list ...`；这不是完整卡组策略或官方强度声明。

具体参数和质量规则见 [TRAJECTORIES_AND_BC.md](TRAJECTORIES_AND_BC.md)。动作未确认、无确定性证据、等级有损或有效样本不足10000时严格训练会拒绝。`evaluate-model`是离线预测，`evaluate`才加载checkpoint运行真实完整原生局。当前严格策略动作空间是WAIT加普通Fireball/Arrows，不能当作完整卡组策略。

`doctor`只清点环境，`verify-version`只校文件身份，`probe-status`只看本机通信。完整验收还须真实状态/动作/录屏/训练记录。失败看返回码、逐局error.log和 [BLOCKERS.md](BLOCKERS.md)。

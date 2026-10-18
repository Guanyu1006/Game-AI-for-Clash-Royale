# 从零理解固定版本原生回放平台

本阶段的可运行系统由真实 Android 游戏程序、项目内隔离运行时、命令探针、Python 适配器和数据流水线组成。它已经真正计算过战斗和终局；最终完成情况请读 `outputs/phase2/acceptance.json`，运行进度读 `phase2/STATUS.md`。计划中的实验不能代替该文件中的实际证据。

本轮最终证据已闭环：A–I 全部 `pass`。正式分母为100并全部记录；严格导出包含18,440条 hard-E 决策，训练/验证/测试为13,431/3,348/1,661。固定画面 F 的181帧、5个raw semantic锚点和两段MP4已通过验证；离线冷启动 B 通过；G 使用保存的 legal-mask constrained policy variant 完成10局自然终局、14次真实模型卡牌消费。模型仍只覆盖 WAIT+普通 Fireball/Arrows，不能称为全卡组或官方强度策略。

## 1. 先分清四个系统

网上称为“私服”的项目可能承担完全不同的职责。账号、箱子和大厅服务器可以只提供会话配置；命令中继可以只传递双方动作；战斗逻辑负责移动、索敌、攻击、伤害、死亡、费用、轮转和终局；renderer 把战斗对象显示为画面。只实现前两项，不能用来验证真实卡牌交互。

本项目选择在本机运行固定 ARM64 游戏库 `libg.so`，由同版本游戏资源初始化战斗，使用固定 probe 调用原生战斗入口。Python 不重新实现攻击伤害规则。Android 位于 Redroid 容器中，容器位于项目目录内的 Ubuntu/QEMU 虚拟机。ARM64 库通过镜像中已有的 `libndk_translation` 执行。Windows Python 经本机 SSH/ADB 转发访问 Android loopback 控制端口。所有正式计算在这个本地进程内完成。

调用链：

```text
真实 source episode + 固定转换假设
   -> reconstruction.py: source_commands / native_match / align_initial_deal
   -> NativeBattleBackend: reset / schedule_journal / advance_exact_ticks
   -> loopback 26789 -> SSH -> guest adb forward -> Android probe
   -> 同版本 libg 的战斗对象、原生 tick、命令注入
   -> cr-minimal.v4 状态 + CardPlay + scheduler receipt
   -> 独立质量比较 / 玩家可见投影 / Parquet / BC
```

画面使用同一 libg 的原生场景。最小客户端画面缺完整手牌 HUD，旁路面板补充所选玩家手牌、圣水、塔血和动作事件；不能据此宣称复现了官方在线客户端全部功能。平台不依赖官方账号、天梯或第三方远程对战服务。

## 2. 固定的究竟是什么

目前选择 `nullsroyale.rel.free` 客户端 build **15.535.13**、content **15.535.86**、ARM64 `libg`，20 ticks/s，每 tick 50ms。`data/phase2/version_manifest.json` 是机器可读身份锁：包含真实 libg/probe/APK、基础和更新资源、转换表及数据锁。通过 `verify-version` 只能证明本地文件身份；实际运行、安装身份和时序对照是另外的证据。

源码包装层锁定 Jason commit `70a08af6807fa4d59a11c7d52bf6272a96afe30c`、FirstLight commit `71944e3993b594b3cc89b38768b5e817dfbdbe0e`。公开取得的整体 APK 与上游历史整体 SHA 不同，实际 libg 与 242 项上游资源 SHA256 相同。完整更新的公共 fingerprint 存在斜杠转义差异，保留原文件及严格 JSON 等价派生证明。`assets.scdb` 的公共 SHA1 元数据有差异，使用独立固定来源 SHA256 单独证明；没有把该例外扩大到其他文件。

来源原始对局没有可靠游戏 build、平衡日期、种子或完整初始牌序。这些字段保持 unknown。我们能建立“固定本地版本下可执行、终局可比、重复确定”的操作兼容子集；仍不能据此证明它与某个官方 build 的完整时序逐帧相同。

开源封装的许可证不能覆盖游戏程序、图片、音效、ARM 翻译组件或原始回放数据。实际来源、哈希和再分发边界见 `NATIVE_AUDIT.md`、`SECONDARY_AUDIT.md`、`DATA_AUDIT.md`。理解包排除 APK、专有素材、虚拟机、环境、缓存和密钥。数据集未声明的许可不能由下载包装器的 Apache 许可补齐。

## 3. 从现有项目启动与检查

在项目根目录 PowerShell 执行。命令不会安装全局依赖：

```powershell
.\phase2.ps1 doctor
.\phase2.ps1 verify-version
.\phase2.ps1 probe-status
.\phase2.ps1 verify-data
.\phase2.ps1 baseline-check
```

`doctor` 是环境清单，不能单独证明完整验收。核心工具使用 `.venv-phase2-core`（stdlib），数据/学习使用 `.venv-phase2-data`（锁定 PyArrow/NumPy），运行时审计工具使用项目内 `.venv-audit`。缓存由 `royale_phase2.runtime.configure` 指向项目内；不要更改全局 HOME、CODEX_HOME 或代理。

如果虚拟机尚未运行，按 `SECONDARY_AUDIT.md` 的项目内 QEMU 启动和 guest 服务步骤恢复。正式模式必须为 `qemu-start --isolated`，即 QEMU usernet `restrict=on`；准备下载阶段才能使用开放外网模式。之后启动 Android、本地包、ADB 转发和 SSH 隧道。请先看该文档和实际 `--help`，不要重复安装或在一场运行中的对局上强制重启。

完整从空目录复现需要先依法自行取得、放置和校验不可再分发依赖。源地址、精确文件名和 SHA 都在版本/来源清单中；来源失效时不能用同名不同版本替代。现有构建工具会验证组件；分享包本身不包含这些大型依赖。

**当前只有一个原生实例。** viewer、回放队列、机制测试、agent 评估都操作同一个进程。必须顺序运行。跨程序 reset 会使 generation 改变，适配器应立即报错；不要把脚本的 worker 参数理解成实际多实例。

## 4. 真实回放怎样变成可执行命令

原始数据锁定 HF `VanguardX101/IL_Replay` revision `059d43a02138a34b1b3009cc2acc7630fb99a638`。已下载 shard 中有 852 局和 59,906 个事件，原 payload 与扁平 actions 双表逐条核对。正式 100 局和开发 20 局冻结在 `data/phase2/frozen_manifest.json`；学习扩展清单另存，不能替换正式分母。

`source-v1/episodes/<id>.json` 保留来源字段，`firstlight-*/<id>.json` 保留转换假设和完整 commands，`reconstruction.py` 再把这些命令驱动到实际原生后端。转换中的关键约定：

- source 与 native owner 分开保存，不能看到输赢不合就翻面。
- 使用固定转换器的 20Hz 时间规则，目标 native tick = source tick + 1；坐标为 cell center + subcell offset，按上游已声明边界规则约束在 x=0..17999、y=0..31999。保留原始坐标和未截断值。
- 双方卡牌等级必须无损。当前原生 serializer 不能表达部分来源混合等级，这些局明确拒绝，不把全部等级悄悄改成 11。
- 完整公开动作序列可约束初始手牌和轮转，但不能告诉我们真实隐藏 seed。使用预先选定假设，最多 8 次 reset 校准实际牌槽排列；保留每次尝试。这个重建假设不能进入 actor 特征。
- 不使用来源最终血量或胜负搜索初始化，不删除动作、不改时间、不注入终局。原生场景的 watchdog 不是来源终局时间。
- source 的 timeline duration 与原生终局时钟语义未独立建立；来源 `terminal_engine_tick` 缺失，所以该字段为 unknown。不能拟合一个时间偏移后宣布精确通过。

运行单局，例如开发样例：

```powershell
.\phase2.ps1 replay 85c0a48b6a1d47cc8057710e05bf7773 --output outputs/phase2/manual/example-01 --grid-ticks 10
```

完成目录不可覆盖。另用新目录重跑。看 `episode.json` 的五个质量维度及 `failures`，再沿 `files` 的路径和 SHA 找原始状态、wire、初始化、动作反馈和决策点。

## 5. 状态、动作和消费证据

`debug_state` 返回原生完整遥测，用于审计。`observe_visible(owner)` 独立生成 actor 的白名单：己方当前牌槽/费用、己方原始圣水、公开 tick/皇冠，以及 reset 时已认证的六塔。对手手牌、精确圣水、双方完整 cycle、隐藏种子、未来命令、内部 target/cooldown、packed 参数及未知可见性非塔实体全部排除。

这是一种保守的部分可观测问题。真实玩家能看到大量单位，本最小 actor 尚未可靠恢复这些可见性，因此策略能力会受限。扩充感知时必须先确定“谁在什么时候能看见什么”，不能直接把所有 debug 实体接进网络。

动作需要经过多个不同阶段：planned -> queued -> native injected -> consumed。scheduler 返回 succeeded 只说明注入调用成功；不能等同于下牌成功。卡牌实际消费通过真实 CardPlay、己方费用/手牌/轮转前后变化补证。英雄事件的 effective ID 可能为 0，进化事件可来自另一个表；确认输入 root 被消费时仍保留具体形态身份 unknown。

技能注入已经能调用，但通用消费证据仍不足。含技能的回放即使其他卡牌全部执行，也会保留未解释输入，不能进入严格成功子集。请求失败或断连后不盲目重发；应从已验证的初始化和 journal 重建。没有完整 snapshot/restore，保存 observation 不等于保存游戏内存。

## 6. 为什么有五种质量状态

|维度|它能说明的事|不能推出的事|
|---|---|---|
|parse_valid|结构、事件可解析|原生能执行|
|runtime_replayable|所有输入有确认消费且自然终局|来源官方时序相同|
|deterministic|同初始化/输入独立 reset 的暴露语义序列相同|程序物理规则正确|
|outcome_consistent|来源可用胜者/皇冠/塔血等对照通过|每一刻位置、伤害都相同|
|trajectory_validated|有独立时序参考并完成比较|未覆盖组合自动正确|

目前没有独立官方逐帧参考的局必须是 `trajectory_validated=not_available`。即使前四项都通过，严格训练子集也只是本地操作兼容等级，不命名为 gold。缺失终局时钟、已消失塔的未读 HP 不能用估计值填上“通过”。

20 机制夹具区分内部回归与独立对照。原始失败、预期修正与重测报告均保存。例：本版本三倍圣水的精确整数速率来自原始 CSV，不能把另一 Python 包里的近似公式当原生真值。Tombstone 子实体还可能沿用父 root 卡 ID，不能把 minimal entity.card 直接当单位原型表。

## 7. 批处理与恢复

```powershell
.\phase2.ps1 batch-replay --name formal100-v1 --split formal --grid-ticks 10
```

队列锁定源清单、版本、转换实现、backend 和采样网格。输入或代码改变后必须新建队列名称，不能复用旧结果冒充同一实验。完整失败也计入结果；执行中断只保留 attempt，不计为完成。相同命令恢复时，从 reset 开始新的 attempt，检查已完成产物 SHA，按 source ID 只计一次。

真实恢复演示在 `outputs/phase2/batches/recovery-verified`：实际消费一张卡后杀死 worker，残留不被接受，再次执行后新 reset 完整结束。更早的日志封存开发错误也保留在 `recovery-demo`，不覆盖历史。

当前只支持一个实际 worker。吞吐需包括 reset、推理、I/O、失败与恢复，不能用 probe 局部 tick 速度推算整条流水线。atomic 与 two-RPC 的局部推进对照是独立的小实验，整队列有另外的 wall time/CPU/内存/磁盘读写统计。

## 8. 可信训练标签和最小 BC

训练记录必须先采样、再提交标签动作、最后读取执行反馈。JIT replay 只在目标 tick 的前一刻登记当前动作，避免把完整未来 journal 混入 actor。常规采样网格上的没有动作构造 sampled WAIT；这不是人类明确点击“等待”。事件额外边界的空行不自动成为 WAIT，受限动作空间外的卡牌也不能改标 WAIT。

合法性与“手牌且付得起”不同。本最小严格 BC 只学习 **WAIT + 当前手牌中的普通 Fireball/Arrows + 原生全场请求坐标**。两类普通法术有版本规则、40 次真实正例、原生反例、两 owner 私有字段扰动和命令准备状态证据。这个 mask 不覆盖建筑 footprint、所有卡牌、特殊形态或技能。有限落点测试结合卡族规则形成作用域证据，没有穷举所有整数坐标。

export 保留完整未过滤统计，把不满足 parse/runtime/determinism/outcome/等级/版本证据的记录隔离。按整局 leakage_group 划分 train/validation/test，换边、重复执行、切片和初始化假设必须在同组；同一 source 的三次重复采集只选一次用于学习，不能复制凑 10,000。

具体 export/train/evaluate 参数见 `TRAJECTORIES_AND_BC.md` 和实际 `--help`：

```powershell
.\phase2.ps1 export --help
.\phase2.ps1 train-bc --help
.\phase2.ps1 evaluate-model --help
```

NumPy BC 是带牌槽分类和连续落点回归头的小网络，特征词表只用 train 整局建立。WAIT 多数类会掩盖出牌能力，因此必须分开报告 WAIT/PLAY、卡槽、落点误差、mask 覆盖与组数；不能以总准确率代替策略效果。checkpoint 保存版本/数据/切分/代码 hash，加载时拒绝版本不一致。新进程在原生后端真正下牌并完成 10 局才是模型闭环；离线预测分数不能代替这一步。

## 9. 后续优化应改哪里

|目标|主要入口|需要的新证据|
|---|---|---|
|更多可执行回放|`reconstruction.py` / 原生 serializer|混合等级、技能消费、初始条件与数据版本映射|
|更可靠交互一致性|`quality.py` / 独立时序参考适配|来源录像/状态锚点，不是同程序自对照|
|看到单位并判断局势|`visibility.py` / 新感知适配|单位原型、可见性、双方视角与泄漏反例|
|扩大可行动作空间|`trajectories.py` / 原生合法性证明|建筑落点、部署区域、技能 readiness 等规则证据|
|更强策略|`learning.py` / 新策略模块|保持数据隔离，比较 play/WAIT 及真实完整局表现|
|RL/self-play|`agent_env.py` / `NativeBattleBackend`|真实 rollout、奖励版本、超时/终局与失败诊断|
|更快批量训练|原子推进、I/O、独立 Android 实例|多实例状态/端口/资源隔离实测，禁止共享单例并发 reset|

问题可建模为部分可观测决策过程：隐藏真实状态 s_t 经原生转移到 s_(t+1)，actor 接受可见历史 o_<=t，输出 WAIT 或 (卡牌/槽位、坐标、时机)。当前 BC 估计公开动作条件分布，不保证最优解；未来可以用记忆网络、模仿学习、受约束搜索或 RL 改进，但前提是版本、轨迹和可见性契约仍成立。

换版本时要新建版本清单、重新核 libg/probe/完整资源、数据 schema 与 ID 映射、机制夹具、时间坐标、mask、回放对照、确定性和渲染锚点。旧数据和 checkpoint 默认不可直接兼容。请保留旧产物，给新版本独立命名空间，避免把平衡更新后的结果混在一个模型中。

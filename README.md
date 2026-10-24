# Royale Lab：皇室战争本地 AI 研究平台

> **双方协作与读码入口（2026-10-24）：** 当前共同开发基线为 `dev/main-start-20261020`。先读[开发交接与代码调用图](docs/DEVELOPMENT_HANDOFF_20261024_ZH.md)，其中区分四套代码、最新已验证能力、剩余验收、资源边界和两人分支操作。[Git 内的证据摘要](docs/audits/development_handoff_20261024.json)可随克隆阅读；下面指向 `outputs/` 的完整原始证据仍保留在原 Codespace。

> **公开对局 JSON 的统一入口：** `.venv-cloud/bin/python tools/cloud/workspace.py play-replay` 会复用或恢复已有 Android，准备连接并启动 8772 页面。页面支持导入单局 RoyaleAPI／IL_Replay JSON、打开、播放、暂停、单步与跳转；也可下载在本机运行的 Android 镜像脚本。使用方式见[导入、执行与显示](docs/NATIVE_VISUAL_ZH.md#公开事件-json导入执行与显示)。导入与接口测试、实际原生运行结果分别记录，不把代码检查算作游戏画面成功。

> **最新实际结果（2026-10-24）：** 92 事件公开样本的部分对局验证已通过，真实浏览器跑通导入、打开、播放、暂停、保存及只读回看流程。原生初手对齐到 tick 90，暂停完成于 tick 670／generation 2；已执行的前 7 条来源动作全部确认原生消费，0 条未确认。[tick 190](outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/frames/000001.png)与[tick 670 原始画面](outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/frames/000006.png)可见真实战场单位及塔血变化。保存的 7 帧可只读回看，原生时钟不变，并已无损导出 [29.05 秒 APNG](outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/animation.png)；页面 JavaScript 错误为 0。完整 92 事件、自然终局和前后向原生重演尚未验证；Windows 镜像也未在本机验收。见[总体验收记录](outputs/cloud/import-play-display-20261024-003/acceptance.json)、[真实浏览器记录](outputs/cloud/import-play-display-20261024-003/live-browser/report.json)与[操作指南](docs/NATIVE_VISUAL_ZH.md#公开事件-json导入执行与显示)。

> **历史浏览器故障（2026-10-24 06:27–06:31 UTC）：** 4 核／16 GB 升配后，早期游戏进程在准备通过约 16 秒后因软件图形等待触发 Android ANR 退出，VM 仍运行。该次浏览器建局尚未发出 `configure-native`。见[历史诊断证据与恢复记录](docs/NATIVE_VISUAL_ZH.md#当前故障准备成功后游戏因-anr-退出)。这些是早期失败现场，后续实际进展见上方。

> **历史 VM 退出诊断（2026-10-24）：** 上传的 Codespaces 日志显示，六次 SIGTERM 均与宿主低可用内存相关，且宿主存在 earlyoom；内存保护是高可信推断，尚无直接发送者回执。见[内存诊断与 16 GB 恢复步骤](docs/CODESPACES_MEMORY_DIAGNOSIS_20261024_ZH.md)。doctor 现包含实际内存余量；后续已取得上方的原生画面与动作消费证据，完整验收仍需继续。

> **原生画面与浏览器操作入口（2026-10-24）：** [原生视觉操作指南](docs/NATIVE_VISUAL_ZH.md)包含 8772 页面、Android PNG 采集、手牌与原生坐标操作、保存画面离线回看、按双方原始动作日志重演与前后定位。首次视觉尝试曾因 VM SIGTERM 中断、只取得加载阶段全黑截图；该历史失败及后续进展均保留在[实现与验收报告](docs/NATIVE_VISUAL_IMPLEMENTATION_20261024_ZH.md)，不以历史六局数值实验替代视觉证据。

> **从零理解并操作整个流程（2026-10-24）：** 先读[实验、训练、原生启动与回放逐步教程](docs/NATIVE_LOOP_ZH.md)。按“查看已有对局／只训练保存数据／生成新原生实验”三条路线操作，并解释 713 维输入、GRU、模仿学习和模型评估。当前接入固定版本 Null’s Royale 原生库及离线探针；保存观测回放尚不等于原版游戏逐帧画面。

> **原生闭环已实机通过（2026-10-20）：** 该次 Codespaces 原生小规模闭环已实机通过：4 场采集、2 轮 GRU 训练、新进程加载后 2 场评估，6/6 自然终局，2424 条转移、24194 个推进 tick、202 次控制方实际消费。见 [操作与验收报告](docs/NATIVE_LOOP_ZH.md)、[系统结构图](docs/SYSTEM_STRUCTURE_AND_LOOP_ZH.md)及[软件 Android 指南](docs/SOFTWARE_ANDROID_TCG_ZH.md)。该次数值实验未覆盖原生画面／实时浏览器控制；普通单位的公平可见性和棋力提升仍未验收。10 月 20 日实验结束时 VM 已停止，保存回放可独立使用；后续运行状态见上方最新进展。

> **两次整个窗口中断：** 见 [窗口中断核查与恢复说明](docs/WINDOW_RESTART_20261020_ZH.md)。已观察到远程容器／进程重建，尚未证实根因；当前采用单台低优先级软件 VM、串行任务和资源采样，不能据此宣称崩溃已修复。

> **当前 Codespaces 开发入口：** 读 [远程开发训练与回放操作指南](docs/REMOTE_DEVELOPMENT_ZH.md)。支持环境配置、两种模型训练与重新加载、validation 验证、保存观测回放和近似模拟器；一键检查为 `.venv-cloud/bin/python tools/cloud/workspace.py smoke --epochs 2`。

> **已导入 Codespace，继续配置 Codex 并提交上传：** 读 [最后配置与上传步骤](docs/CODEX_CODESPACES_ZH.md)。

> **GitHub / Codespaces 双人开发：** 先读 [上传、重建环境与共同操作教程](docs/GITHUB_CODESPACES_ZH.md)。使用经过整理的 GitHub 上传包；云端入口为 `.venv-cloud/bin/python tools/cloud/workspace.py serve`，支持已保存样例推理和 CPU 训练。原生 Android 运行与对局验收进度见上方指南；下面的 Windows 本地入口继续保留。


> **先体验“我下牌、AI 回应”：双击项目内 `play.cmd`，或在 PowerShell 运行 `.\run_play.ps1`。** 手动打开终端打印的地址，默认 `http://127.0.0.1:8770`；你是下方蓝方，AI 是上方红方。用画面下方的中文手牌拖到战场，默认每次推进1秒后暂停。只需先读 [人机演示入门](phase2/PLAY_GUIDE_ZH.md)。AI 决策函数在 [examples/human_ai_policy.py](examples/human_ai_policy.py)，当前为可解释规则，未经过 RL 训练。

> **当前固定版本原生平台入口（2026-10-18）：** 先读 [快速入口](START_HERE_PLATFORM_ZH.txt) 和 [从零完整操作教程](phase2/PLATFORM_TUTORIAL_ZH.md)。在项目 PowerShell 运行 `.\run_platform.ps1 -Mode Archive`，手动访问 `http://127.0.0.1:8770`；`-Mode Replay` 重建来源对局，`-Mode Agent` 运行真实 AI 闭环，`-Mode Stop` 停止项目运行时。研究 oracle 展示全部**已采集**原生字段，尚不等于完整官方交互等价；[平台缺口审计](outputs/platform_next/audit/platform_gap_audit.json) 保留明确范围。下文的 `start_demo.ps1`、18765 和 `royale_lab` 内容介绍保留的第一阶段近似模拟器基线。

下面保留第一阶段近似模拟器 `royale_lab` 的操作与训练结果；原12卡 MVP `royale_ai` 及快照 `baseline/mvp_v0_1` 也保留。它与上方固定版本原生人机演示有各自的引擎、策略和验收，不能把第一阶段的 PPO 训练结果当成原生演示 AI 已训练。社区引擎仍有近似，不能称官方全交互私服。

## 第一阶段近似模拟器（保留基线）

```powershell
Set-Location -LiteralPath 'D:\研究生\Clash Royal'
.\start_demo.ps1
```

访问 http://127.0.0.1:18765，选卡组/控制，创建对局，选牌点棋盘或看AI对战。支持暂停、单步、快进、保存、恢复、分叉、回放导出。Ctrl+C停止。依赖修复运行 `.\bootstrap.ps1`。

## 实际结果

- 主C++引擎173注册（132非进化+41进化），源码/编译/部署/机制测试与覆盖矩阵存在；混合日期/等级和已知误差明确保留。
- 原生740测试：739通过、1已知预期路径失败。Java参考974通过。平台21接口/隐私/确定性/模型测试通过，原MVP26回归通过。
- 数值外置173行中171可调、25类适用字段；真实JSON改费用/HP/伤害/法术波次/建筑寿命已测试。特殊效果、子体、塔与等级等尚未全部外置。
- 7策略各100局，共700局：搜索63胜、规则50胜、模仿48胜、PPO采样37胜、随机34胜；全部0崩溃/0拒绝。PPO argmax的0胜保留作诊断。这不是官方游戏胜率。
- BC1344样本、整局train/val；PPO4096步27局，checkpoint/曲线/加载完整对局已验证。
- 32篇PDF、20份固定源码包/明确范围快照、12篇精读；资料最低目标已达，失败/部分下载明示。

## 文档导航

| 目的 | 入口 |
|---|---|
| 原生画面、浏览器下牌、保存图片与原生引擎重演 | [原生视觉操作指南](docs/NATIVE_VISUAL_ZH.md) |
| 从零启动实验、训练、原生引擎与回放，理解学习原理 | [操作与原理逐步教程](docs/NATIVE_LOOP_ZH.md) |
| 当前研究平台能力、调用链与优先开发任务 | [平台审计与开发建议](docs/PLATFORM_AUDIT_20261020_ZH.md) |
| 安装、操作、故障 | docs/START_HERE.md |
| 观测、隐藏变量、时序、最优边界 | docs/MODELING.md |
| 观察到实际执行的调用链/API | docs/ARCHITECTURE.md |
| 引擎版本、误差、改牌/测试 | docs/ENGINE_GUIDE.md |
| JSON平衡配置、生效验证、模型指纹 | docs/BALANCE_CONFIG.md |
| 策略文件/类/函数/指标定位 | docs/STRATEGY_MAP.md |
| 改奖励、搜索、网络、训练、消融 | docs/TUTORIAL.md |
| 论文/项目与学习顺序 | docs/research/INDEX.md、docs/LEARNING_PATH.md |
| 服务器/私服候选 | docs/SERVER_SELECTION.md |
| 真实视觉与未验证项 | docs/PIXEL_PATH.md |
| 实验/路线/验收 | docs/EXPERIMENTS.md、docs/ROADMAP.md、DELIVERY_REPORT.md |

## 统一命令

```powershell
.\run.ps1 -Action tests
.\run.ps1 -Action train-bc
.\run.ps1 -Action train-ppo
.\run.ps1 -Action evaluate
.\run.ps1 -Action research
.\.venv\Scripts\python.exe -m royale_lab demo --policies search greedy
.\.venv\Scripts\python.exe -m royale_lab evaluate --policies ppo_sample --games 100 --output outputs/evaluation/ppo-sampling
.\.venv\Scripts\python.exe tools\platform_verify.py
```

requirements.lock锁定Windows/Python3.14环境，干净.venv-repro证据在outputs/audit_candidates/repro。环境、缓存、源码、论文、模型、临时文件与结果都在项目内。

官方客户端协议、全部连续落点/时序组合、真实设备像素闭环尚未验收。版本/范围以SPEC/STATUS/DELIVERY_REPORT、coverage与日志为准。旧MVP合成视觉不能算真实游戏识别成功。

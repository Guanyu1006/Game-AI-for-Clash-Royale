# Royale Lab：皇室战争本地 AI 研究平台

> **已导入 Codespace，继续配置 Codex 并提交上传：** 读 [最后配置与上传步骤](docs/CODEX_CODESPACES_ZH.md)。

> **GitHub / Codespaces 双人开发：** 先读 [上传、重建环境与共同操作教程](docs/GITHUB_CODESPACES_ZH.md)。使用经过整理的 GitHub 上传包；云端入口为 `python tools/cloud/workspace.py serve`，支持已保存样例推理和 CPU 训练。当前没有把原生 Android 对局迁移到 Codespaces；下面的 Windows 本地入口继续保留。


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

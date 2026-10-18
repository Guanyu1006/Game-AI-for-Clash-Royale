# 皇室战争 AI 研究资料导航

这份资料库服务于可见观测、真实动作闭环和可验证模拟器。访问日期为 2026-10-13；直接研究与可迁移方法分开。当前登记 **33 篇原始论文**，**32 个 PDF 下载且 32 个已解析正文**；登记 **21 个项目**，**20 个固定源码 archive/子集**（完整源码 3、代码/许可子集或原始blob快照 17）。具体以 [manifest](<D:/研究生/Clash Royal/references/library/manifest.json>) 和 [校验日志](<D:/研究生/Clash Royal/outputs/research/verification.json>) 为准。资料不包含论文算法的本机棋力复现声明。

## 文件入口

- [大厂游戏 AI 论文的评价方法与皇室战争迁移设计](BIG_TECH_GAME_AI_EVALUATION_ZH.md)：OpenAI Five、VPT、Dreamer、SIMA、MineDojo、Voyager及微软、腾讯、索尼相关评价设计。
- [皇室战争 AI 系统评测调研与 Benchmark 设计草案](BENCHMARK_DESIGN_ZH.md)：2026-10-18新增，直接相关论文、评价方法迁移、实验矩阵、统计与论文证据设计。
- [33 篇逐项中文分析](PAPERS.md)：问题、输入输出、方法、作者实验、边界和本项目用途。
- [开源项目目录](PROJECTS.md)：commit、许可证、模块、依赖、修改成本与下载/安装状态。
- [核心论文正文精读](DEEP_READS.md)：方法细节、公式、页码、能做的实验和失败风险。
- [方法比较和优化定位](METHOD_COMPARISON.md)：按问题选择改进，不把新算法名字当作棋力。
- [研究缺口及实证路线](GAPS.md)：能由近似引擎支持的结论、必须真实对照的结论。
- [数据与权重获取计划](DATASETS.md)：已保存的小样本与受限外部资源。
- [BibTeX](<D:/研究生/Clash Royal/references/library/bibliography.bib>)；[下载日志](<D:/研究生/Clash Royal/outputs/research/download_log.jsonl>)。
- [资料库验收](<D:/研究生/Clash Royal/outputs/research/corpus_acceptance.json>)：最低数量与已交付文件完整性；失败/部分数据资源仍明确列出。
- [从零学习本平台](<D:/研究生/Clash Royal/docs/LEARNING_PATH.md>)：12 章，每章连接真实函数与本地论文。

## 从零开始的阅读顺序

1. 先运行平台、观察手牌/圣水/塔血和一次实际下牌；读项目建模文档，区分 state、observation、action、reward。在源码中跟一遍 `session → policy → backend`。
2. 阅读 SEAT 与 KataCR：一个主要是嵌入式近似模拟实验，另一个采用真实像素/设备；先弄清不同证据支持什么结论。
3. 阅读 action_masking、参数化动作两篇：能付得起的卡、能下的格子、WAIT 共同定义策略分布；边界错误优先修复。
4. 阅读 POMCP、DRQN：手牌/圣水不确定性是信念或记忆问题。用同一可见观测比较显式信念与记忆网络。
5. 阅读 DAgger、Decision Transformer、IQL/CQL：先把按整局划分的轨迹数据、等待标签、专家查询和评估弄正确，再换网络。
6. 阅读 PPO 与 GAE，启动短程训练并加载 checkpoint 完整打完一局；学习曲线只验证优化过程，独立对局才评价策略。
7. 阅读 PSRO/NFSP 与 AlphaZero：训练对手和验证对手分开，保存历史策略池，查看克制循环与跨卡组表现。
8. 需要真实设备时再读 YOLO/RT-DETR、DBNet、ByteTrack、domain randomization；先标注真实测试帧及误差，再接策略。
9. 高吞吐环境和足量轨迹齐备后，再研究 MuZero、Dreamer、Trajectory Transformer。先测多步模型误差和端到端 p95 延迟。

必备概念：概率分布与置信区间、POMDP、Bellman 目标、策略梯度、on/off-policy、数据分布偏移、动作屏蔽、模仿误差累积、自博弈、信念更新、MCTS/rollout、版本化数据、样本泄漏。

## 分类统计

| 类别 | 原始论文数 |
| --- | --- |
| 世界模型 | 2 |
| 仿真校准 | 1 |
| 仿真迁移 | 1 |
| 动作建模 | 3 |
| 在线RL | 2 |
| 实时博弈/规模 | 1 |
| 对手建模 | 2 |
| 序列决策 | 1 |
| 序列决策/搜索 | 1 |
| 搜索/世界模型 | 1 |
| 搜索/自博弈 | 1 |
| 模仿学习 | 2 |
| 直接研究 | 2 |
| 离线RL | 3 |
| 自博弈/策略池 | 2 |
| 视觉OCR | 1 |
| 视觉检测 | 2 |
| 视觉追踪 | 2 |
| 部分可观察/搜索 | 1 |
| 部分可观察/记忆 | 2 |

## 复核与恢复下载

在项目根目录运行（使用项目自带 Python 环境）：

```powershell
.\.venv\Scripts\python.exe -B tools\research_fetch.py --verify
.\.venv\Scripts\python.exe -B tools\research_verify.py
.\.venv\Scripts\python.exe -B tools\research_fetch.py --papers
.\.venv\Scripts\python.exe -B tools\research_fetch.py --projects
.\.venv\Scripts\python.exe -B tools\research_fetch.py --projects --source-only --workers 3
.\.venv\Scripts\python.exe -B tools\research_assets.py
.\.venv\Scripts\python.exe -B tools\research_report.py
```

下载器缓存已完成资源、固定 commit，重跑不会追踪最新版；`--only ppo,r2d2` 可定向重试。本机 raw GitHub/metadata 通过配置代理，codeload 请求级无代理，不修改系统代理。大 archive 超时可加 `--source-only` 保存固定 commit 的重要源文件、依赖和许可；不会将子集标为完整。若 Python 环境没有 `pypdf`，PDF 保存/头部/SHA 校验照常完成，但正文解析状态明确为未执行；安装 `pypdf` 后可重跑解析。所有临时目录位于项目 `tmp/research`，缓存位于 `.cache/research`。

`research_fetch.py --verify` 是严格全项检查，会因未获得的 optional PDF 或未完整下载数据集返回非零；`research_verify.py` 检查实际交付文件完整性和 ≥30 PDF/≥15 固定项目源码/≥10 精读的最低目标，保留已知缺项。完整数据集和游戏专用模型下载与验证另见 DATASETS.md。官方机制一致性必须由真实录像/轨迹对照证明；论文/仓库的宣传和本项目自写测试都不能替代这项证据。

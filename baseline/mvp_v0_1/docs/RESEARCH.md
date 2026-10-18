# 文献与开源调研

检索日期：2026-10-13。区分论文作者报告、仓库作者声明和本项目实际验证。下载的四篇论文、固定版本源码参考、许可证及 SHA256 在 `references/`。本项目没有执行下载的第三方代码。

## 直接相关研究

| 来源 | 核心方法 | 对本项目的启发 | 实验边界 |
| --- | --- | --- | --- |
| Liu 等，IJCAI 2019，SEAT | 将卡牌选择与使用位置拆成 selection/attention，使用深度强化学习 | 动作结构分解；同一卡的合法落点不同 | 论文实验使用 Lua 模拟环境，不能把结果当作真实屏幕闭环效果 |
| Wu 等，ICIRA 2024 / arXiv 2025，KataCR | 游戏画面目标检测与 OCR，融合特征，离线序列决策；引入动作 delay | 感知/决策分层、专家视频数据、等待与动作不平衡处理 | 固定卡组与内置 AI；论文表 3 的最高报告胜率为 10%，不是稳定人类级表现 |
| Chen 等，2021，Decision Transformer | 用状态、动作和目标回报序列训练 Transformer | 后续可利用长时出牌历史和目标回报 | 不是拿随机初始网络即可下出高质量动作，需要有质量的数据 |
| Schulman 等，2017，PPO | 策略梯度和约束策略更新的替代目标 | 高保真环境成熟后作为在线训练基线 | 需要大量环境交互，当前简化环境会产生迁移偏差 |

原文链接：[SEAT / IJCAI 2019](https://www.ijcai.org/proceedings/2019/0631.pdf)、[KataCR 论文](https://arxiv.org/abs/2504.04783)、[Decision Transformer](https://arxiv.org/abs/2106.01345)、[PPO](https://arxiv.org/abs/1707.06347)。

KataCR 论文记录的动作帧仅约 4%，说明直接逐帧分类容易学到大量等待。它还明确讨论了动作延迟、视觉融合时间与固定卡组限制。本项目因此在模仿训练报告中单独列出等待比例和出牌帧准确率，不以总准确率代替棋力。[论文正文，5.3 与结论](https://arxiv.org/html/2504.04783v1)

## 开源实现评估

| 项目 | 检查结果 | 当前采用方式 |
| --- | --- | --- |
| [wty-yy/KataCR](https://github.com/wty-yy/KataCR) | MIT；完整屏幕感知与离线策略；README 的验证流程依赖 Linux、V4L2、GPU/JAX | 保存固定 commit 的卡牌费用表与 MIT；用 AST 只读核对 12 卡费用；参考模块边界，不直接安装整套系统 |
| [Pbatch/ClashRoyaleBuildABot](https://github.com/Pbatch/ClashRoyaleBuildABot) | MIT；面向 ADB/模拟器的状态生成和视觉基础 | 保存固定 commit 的许可证，列为后续真实感知集成候选；没有声称已验证其权重 |
| [itzik123/ClashRoyaleAi](https://github.com/itzik123/ClashRoyaleAi) | MIT；仓库提供 C++ 战斗模拟、Python 绑定、循环 PPO 与前瞻搜索 | 保存许可证，作为高保真环境替换候选；作者的速度、卡牌覆盖和测试数量未在此机复测 |

以上 commit 在 `references/manifest.json` 中固定，避免只记录易变化的 main/master。保存的源码片段仅供研究和费用审计；本项目核心模拟、策略、感知和训练代码独立编写，不包含未经核验的第三方机器人执行脚本。

没有直接选择“一键安装某个机器人”的原因是技术适配：这台电脑的 Python 3.14 和 Windows 环境并不等同于论文的 Linux/GPU 环境；仓库里的模型类别、阵营编码和卡牌覆盖也不同。先建立无重依赖的基线，才能验证后续替换模块有没有改善。

## 数据资源

- [Clash-Royale-Detection-Dataset](https://github.com/wty-yy/Clash-Royale-Detection-Dataset)：生成检测数据及真实画面验证数据的参考入口。
- [Clash-Royale-Replay-Dataset](https://github.com/wty-yy/Clash-Royale-Replay-Dataset)：离线决策训练的专家回放参考入口。
- 本地 `outputs/demo.jsonl` 等日志：本项目搜索策略产生的合成专家数据，可用来验证训练代码，不能代替真实人类专家。

本次没有下载大规模数据集或 GPU 权重。后续使用前需要检查当前许可证、素材权利、标签格式、版本和卡组；不要把公开 GitHub 地址视为所有素材均可无限制再分发。

## 实现文档

- [Android 官方 ADB 文档](https://developer.android.com/tools/adb)：设备选择及 `exec-out screencap -p` 获取原始 PNG；本项目用参数列表调用，避免 Windows shell 处理二进制截图。
- [Ultralytics 官方 predict 文档](https://docs.ultralytics.com/modes/predict)：本项目的可选 YOLO 后端读取 boxes、类别、分数和坐标；必须提供游戏专用且类别兼容的本地权重。
- [Supercell 服务条款](https://supercell.com/en/terms-of-service/)：列明未经授权的 bots/自动化限制；本地模拟与离线研究可独立推进。

## 当前结论与研究假设

文献和项目支持“视觉获取局势 → 结构化状态 → 策略 → 动作”这一工程路线，但不能证明现成模型能在当前环境与未知卡组中实现最优下牌。

本项目暂用三条可检验假设：

1. 在固定卡组和相同近似环境中，短时前瞻应优于随机策略，并可与透明规则策略比较。
2. 对手出牌历史可改善候选生成，但漏检时必须保留不确定性，避免精确假象。
3. 搜索日志可以训练较快的候选排名模型，但必须按整局划分数据并单独测出牌帧。

这些是本项目的推断与待验证假设，不是引用论文已经证明的结果。

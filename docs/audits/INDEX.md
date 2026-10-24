# 候选引擎审计导航与数据分母

本轮原生系统与闭环的机器可读状态见 [native_loop_20261020.json](native_loop_20261020.json)；状态按实际闭环、轨迹和浏览器回执记录；历史失败保留。

当前原生资源实际配置、小规模保存数据训练及原生闭环运行门槛，见 [原生闭环操作指南](../NATIVE_LOOP_ZH.md)。原生局数与离线／接口结果分别报告。

2026 年 10 月 20 日后续实机进展：[安装回执](../../outputs/native_loop_20261020/android-install-001/report.json)确认候选包与内容安装检查；[恢复后的游戏日志](../../outputs/native_loop_20261020/android-display-002/report.json)记录 ARM64 游戏库与探针加载；[12:53 UTC 状态](../../outputs/native_loop_20261020/android-window-after-confirm-001/report.json)确认内容就绪。随后[实际身份核对](../../outputs/native_loop_20261020/identity-check-003/report.json)通过；[首次原生局中断现场](../../outputs/native_loop_20261020/native-smoke-001/interruption.json)保留 254 条转移、15 次消费。该中断局不计作自然终局。随后[新批次](../../outputs/native_loop_20261020/native-run-002/report.json)完成 4 场采集、2 轮训练及 2 场新模型评估，6/6 自然终局；[独立验收](../../outputs/native_loop_20261020/native-run-002-acceptance/report.json)通过。

两次整个窗口中断的事实、原因边界与资源控制见[窗口中断核查与恢复说明](../WINDOW_RESTART_20261020_ZH.md)及[核查回执](../../outputs/native_loop_20261020/window-restart-audit-001/report.json)。已有证据确认远程环境重启，尚不能认定根因或宣称崩溃已修复。

2026 年 10 月 20 日远程修复后的使用方法与实际验证见 [远程开发操作指南](../REMOTE_DEVELOPMENT_ZH.md) 和 [结果摘要](remote_development_20261020.json)。

当前工作台、不同引擎与学习路线的连接图，以及与 FirstLight_CR 的源码对照和最小研究闭环验收门槛，见 [系统结构与闭环差距](../SYSTEM_STRUCTURE_AND_LOOP_ZH.md)。

当前 Codespaces 与固定版本研究平台的源码调用链、实际运行结果和任务优先级见 [2026 年 10 月 20 日平台审计](../PLATFORM_AUDIT_20261020_ZH.md)。该审计独立区分当前可运行能力与下列历史结果。

本次主后端的审计由 `ClashRoyaleAi.md` 记录；独立候选审计如下。结论基于固定源代码和安装/运行结果，不沿用 README 的完整性宣传。

| 候选 | 类型 | 本机结果 | 用途 |
|---|---|---|---|
| ClashRoyaleAi | C++ headless battle engine | 见主引擎独立报告 | 主平台后端 |
| [crforge](CRFORGE.md) | Java headless engine + ZMQ/Gymnasium | 编译成功，974 个上游测试通过，3 局真实 bridge episode 完成 | 第二引擎、数据和差分参考 |
| [MSU-AI gym](MSU_GYM.md) | 未完成的 Gym 接口骨架 | Python 3.11 包安装成功；环境构建/重置失败 | 接口反例和结构参考 |
| [KataCR](KataCR.md) | 原游戏视觉/ADB + 离线策略 | 110 个源码解析、费用 utility 成功；Windows CUDA 固定依赖失败 | 感知路径与论文实现资料 |
| [SlashRoyaleLegacy](PRIVATE_SERVERS.md) | 老版本协议/账号/数据库/命令中继 | 固定源码阅读；.NET build 因无 SDK 失败 | 不能作为计算战斗的 backend |

主平台另有 [干净 Python 环境复现验收](REPRO.md)：精确安装、模型加载与真实出牌 demo、20 个平台测试全部通过。另见 [PPO 解码与续训诊断](PPO_BEHAVIOR.md)，其中独立 40 局比较确认单项 argmax 的 WAIT 模式与训练采样不一致。

新增 [数值配置接口独立复查](NUMERIC_CONFIG_REVIEW.md)：实际发现滚动/buff/clone 分支中接受但无效的参数，保留原版 80 tick 轨迹证据；修复后 8 个字段拒绝和 4 个合法控制独立重测通过，复合子体/死亡效果的不联动亦实测并对应排除清单。

数据入口：`data/audit/requested_mechanics.{json,csv}` 明确列出 55 个用户要求的机制族。它只是需求分母，没有把任意机制标成已实现。各后端的真实覆盖和测试应从其 coverage 矩阵连接，不能把这个列表当验收通过表。

`data/audit/crforge_card_catalog.csv` / `crforge_cards_snapshot.json` 保存 240 个社区配置条目，包含进化、英雄、活动卡及变体；130 个单位、63 个弹道不是标准卡牌分母。每行状态为行为未验证。官方参考平衡公告日期为 2026-10-06，原始 HTML 保存为 `official_october_2026_balance.html`；目前研究引擎的数据快照没有证据证明完整匹配这一官方版本。

`catalog_manifest.json` 保存来源、固定 commit、大小、SHA256 和失败。RoyaleAPI 的固定数据提交为 d5461b0a59bff33c4da2fc845b07275b66b2d6ff。本次成功下载 full card stats JSON 和 spell stats JSON，原始 bytes 的 cards.json / cards_evo.json / i18n 请求失败，原文新内容支持页返回 403。后来通过原始源码的分页文本视图取得 cards.json 全部 1562 行，解析并仅保留 120 行事实字段于 `royaleapi_public_card_metadata_web.json`；该文件明确不是原始 bytes 下载，也没有存储描述文案，不能称为当前官方标准全卡完整目录。未成功的条目不计为下载成功。RoyaleAPI 仓库没有在本次快照中取得授权明确的 LICENSE；公开可读不等于任意再分发授权。

跨引擎实际结果：`outputs/audit_candidates/backend_differential.json`，其中五个法术塔伤一致、毒药 168 vs 160。初次发现的基础圣水恢复率与三倍阶段差异已经用于 calibrated-v2 修复，并保留修改前基线；仍记录边界时刻、场地/塔初值差异。五个整数一致不代表所有交互官方一致。

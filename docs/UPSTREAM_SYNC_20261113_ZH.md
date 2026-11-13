# 2026-11-13：协作分支同步核查与补充上游分析

用户本轮明确以**本项目远端／朋友的重构分支**为同步重点：若重构已推送，合入、检查并以后续同步后的版本为开发基准。截至本次查询，当前已配置远端没有新增提交；尚未定位到另一份重构分支或仓库，不能把外部引擎的重构当成朋友的改动。

## 本项目协作状态

核查对象：[Guanyu1006/Game-AI-for-Clash-Royale](https://github.com/Guanyu1006/Game-AI-for-Clash-Royale)。本次查询前本地工作区干净，`main` 与 `origin/main` 都为上次上传的 `ceec6b9b54058e1e097373ee857ee7f663a6efba`，GitHub `pushed_at` 为 2026-11-07 10:21:09 UTC。

| 检查项 | 结果 |
| --- | --- |
| 拉取后的本地 main / origin/main | ahead 0、behind 0 |
| 远端开发分支 | `dev/main-start-20261020`：`9f1768a`，没有 main 尚未包含的提交 |
| 本地开发分支 | `7392e3c`，其提交也已包含在 main；不需要再次合并 |
| 待合并 PR / 公开 pull head refs | 均未找到 |
| GitHub 公开 fork / 当前安装内相关仓库搜索 | fork 计数 0；相关名称只找到本仓库 |

因此，本轮**没有可从已知协作远端补合并的代码**。本次 Git 变更只保存核查说明、来源审计与 AI 技术分析。朋友尚未推送的本地内容、另一个未关联的独立仓库，不在查询可见范围内；重构源地址或分支尚待确定。

后续同步已获用户授权。定位重构来源后，应先检查提交关系和改动范围，保存当前未提交工作，合并并处理冲突，运行与重构相关的检查；验收后更新共同基线和操作说明。保留历史资产与结果，后续开发按同步后的结构进行。本次没有先行改造旧架构，也没有假设重构已完成。

证据：[协作同步审计](audits/collaboration_sync_20261113.json)。主要查询命令：

```bash
git fetch origin
git branch -avv
git rev-list --left-right --count main...origin/main
git log --oneline origin/main..origin/dev/main-start-20261020
git log --oneline main..dev/main-start-20261020
git ls-remote origin 'refs/heads/*' 'refs/pull/*/head'
```

## 补充核对的外部项目

在用户明确协作范围前，已并行检查本项目引用的主要上游。以下都是**外部项目**，不是朋友的重构分支，也不是当前研究工作台自动跟随的依赖。

| 来源 | 11 月 7 日上传后的更新 | 本次处理 |
| --- | --- | --- |
| [ClashRoyaleAi](https://github.com/itzik123/ClashRoyaleAi/commit/53a6ef7d029c29f4451bff148b0aba4991b7c7a1) | 6 次提交；新 HEAD `53a6ef7`；相对历史 pin 共 8 次 | 保存独立源码审阅快照，584 文件，MIT 许可保留 |
| [crforge](https://github.com/voonhous/crforge/commit/69277c1002499da7c23932c303c41ac00bd82eaa) | 15 次提交；新 HEAD `69277c1`；相对历史 pin 共 810 次 | 保存独立源码审阅快照，1037 文件，Apache-2.0 的 LICENSE / NOTICE 保留 |
| FirstLight 原 GitLab | HEAD 仍为本机固定的 `71944e3` | 无需更新该来源；GitHub 镜像为另一条历史，不能直接 merge |
| [KataCR](https://github.com/wty-yy/KataCR/commit/36ceb9fcfbd117c2ce3d97eacee435c1898eb7b8) | HEAD 仍为本机固定的 `36ceb9f` | 无新代码可同步 |
| [Jason-XII/daybreak-cr](https://github.com/Jason-XII/daybreak-cr/commit/693057d14dd6f82f193050659a2bf20479f6f5e2) | 原 clash-royale-battle-engine 改名；上传后 1 次提交，HEAD `693057d` | 只保存版本与差异元数据；未取得明确源码许可，不导入源码 |

ClashRoyaleAi 的新工作主要连接“视觉识别 → 当前局势镜像 → UtilityTeacher 提案／短程试走 → 执行”，同时修复塔血读数、重复手牌、过期帧、异步执行和计划时钟追赶。相对历史固定版本，C++ 核心仅增加只读 getter；跨标准库随机种子一致性仍是提案，不能写成已修复。视觉镜像中敌方手牌、圣水和缺失 HP 含假设，不能直接当作本项目公平观测的真实标签。[提交对照](https://github.com/itzik123/ClashRoyaleAi/compare/2f172925a15b17f973291f41a789b19a29f0fc43...53a6ef7d029c29f4451bff148b0aba4991b7c7a1)

crforge 相对旧 pin 已有大规模架构变化：Java 21、共享 buffer 环境、schema 4、玩家 SeatView 和可 fork 状态，替代旧 gym-bridge／ZMQ 接口。最近修复包括动作生成 champion 的能力寻址、Mirror 进化排除规则、边界外施放取消与不支持动作提前拒绝；DeepCopy 复用不可变对象用于加速分叉。约 3 倍加速是上游声明，本机未运行测速。当前参考客户端系列与本项目固定的 15.535 原生版本不同，不能直接替换。[提交对照](https://github.com/voonhous/crforge/compare/b41cf9b6276945152ec4c869ce56f98a5fa3f513...69277c1002499da7c23932c303c41ac00bd82eaa)

daybreak-cr 最近修复 live 诊断中的 ID 别名、card 映射和计数污染；其早先已删除 vendor FirstLight 和预编译 probe，新架构还依赖另行提供的引擎与读取端。把新 HEAD 覆盖到本项目的锁定原生资源，会破坏现有版本身份，不能作为例行同步处理。详细版本、许可、路径和兼容边界见 [上游审计](audits/upstream_sync_20261113.json)。

## 保存与验证范围

两套候选源码均保存于 `outputs/cloud/sync-review-20261113/upstreams/<仓库>-<完整 commit>/`。它们是用于阅读的源码子集，排除模型、数据、游戏资产、二进制及缓存；不是可直接安装运行的完整发行包。共 1621 个文件已按上游 Git blob SHA1、大小与 SHA256 核验，ZIP CRC 检查通过。

源码、ZIP 和逐文件 manifest 只留在本工作区被忽略的 `outputs/` 中；本次提交记录其哈希、固定树 URL 和筛选规则，不上传这些快照。新克隆的仓库有核查说明，没有自动获得全部本机资料。当前工作区复核／重建快照可使用：

```bash
.venv-cloud/bin/python outputs/cloud/sync-review-20261113/upstreams/fetch_source_snapshots.py
```

该辅助脚本本身也位于本机 `outputs/`，不是新克隆可直接调用的项目入口。固定来源与重新获取规则写在审计 JSON 中。

本次 `.venv-cloud/bin/python tools/cloud/workspace.py doctor` 返回 0，572 个可移植资产完整性检查通过，Windows 入口保留。此检查没有启动原生对局，不能扩大原生兼容性或棋力结论。应用代码、依赖、运行 pin、历史补丁与 baseline 未改变；检查重点为 JSON、文档链接、快照哈希和 Git 差异。

AI 20 胜、榜首事件及技术实现分析另见 [专题说明](research/CLASH_ROYALE_AI_20261113_ZH.md)。本项目尚无官方 20 胜或全球榜首验收。

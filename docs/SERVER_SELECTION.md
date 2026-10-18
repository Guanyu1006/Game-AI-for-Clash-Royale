# 为什么选择研究模拟后端，以及如何替换

当前主平台选择 `ClashRoyaleAi` 的 C++ 原生战斗引擎，经本项目校准及 Python 适配后运行；Java `crforge` 保留为独立参考后端。二者都不是官方服务端，也没有证据能完全复刻 2026 年 10 月版本的所有交互。详细实测见 [候选审计导航](audits/INDEX.md) 和主引擎审计报告。

## 先区分四种系统

| 系统类型 | 谁计算战斗 | 能否离线加速训练 | 本项目中的例子 |
|---|---|---|---|
| 大厅/账号/匹配私服 | 可能没有战斗计算 | 不能由大厅功能推断 | HashRoyale 系列包装器 |
| 战斗命令中继 | 客户端根据命令自行运行 | 没有客户端逻辑就不能 | SlashRoyaleLegacy 的 .NET Battles 服务 |
| 独立 battle simulator | 开源代码推进实体、伤害、规则 | 可以，误差需独立校准 | ClashRoyaleAi、crforge |
| 截图/ADB agent | 原游戏在设备上计算 | 通常受实时速度限制 | KataCR、ClashRoyaleBuildABot |

“私服”能让改版客户端登录，并不意味着服务器自身持有可调用的战斗状态。SlashRoyaleLegacy 的 `LogicBattle.Tick` 只转发队列和 heartbeat，其结束处理不能从协议判断获胜者；`DoSpellCommand.Process` 编码并转发坐标/卡牌。源码和 .NET 构建失败证据见 [私服审计](audits/PRIVATE_SERVERS.md)。因此它不能直接提供动作后的胜负、合法性、伤害、快照或 rollout。

## 候选决策

| 候选 | 源码与本机证据 | 决策 |
|---|---|---|
| ClashRoyaleAi / C++ | 真实 tick、原生 Python API、搜索所需状态复制；本机编译和机制测试由主引擎报告记录 | 主平台使用；继续保留机制误差/版本覆盖表 |
| [crforge / Java](audits/CRFORGE.md) | 974 个上游测试通过，实际 Java ZMQ 服务和 Python 客户端完成 3 局；240 个配置行不代表 240 个标准卡行为全部验证 | 用作第二套实现和差分参考 |
| [MSU-AI gym](audits/MSU_GYM.md) | 源码包安装成功，实际空 Dict + Discrete(1)，reset/step 为 pass | 不采用为引擎 |
| [KataCR](audits/KataCR.md) | utility/语法验证和 Windows 固定依赖探测；实时路径依赖真实手机流和权重 | 用作感知、动作执行、离线策略的方法参考 |
| [SlashRoyaleLegacy](audits/PRIVATE_SERVERS.md) | 固定源码命令中继，不运行单位仿真；本机 .NET build 因无 SDK 失败 | 保留协议研究资料，不启动整套私服 |
| 官方 native logic 包装器 | 搜索发现 libg.so / stock renderer 方案，配套二进制及兼容客户端未取得 | 未复现，不计为已有引擎 |

选择 C++ 的理由是已经具备可本机编译的 Python 绑定、可复制的局势、灵活落点和真实 headless tick，便于主平台实现搜索与批量训练。不是因为 README 使用了“accurate”或“complete”这样的词。crforge 源码更强调 data-driven loader 和模块化 Java systems，但没有完整公开 snapshot/restore，且默认观测泄漏双方手牌/圣水，动作失败反馈还有实际发现的时序错误。

crforge 的 FidelityLedger 对 138 个 core 主模块类报告：已追踪一致 0、部分追踪 1、推断 137。唯一部分追踪为队形 offset/整数取整与独立实现的案例一致，其他侧别和最终碰撞位置仍未验证。这是来源可信度标签，不是卡牌覆盖率；974 个自测通过也不能使“推断”变成“官方一致”。实际结果在 `outputs/audit_candidates/crforge_build/fidelity.log`。

## Java 参考工具都在项目内

- 固定源码：`third_party/audit_candidates/crforge_git_sparse`，commit b41cf9b6276945152ec4c869ce56f98a5fa3f513，Apache-2.0 / NOTICE。
- 本机读取已存在 JDK 21：`C:\Users\63047\AppData\Local\Programs\Microsoft\jdk-21.0.7.6-hotspot`。没有安装系统 Java；使用它编译 `--release 17`。路径是本机条件，其他机器应通过 JAVA_HOME 指定已有 JDK 17 或更新版本。
- jar 缓存：`.cache/audit/maven`；编译与测试：`outputs/audit_candidates/crforge_build`；Python 适配隔离环境：`.venv-audit`（3.11）。
- 构建：`tools/audit_build_crforge.py`；真实三局：`tools/audit_crforge_smoke.py`；机制测试：`tools/audit_crforge_mechanics.py`；跨引擎结果：`tools/audit_compare_backends.py`。
- Windows FidelityLedger 路径修复只在编译副本中进行，没有改动战斗规则或固定源快照。

## 替换 BattleBackend 的步骤

先查看主平台 `royale_lab/backend.py` 与文档中的接口，不把策略直接绑定到第三方完整状态。Java 适配器可以调用 `CRForgeEnv`，但推荐使用 `BridgeClient(binary_obs=False)` 获得可检查的 JSON，再转换为统一观察和动作结果。

1. 创建/重置：把研究卡组 ID 映射到 `CardRegistry` ID，固定等级、随机种子和 tick 步长。两套场地高度不同，落点应通过归一化坐标和各自合法区域变换，不能简单复用绝对坐标。
2. 观测：自方手牌/圣水保留，敌方只保留屏幕可见实体/塔和已确认出牌历史；隐藏敌方手牌、下一张与精确圣水。独立引擎的完整 observation 只作为 oracle 测试。
3. 动作执行：明确 WAIT、卡槽、落点和技能。修复 Java queued deployment 在执行前被误标失败的问题；应记录接受 ID、拒绝原因和费用/轮转变化，不能依赖其当前 `action_failed`。
4. 快照：crforge 尚无完整 restore API。可先用 seed + 已接受动作 journal 重新播放恢复，并验证状态哈希和结果；搜索的大量分叉需要实现原生 copy/serialize，才能接近 C++ 的成本。不可仅保存 observation 并声称完整状态已恢复。
5. 测试：复用后端契约测试、重置确定性、合法动作、终局、断线/超时、隐私屏蔽和动作去重；再建立能对齐条件的差分夹具。
6. 校准：已取得五个法术塔伤对照、圣水阶段和手牌轮转证据；毒药仍存在差异。进一步验证位置边界、路径、碰撞、状态顺序、英雄/进化和塔等级。录像/官方版本证据须单独记录。

替换后重新训练和冻结评估，不复用旧模拟数据的胜率结论。已有 checkpoint 的版本标识应与后端规则和卡牌数据一致。

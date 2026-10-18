# 社区私服为何不能直接替代战斗模拟器

审计日期：2026-10-13。明确区分账号/大厅/匹配、战斗命令转发、实际单位模拟和官方客户端二进制封装。

本次重点阅读 [SlashRoyaleLegacy](https://github.com/avoidr/SlashRoyaleLegacy/tree/a47d02474efc755427c8416b15e96daa9331d993)，固定 a47d02474efc755427c8416b15e96daa9331d993。它包装 HashRoyale/ZrdRoyale 系列的 1.9.2 老客户端协议，提供 .NET 8 服务、MariaDB、网页管理面板及 APK 构建工具。内部 `server/SlashRoyale/LICENSE` 是 GPL 第 3 版；外围包装器未在本审计中确认独立许可证，不能将整仓库随意标为 MIT。

源码级结论：它的 battle server 是命令中继，不能独立预测单位交互和胜负。

| 源码 | 实际做什么 | 对 AI 模拟的含义 |
|---|---|---|
| `ClashRoyale.Battles/Logic/Battle/LogicBattle.cs` | 每 500 ms Tick 发送 SectorHeartbeat 和本客户端的 queued bytes；按客户端命令活跃度判断会话结束 | 没有实体、索敌、碰撞和伤害推进 |
| `ClashRoyale.Battles/Protocol/Commands/DoSpellCommand.cs::Process` | 将卡牌 ID、等级、X/Y 编码到双方队列 | 转发下牌命令，没有根据状态合法性/费用/命中结果进行独立仿真 |
| `LogicBattle.Tick` 结束分支 | 源码注释明确结果不能由协议确定，主服务把各玩家当作获胜者处理 | 无法作为可信胜负 reward 计算器 |
| README 的 CSV 编辑说明 | 说明卡牌 HP/伤害/数量由客户端 APK 中 CSV 计算，服务器只 relay commands | 服务端 CSV 数值并不等于可用于 headless 训练的执行引擎 |

具体源码可见 [LogicBattle](https://github.com/avoidr/SlashRoyaleLegacy/blob/a47d02474efc755427c8416b15e96daa9331d993/server/SlashRoyale/src/ClashRoyale.Battles/Logic/Battle/LogicBattle.cs) 与 [DoSpellCommand](https://github.com/avoidr/SlashRoyaleLegacy/blob/a47d02474efc755427c8416b15e96daa9331d993/server/SlashRoyale/src/ClashRoyale.Battles/Protocol/Commands/DoSpellCommand.cs)。原始网页/source view 证据在 `outputs/audit_candidates/SlashRoyaleLegacy_*_source_view.txt`，并下载了实际 LogicBattle.cs；过滤 Git 克隆含固定提交树，完整源码选择性下载状态由 snapshot manifest 记录。中断的 ZIP/未下载的资源不能列为成功完整下载。

本机实际尝试 `dotnet build .../ClashRoyale.Battles.csproj`，日志 `outputs/audit_candidates/SlashRoyaleLegacy_dotnet_build.log` 显示只有 .NET 9 runtime，没有 SDK，命令无法加载。没有启动数据库，没有取得配套 1.9.2 客户端，没有编造对战或吞吐结果。由于源码已证明它不具备需要的服务端战斗运算，本平台不为此安装额外大体积 .NET/MariaDB/APK 运行栈。它可供老版本协议、会话、管理界面研究，不能当作完整战斗引擎。

搜索还找到两类候选：

- [Jason-XII/Clash-Royale-Battle-Engine](https://github.com/Jason-XII/Clash-Royale-Battle-Engine) 的 Python Engine 包装原客户端/native renderer，提供 paused stepping / resident headless 等接口。可用的官方二进制、对应版本和运行依赖不在本项目，因此没有进行安装/完整战斗验证，不能作为已取得的第三方开源逻辑。
- [Supercell/Scroll](https://git.xeondev.com/Supercell/Scroll) 说明利用 libg.so 共享的 Logic 类运行实验服务器，同样需要私有客户端二进制与平台适配。本次只做范围检索，未下载或执行二进制。

能连接原客户端的私服名称和“Battle”项目目录都不足以证明有战斗模拟。选择本地 C++ 研究后端并用 Java 独立参考引擎做差分，比依赖只有命令 relay 的私服更符合可加速训练、回放分叉和可解释误差的需求。精确官方交互仍需另行取得合法可验证的录像/轨迹基准。

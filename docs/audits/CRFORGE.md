# crforge：独立参考引擎实际构建、测试及完整对局

审计日期：2026-10-13。固定提交 [b41cf9b6276945152ec4c869ce56f98a5fa3f513](https://github.com/voonhous/crforge/tree/b41cf9b6276945152ec4c869ce56f98a5fa3f513)，Apache-2.0；保留 LICENSE 和 NOTICE。源码位于 `third_party/audit_candidates/crforge_git_sparse`。这是一份固定 Git 提交的稀疏源码检出，包含 core、data、gym-bridge、python、docs、gradle；未下载 desktop 的媒体资产。

结论：是可以真实运行的第二个独立研究模拟器，可用于参考差分。主平台采用 C++ 后端，主要考虑已有原生绑定、复制局势的搜索能力和集成成本。crforge 没有公开的完整 snapshot/restore API，直接使用它替换主后端还需增加状态序列化、私有信息过滤和动作反馈修复。

**实际运行证据**：Java 主源码、测试源码均成功编译。974 个上游测试全部通过；项目内运行 Java ZMQ 服务并通过 Python Gymnasium 客户端完成随机、规则、无动作三种对手的 3 局，分别 298、145、143 个 step，93 次出牌由手牌变化证实执行。单次实测吞吐约 981–2209 step/s，每 step 推进 20 tick；这个短样本不是跨机器性能保证。

| 关键职责 | 真实实现路径（相对源码根） |
|---|---|
| tick | `core/src/main/java/org/crforge/core/engine/GameEngine.java::tick`，常量实际为 20 Hz；若干注释仍写 30 Hz |
| 结算顺序 | pending → economy → collector → deployment → spawner → delayed death spawn → status → attachment → timer → area effect → targeting → ability → combat/projectile → transformation → physics → deaths → time/result |
| 规则与终局 | `core/.../match/Standard1v1Match.java`、`engine/GameEngine.java::checkTimeLimit` |
| 移动、碰撞、索敌 | `core/.../physics/{PhysicsSystem,Pathfinder}.java`、`combat/TargetingSystem.java` |
| 伤害、弹道 | `core/.../combat/{CombatSystem,ProjectileSystem,AoeDamageService,DamageUtil}.java` |
| 状态和特殊行为 | `effect/StatusEffectSystem.java`、`ability/handler/*`；charge、dash、hook、reflect、stealth、hiding、tunnel、variable damage |
| 生成与死亡 | `entity/{SpawnerSystem,DeathHandlingSystem,SpawnFactory}.java` |
| 数据加载 | `data/.../card/CardRegistry.java`；依次载入 buffs → projectiles → units → cards |
| 完整局势/动作 | `gym-bridge/.../GameSession.java::{init,reset,step,stepBinary,observe}` |
| 传输 | `gym-bridge/.../BridgeServer.java`、`BridgeSession.java`，ZMQ PAIR JSON/binary |
| Python API | `python/crforge_gym/{env,bridge}.py`，`CRForgeEnv` 和 `BridgeClient` |

数据快照包含 240 个卡配置行、130 个单位记录、63 个弹道记录。240 行包括普通、进化、英雄、特殊活动/超级卡与变体，不能称为 240 张标准可用卡全部实现。`data/audit/crforge_card_catalog.csv` 将每行明确标为未验证行为目录。NOTICE 声明数值来自社区公开游戏数据的解码；未固定到已校准的官方客户端和平衡版本。

原 Gradle wrapper 使用 9.7.1，并要求 Java 17 toolchain。常规和指定代理下载 wrapper 的两次尝试未在审计预算内完成，日志保存在 `crforge_gradle_upstream.log` 和 `crforge_gradle_proxy.log`。系统已有 JDK 21，于是使用 `tools/audit_build_crforge.py` 直接下载固定 Maven 依赖，调用 `javac --release 17`。所有 jar、源码参数、编译产物及 JUnit XML 均保存在项目内，不安装系统组件。

初次 Windows 执行 971/974 通过：`FidelityLedger.toBinaryName` 只替换 `/`，未替换 Windows `\`，造成 3 个扫描测试失败。审计脚本在 `outputs/.../crforge_build/patches` 生成只修改路径转换的一份编译副本，上游源码保持原样；随后 974/974 通过。原失败日志 `junit_original_windows_failure.log`、修复说明 `windows_compatibility_patch.txt`、最终 `junit.log` 均保留。

```powershell
.venv\Scripts\python.exe tools\audit_build_crforge.py
.venv-audit\Scripts\python.exe tools\audit_crforge_smoke.py
.venv-audit\Scripts\python.exe tools\audit_crforge_mechanics.py
.venv\Scripts\python.exe tools\audit_compare_backends.py
```

`.venv-audit` 的 Python 3.11 安装了该项目 Python package、gymnasium 1.4.0、numpy 2.4.6、pyzmq 27.2.0，安装日志为 `crforge_python_install.log`。独立 audit 服务由 smoke 脚本启动并在 finally 中停止，使用 29876 / 29877 端口。

两个必须保留的限制：

1. `ObservationBuilder.build` 同时输出双方精确圣水、四张手牌与下一张，binary encoder 也使用这些值。上述独立运行属于 oracle 观测；主策略不能直接使用这种结构。
2. `GameSession.step/stepBinary` 在 `engine.tick` 之前判断圣水是否下降，但 `DeploymentSystem.queueAction` 只入队，实际费用和轮转在 tick 中发生。因此 93 次实际执行的牌都错误返回 `action_failed=true`。本报告以手牌轮转确认执行，不把错误反馈当作合法拒绝率。若接入，需在 deployment 层返回接受 ID 和结果，而非在推进前比较圣水。

独立机制结果在 `outputs/audit_candidates/crforge_build/mechanics_results.json`。在各自敌方公主塔中心施放，箭雨 90、火球 206、电击 57、火箭 371、雷电 285 与修复后的 C++ 相同；毒药为 160，C++ 为 168，已记录 8 点差异。C++ 此次减伤修复参考了 crforge 的比例数据，因此这些一致值是校准后的回归证据，而不是两个互不相关数据源对官方数值的独立证明。此处只比较塔伤，范围边界、单位组合和时序并未因此验证。

已验证 crforge 的 1/2.8 圣水基础恢复、上限 10、120 秒双倍、180 秒加时、240 秒三倍（该引擎在跨过边界后一个 tick 更新标志）和五次出牌后的八卡轮转不变量。初始差分发现 C++ 基础恢复率为 0.35/秒，且 180 秒提前进入三倍；本项目的 calibrated-v2 已修正为 1/2.8 和 240 秒三倍，重新测量两套引擎 1 秒与 5 秒的圣水相同。仍有边界更新时刻 0.05 秒差异。旧基线保存为 `backend_differential_before_economy_fix.json`，当前结果为 `backend_differential.json`。两者场地分别 18×32 与 18×34，初始塔 HP 也不同，不能逐坐标直接比较整局轨迹。

其自带 FidelityLedger 在 138 个主模块类上报告 TRACED=0、PARTIAL=1、GUESS=137。唯一 PARTIAL 是队形 offset/取整与独立实现的 180 个案例一致；侧别、分路、碰撞后的终点仍未验证。这个分类不是卡牌覆盖率，也不是上游测试通过率。源码本身承认自测通过无法证明官方一致，见 [fidelity 状态定义](https://github.com/voonhous/crforge/blob/b41cf9b6276945152ec4c869ce56f98a5fa3f513/core/src/main/java/org/crforge/core/fidelity/FidelityStatus.java)。

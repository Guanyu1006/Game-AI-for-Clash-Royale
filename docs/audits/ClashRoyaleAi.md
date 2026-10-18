# ClashRoyaleAi 源码审计与 Windows 实测

审计对象是 [itzik123/ClashRoyaleAi](https://github.com/itzik123/ClashRoyaleAi)，固定提交 `2f172925a15b17f973291f41a789b19a29f0fc43`，上游提交时间 2026-09-29。项目目录 `third_party/ClashRoyaleAi`；保留 MIT LICENSE。本平台补丁版本为 `ClashRoyaleAi@2f172925a15b+lab-calibrated-v2`。以下结论来自源码和本机运行，不采用 README 的准确性、吞吐或测试数量作为验证结果。

## 实际是哪类项目

这是实际执行战斗结算的 header-only C++17 研究引擎，不是仅提供账号、大厅、匹配的社区私服。它没有供官方客户端登录的完整协议服务器；本项目的 HTTP 服务由 `royale_lab` 提供。

`include/core/GameManager.h::step` 每 tick 恢复圣水、减少手牌冷却、提交待生成实体、逐个更新实体、结算圣水生成、再次提交、处理碰撞、判断终局和死亡。`GameManager::playCard` 处理手牌、费用、区域、Mirror/Spirit Empress、进化生成和事件。`CombatEntity.h`、`Troop.h`、`Projectile.h`、`AreaSpell.h` 是具体战斗路径，能够实际产生塔伤、死亡和对局结果。

## 获取与构建证据

- git shallow clone 超时；两个 codeload ZIP 分别在 24,641,536 / 15,728,640 字节中断。它们不是通过 CRC 的完整源码包，不能被引用为成功下载。
- 改用固定提交 GitHub tree API 和 raw blob 获取，847 个源码/资源文件已记录 SHA256；30,896,276 字节的 bundled ONNX 未放入源码目录，后来已单独下载至 `references/library/assets/itzik123__ClashRoyaleAi/perception/clashroyalebuildabot/models/units_M_480x352.onnx`，由资料库清单记录SHA。源码清单：`outputs/native_engine_download.json`。
- Python 3.14.5、已有 Visual Studio 2022 Community MSVC 19.43.34809.0、pybind11 3.0.1、Catch2 3.7.1。pybind11/Catch2/临时文件/编译对象均放在项目内 `.cache` / `.tmp`。
- 上游 CMake 使用 pybind11 2.11.1。系统 CMake 4.0.0-rc2 与本地 wheel CMake 3.31.10 在 configure 阶段返回 `0xc0000409`；原因未确定。直接使用现有 MSVC 编译成功，无系统安装。
- 统一构建命令：`powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_native.ps1 -Tests`。缺失的 pybind11 自动安装到 `.cache/native_pybind11`，缺失的 Catch2 从固定 v3.7.1 获取到 `.cache/native_catch2`。
- 当前模块：`third_party/ClashRoyaleAi/python_ai/clash_royale_env.cp314-win_amd64.pyd`。构建和源码补丁证据：`outputs/native_engine/build_manifest.json`、`third_party/ClashRoyaleAi/LOCAL_PATCH.patch`、`outputs/native_engine_build.log`。

## 运行结果

未修改战斗规则时实际跑了 **734 个 Catch2 TEST_CASE：733 通过，1 个已标记 `!mayfail` 的已知失败**；不是 README 的 715。对应 `outputs/native_engine_tests_upstream.xml` / `_summary.txt`。

五法术修正后为 735 个；增加经济和动态合法性回归后为 **738 个：737 通过、1 个预期失败，8,400 个断言中 8,399 通过**。JUnit 将带 SECTION 的 TEST_CASE 分成更多行；`interaction_tests.json` 记录源码定义与运行行的映射。预期失败不能解释成机制已经修复：`test_navigation_wedge.cpp` 中石头人被两个建筑夹住，持续推进后的位移仍为零。

数值外置后新增2个C++回归，**740个TEST_CASE：739通过、1个相同预期失败，8,413个断言中8,412通过**；Python另有34项JSON真实行为/原子校验。隔离编译结果 `outputs/native_engine_numeric_tests.xml`，配置结算证据 `outputs/native_engine_numeric_stats.json`。独立审查发现滚动分支忽略普通半径/波次/间隔/击退/召唤环，Rage/Clone也忽略普通击退；接口已排除这些字段，滚动击退仅接受非负值，并加入拒绝回归。

173 个注册行全部完成真实下牌、推进和 snapshot 冒烟；41 个进化行执行了到进化触发的周期。此测试仅证明相关路径可执行，不证明特殊能力、全部配对或官方一致。

校准 v2 的 100 局原生双外部随机策略完整对局实测约 0.322 秒、310 局/秒；3,288 次实际接受动作。原始随机动作没有 mask，20,800 次拒绝是故意保留的诊断数量。10 ticks 推进 p50/p95 为 0.0153/0.0326 ms，snapshot p50/p95 为 4.5/5.2 µs。这个 benchmark 包含 C++ 日志，但不包含 Python 搜索、神经网络、服务/UI 成本，且牌组和动作比较稀疏，不能当作整个平台吞吐。证据 `outputs/native_engine_smoke.json`。

## 本地 API 补丁

Python 路径加入 `third_party/ClashRoyaleAi/python_ai`，import `clash_royale_env`。

| 接口 | 含义与边界 |
|---|---|
| `ClashRoyaleEnv(deck0, deck1, max_ticks)` / `seed(s)` / `reset()` | 两个 8 卡组、固定 seed；平台默认 3,000 ticks |
| `direct_play(player, slot, x, y) -> bool` | **手牌槽位** 0..3；绝对坐标；不推进 tick；返回实际引擎接受结果 |
| `can_play(player, slot, x, y) -> bool` | 深拷贝后走同一 playCard；不改变原局势；包括 Mirror 动态费用/法术落点、Spirit Empress 3/6 费用 |
| `advance_ticks(ticks)` | 两个玩家均由外部控制；不调用内置 heuristic opponent |
| `activate_champion_ability(player, slot)` | slot 为原始卡组技能槽 1 或 2；引擎验证费用/冷却/次数 |
| `state_dict()` | oracle/debug：两边手牌、队列、圣水、单位、塔、终局；不得直接送主策略 |
| `snapshot()` | 独立深拷贝；包括待生成实体、弹道目标引用、统计、随机状态、状态计时；不复制原日志 |
| `get_registry_card_ids()` / `get_card_info(id)` | 包括进化的 173 行元数据；`get_all_card_ids()` 只有 132 非进化行 |
| `get_card_profile(id, evolved=False)` | 实际调用工厂，检查初始数量、HP、攻击、速度、防空、法术波数/延迟/塔伤；不包括迟发/死亡召唤 |
| `export_numeric_stats(defaults=False)` | 当前有效主工厂纯数值；defaults=True返回编译基准；173行、41进化变体，元数据在cards之外 |
| `apply_numeric_overrides(payload, reset=True)` | 开战前原子载入全量/稀疏JSON；真实重绑工厂；保留特殊effect对象；第一局建立后全进程锁定 |
| `save_log(path)` | 原生逐 tick JSON 回放；文件路径须在项目内 |

坐标为 18×34，x∈[0,17]、y∈[0,33]；team0 在低 y，team1 在高 y；两桥中心为 (2.5,16.5)/(14.5,16.5)。`direct_play/can_play` 使用绝对坐标。上游 `step_self_play` 的 team1 y 参数会内部镜像；平台自行统一观测坐标，主闭环采用 direct/advance 接口，不要再次调用带内置对手的 `step()`。

## 发现并修正的严重偏差

本机先测原版，再通过另一套实际执行战斗的 Java crforge 核对，之后修正本地源。这个校准是社区引擎对照，**不是官方引擎证明**。

| 法术 | 原生原版塔伤 | 本地校准塔伤 | 同位置静止单位伤害 | crforge 实际塔伤 |
|---|---:|---:|---:|---:|
| Arrows | 369 | 90 | 369 | 90 |
| Fireball | 689 | 206 | 689 | 206 |
| Zap | 192 | 57 | 192 | 57 |
| Rocket | 1485 | 371 | 1485 | 371 |
| Lightning | 1057 | 285 | 1057 | 285 |
| Poison | 168 | 168（保留原生 21×8） | 736 | 160 |

五个修正来自 crforge pinned data 的 crownTowerDamagePercent 和 DamageUtil 整数下取整逻辑；比如 Fireball 689×30%=206.7，取 206。官方可能采用不同的逐等级/逐波数据或舍入，不能称 206 是已验证官方值。Poison 保留原生来源注释的 21/波，两个社区引擎还有 8 点误差。修正处在 `CardRegistry.h`；回归 `test_local_crown_calibration.cpp`。证据 `native_engine_spell_baseline.json` / `_patched.json`、`outputs/audit_candidates/backend_differential*.json`。

原生圣水为每 tick 0.035，且 180 秒即 3×，造成整个首段加时严重偏快。本地 v2 改为 1/28（1 圣水/2.8秒）、120秒 2×、240秒 3×、180秒进入加时；平台时间上限改为 300秒。crforge 20 Hz 在阈值后下一 tick 改倍率，所以仍有 0.05 秒边界差；本地只有 10 Hz。保存了 before/after 和旧测试预期，未将旧版实验并入 v2。证据 `native_engine_economy_baseline.json` / `_patched.json`；新回归 `test_local_economy_legality.cpp`。

## 仍然不能称高保真全复刻的原因

1. Registry 注释宣称 level11，但标准王塔4008/公主塔2534，crforge level11 公主塔3052；Spirit Empress 还明确引用 level9。参数混合多个日期/等级。当前版本只能称固定社区快照。
2. 25类主工厂数值已由 `balance_v2.json` 真实外置，编辑它能改变结算；173导出行中171行可修改，普通/进化分开。Mirror/SpiritEmpress特殊路径、子实体、effect内部、塔兵种、等级自动换算仍未外置。`unsupported_numeric_parameters.json`明确列剩余参数；`card_catalog.json`仍是导出元数据，不负责加载。
3. Executioner 的斧头没有全轨迹穿透；Hunter 用距离线性缩放；部分进化用平面增益或额外生成体近似；Graveyard 固定七点是近似；Freeze 不造成伤害；若干技能/冲锋数值没有完整外部来源。
4. 极端建筑夹角仍会卡死；存在对象遍历顺序影响同 tick 结算的风险。没穷举连续坐标、时序、多单位和所有组合。
5. 玩家观测重建用于搜索时，会重新估计敌方手牌/圣水，且负 ID 死亡子实体、完整状态计时、进化进度、在途弹道可能无法从可见观测准确恢复；自身 next/冷却与完整未知队列也需要额外校准。它与 debug snapshot 是两条不同路径。
6. 没有官方引擎、完整客户端数据的版本锁定和系统性官方回放轨迹 goldens。`card_coverage.csv` 的“官方已验证一致”分子为 0，部署成功不能替代这个检验。

数值加载保持原来的CardStats/effect结构，仅重新绑定实际factory。无修改默认值时，100局既有评估回放中旧模块、新模块、完整默认JSON每路5,314次接受动作和169,994个完整state_dict点逐tick/逐动作一致，零差异；`outputs/native_engine_numeric_replay_equivalence.json`保留哈希和输入回放SHA。该实验只证明与本地v2相同，不建立官方一致性。自定义数值由平台附加配置hash版本与checkpoint匹配；进程全局配置开战后不可变，避免旧实体与新费用混杂。

下一步应继续外置子实体/塔数据并锁定一致等级/日期，建立官方/合法参考录像校准集，再扩展最影响决策的特殊能力和精确时序。增加策略搜索深度无法消除错误引擎带来的策略偏差。

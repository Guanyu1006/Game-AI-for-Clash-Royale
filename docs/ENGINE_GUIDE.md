# 从零理解、测试和修改战斗引擎

项目根目录固定为 `D:\研究生\Clash Royal`。主平台是 `royale_lab`；以前的 `royale_ai` 12 卡近似 MVP 保留为基线。主平台采用真正运行 C++ 战斗的社区引擎，其本地版本为 `ClashRoyaleAi@2f172925a15b+lab-calibrated-v2`。它可用于本地训练实验；还不能称官方服务器或所有官方交互的完全复制。

## 先实际运行一个最小战斗

在该目录打开 PowerShell，运行：

```powershell
cd 'D:\研究生\Clash Royal'
.\.venv\Scripts\python.exe tools/native_smoke.py --games 100
.\.venv\Scripts\python.exe tools/native_economy_audit.py
```

第一个命令验证 173 注册行的真实下牌/快照、14 项接口检查，并完成 100 局原生随机对局；日志写入 `outputs/native_engine_smoke.json`。第二个验证圣水倍率和 can_play 不改变局势。它们不是服务器 UI；完整服务启动请按 README/QUICKSTART 的统一入口。

只有需要改 C++ 时才重编译，先停止所有持有 `.pyd` 的服务/训练进程，然后：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/build_native.ps1 -Tests
.\.venv\Scripts\python.exe tools/native_coverage.py
```

已有 MSVC 来自 Visual Studio 2022 Community；脚本没有安装系统级工具。Python 使用项目内 `.venv`，编译依赖、对象、Catch2 和临时文件在 `.cache` / `.tmp`。上游 CMake 在本机 configure 崩溃，已经用直接 MSVC 编译跑通。不要仅复制原版 CMake 命令并认为安装已经验证。

## “173 张”究竟是什么意思

当前注册目录 **173 行 = 132 个非进化行 + 41 个进化行**。这些数字来自实际 `get_registry_card_ids()` / `get_all_card_ids()`，不是从 README 抄取；其中含冠军/英雄，塔兵种不是同一个分母。

- `data/platform/card_coverage.csv`：逐卡实现路径、部署/快照/进化周期证据、误差。
- `data/platform/mechanic_coverage.csv`：51 项明确机制检查、实现与测试路径、未支持项。
- `data/platform/interaction_tests.json`：740 个原生 TEST_CASE 及运行证据；带 SECTION 的测试会产生更多 JUnit 行。
- `data/platform/card_catalog.json`：平台导出元数据；`outputs/native_engine_profiles.json` 是真实工厂的初始实体配置。

173 行全都能部署和 snapshot，不代表特殊能力和所有配对全部正确。**官方版本对照“已验证一致”的分子为 0**；当前是社区近似实现加本机机制测试。覆盖率的分母是当前固定注册目录，不能写成“官方全部交互覆盖173/173”。

## 一次下牌如何变成伤害

1. `royale_lab/backend.py::NativeBackend.apply` 接收 WAIT、card、ability。card 的 `slot` 是当前手牌位置0..3，不能把卡牌 ID 当 slot。
2. C++ `src/bindings.cpp::direct_play` 验证参数，调用 `GameManager::playCard`。此函数检查费用、手牌冷却、区域、Mirror/Spirit Empress 的动态规则、进化状态，扣圣水并放入待提交队列。
3. `advance_ticks` 只推进战斗，没有内置对手自动出牌。`GameManager::step` 把 pending 变为活动实体，更新移动/攻击/弹道/法术，处理碰撞、死亡和终局。
4. `NativeLabBindings.h::nativeLabState` 导出 oracle 状态。`NativeBackend.observe` 转换玩家视角并筛选公开信息；主策略得到的是这条路径。debug/UI 的全状态与主策略观测必须分开。

`can_play` 在深拷贝上走实际 playCard，因此可检查动态费用/落点而不消耗真实圣水。使用它能避免 Mirror 复制法术时被错误限制在自己半场，或把灵体女皇的3/6费用当成固定费用。

下牌立即得到 bool 接受反馈，但实体通常要下一 tick 提交才出现。检测“执行成功”应看实际接受、手牌/圣水和后续实体，而不能仅看屏幕动画或动作建议打印。

## 战斗源码地图

| 需要理解/修改什么 | 真实源码入口 |
|---|---|
| 已外置的卡牌数值 | `data/platform/balance_v2.json`、`balance_v2.schema.json` |
| 默认数值、配置校验和工厂重新绑定 | `third_party/ClashRoyaleAi/include/core/CardRegistry.h`、`CardStats.h`、`NativeNumericBindings.h` |
| 工厂、数量、次级单位、出生延迟 | `include/core/CardFactories.h` |
| 圣水/手牌/进化周期 | `include/core/GameManager.h`、`PlayerState.h` |
| 桥/区域/碰撞/待生成提交 | `include/core/Board.h`、`ArenaLayout.h` |
| 索敌、锁定、冻结、护盾、冲锋、攻击 | `include/entities/CombatEntity.h` |
| 移动/建筑优先目标 | `include/entities/Troop.h`、`BuildingTargeter.h` |
| 弹道/穿透 | `include/entities/Projectile.h` |
| 法术范围、重复、滚动和塔伤 | `include/entities/AreaSpell.h` |
| 王塔激活/塔兵种 | `include/entities/Tower.h`、`include/core/TowerTroops.h` |
| 冠军/英雄技能 | `include/core/*Effect.h`、`GameManager::activateChampionAbility` |
| 加时/决胜 | `include/core/MatchRules.h`、`TimeoutRules.h` |
| Python 接口 | `src/bindings.cpp`、`include/core/NativeLabBindings.h` |

表中的 `include/` 和 `src/` 均相对 `third_party/ClashRoyaleAi`。完整源码审计、许可证、提交及参数误差见 `docs/audits/ClashRoyaleAi.md`。

## 固定规则与必须记住的误差

本地引擎10 ticks/秒；原点一侧 team0 低y，18×34场地，两个桥中心(2.5,16.5)/(14.5,16.5)。平台玩家1观测转成自己视角，调用 native 前再转换到绝对坐标。`direct_play/can_play` 使用绝对坐标，上游 `step_self_play` 对team1另做y镜像，两套接口不要混用。

v2 已修正圣水：每tick1/28，120秒2×，180秒加时，240秒3×，平台默认最多300秒。原版在180秒就3×且基础恢复偏慢；旧版训练/评估已经隔离保存，不能与新版本合并报告。

原版 Fireball 等五法术会对塔造成完整单位伤害，已依据独立 crforge 数据/实测修正。例如 Fireball 塔伤689→206，单位仍689。其整数舍入来自另一个社区模型，不是官方确认。Poison 的塔伤native168与crforge160仍不同。

等级也没有统一：上游多数单位注释称level11，而王塔4008、公主塔2534与另一个level11参考塔3052不一致，Spirit Empress 还使用level9数据。它是混合日期/等级的固定研究快照。当前学习出来的最优行为最多针对该近似引擎；不能直接推断官方对局强度。

## 修改一张牌与更新数据

`data/platform/balance_v2.json` 已是真正参与结算的配置。C++ 保留每行原始 `CardStats` 和普通/进化工厂，载入 JSON 后按数值重新绑定工厂，同时保留原来的特殊 effect 对象。默认数据从固定源码导出，173 行中 171 行支持修改；Mirror164和Spirit Empress165走特殊路径，修改其数值会报错。`card_catalog.json` 仍只是元数据，编辑它不能改变战斗。

配置文件以卡ID为键：`cards['0']['base']` 是Knight，`cards['7']['base']` 是Fireball。41个进化行还有独立 `evolved` 数值；进化费用修改 `base.cost`，没有独立进化费用。普通行的 `evolved` 为null。`field_schema` 给出25类字段的类型、范围、单位，每张卡仅暴露实际适用的字段。

先复制完整默认文件到项目内，例如 `data/platform/balance_custom.json`，再修改：

```python
import json
from pathlib import Path
root = Path(r'D:\研究生\Clash Royal')
data = json.loads((root/'data/platform/balance_v2.json').read_text(encoding='utf-8'))
data['cards']['0']['base'].update(hp=2200, damage=303, cost=2)
data['cards']['7']['base']['crown_damage_per_application'] = 123
(root/'data/platform/balance_custom.json').write_text(
    json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
```

随后在**新的进程**设置 `ROYALE_BALANCE_FILE` 为项目内该文件并运行原有服务/训练命令。默认不设置时读取 `data/platform/balance_v2.json`。数值配置全进程共享，第一局建立后锁定，不能在已运行的服务中热切换；相同数值重复载入允许。平台对 `cards` 的有效数值计算SHA256，自定义数值改变引擎版本后缀，模型/回放必须与配置匹配。

直接实验原生接口时也可以使用稀疏补丁，无需重编译或修改策略：

```python
import sys
sys.path.insert(0, r'D:\研究生\Clash Royal\third_party\ClashRoyaleAi\python_ai')
import clash_royale_env as native
baseline = native.export_numeric_stats(defaults=True)
native.apply_numeric_overrides({'cards': {
    '0': {'base': {'hp': 2200, 'damage': 303, 'cost': 2}},
    '7': {'base': {'crown_damage_per_application': 123}}
}}, reset=True)  # 在创建任何ClashRoyaleEnv之前
env = native.ClashRoyaleEnv(list(range(8)), list(range(8)), 3000)
```

`reset=True` 从编译基准起构造整个候选配置；`reset=False` 在当前配置叠加。未知卡ID/字段、不适用字段、错误类型、NaN/无穷和超范围值会拒绝整个批次，不留下部分修改。

已支持HP、费用、伤害、攻击/视野/最小射程、速度、攻击冷却、出生/首击延迟、护盾、范围伤害、建筑寿命、普通范围法术的伤害/半径/延迟/波次/间隔/每波塔伤/击退，以及已有滚动法术的距离/宽度/速度/非负击退和非滚动召唤法术的召唤环半径。每张卡只暴露实际分支可用字段：滚动法术不接受普通半径/波次/间隔/普通击退/召唤环，Rage和Clone不接受被其分支忽略的普通击退/伤害。间隔和召唤环须配合多波应用才能体现效果。速度单位是**已转换后的tiles/tick**，直接填0.2表示每tick移动0.2格；不能把源码转换前的数值或tiles/min直接填入。伤害字段整数，时间单位tick（10tick=1秒）；`crown_damage_per_application=-1` 继承单位伤害，建筑寿命0为不自动到期。

实际回归 `tools/native_numeric_stats_test.py` 验证 JSON→工厂→战斗：Knight出生HP2200/实际每次击中303/花费2，Fireball塔伤123而兵伤仍689，Arrows三波改两波塔伤60，Cannon寿命10tick，普通/进化HP分开。34项检查结果在 `outputs/native_engine_numeric_stats.json`，包括对无结算作用的滚动/增益/克隆字段及伪装版本号的bool拒绝。100局既有评估回放按seed和接受动作复放，旧v2、新默认、新完整JSON默认每路169,994个完整state_dict点零差异；结果 `outputs/native_engine_numeric_replay_equivalence.json`，保留了各路逐tick/逐动作哈希。默认数字不变，所以默认模型/700局结果仍属于同一个物理版本。

尚未外置的参数在 `data/platform/unsupported_numeric_parameters.json` 逐项列出，并在导出 `card_metadata` 指明该牌使用的不可编辑effect槽。例如死亡爆炸半径/伤害在 `AreaDamageOnDeath`，周期召唤的子牌在 `PeriodicSpawnEffect::childStats`，Freeze的ticks/slowFactor在 `FreezeOnHit`；它们不随主牌damage/HP变化。特殊技能内部参数、冲锋/跳跃/递增倍数、死亡/召唤子实体、塔兵种、队形/数量/语义标志、进化次数仍需要C++修改。JSON外置也没有自动统一官方等级/日期，任意等级自动换算仍未实现。

需要增加卡或特殊机制时，在 `CardRegistry.h` 找到 `add(troop(...))` / `add(spell(...))` / `addEvolution(...)`。新形状使用 `CardStats` 和已有 `CardFactories`；新机制需要 effect 或实体逻辑，增加卡名或数值行不能自动实现能力。

加入新ID还要检查 `CARD_ID_COUNT`、特殊槽位合法性、factory snapshot复制、clone、子实体负ID、命名/统计归因。修改后必须编译、跑对应TEST_CASE和全套检查，更新 version 和coverage，再重新生成轨迹/训练模型。

继续外置时优先扩展子实体和塔数据、统一等级/日期，并为每个新增字段增加“JSON修改→实际结算变化”测试。本次25类字段已经具备这条闭环，效果内部参数仍需逐项迁移。

## 新增一个有用的测试

参考 `tests/core/test_local_crown_calibration.cpp` 和 `test_local_economy_legality.cpp`。新增文件必须匹配 `tests/core/*.cpp` 或 `tests/entities/*.cpp`，构建脚本会自动收集。

```cpp
#include <catch_amalgamated.hpp>
#include "GameManager.h"
TEST_CASE("my rule: no triple elixir before four minutes", "[local][elixir]") {
    REQUIRE(GameManager::elixirMultiplierAtTick(2399) == 2.0f);
    REQUIRE(GameManager::elixirMultiplierAtTick(2400) == 3.0f);
}
```

优先测试范围边界、同tick先后、击退后目标切换、死亡衍生、镜像费用、重复动作反馈。常规测试证明实现符合声明；要证明官方一致，还需要独立官方/合法参考轨迹和误差容许值。

对照实验先固定seed和场景，保存before/after；修改引擎规则后重新训练/评估。不要通过修改期望值让不理解的失败“消失”。本次经济校准只改了明确对应旧恢复率/旧阈值的预期，并加入独立新回归。

## 复现已知失败和排查问题

```powershell
& '.\.cache\native_direct\ClashRoyaleTests.exe' 'a troop pinched between two buildings still makes progress' --reporter compact
& '.\.cache\native_direct\ClashRoyaleTests.exe' '[local_calibration]' --reporter compact
.\.venv\Scripts\python.exe tools/native_spell_audit.py --output outputs/native_engine_spell_check.json
```

第一项复现石头人被两个建筑夹住位移为零；它是上游 `!mayfail`，整体runner成功退出也不意味着它通过。第二项运行本地法术、经济、动态合法性回归。第三项把法术同时施加在静止单位与塔上，排除移动/攻击混淆。

模块import失败时查看Python ABI、`outputs/native_engine/build_manifest.json`、实际 `.pyd` 路径；重编译前退出旧进程，使用新的Python进程加载，避免旧DLL留在内存。遇到CMake崩溃使用已验证的 `build_native.ps1`。遇到动作拒绝先检查slot、圣水、冷却、坐标视角、游戏终局和 can_play。

如果搜索预测和真实后续不同，先从保存局势复现固定动作，再比较单位数、负ID子单位、部署/冻结/护盾、在途弹道、自己的下一张和队列。可见状态重建是近似世界模型；debug snapshot是精确引擎状态，两者不能混称。

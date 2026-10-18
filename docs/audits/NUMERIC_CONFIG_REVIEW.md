# 数值配置接口独立复查

审查源为 `include/core/NativeNumericBindings.h`、`CardRegistry.h`、`CardFactories.h`、`AreaSpell.h`；使用新进程的实际 Python→C++ 调用，未修改引擎源码。2026-10-14 复查初版 `.pyd` SHA256 `47072548cdb857622ac228ae9ebb4d78b0e442eed8afb25724866b574a350599`，它与本地 v2 的 100 局默认回放一致性记录是另一项实验。

初版 binary 副本保存在 `.cache/audit/numeric_review_original`。运行 `tools/audit_numeric_config_review.py` 生成 [numeric_config_review.json](../../outputs/audit_candidates/numeric_config_review.json)，每个 before/after 用独立进程、相同 seed/固定局面，保留 80 tick 的单位 HP/位置/塔 HP 轨迹。没有用目录中的 README 宣称代替结算实测。

## 实际发现并修复的问题

| 字段/分支 | 真实证据 | 源码原因 | 建议修正 |
| --- | --- | --- | --- |
| The Log 的 `spell_radius`、`spell_applications`、`spell_interval_ticks`、`spell_knockback` | 修改被接受、配置计数变化，80 tick 战斗轨迹完全相同 | `AreaSpell::update` 调 `updateRoll` 后返回；滚动逻辑不用圆盘参数 | 按圆盘/滚动分支分别导出和拒绝不适用字段 |
| Barbarian Barrel 的 `spell_spawn_ring_radius` | 修改被接受，实际子召唤/兵 HP/位置不变 | `updateRoll` 固定在滚动终点 position 召唤，不经过 spawnPoint/ring | 滚动法术排除 SpawnRing |
| Rage/Clone 的 `spell_knockback` | 修改被接受，受影响兵位置/HP 轨迹不变 | buffsAllies/clonesAllies 分支先 continue，跳过径向击退 | 这两个模式排除 knockback |
| `spell_roll_knockback=-5` | 被接受，但与设置 0 的轨迹完全相同 | `updateRoll` 只在值 >0 时执行 pushAlong | 范围限定 0..50，不暗示负值支持拉扯 |

这些是“字段被接受但结算不会使用”的真实接口缺陷；不是默认 v2 动力学改变。实现者已修复，新 stage 模块 SHA256 为 `6160b968f9dfde6ab9b047627596c5d50383b0af8728ed5036fc49239edccd53`。独立重测结果见 [numeric_config_review_fixed.json](../../outputs/audit_candidates/numeric_config_review_fixed.json)：8 个不适用/非法字段全部拒绝，配置哈希不变；不适用字段不再导出，滚动 knockback 保留但拒绝负值。4 个合法控制（Log delay、Log roll knockback=0、Fireball radius、Graveyard ring）仍可加载。初版结算证据保留，不覆盖失败发现。

另有低影响 schema 一致性问题：初版 `schema_version=true` 被转换为整数 1 而接受，`schema_version=1.0` 则拒绝；修复后 bool 已实际报 `ValueError: expected exact integer version/rate tag`。`field_schema` 是导出说明，输入中更改它不会放宽 C++ 实际校验范围；不应将它视为可自定义验证规则。

## 原子性、锁和复合效果的实测

五类独立非法批次全部拒绝且有效配置哈希不变，包括先合法修改 Knight HP 再遇到非法 Fireball 波次、死亡效果未知字段、进化独立 cost、布尔 HP 与负 HP。`reset=False` 叠加保留前一次卡牌修改，`reset=True` 将补丁未指定的卡恢复编译默认。建立环境后修改会拒绝，环境状态与配置哈希均不变；删除环境并 GC 后仍拒绝，符合全进程锁定而非每局热加载的声明。

上述 5 类原子拒绝、reset/merge 和建立/删除环境后的锁均在修复后 stage 再次实测通过，证据为 [numeric_config_review_fixed_validation.json](../../outputs/audit_candidates/numeric_config_review_fixed_validation.json)，与修复适用性测试使用同一 SHA256 模块。

Goblin Gang 改主牌 HP/damage 后，三只主 Goblin 变为 HP2222/damage999，三只 secondary Spear Goblin 仍为 HP133/damage81。Giant Skeleton 主 damage 改为 999999，死亡后 Knight 从1766变880，延迟炸弹仍为886；before/after死亡轨迹一致。它们没有随主牌修改是预期限制，`unsupported_numeric_parameters.json`、导出 effect/secondary 槽和 `ENGINE_GUIDE.md` 已明确描述，未认定为支持缺陷。

本轮未发现一般非 spell 主工厂数值必然不被使用的其他情况。native Python 调用未释放 GIL，平台另有 RLock；已验证此 Python 入口下的顺序锁和拒绝原子性，没有对任意外部 C++ 多线程访问、内存分配失败等异常做原子性证明。没有逐项测试 25 类字段在所有卡/所有组合上的实际效果，更没有官方游戏等价证明。

## 复现命令

```powershell
.\.venv\Scripts\python.exe tools/audit_numeric_config_review.py
.\.venv\Scripts\python.exe tools/audit_numeric_applicability_fixed.py --module-dir .cache/native_numeric_stage
```

第一条固定使用发现缺陷的 binary 副本。第二条对修复后的模块单独检查 8 个拒绝/不变条件及 4 个允许控制，已实际全部通过。此范围内的字段适用性问题已修复；这项审计不替代新的数值到攻击结算回归或默认数据回放一致性检查。

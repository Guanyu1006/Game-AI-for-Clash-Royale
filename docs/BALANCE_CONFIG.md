# 修改JSON，让数值真正进入战斗

data/platform/balance_v2.json导出173个注册ID的base/evolved数值；schema在balance_v2.schema.json。backend.native_module先载入数据，再创建对局。card_catalog.json只是元数据导出。

支持25类按形状适用字段：费用、HP、伤害、范围、速度、冷却、部署、技能费用/冷却、护盾、法术半径/延迟/波次/塔伤、建筑寿命等。173行中171可调；Mirror164、SpiritEmpress165特殊路径禁止修改。滚动/buff/clone法术只允许真正参与结算的字段。

效果内部、负ID子实体、塔HP/兵种、等级自动换算、语义标志与另外68个CardStats数字成员尚未外置，见unsupported_numeric_parameters.json。不能称全部参数数据化。

## 已验证的覆盖

保存到项目内文件，例如outputs/experiments/my-balance.json：

```json
{"cards":{"0":{"base":{"hp":2200,"damage":303,"cost":2}},"7":{"base":{"crown_damage_per_application":123}}}}
```

在新的PowerShell进程：

```powershell
$env:ROYALE_BALANCE_FILE='outputs/experiments/my-balance.json'
.\.venv\Scripts\python.exe -m royale_lab info
.\.venv\Scripts\python.exe -m royale_lab demo --policies random greedy --max-ticks 600
```

首次battle后配置锁定，避免旧实体与新费用混用；改文件后重启。移除当前进程ROYALE_BALANCE_FILE再重启，恢复默认。未知/不适用字段、NaN、布尔冒充数字、非法范围拒绝；验证失败不会部分修改。相同数值no-op允许。

```powershell
.\.venv\Scripts\python.exe tools\balance_integration.py
```

工具自动生成覆盖JSON并起隔离子进程，验证正常下牌费用2/出生HP2200、隔离交战攻击303、火球塔伤123；证据outputs/platform/numeric_integration.json。原生回归另验证Arrows3→2波实测60塔伤、Cannon300→10tick消失、普通/进化HP独立与非法参数拒绝。

## 指纹和模型隔离

生效cards有canonical SHA256。默认固定v2不改版本；自定义数值追加balance指纹。不同版本局势拒绝重放。新checkpoint记录balance_hash；旧默认checkpoint在自定义配置下拒绝，须重训。策略代码及Policy接口不用改。

自定义catalog写data/platform/catalogs，避免覆盖默认。评估experiment_config固定引擎/数值/预算/模型SHA；变更后用新--output。默认100局5314动作、169994完整状态点与旧v2逐点零差异，只证明本地兼容。收紧无效字段改变schema指纹时保留旧元数据，只有零物理差异证据才迁移实验配置。

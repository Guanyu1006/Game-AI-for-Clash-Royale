# 真实截图与学习策略之间的缺测边界

旧 `arena-public-v2` 包含精确 HP、时钟、状态和己方轮转等数值字段。截图检测器目前不能可靠读出全部字段。把未知 HP 填为 0 会让网络把活单位视作死亡；把慢速 Android 的墙钟间隔当游戏逻辑时间，又会破坏时序模型。因此旧 checkpoint 不接受直接像素桥接。

`pixel_policy.py` 新增 `arena-masked-pixel-v3`。每个全局／实体特征都有对应 known mask；未知数值置 0 且 mask 为 0，两者一起进入网络。当前支持固定 8 张基础卡，保留已识别手牌、保守圣水下界、可见单位类型和位置。HP、时钟、皇冠、等级、特殊状态、技能按钮及未经校准的阵营判断保持未知。超容量拒绝，不悄悄截掉单位。

训练输入通过 `pixel_data.py` 从独立同版本自采数据投影，去掉与截图不可同等取得的字段。部署使用相同 schema 和坐标转换。它是**从模拟器向像素输入迁移的候选**，尚未包含真实检测器误差训练，不能把投影数据叫作真实像素监督。

## 输入、动作和时间

截图下方控制方的原生坐标向下，模型坐标以己方为起点向上；转换为 `x/18000, 1-y/32000`。模型 18×32 格点输出再逆变换为原生坐标。部署普通卡限制己方半场，法术可全场，未知技能禁用。识别未知、圣水下界不足时只有 WAIT 合法。

战场特征只读取 `PixelObserver` 的截图结果，不读取原生完整状态、敌方手牌或圣水。原生状态可单独保存为对齐审计目标，不能经过桥接混入 actor。

墙钟只用于采集延迟。慢速或暂停推进的 Android 中，墙钟秒数不等于游戏秒数；只有控制器自己已确认完成的 `control_feedback.elapsed_ticks` 可以提供逻辑经过时间。没有这项证据时清空循环记忆，明确记录 `elapsed_game_time_known=false`，不会假装每帧经过 0.5 秒。该退化路径仍需单独评估。

```bash
.venv-cloud/bin/python -m arena_lab.pixel_data \
  --index outputs/arena-next-20261113/data-pixel-native8-v1/index.json \
  --output outputs/my-pixel-projection

.venv-cloud/bin/python -m arena_lab train-timing \
  --index outputs/my-pixel-projection/index.json \
  --output outputs/my-pixel-model --epochs 12 --threads 1
```

设备入口传入 `--checkpoint <masked-pixel-v3 best.pt>` 后使用 `PixelModelController`；它严格拒绝旧 v2 模型。不同屏幕分辨率、画面布局、卡牌图案或阵营识别变化，需要重新校准和新像素验收。正式实时标准应覆盖帧新鲜度、全局端到端 p95 延迟、实体漏检/误检、追踪失配、动作视觉一致性和完整自然终局；不能仅凭检测器返回框或 ADB 返回成功判定达标。

# 设备审计摘要

这里保留 2026-11-13 首轮原生准备失败及随后实际恢复的冻结证据。早期摘要不改写；11 月 14 至 15 日的后续结果另列于下方。目录不包含模型权重、APK、VM 磁盘或整段录屏；本次新增 3 张经过审查、未经修改的实际 Android 小图作为动作链证据。

- [manifest.json](manifest.json)：各原始文件的路径／字节数／SHA256、运行版本身份、视觉资产锁、尚未通过的设备门槛和接口测试结果。
- [pre_retry_failure_summary.json](pre_retry_failure_summary.json)：三次准备失败时间线，以及确实取得加载黑帧的两次独立采集延迟。
- [loading_raw_capture.json](loading_raw_capture.json)：原始 RGBA 输送、宿主无损编码的单调时钟和像素哈希。
- [historical_development_audit.json](historical_development_audit.json)：历史 7 帧的开发诊断；6 个候选匹配中类别正确 3 个。帧参与过修正、渲染 tick 未校准，不能用作独立识别准确率。
- [post_rebuild_result.json](post_rebuild_result.json)：第二轮实际于 17:37:35 UTC 到达内容 ready，后续 PID 守卫拒绝 identity；同时保留两轮加载活动诊断和实际 persistent raw 黑帧采集。

**截至 11 月 13 日首批失败证据保存时**，该批新原生战局、动作请求和战斗帧均为 0。PNG 11.581 秒、raw＋宿主 PNG 7.403 秒来自两个不同加载阶段的调用，不能称为战斗吞吐、配对加速基准或闭环成功。重建原因未知，没有 OOM 结论。

新增的 `--adb-read-timeout` 解决的是慢速软件 Android 下只读检查预算的可配置性，73 项恢复契约测试通过。第二轮内容 ready 已到达，但 identity 的 PID 在哈希前不再匹配；这不是哈希不一致，也不能以单点栈断言加载死锁。PID 守卫补充了失败的实际 PID／stat 证据，13 项 identity 测试通过，拒绝及不重试约定不变。完整实现、重新运行命令和时间／未知字段约定见 [VISION_ZH.md](../../../../arena_lab/VISION_ZH.md)。

这些原始路径位于当前工作区 `outputs/`，通常不随 Git 分发。已提交摘要可复核报告内部范围，重新取得原始 Android 图像和设备回执仍需对应资源与运行环境。配置 level 11 请求不是实际等级读取；最小原生状态没有已验证的等级字段，实际等级门槛仍为 false。

## 11 月 14 至 15 日新增证据

- [short_pixel_20261114.json](short_pixel_20261114.json)：11 月 14 日默认 1 倍的 13 帧／12 次 WAIT，0 请求，保留原失败范围。
- [render_20261115/summary.json](render_20261115/summary.json)：同时包含 11 月 14 日和 15 日两次默认倍率失败摘要，以及 0.25 倍实验的实际保存前缀。后者中断于 **12/24 决策、13 帧、tick 90→210**；帧编号 6（从 0 计数）起 7 帧有真实 HUD，巨人 tick 151、弓箭手 tick 171 均有独立原生消费。两次中只有巨人满足完整视觉一致性；另一次手牌变化保持 unknown。
- [render_20261115/interruption.json](render_20261115/interruption.json)：PID 1 于 02:11:15 UTC 重建，旧设备进程均已消失。原实验 `report.json` 的 `running` 原样保留，最后状态非终局，未执行独立复测；只读恢复没有重启 VM、复用旧连接或重放动作，根因未知。
- [render_20261115/prepare-summaries.json](render_20261115/prepare-summaries.json) 与 [采集基准摘要](render_20261115/benchmark-20261114-summary.json)：新进程身份、就绪轮询与有限稳定性窗口；暂停场景两种 raw 输送各 3 次，不宣称配对加速或稳定 p95。
- [render_timing_protocol_review_20261115.json](render_timing_protocol_review_20261115.json)：固定库／源码／日志的只读协议检查。逻辑 clock 的钳位与未命名组件的不同 delta 已确认，VS 组件和倍率因果尚未定位。

随 Git 保存的三张未修改原图：[首次 HUD／出牌前](render_20261115/quarter-frame06.png)、[巨人消费后](render_20261115/quarter-frame07.png)、[最后保存帧](render_20261115/quarter-frame12.png)。全部来自同一已声明的开发实验，不能用作独立识别准确率或自然整局验收。原工作区 13 张图及对应状态都经过哈希／格式／时钟核对；便携包只携带其中 3 张图、13 条帧元数据和完整两笔请求／消费／视觉账本。

```bash
.venv-cloud/bin/python docs/audits/arena_next_20261113/device/render_20261115/verify.py
```

工具只依赖标准库，校验包内 21 个文件的字节数与 SHA256、10 tick 窗口、请求对应、独立消费事件以及中断范围。首末截图跨度 310.64 秒仅覆盖 6 秒受控逻辑时间，不能称为实时作战；自然终局、剩余预算、独立短测、渲染对齐、物理手机和官方在线验收仍未完成。

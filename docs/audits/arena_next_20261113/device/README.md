# 设备审计摘要

这些是 2026-11-13 首轮原生准备失败、容器重建后的冻结证据，后续重试应另增记录。没有将模型权重、APK、VM 磁盘、录屏或图像纳入此目录。

- [manifest.json](manifest.json)：各原始文件的路径／字节数／SHA256、运行版本身份、视觉资产锁、尚未通过的设备门槛和接口测试结果。
- [pre_retry_failure_summary.json](pre_retry_failure_summary.json)：三次准备失败时间线，以及确实取得加载黑帧的两次独立采集延迟。
- [loading_raw_capture.json](loading_raw_capture.json)：原始 RGBA 输送、宿主无损编码的单调时钟和像素哈希。
- [historical_development_audit.json](historical_development_audit.json)：历史 7 帧的开发诊断；6 个候选匹配中类别正确 3 个。帧参与过修正、渲染 tick 未校准，不能用作独立识别准确率。

**本批新原生战局、动作请求和战斗帧均为 0。** PNG 11.581 秒、raw＋宿主 PNG 7.403 秒来自两个不同加载阶段的调用，不能称为战斗吞吐、配对加速基准或闭环成功。重建原因未知，没有 OOM 结论。

新增的 `--adb-read-timeout` 解决的是慢速软件 Android 下只读检查预算的可配置性，73 项恢复契约测试通过；尚无证据表明它解决了游戏 `contentReady` 卡住的根因。完整实现、重新运行命令和时间／未知字段约定见 [VISION_ZH.md](../../../../arena_lab/VISION_ZH.md)。

这些原始路径位于当前工作区 `outputs/`，通常不随 Git 分发。已提交摘要可复核报告内部范围，重新取得原始 Android 图像和设备回执仍需对应资源与运行环境。配置 level 11 请求不是实际等级读取；最小原生状态没有已验证的等级字段，实际等级门槛仍为 false。

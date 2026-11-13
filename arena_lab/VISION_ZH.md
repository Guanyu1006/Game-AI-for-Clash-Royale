# 截图观察、执行回执与设备复现

`vision.py` 从 PNG 读取战场目标、手牌和圣水，做有过期时间的单位跟踪；`device.py` 将它接入离线原生 Android 回合控制器。传 `--checkpoint` 使用新的 `PixelModelController`，不传则使用保守规则诊断控制器。原生完整状态只写审计档案，不能进入截图策略。

当前实现支持 **360×640 的固定原生观战布局、画面下方 owner 1、8 张普通卡**：骑士、弓箭手、巨人、火枪手、迷你皮卡、加农炮、火球、万箭齐发。它没有完成通用分辨率、完整卡池、塔血 OCR、隐形单位或特殊技能适配。布局或资源图像变化必须重新校准，代码拒绝悄悄缩放。`PixelModelController` 的缺测规则和模拟训练域限制见 [PIXEL_MODEL_ZH.md](PIXEL_MODEL_ZH.md)。

## 安装与来源

云端基础解释器仍为仓库 `.venv-cloud/bin/python`。视觉依赖单独锁定，不覆盖 Windows 历史环境：

```bash
.venv-cloud/bin/python -m pip install -r requirements-arena-vision.txt
.venv-cloud/bin/python -m arena_lab.vision_assets
.venv-cloud/bin/python -m unittest tests_cloud.test_arena_device -v
```

`vision_assets.json` 锁定 [itzik123/ClashRoyaleAi 的源码提交](https://github.com/itzik123/ClashRoyaleAi/tree/2f172925a15b17f973291f41a789b19a29f0fc43)、每个模型／模板文件的 URL、字节数和 SHA256。下载到 `.cache/arena-lab/vision`，已有不同文件或中断文件会保留并报错。不会加载 pickle。

上游仓库源码许可证为 MIT；**单位 ONNX 模型元数据标注 AGPL-3.0**，不能把它随仓库源码许可证改称 MIT。两个模型与图片没有纳入 Git。准确哈希以资产锁与 `vision.py::MODEL_SHA` 为准。

## 低资源原生恢复

以下命令使用已经安装的离线 Android 磁盘，不连线上账号。4 CPU 的工作区内不要与训练同时运行。VM 的输出目录要短，Unix QMP socket 不接受过长路径；每次保留工具返回的新 attempt 目录。

```bash
nice -n 10 .venv-cloud/bin/python tools/cloud/software_android_vm.py start \
  --cache .cache/native-tools/android-recovery-001 \
  --output outputs/device-vm \
  --vcpus 2 --timeout-multiplier 20 --tb-size-mib 64 \
  --memory-mib 3072 --display-fps 5 \
  --resume-attempt research/vm/20261107T075824.538226Z-vm

nice -n 10 .venv-cloud/bin/python tools/cloud/native_prepare.py \
  --manifest outputs/native_loop_20261020/local-runtime-version-001.json \
  --output outputs/my-device-prepare --display-compat \
  --adb .cache/native-tools/bin/adb --adb-port 5038 \
  --serial 127.0.0.1:15555 --root-shell su0 --adb-read-timeout 90
```

只有 `native_prepare` 成功生成、验证当前实例的 `connection.json` 后才执行设备实验；不要复用旧 PID 或旧 identity，容器重建后缓存的 `active-vm.json` 可能过期。准备超时不会绕过 `contentReady` 检查，也不修改哈希锁定的 probe。失败后的 app 重启需显式使用 `--restart-app`，不盲目重放动作。

本轮曾两次在只读检查的默认 30 秒预算内超时，而父启动阶段仍有较多预算。整条准备链现在均可传 `--adb-read-timeout`，范围 1..180 秒、默认 30；上面为慢速 TCG 显式设为 90。它仅延长应用／显示设置／身份读取，不增加启动次数、不改变写设置和启动动作超时、不自动重放。73 项恢复工具契约测试通过；这修复的是恢复检查预算，不能证明内容加载阻塞已被解决。

```bash
.venv-cloud/bin/python -m arena_lab.device \
  --connection outputs/my-device-prepare/connection.json \
  --output outputs/my-device-run \
  --checkpoint outputs/my-pixel-model/best.pt \
  --level 11 --capture-format raw --decisions 12 --step-ticks 10
```

`--output` 必须为新的目录。`--level 11` 仅修改这局配置副本的卡等上下限、avatar 显示等级和 `hbd.kt`；完整配置写 `initialization.json`。原生拒绝配置会停止，不回退 16 级。当前最小原生状态不导出已校准的等级字段，因此 `level-audit.json` 同时保存实际 HP、手牌参数原值并明确 `actual_levels_verified=false`。配置请求成功不能当作运行实际等级已独立验证。此项仍需固定版本等级表或可靠等级像素读数补证。

默认截图是 Android PNG。`--capture-format raw` 使用当前有 root 的离线 Android `screencap` RGBA 数据，在宿主进行无损 PNG 编码；保留 raw/RGBA/PNG 哈希和分阶段时间。此模式不是对任意未 root 手机的承诺。独立 `AdbTapActuator` 可发送两次 UI tap，但本轮没有物理手机接入或真实 UI tap 验收，设备入口实际使用明确标记的 `offline_native_rpc`。

物理 ADB 的独立入口已串联 PNG 截图、同一个 `PixelObserver`／`PixelModelController`、两次 UI 点击及后续视觉回执。仅适用于已经打开的离线 `nullsroyale.rel.free` 战斗、相同的 360×640 底方布局；每次截图先检查前台应用。不会启动应用、登录、匹配、缩放图片或调用原生状态。下面的 `YOUR_EXPLICIT_SERIAL` 必须换成用户指定设备：

```bash
.venv-cloud/bin/python -m arena_lab.device_live \
  --serial YOUR_EXPLICIT_SERIAL --checkpoint arena_lab/artifacts/pixel-masked-v1.pt \
  --output outputs/my-physical-pixel-attempt
```

实际运行时间未知，因此跟踪及模型记忆按缺测处理，不能声称 0.5 秒逻辑动作窗口已在物理设备校准。这个入口的 `--max-age-ms` 从截图检查开始计时，包含截图与推理，比暂停原生诊断的年龄定义更保守。没有独立消费回执，所以**首个出牌尝试后的截图一旦取得即停止**：即使手牌变化与圣水下降一致，仍记 `game_consumed=null`，不继续重复点击。截图或传输失败保留未结请求。这个有界入口供部署标定与回执验证，尚不是完整自动实战；当前真实物理设备调用次数为 0。

可选 `--capture-format raw-persistent` 保留一个 `adb shell -T su 0 sh -s` 无 PTY 双向会话，每次仅发送固定 `screencap` 命令，仍逐帧执行截图，保持 360×640 和像素内容。`exec-out` 只转发输出，不能用于需要继续输入指令的持久会话，这一边界来自 [Android ADB 官方实现](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/client/commandline.cpp)。流按 Android 12 的 16 字节头及确切 RGBA 字节数拆帧；超时／半帧／格式变化会关闭并标记会话失效，不偷偷重连重读。接口测试覆盖分块输送、连续两帧不串帧、半帧和超时；**实际 Android 兼容性与加速倍数仍待下一轮实测**，不能从减少连接次数推出吞吐已达标。

准备成功后，可在一局新的合法配置、同一暂停 native 状态下交替测量两种 raw 输送；各取 3 次，保留冷启动样本、完整字节／状态哈希和原始时延，不外推稳定 p95。该基准不会发送卡牌动作，也不是整局验收：

```bash
.venv-cloud/bin/python -m arena_lab.device_benchmark \
  --connection outputs/my-device-prepare/connection.json \
  --output outputs/my-device-capture-benchmark --level 11 --repetitions 3
```

## 证据与验收含义

每次运行保存这些相互独立的事件：

| 文件 | 证明范围 |
| --- | --- |
| `frames/`、`captures.jsonl` | 实际 Android 帧、字节哈希、采集起止、前后 native tick/generation |
| `states/`、`journal.json` | 只供审计的原生状态／初始化／事件；不进入 actor |
| `observations.jsonl` | PNG 所得目标、未知字段、跟踪、HUD、推理时间、控制器自己的确认步进反馈 |
| `requests.jsonl` | 发 RPC 前即落盘的请求、截图时间和请求时间 |
| `acknowledgements.jsonl` | 调度被接受；仍不能证明牌被消费 |
| `consumption.jsonl` | 原生独立执行回执；缺失回执保留 unknown |
| `actions.jsonl` | 后续截图的手牌变化／圣水下降一致性；不是消费证明 |
| `decisions.jsonl` | 策略耗时、步进耗时和每次前后 tick |
| `report.json` | 实际完成范围、失败、耗时序列和未通过的门槛 |

同一时刻最多一个未结请求；超时不自动重试。截图时 native tick 和 generation 不变，只能证明暂停状态绑定，不能证明 GPU／SurfaceFlinger 显示的是这个 tick。未校准的 `rendered_state_tick`、`render_latency_bound_ms` 保持 null。必须另行测量渲染滞后，不能将 state/frame 配对门槛偷换为画面新鲜度门槛。

设备 actor 的逻辑时间只来自自己已确认完成的 `elapsed_ticks` 和固定原生 20 Hz 协议；墙钟用于真实采集延迟。跟踪器有确认步进时按自己累计的逻辑经过时间关联／过期，避免把 TCG 的 7 秒截图误当作经过 140 个游戏 tick；没有回执或切换时间源时清空跟踪记忆，两种时间均单独记录，不把同一观测计两次。这不能证明渲染已经刷新。

`--step-ticks 10` 是 0.5 秒逻辑步长。传 checkpoint 时，入口严格核对其 `timing_config.tick_hz=20` 且 `decision_ticks` 与参数一致；如果 checkpoint 训练跨度为 10，默认规则实验跨度 100 会被拒绝，必须显式传 10。elapsed 反馈和循环记忆重置都不能让固定 hazard 窗口自动变成其他跨度。默认规则诊断并不等于已训练模仿策略。

`--max-age-ms` 拒绝的是从截图传输结束到发请求的宿主额外年龄；采集本身耗时另列 `capture_ms`，显示渲染年龄尚未知。正式实时验收需独立测完整端到端 p50/p95、漏检／误检、跨帧 ID 错配、截图 freshness、动作消费和自然终局，不应把该参数当作已经满足实时 SLA。

## 2026-11-13 的实际结果

原始证据在 `outputs/arena-next-20261113/device-evidence/report.json`，本轮运行固定指纹为 `7d62f3e6385490b06ff1d376f5660efda4e7603319f963540449522b96c359b6`。

第一轮 VM 完成 Android 启动和游戏进程启动，内容根指针发布，但 `contentReady` 连续 900 秒未到达，准备于 15:20:11 UTC 失败。第一次恢复在 display-compat 读取超时；第二次恢复已 force-stop 游戏，但 check-app 读取在 15:23:11 UTC 超时。最后一条设备操作与约 15:35 UTC 的容器重建相隔约 12 分钟。没有证据证明重建原因，不能写成 OOM；VM 日志为空，旧进程已经消失。

本轮确实取得 Android 加载黑帧：PNG 路径一次 11.581 秒，raw RGBA＋宿主 PNG 一次 7.403 秒。它们是不同调用、不同时间的两次测量，不能当成配对加速基准，更不能当成战斗帧吞吐。当前**新原生战局数、动作请求数、战斗帧数均为 0**；真实截图闭环、学习策略设备闭环、实时门槛均未通过。

另外对历史同指纹 7 帧进行了开发诊断。修正 HUD 误检区域、蓝方颜色识别和圣水分隔线后，6 个能粗略匹配的单位里类型正确 3 个、颜色侧别正确 6 个；其中炮被识别成哥布林牢笼、吹箭哥布林被识别成弓箭手。平均坐标误差与时钟绑定尚未经独立校准，样本又参与过修正，因此这些数值**不是独立测试准确率**。历史记录见 `device-historical/development-audit.json`。初始 VS 画面现在必须先检测到手牌和圣水 HUD，才会把目标交给策略。

最近的开发测试覆盖未知数据、请求与执行区别、顺序与超时、跟踪过期、像素无损、布局拒绝和等级证据。测试通过只证明这些接口边界，不替代新设备闭环试验。

## 后续集中验收顺序

先停止并发训练，以低资源 VM 重试内容加载；若再次失败，保存 Android binder／Mainloop 活性、内容 ready、渲染与文件读取证据，定位具体阻塞再改工具。不可在空白屏幕上持续测推理而宣称战场识别完成。

已经逐项对比 11 月 7 日成功与 11 月 13 日失败的启动参数：同一个 10 月 20 日 mutable RAW ext4、2 vCPU、3072 MiB、TCG TB64、5 FPS、内核、9p 与 initramfs；initramfs SHA256 均为 `6df24b5b61c9d865ec2a258d28447b46ed628c6b74e26193b7e17fe6f3058821`，不存在新 qcow overlay 链差异。两个历史成功内容轮询窗口为 295.6 秒与 314.2 秒。旧 debuggerd 中 Java 主线程在 epoll、SwiftShader 队列等候任务，ARM Mainloop 无可解析客体栈、两个翻译工作线程位于 mmap；单点栈不能判定死锁或根因。

重试在内容状态持续约 4 分钟无变化时，使用下列有界只读诊断采两轮，结合线程 CPU／wait channel／syscall、进程 IO／maps 数、SurfaceFlinger 层与帧统计判断是否有进展。它不会启动或重启进程，不复用历史 PID，也不改 ready 位；访问失败和超时保留原始输出。仍需解释实际两轮结果，不能把工具存在当作根因已解决：

```bash
.venv-cloud/bin/python -m arena_lab.device_diagnostics \
  --serial 127.0.0.1:15555 --output outputs/my-loading-diagnostics \
  --samples 2 --interval 30 --timeout 90
```

进局后先用新截图独立标注 HUD 与单位，实测 fixed-8 手牌识别，修正 detector 的类混淆，并把开发标定帧与验收整局分离；之后运行短规则诊断与同输入的学习策略。配对审计再校准显示时延，最后做多种子完整自然终局、动作确认和 p95 延迟验收。缺少物理设备时保留物理设备验收未通过，Android 软件 VM 的结果独立报告。

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
  --checkpoint arena_lab/artifacts/pixel-masked-v1.pt \
  --level 11 --capture-format raw --decisions 24 --step-ticks 10 --render-speed 0.25
```

这是下文已取得两次真实消费的开发诊断参数；该次运行中断于 12/24 个窗口，完整预算和独立种子复测尚未完成。默认 `--render-speed 1` 保留，不能从此命令推断 0.25 倍已经解决全部入场或渲染问题。

`--output` 必须为新的目录。`--level 11` 仅修改这局配置副本的卡等上下限、avatar 显示等级和 `hbd.kt`；完整配置写 `initialization.json`。原生拒绝配置会停止，不回退 16 级。当前最小原生状态不导出已校准的等级字段，因此 `level-audit.json` 同时保存实际 HP、手牌参数原值并明确 `actual_levels_verified=false`。配置请求成功不能当作运行实际等级已独立验证。此项仍需固定版本等级表或可靠等级像素读数补证。

默认截图是 Android PNG。`--capture-format raw` 使用当前有 root 的离线 Android `screencap` RGBA 数据，在宿主进行无损 PNG 编码；保留 raw/RGBA/PNG 哈希和分阶段时间。此模式不是对任意未 root 手机的承诺。独立 `AdbTapActuator` 可发送两次 UI tap，但本轮没有物理手机接入或真实 UI tap 验收，设备入口实际使用明确标记的 `offline_native_rpc`。

物理 ADB 的独立入口已串联 PNG 截图、同一个 `PixelObserver`／`PixelModelController`、两次 UI 点击及后续视觉回执。仅适用于已经打开的离线 `nullsroyale.rel.free` 战斗、相同的 360×640 底方布局；每次截图先检查前台应用。不会启动应用、登录、匹配、缩放图片或调用原生状态。下面的 `YOUR_EXPLICIT_SERIAL` 必须换成用户指定设备：

```bash
.venv-cloud/bin/python -m arena_lab.device_live \
  --serial YOUR_EXPLICIT_SERIAL --checkpoint arena_lab/artifacts/pixel-masked-v1.pt \
  --output outputs/my-physical-pixel-attempt
```

实际运行时间未知，因此跟踪及模型记忆按缺测处理，不能声称 0.5 秒逻辑动作窗口已在物理设备校准。这个入口的 `--max-age-ms` 从截图检查开始计时，包含截图与推理，比暂停原生诊断的年龄定义更保守。没有独立消费回执，所以**首个出牌尝试后的截图一旦取得即停止**：即使手牌变化与圣水下降一致，仍记 `game_consumed=null`，不继续重复点击。截图或传输失败保留未结请求。这个有界入口供部署标定与回执验证，尚不是完整自动实战；当前真实物理设备调用次数为 0。

可选 `--capture-format raw-persistent` 保留一个 `adb shell -T su 0 sh -s` 无 PTY 双向会话，每次仅发送固定 `screencap` 命令，仍逐帧执行截图，保持 360×640 和像素内容。`exec-out` 只转发输出，不能用于需要继续输入指令的持久会话，这一边界来自 [Android ADB 官方实现](https://android.googlesource.com/platform/packages/modules/adb/+/refs/heads/main/client/commandline.cpp)。流按 Android 12 的 16 字节头及确切 RGBA 字节数拆帧；超时／半帧／格式变化会关闭并标记会话失效，不偷偷重连重读。接口测试覆盖分块输送、连续两帧不串帧、半帧和超时。11 月 13 日先取得一张实际加载黑帧，耗时 5.494 秒；11 月 14 日又完成暂停入场场景的两种输送各 3 次采集，详见后文。**可见 HUD 战斗画面上的配对速度验收尚未完成**，不能从减少连接次数推出吞吐已达标。

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

没有本方出牌请求的窗口现在只调用一次 `advance-native 10`，对手排队动作仍由原生逐 tick 调度；有本方请求时仍先推进 1 tick，取得独立消费回执，再补 9 tick。这样减少不必要的暂停边界，没有改变策略采样间隔。`--render-speed` 只接受 0.25／0.5／1／2／4，默认 1；非默认值在每次 reset 完成后设置，回执必须确认相同 tick、暂停状态和正确倍率，否则停止。它改变原生播放倍率，**不改变 20 Hz 的逻辑时间单位或模型 10 tick 的动作窗口**。初始化到 tick 90 仍按 reset 的 1 倍执行，随后实际倍率及回执保存在 `render_control`。

`--max-age-ms` 拒绝的是从截图传输结束到发请求的宿主额外年龄；采集本身耗时另列 `capture_ms`，显示渲染年龄尚未知。正式实时验收需独立测完整端到端 p50/p95、漏检／误检、跨帧 ID 错配、截图 freshness、动作消费和自然终局，不应把该参数当作已经满足实时 SLA。

## 2026-11-13 的实际结果

原始证据在 `outputs/arena-next-20261113/device-evidence/report.json`，本轮运行固定指纹为 `7d62f3e6385490b06ff1d376f5660efda4e7603319f963540449522b96c359b6`。

第一轮 VM 完成 Android 启动和游戏进程启动，内容根指针发布，但 `contentReady` 连续 900 秒未到达，准备于 15:20:11 UTC 失败。第一次恢复在 display-compat 读取超时；第二次恢复已 force-stop 游戏，但 check-app 读取在 15:23:11 UTC 超时。最后一条设备操作与约 15:35 UTC 的容器重建相隔约 12 分钟。没有证据证明重建原因，不能写成 OOM；VM 日志为空，旧进程已经消失。

截至 **11 月 13 日首轮准备失败时**，确实取得 Android 加载黑帧：PNG 路径一次 11.581 秒，raw RGBA＋宿主 PNG 一次 7.403 秒。它们是不同调用、不同时间的两次测量，不能当成配对加速基准，更不能当成战斗帧吞吐。**该首轮**新原生战局数、动作请求数、战斗帧数均为 0；当时真实截图闭环、学习策略设备闭环、实时门槛均未通过。11 月 14 至 15 日的新增画面和消费另列于后文。

另外对历史同指纹 7 帧进行了开发诊断。修正 HUD 误检区域、蓝方颜色识别和圣水分隔线后，6 个能粗略匹配的单位里类型正确 3 个、颜色侧别正确 6 个；其中炮被识别成哥布林牢笼、吹箭哥布林被识别成弓箭手。平均坐标误差与时钟绑定尚未经独立校准，样本又参与过修正，因此这些数值**不是独立测试准确率**。历史记录见 `device-historical/development-audit.json`。初始 VS 画面现在必须先检测到手牌和圣水 HUD，才会把目标交给策略。

最近的开发测试覆盖未知数据、请求与执行区别、顺序与超时、跟踪过期、像素无损、布局拒绝和等级证据。测试通过只证明这些接口边界，不替代新设备闭环试验。

第二轮低资源 VM 的准备于 17:25:42 UTC 开始，内容轮询在 17:37:35 UTC 首次同时读到 `contentReady=true`、`coldReady=true`，轮询历时 390.015 秒。因此这次并非内容加载超时。后续 identity 首次读取的 PID 为 1883，但在哈希检查前的 PID 守卫以 exit 32 拒绝；输出为空，尚未开始读取安装文件哈希。旧日志只能证明包的当前 PID 已不等于 1883，不能区分进程退出、重启或多 PID，也不能写成资源哈希错误。

两次只读线程采样之间，Mainloop CPU 增加 1694 tick，映射数增加 63，RSS 增加约 52 MiB，进程读取也有进展；`gfx_frames` 未变化。这些数据支持“加载仍有活动”，不能凭内容指针未变化判为死锁。同一时间段曾运行 `debuggerd -b`，17:37:43 日志有 dump 请求，与 identity 检查交叠；尚无因果证据。详见已提交的 [第二轮审计](../docs/audits/arena_next_20261113/device/post_rebuild_result.json)。

为保留下次失败原因，identity 的 PID 守卫现在输出预期／实际 PID，以及这些 PID 可读的 `/proc/PID/stat`；进程消失、变更或多 PID 仍立即拒绝，不自动重新采样、重试哈希或写 connection。13 项 identity 测试通过，包含进程消失和多 PID 的真实 shell 守卫测试。2026-11-14 14:16:31 UTC 的新容器中旧 QEMU／ADB 均已不存在；重建原因未知，新尝试使用新 attempt 与全新 identity，不复用旧连接。

## 11 月 14 至 15 日的后续实测

11 月 14 日重新准备后的暂停 generation 1／tick 90 场景，交替采集 raw 与 raw-persistent 各 3 次。raw 三次为 6.988／2.767／2.718 秒，persistent 为 5.660／4.449／2.192 秒，均保存实际像素及状态哈希。raw 中位数 2.767 秒、persistent 4.449 秒；这批小样本**不能证明 persistent 更快**，也没有稳定 p95 或可见 HUD 战斗画面的速度验收。

| 实验 | 实际完成 | HUD 与本方动作 | 结论 |
| --- | --- | --- | --- |
| 11 月 14 日、1 倍、1+9 tick | 12 次决策／13 帧，tick 90→210，210.833 秒 | HUD 0 帧，12 次 WAIT，0 请求 | 有界接口循环完成，作战未开始 |
| 11 月 15 日、新进程首场、1 倍、WAIT 合并 10 tick | 12 次决策／13 帧，tick 90→210，301.303 秒 | 首帧黑屏，随后 VS；HUD 0 帧，0 请求 | 合并边界本身尚未解决遮挡 |
| 11 月 15 日、同进程新场景、0.25 倍、10 tick | **中断于 12/24 次决策**，13 帧，tick 90→210 | frame 6／tick 150 起有 7 帧真实 HUD；2 请求、2 独立消费 | 已实际执行截图策略的动作，完整预算与独立复测未完成 |

11 月 15 日的全新准备于北京时间 09:51:55（01:51:55 UTC）完成，内容轮询 315.4 秒到达 ready，fresh identity 的 PID 为 1783，65.19 秒稳定性窗口 9/9 次检查通过。两个新场景使用同一份已训练像素权重、11 级配置请求、20 Hz 时钟与 10 tick 动作窗口，没有修改测试后阈值。0.25 倍实验的实际回执为 tick 90／paused=true／speed=0.25。

以下 frame 编号从 0 计数。0.25 倍实验 frame 6 的真实画面可见双方手牌、圣水、FIGHT 和战场单位。模型从截图识别巨人手牌，选择落点后由离线原生 RPC 执行；tick **151 的巨人、171 的弓箭手**均匹配唯一原生消费事件，且有同槽换牌证据。巨人的后续截图同时支持换牌与圣水下降；弓箭手的圣水下降可见，换牌识别为 unknown，因此两次中仅一次满足完整视觉一致性。frame 12 可见计时 2:50、双方 HUD 和移动后的巨人／弓箭手，VS 已消失。原图与完整小型账本见[便携审计](../docs/audits/arena_next_20261113/device/render_20261115/summary.json)、[首次 HUD](../docs/audits/arena_next_20261113/device/render_20261115/quarter-frame06.png)、[首次消费后](../docs/audits/arena_next_20261113/device/render_20261115/quarter-frame07.png)、[最后保存帧](../docs/audits/arena_next_20261113/device/render_20261115/quarter-frame12.png)。这些开发帧不能作为独立识别准确率测试。

该保存前缀从首帧采集开始至末帧采集结束为 **310.64 秒**，其中模型只经历 6 秒受控逻辑时间；13 次截图中位数 **2.918 秒**、范围 2.667–3.253 秒，12 次推进中位数 **20.466 秒**。整个进程耗时因 finally 未完成而未知。它说明动作链确实被执行过，同时表明当前软件 VM 远不能满足实时实战。

后续宿主再次重建，当前 PID 1 起于北京时间 10:11:15（02:11:15 UTC），QEMU、ADB 和实验进程均已不存在。原 `report.json` 的 `status=running` 原样保留；新增[中断记录](../docs/audits/arena_next_20261113/device/render_20261115/interruption.json)标明只完成 12/24、最后状态非终局、未运行独立种子复测、未重发动作，重建根因未知。保存前缀中的两笔请求均有消费与视觉尝试账本，最后回执之后是否存在未记录的原生推进仍未知。

源码与固定库只读审查确认：full update 给原版逻辑 clock 的 delta 先被钳到 0.1 秒，再乘播放倍率；后续另一个未命名组件收到未钳 delta 及倍率缩放 delta。实测日志也出现 1 倍下 full delta 8.461454 秒而 clock delta 0.1 秒。该组件是否负责 VS 动画尚未定位；不同初始化、场景 generation 与倍率同时变化，不能把本次恢复 HUD 归因为单一参数。原始探针和库未修改，详见[协议审查](../docs/audits/arena_next_20261113/device/render_timing_protocol_review_20261115.json)。

便携证据可独立核验，不需要 Android 或视觉依赖：

```bash
.venv-cloud/bin/python docs/audits/arena_next_20261113/device/render_20261115/verify.py
```

原工作区的 13 张 PNG、对应状态哈希和 12 个时间窗口已逐项核验；Git 随附 3 张原图。便携工具核对 21 个绑定文件、13 条帧元数据和两笔消费账本。18 项设备契约测试通过。**自然整局、独立短测、画面与状态的时间对齐、全部实际卡等、物理手机和官方实时实战仍未验收。**

## 后续集中验收顺序

下次继续时先确认新 VM／新进程身份，不复用本次已经失效的 connection。当前已经取得真实 HUD、像素策略出牌与两次独立消费，接下来应完成固定参数的完整短测预算和独立新种子复测，再校准显示时延、扩展自然整局；无需为证明接口而反复跑全 WAIT。若内容准备再次失败，保存 Android binder／Mainloop 活性、内容 ready、渲染与文件读取证据，定位具体阻塞再改工具。

已经逐项对比 11 月 7 日成功与 11 月 13 日失败的启动参数：同一个 10 月 20 日 mutable RAW ext4、2 vCPU、3072 MiB、TCG TB64、5 FPS、内核、9p 与 initramfs；initramfs SHA256 均为 `6df24b5b61c9d865ec2a258d28447b46ed628c6b74e26193b7e17fe6f3058821`，不存在新 qcow overlay 链差异。两个历史成功内容轮询窗口为 295.6 秒与 314.2 秒。旧 debuggerd 中 Java 主线程在 epoll、SwiftShader 队列等候任务，ARM Mainloop 无可解析客体栈、两个翻译工作线程位于 mmap；单点栈不能判定死锁或根因。

重试在内容状态持续约 4 分钟无变化时，使用下列有界只读诊断采两轮，结合线程 CPU／wait channel／syscall、进程 IO／maps 数、SurfaceFlinger 层与帧统计判断是否有进展。它不会启动或重启进程，不复用历史 PID，也不改 ready 位；访问失败和超时保留原始输出。仍需解释实际两轮结果，不能把工具存在当作根因已解决：

```bash
.venv-cloud/bin/python -m arena_lab.device_diagnostics \
  --serial 127.0.0.1:15555 --output outputs/my-loading-diagnostics \
  --samples 2 --interval 30 --timeout 90
```

进局后先用新截图独立标注 HUD 与单位，实测 fixed-8 手牌识别，修正 detector 的类混淆，并把开发标定帧与验收整局分离；之后运行短规则诊断与同输入的学习策略。配对审计再校准显示时延，最后做多种子完整自然终局、动作确认和 p95 延迟验收。缺少物理设备时保留物理设备验收未通过，Android 软件 VM 的结果独立报告。

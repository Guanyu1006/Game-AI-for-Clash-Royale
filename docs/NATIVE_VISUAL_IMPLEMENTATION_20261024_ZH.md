# 原生画面与回放实现、验证和剩余阻塞

**最新实际结果（2026-10-24）：** 92 事件公开样本的部分对局验证已通过，真实浏览器跑通导入、打开、播放、暂停、保存及只读回看流程。初手对齐经过两次尝试后在原生 tick 90 完成，暂停任务成功结束于 tick 670／generation 2；已执行的前 7 条来源动作全部确认原生消费，0 条未确认，包括 tick 143 的 Hog 与 tick 199 的 Rocket。[tick 190 原始 PNG](../outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/frames/000001.png)与[tick 670 原始 PNG](../outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/frames/000006.png)已人工看见真实战场单位及塔血变化。当前局保存成功，7 帧只读回看不改变原生时钟，页面 JavaScript 错误为 0。[真实浏览器验收记录](../outputs/cloud/import-play-display-20261024-003/live-browser/report.json)为 `passed`，同目录保留 Android 原始暂停画面、浏览器暂停页和保存帧播放器截图；[执行反馈](../outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/source-execution.json)保留消费证据。7 帧已无损导出为 [29.05 秒 APNG](../outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/animation.png)，[CLI 校验与导出报告](../outputs/cloud/native-archives/source-20261024t073115.952441z-b08237/export-report.json)通过；[总体验收记录](../outputs/cloud/import-play-display-20261024-003/acceptance.json)将结果限定为部分对局验证。完整 92 事件、自然终局与前后向原生重演仍待验证，Windows 镜像尚未在用户本机验收。操作方式和现行参数见[原生视觉指南顶部](NATIVE_VISUAL_ZH.md#公开事件-json导入执行与显示)。

**历史手动进展（06:27–06:31 UTC）：** 16 GB／4 核升配生效后，06:27:20 UTC 的准备报告已通过内容与身份检查；但 PID 1978 于 06:27:36 因 Android ANR 退出，VM 3394 仍运行。浏览器 06:31:06 的首次建局只尝试了 `status`，并未调用 `configure-native`。这次实际退出原因与之前宿主内存关联的 VM SIGTERM 不同。[诊断报告](../outputs/cloud/native-render-diagnostic-20261024-001/report.json)包含只读 Android 退出信息与图形线程栈，支持单核软件图形编译／调度超过输入门限的解释；当时提出 2 vCPU／倍率 20 的[恢复步骤](NATIVE_VISUAL_ZH.md#当前故障准备成功后游戏因-anr-退出)。传输错误阶段、页面错误提示及准备后存活观察的实现检查不能替代视觉验收。下文首次实现的 VM 停止状态与测试计数按当时记录保留。

**历史宿主内存诊断：** 用户上传 Codespaces 日志后，六次终止均与宿主低可用内存相关，宿主还存在 earlyoom；高度指向用户态内存保护，未直接确认发送者。当时选择优先升至 16 GB 并新增 doctor 内存字段，详见[诊断记录](CODESPACES_MEMORY_DIAGNOSIS_20261024_ZH.md)。下面的五次尝试与测试计数保留为首次实现验收时的记录，不是后续的新实机结果。

首次实现基于 `dev/main-start-20261020`、提交 `f2cdd1355f408d6e216799bc32ca7acd7d982291` 开发。当时远程 `main` 为 `2d4b0a6002be4bcb02c092290a03a08b4791517a`，与本地 `origin/main` 相同，且是当时 HEAD 的祖先。该次没有重置、合并或推送分支。

**首次实现验收结论（历史）：浏览器控制、PNG 归档、离线播放器和原生动作重演代码已实现，当时尚未通过真实视觉验收。** 该次 Android 在内容就绪前被 SIGTERM 终止，实际截图为加载阶段全黑。历史六局是十月二十日的数值闭环证据，不能填补视觉验收。后续已取得本页顶部的实际画面与动作消费证据，完整逐步命令见[原生视觉指南](NATIVE_VISUAL_ZH.md)。

## 实现调用链

```mermaid
flowchart TD
    Browser[浏览器 8772] -->|POST 建局 下牌 推进 暂停| HTTP[native_browser.py HTTP 接口]
    HTTP --> Queue[单 worker 队列与原生会话锁]
    Queue --> Backend[NativeBattleBackend rendered=True]
    Backend --> Probe[固定版本 Android 探针]
    Probe --> Game[Null’s Royale 原生库与渲染客户端]
    Game -->|ADB screencap 原始 PNG| Frames[PNG 及 tick / 状态绑定归档]
    Frames -->|HTTP 图片| Browser
    Queue --> Journal[双方登记命令 原始状态 消费 终局]
    Journal --> Replay[CloudNativeReplay 重置并重演前缀]
    Replay --> Backend
    Frames --> Offline[无需 Android 的保存画面播放器 / APNG]
```

| 源码入口 | 输入 → 输出和行为 |
|---|---|
| [`native_prepare.prepare`](../tools/cloud/native_prepare.py) | 固定 manifest、明确 ADB/serial、新输出目录 → 启动检查、内容就绪、当前身份、connection；不建局。默认不重启，`--restart-app` 才显式结束当前游戏进程。失败保留阶段日志，不自动重试下牌或重启。 |
| [`native_browser.make_server`](../tools/cloud/native_browser.py) | JSON POST → `202 + job_id`；状态 GET 只读缓存，不推进游戏。会话令牌、同源和 Codespaces 转发主机检查。 |
| [`CloudNativeSession.submit/_worker`](../royale_phase2/cloud_native.py) | 建局／下牌／推进／回放任务 → 单线程 RPC、手牌与消费反馈、归档编号。旧 generation／过期手牌不得直接重新发送。终局、截断分别记录。 |
| [`NativeBattleBackend`](../royale_phase2/backend.py) | 固定 match、原生整数坐标、指定 tick 命令 → 探针状态、原始事件、journal；新增入口复用该后端，不引入另一套战斗物理。 |
| [`CloudAndroidBridge.capture/frame_info`](../royale_phase2/cloud_frames.py) | 显式 `adb exec-out … screencap -p` → 未改动 PNG、CRC/SHA256、尺寸、像素诊断和耗时；非黑不能单独证明游戏内容正确。 |
| [`CloudNativeSession._capture/_save`](../royale_phase2/cloud_native.py) | 暂停后的原生状态与 PNG → 帧索引、语义状态哈希、双方命令、消费与终局。截图前后核对 tick/generation；该核对不能证明呈现延迟为零。封存索引与来源文件按哈希检查。 |
| [`NativeReplaySource.load/CloudNativeReplay.seek`](../royale_phase2/cloud_replay.py) | 同版本 journal、初始化、原始状态、feedback/result → 在原始登记 tick 注册命令，比较保存检查点；向过去跳转则 reset 后重演。仅归一化 generation 和事件序号起点。 |
| [`native.html`](../tools/cloud/native.html) | 当前状态、手牌与真实 PNG → 操作面板、落点输入盘、原生时间线及保存图片播放器。坐标盘不是游戏画面；截图没有标定时禁用截图点选下牌。 |
| [`validate_frame_index/export_apng`](../royale_phase2/cloud_frames.py) | 原始 PNG、帧索引及状态绑定 → 校验报告／无损 APNG，使用原 tick 间隔，不插值，不补造历史画面。 |
| [`native_visual_check.NativeVisualClient`](../tools/cloud/native_visual_check.py) | 已运行的本地服务 → 有预算的 smoke、完整采样 record、原生 replay 检查；每个变更 POST 只发一次，后续轮询 job，不因超时重发。 |

训练仍使用已有的阶段三 GRU／模仿学习链；本轮没有启动新训练，也没有把视觉代码的测试计入棋力提升。

## 首次实现的真实证据（历史）

| 检查 | 结果与证据 |
|---|---|
| 开发环境 | 仓库 Python 3.12.11；NumPy、Torch、PyArrow、Gymnasium 可用；572 个可移植资产哈希通过。[doctor](../outputs/native_visual_20261024/final-doctor.json)。 |
| 可移植回归 | 全部 305 项通过，0 失败／错误。[完整日志](../outputs/native_visual_20261024/final-portable-tests-002.stderr.log)。这些是实现契约和保存数据检查，不是新的原生对局。 |
| 回放／帧／会话／HTTP | 63 项通过，真实 Android/ADB/RPC 调用为 0。[范围与结果](../outputs/native_visual_20261024/final-contracts-002/report.json)。准备流程另外 10 项模拟检查通过，[结果](../outputs/native_visual_20261024/prepare-contracts-001/report.json)。 |
| 实际浏览器离线入口 | Chromium 134，4 项检查通过，0 页面 JS 错误。读取真实历史原生归档元数据、禁用引擎控制、明确拒绝缺失历史图片。[结果](../outputs/native_visual_20261024/offline-real-source-browser-001/report.json)。不属于真实对局画面验收。 |
| 历史数据兼容性 | 新加载器只读核验全部六局原始来源、初始化、动作时序、保存状态与自然终局，未连接引擎。[结果](../outputs/native_visual_20261024/historical-source-validation.json)。 |
| Android 与游戏启动 | 最后一次 BOOT=1，游戏启动命令成功；未等到新的内容就绪和身份验证。[准备报告](../outputs/native_visual_20261024/attempt-001/prepare-006/report.json)。报告最后的 KeyboardInterrupt 是 VM 已退出后主动停止无效等待，不能与之前 QEMU 收到的 SIGTERM 混为一谈。 |
| 实际 PNG 采集 | 360×640，1003 字节，CRC 正确，100% 黑色；单次采集约 20.87 秒。[回执](../outputs/native_visual_20261024/attempt-001/lowcache-system-capture.json)、[原始图片](../outputs/native_visual_20261024/attempt-001/lowcache-system.png)。它采于加载阶段，没有建立 rendered 对局，因此既不能证明渲染成功，也不能据此断言渲染引擎永久故障。 |
| 中断现场 | QEMU 收到 `SIGTERM / SI_USER / si_pid=0 / si_uid=61876`，[最后一次信号日志](../outputs/native_visual_20261024/attempt-001/vm-low-cache-signals.log)。容器内不能定位发送方，不能归因于某个具体服务。[内存采样](../outputs/native_visual_20261024/attempt-001/low-cache-resources.jsonl)末值约 1.49 GiB RSS，64 MiB TCG 缓存仍未避免中断。 |

首次实现验收合计五次软件 VM 尝试，保留失败现场；最后两次直接捕获到上述终止信号。没有通过绕过终止信号继续运行。该次结束时 VM 已退出，没有留下真实对局控制者或新的训练任务；后续运行状态与新证据见本页顶部。

## 距离用户要求还差什么

1. **整局运行稳定性。** 当前 Codespaces 通过软件 TCG VM 提供 guest Binder，后续已完成真实原生初始化和前 7 条动作消费；这段运行窗口仍不能代替整局稳定性验证。历史外部 SIGTERM 与后来的 guest 退出分别保留证据，不混为同一原因。
2. **整局的实际画面覆盖。** 本次已保存并只读回看 7 帧，tick 190 与 tick 670 的原始 PNG 可人工辨认真实战场单位及塔血变化；这些证据覆盖已完成的短前缀，尚未覆盖完整对局。黑屏和非黑加载界面不算战场证据。
3. **完整来源执行与人工操作分别验证。** 公开文件的前 7 条动作已有原生消费，完整 92 事件及自然终局尚未验证。在真实页面人工选择手牌、提交坐标的完整操作链也不能由来源自动执行代替。
4. **完整画面覆盖与原生重演。** 本次短前缀的 7 帧保存、只读回看和无损 APNG 导出已通过；仍需从初始化 tick 90 到自然终局保存指定密度 PNG，并将来源重演至终点、回退至初始化、再次重演至终点，核对状态、消费、终局。历史没有保存的图片不能补称“当时录像”。
5. **流畅录像与呈现标定。** 统一入口默认每 100 tick 保存一次，即每 5 游戏秒约一张；直接运行浏览器服务仍默认每 20 tick 一张。新启动 VM 默认 3072 MiB guest RAM／5 FPS 显示，已有 VM 保留原设置，实际出帧受软件渲染与 TCG 性能限制。`capture-ticks=1` 是逐逻辑 tick 采样，不能保证 20 张新画面；scrcpy 提高传输上限也不会提高源渲染帧率。归档将 `full_20hz_samples` 与尚未验证的 `full_20hz_frames/fresh_frame_rate_verified` 分开。截图落点标定、稳定高帧率传输、音频和完整加载过程录像不在已交付范围；Windows 镜像还需本机验收。

“实时操作”在本实现中是持续推进期间接受人工动作，软件 TCG 不保证与现实时间同步。暂停画面逐张采集方式也不等同低延迟视频流。首次视觉尝试取样的 ADB 截图接近 21 秒，该历史延迟不能代表后续内容就绪后的性能，也不能据此承诺浏览器实时视频体验。

## 继续验收与替代运行位置

按照[逐步指南](NATIVE_VISUAL_ZH.md)准备到 `status=prepared` 并启动 8772 后，先只运行短检查：

```bash
.venv-cloud/bin/python tools/cloud/native_visual_check.py smoke \
  --base http://127.0.0.1:8772 \
  --output outputs/cloud/native-visual-smoke-001 --wall-seconds 600
```

短检查通过并人工查看 PNG 后，再按实际耗时选择完整局预算。以下命令必须各自使用新输出目录；它们不会启动大规模训练：

```bash
.venv-cloud/bin/python tools/cloud/native_visual_check.py record \
  --base http://127.0.0.1:8772 \
  --output outputs/cloud/native-visual-record-001 --wall-seconds 1800

.venv-cloud/bin/python tools/cloud/native_visual_check.py replay \
  --base http://127.0.0.1:8772 --archive 实际归档编号 \
  --output outputs/cloud/native-visual-replay-001 \
  --wall-seconds 1800 --job-seconds 1200
```

预算耗尽只表示本次未完成，不应改成自然终局成功；长局可先根据短检查实测耗时决定下一次预算。

如果本 Codespaces 的 VM 继续被终止，可把 Android 放到用户持有的稳定主机上，Codespaces 保留代码、训练和浏览器服务。这台主机需要：已安装且有 root 的兼容 Android、固定版本 APK／探针／更新资源、可运行的游戏渲染与明确 ADB 设备。普通最新版 Null’s APK 不能直接代替本项目已固定版本，因为 ABI、函数地址和版本指纹可能不同。现有安装工具和更新包入口见[安装参考](NATIVE_LOOP_ZH.md#重建环境时的安装与历史恢复参考)。

已有主机可使用[原生连接指南](NATIVE_CONNECTION_ZH.md)的 loopback／SSH 转发路线。视觉功能除探针端口外还需要明确设备的 ADB 截图通道；只转发 26789 无法得到图片。先保证 Codespaces 的显式 ADB 能访问该设备，再用相同 `--adb-port/--serial/--root-shell` 执行准备和浏览器服务，重新核对当前进程身份。外部主机也必须重新完成同一套视觉验收，不能继承本机历史成功标记。

本次没有使用外部 Android 主机连接，不声称已部署或已在另一台主机跑通。现有源码、资源和已安装磁盘均保留；Codespaces 内已取得的实际画面与动作消费见本页顶部，完整视觉覆盖、原生重演和本机 Windows 镜像仍待验收。

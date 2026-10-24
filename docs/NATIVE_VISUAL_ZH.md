# 原生画面、浏览器操作与引擎回放

## 公开事件 JSON：导入、执行与显示

在 Codespaces 仓库终端执行一条命令：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py play-replay
```

入口会检查端口和原生控制锁，复用现有 VM；只有已停止且安装盘身份可核对时才恢复原盘。随后串行准备 Android 与游戏，启动 `8772/native`。不要同时启动旧训练器或第二个原生控制器。终端需保持运行，`Ctrl+C` 停止本入口的页面，VM 和安装盘保留。

也可启动时导入仓库内的单局文件：

```bash
.venv-cloud/bin/python tools/cloud/workspace.py play-replay --source data/my-replay.json
```

在页面的“导入公开对局”中选择 JSON，点击“导入 JSON”→“打开对局”→“播放”。打开前只处理文件；打开时才创建原生对局。双方出牌均来自记录，不加入规则对手。暂停、单步与跳转共用同一原生 worker；后退通过从头计算实现。初始化需要等待场景和初手对齐，页面显示运行阶段，不自动重复提交失败动作。

支持 IL_Replay 的单行 `payload_json`、结构化 `battle/events` 和本项目转换后的 source episode；每份文件最多 2 MiB。原文件、转换计划与假设保存在 `outputs/cloud/public-replays/`，本次实际执行、动作反馈与图片保存在 `outputs/cloud/native-archives/`。无法识别的牌、缺少方向、混合等级及无法确定来源的技能会明确报错，不静默删动作。缺少官方初始状态只显示本次使用的假设，不要求额外确认；是否像原比赛由观看者判断。

左侧显示 Android 原始截图。要在本机窗口观看，展开“在本机打开 Android 镜像”，下载脚本，在**本机 Windows PowerShell**执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\android_mirror.ps1
```

脚本已填写当前 Codespace，默认仅镜像屏幕，不向游戏发送触摸或按键。需要本机官方 portable scrcpy 和已登录的 GitHub CLI；缺少工具时脚本打印确切位置与官方链接。完整说明见[Android 镜像指南](ANDROID_MIRROR_ZH.md)。云端不启动本机图形窗口；浏览器截图和本机镜像是两种显示入口。

统一入口新启动 VM 时默认使用 2 vCPU、3072 MiB guest RAM、5 FPS 和超时倍率 20；复用已运行 VM 时不更改参数。`--memory-mib`、`--display-fps` 只影响下一次启动。默认 `--display-compat` 在选定 Redroid guest 内关闭 cached-app freezer、保持唤醒，并保存原设置与恢复脚本；这是针对本机 Binder 冻结失败的兼容配置，不修改宿主、APK 或原生库。可显式使用 `--no-display-compat` 关闭这一步。

只做离线转换可用 `.venv-cloud/bin/python tools/cloud/public_replay.py 单局.json --output 全新目录`。转换不会连接 Android；画面、模型训练和棋力不由转换成功推导。下面保留早期故障诊断与逐步操作记录。

本页说明新增的 Codespaces 原生页面，入口为 `tools/cloud/native_browser.py`，默认端口 **8772**。它与 8771 的保存 fair 观测／近似沙盒是两套入口。当前接入固定版本 Null’s Royale 离线客户端的原生库和探针，不连接官方服务器，也不代表 Supercell 官方 SDK 或官方交互等价。

**当前手动进度（2026-10-24）：** 已升配为 4 核／16 GB，游戏资源与当前进程身份于 06:27:20 UTC（北京时间 14:27:20）通过准备检查。但该游戏进程约 16 秒后因 Android 界面无响应（ANR）被结束；浏览器首次建局只尝试了 `status` 检查，尚未发送 `configure-native`，所以本次仍没有原生对局画面。先按下面的[ANR 恢复步骤](#当前故障准备成功后游戏因-anr-退出)操作，不能只等待网页或反复点建局。

**此前宿主内存问题：** 六次 QEMU SIGTERM 均发生在旧宿主可用内存约 800–960 MiB 附近，且宿主存在 earlyoom；内存保护是高可信推断，发送者缺少直接服务日志证明。见[内存诊断与升配步骤](CODESPACES_MEMORY_DIAGNOSIS_20261024_ZH.md)。本次 ANR 核查时 QEMU 3394 仍在运行、宿主可用约 10.69 GiB，与此前 VM 被终止是不同事件。

**本轮实际视觉验收未通过（2026-10-24）。** 已从真实 Android 取得 360×640 PNG，但像素全黑，不能称为游戏画面成功。此前自动诊断的最后一台 VM 在游戏内容就绪前再次收到 SIGTERM；信号来源未知，已停止自动重启。没有本轮真实游戏画面、浏览器下牌或原生视觉重演成功证据。页面与接口实现、浏览器夹具检查通过，不能替代原生视觉验收；原有六局数值闭环也不能替代。

实际证据：

- [截图诊断](../outputs/native_visual_20261024/attempt-001/lowcache-system-capture.json)：ADB 成功返回 PNG，`non_black=false`、`pixel_variance=0`、`game_content_verified=false`，且没有原生 tick 绑定证明。
- [VM 信号记录](../outputs/native_visual_20261024/attempt-001/vm-low-cache-signals.log)：`SIGTERM`、`si_pid=0`、`si_uid=61876`，不足以识别发送源或根因。
- [准备中断报告](../outputs/native_visual_20261024/attempt-001/prepare-006/report.json)：`status="interrupted"`，游戏启动命令完成，但整体准备未到 `prepared`，未形成本轮可用连接和视觉验收。

以下保留复现与继续开发的手动步骤，**不表示当前环境已能成功走完全程**。应先查明 VM 稳定性和渲染条件，再做真实验收；改用用户交互终端只能作为诊断尝试，不能保证解决 SIGTERM 或全黑问题。

## 当前故障：准备成功后游戏因 ANR 退出

本次的[只读诊断报告](../outputs/cloud/native-render-diagnostic-20261024-001/report.json)保留 Android 日志、进程退出记录、ANR 线程栈、VM 状态和文件 SHA256。以下时间均为北京时间，原始日志使用 UTC：

| 时间 | 实际事件 |
|---|---|
| 14:27:20 | 准备工具输出 `prepared`，身份对应游戏 PID 1978。 |
| 14:27:36 | Android 结束 PID 1978；`exit-info` 明确记录 `reason=6 (ANR)`。 |
| 14:31:06 | 浏览器建局的第一个外层 `status` 请求失败，约 0.114 秒连接提前结束；尚未发送建局或推进。 |

[`anr-trace.txt`](../outputs/cloud/native-render-diagnostic-20261024-001/anr-trace.txt)显示：主线程等待 HWUI 渲染结果，HWUI 线程等待 ANGLE／Vulkan 队列，Vulkan 渲染线程仍在执行 LLVM 着色器编译。[主日志](../outputs/cloud/native-render-diagnostic-20261024-001/main-log.stdout.txt)记录焦点事件最终耗时 84,375 毫秒，但已经超过约 50 秒输入超时，系统已决定结束应用。这支持“单 vCPU 软件图形编译与调度太慢”的解释，不能据此断定永久死锁。`user request after error` 是 Android 的退出描述，不能单凭该字符串认定用户点击了强退。

恢复采用 **2 vCPU、超时倍率 20**，保持原有 1536 MiB guest RAM、64 MiB TCG 缓存、软件渲染配置和安装磁盘。Android 12 的输入超时会乘以 `ro.hw_timeout_multiplier`，见 [AOSP InputConstants](https://raw.githubusercontent.com/aosp-mirror/platform_frameworks_base/android-12.0.0_r1/core/java/android/os/InputConstants.java)。本次预期预算从约 50 秒延至约 100 秒；多核 TCG 与较长预算仍须实测，不能承诺修复。不会关闭 ANR 检测，也不改锁定 APK／探针／素材。

**第一步：** 在正在运行 `native_browser.py serve` 的终端按一次 `Ctrl+C`，等待命令提示符回来。保留旧失败归档，不再点击该页建局。

**第二步：** 在同一仓库停止记录中的 VM，按新参数恢复同一已安装磁盘。每条命令成功后再执行下一条；若返回错误就保留输出，不继续启动第二台。

```bash
cd /workspaces/Game-AI-for-Clash-Royale
LAB_VM_ATTEMPT="$(.venv-cloud/bin/python -c 'import json; print(json.load(open(".cache/native-tools/android-recovery-001/active-vm.json"))["attempt"])')"

.cache/native-tools/bin/adb -H 127.0.0.1 -P 5038 \
  -s 127.0.0.1:15555 shell su 0 sync

.venv-cloud/bin/python tools/cloud/software_android_vm.py stop \
  --cache .cache/native-tools/android-recovery-001

nice -n 10 .venv-cloud/bin/python tools/cloud/software_android_vm.py start \
  --cache .cache/native-tools/android-recovery-001 \
  --output outputs/cloud/avm --vcpus 2 --timeout-multiplier 20 --tb-size-mib 64 \
  --resume-attempt "$LAB_VM_ATTEMPT"

.venv-cloud/bin/python tools/cloud/software_android_vm.py status \
  --cache .cache/native-tools/android-recovery-001
```

核对新记录为 `vcpus=2`、`timeout_multiplier=20`、`memory_mib=1536`、`tb_size_mib=64` 和 QMP `status=running`。启动工具会拒绝仍被占用的恢复磁盘，不需要重装游戏。

**第三步：** 使用新目录重新准备，旧连接中的 PID 已失效：

```bash
LAB_VISUAL="outputs/cloud/native-visual-$(date -u +%Y%m%dT%H%M%SZ)"
nice -n 10 .venv-cloud/bin/python tools/cloud/native_prepare.py \
  --manifest outputs/native_loop_20261020/local-runtime-version-001.json \
  --output "$LAB_VISUAL/prepare" --stability-seconds 60 \
  --adb .cache/native-tools/bin/adb --adb-port 5038 \
  --serial 127.0.0.1:15555 --root-shell su0
```

新增 `runtime-stability` 阶段在完整身份检查之后观察 60 秒，校验原 PID／启动时间和 `coldReady/contentReady`；发生退出、换进程或连接错误会保留失败且不生成连接，不自动重启。`--stability-seconds 0` 只做即时最终检查，不能算稳定观察。观察通过仍只证明这段窗口内的存活与资源状态，不证明图形可响应、未来始终存活或原生建局成功。

先核对新的 `prepared` 与 `runtime_stability` 结果，再按第 3 节用 **这个新连接**启动浏览器，进行一次明确的建局。原生视觉验收仍要实际截图、动作消费、终局与重演证据。

本次修补的检查结果：准备／传输／身份／连接 [66 项通过](../outputs/cloud/runtime-stability-contracts-20261024-001/report.json)，传输／启动／VM 工件／HTTP [58 项通过](../outputs/cloud/native-anr-recovery-checks-20261024-001/report.json)，两组包含重复传输测试；独立 HTTP＋Chromium 的[4 项错误提示检查](../outputs/cloud/native-ui-error-20261024-001/report.json)通过。这些检查使用本地夹具，没有操作用户的实际 VM 或原生对局，也不证明恢复参数已经通过实机验收。

## 三种操作分别做什么

| 页面功能 | 是否运行 Android／原生引擎 | 看见什么 | 拖动时发生什么 |
|---|---|---|---|
| 实时原生操作 | 需要 | 当前实际 Android 截图和探针数值 | 选择手牌并提交动作；引擎确认实际消费 |
| 只读查看保存画面 | HTTP 服务需要运行；保存 PNG 本身不需要 Android | 归档中真实保存的 PNG | 选择已有图片，不修改任何对局 |
| 原生引擎回放 | 需要同版本原生环境 | 使用原始种子、初始化和命令重新计算后的实际截图 | 向过去跳转时重置引擎并重放命令前缀；向前则继续计算 |

原生引擎回放不是把截图排成幻灯片。它在原始登记 tick 重新提交双方命令，比较保存过的最小状态、消费事件和终局证据。核对通过说明这些检查点一致，不证明所有未保存 tick 或每个像素均相同。

历史实验没有保存的游戏画面，不能从数值观测中补造。可以使用原生引擎重演生成一份新画面记录，但必须标为新重演，不称为当时录屏。

## 1. 检查环境与现有 VM

在仓库根目录使用 `.venv-cloud/bin/python`，不要依赖别的终端是否激活环境：

```bash
cd /workspaces/Game-AI-for-Clash-Royale
.venv-cloud/bin/python tools/cloud/workspace.py doctor
.venv-cloud/bin/python tools/cloud/software_android_vm.py status \
  --cache .cache/native-tools/android-recovery-001
```

如果 VM 已运行，复用这一台，不启动第二台。若已停止，可沿用[原生逐步教程](NATIVE_LOOP_ZH.md)中的恢复磁盘步骤。首次克隆不包含已安装 VM、APK 和大型资源，需要先按[软件 Android 指南](SOFTWARE_ANDROID_TCG_ZH.md)准备；本页的准备工具不会下载或安装这些资源。

如果 `status` 返回 `Recorded VM is not running; refusing any other process`，表示保存的 VM 进程身份已不匹配当前运行进程；常见情况是旧 VM 已停止。它不是 Python 环境检查失败，也不是要求删除记录。先核对 `.cache/native-tools/android-recovery-001/active-vm.json` 中的最新 `attempt`，确认旧进程与磁盘未被占用，再使用下面的恢复入口。`doctor` 的宿主 Binder 为 false 也不阻止这条软件 VM 路线；Android 的 Binder 在 guest 内提供。

当前机器使用软件 TCG，只运行一台 VM，训练和视觉实验串行进行。旧 8 GB 恢复基线使用一个 vCPU；当前 16 GB 机器已确认软件图形 ANR，采用上方 2 vCPU／倍率 20 的恢复试验。`--tb-size-mib` 默认 **64 MiB**，guest RAM 为 1536 MiB；两者都不是 QEMU 总内存上限。下面示例中的 `--resume-attempt` 必须指向本机已经存在且已停止的安装记录：

```bash
LAB_VM_ATTEMPT="$(.venv-cloud/bin/python -c 'import json; print(json.load(open(".cache/native-tools/android-recovery-001/active-vm.json"))["attempt"])')"

nice -n 10 .venv-cloud/bin/python tools/cloud/software_android_vm.py start \
  --cache .cache/native-tools/android-recovery-001 \
  --output outputs/cloud/avm --vcpus 2 --timeout-multiplier 20 --tb-size-mib 64 \
  --resume-attempt "$LAB_VM_ATTEMPT"
```

`LAB_VM_ATTEMPT` 从同一 cache 读取最新记录，不沿用文档中的旧路径；本次 ANR 诊断对应记录为 `outputs/cloud/avm/20261024T060502.204016Z-vm`，后续启动会再次更新。原有安装磁盘仍在。启动工具还会核验恢复链、进程身份与占盘情况。启动命令打印新 PID、attempt 等记录只说明已创建进程；其中 `android_boot_verified=false` 和 `native_game_execution_verified=false` 是初始状态。可再执行一次上方 `status`，看到 `qmp.result.return.status="running"` 后进入第 2 步，Android boot 和游戏内容就绪由准备工具继续等待。

不要因网页尚未显示画面而再启动一台 VM。VM 运行、Android 启动完成、游戏内容就绪和渲染成功是不同条件。调小 TCG 代码缓存是资源控制措施，**不能证明先前窗口中断或外部 SIGTERM 已解决**；信号来源与中断根因仍须分别取证。

## 2. 一次串行准备连接

在同一终端执行，创建本次独立目录名：

```bash
LAB_VISUAL="outputs/cloud/native-visual-$(date -u +%Y%m%dT%H%M%SZ)"

nice -n 10 .venv-cloud/bin/python tools/cloud/native_prepare.py \
  --manifest outputs/native_loop_20261020/local-runtime-version-001.json \
  --output "$LAB_VISUAL/prepare" \
  --adb .cache/native-tools/bin/adb --adb-port 5038 \
  --serial 127.0.0.1:15555 --root-shell su0
```

`native_prepare.py` 串行完成：启动仓库 ADB 服务、连接设备、等待 Android boot、启动游戏与端口转发、等待 `coldReady/contentReady`、核对当前进程身份、观察默认 60 秒的原进程存活与内容状态、写入连接文件。保存每一步日志和等待心跳，失败或中断时保留现场；它不自动建局、不启动训练，也不证明渲染成功。

成功条件是最后的 `status="prepared"`，并生成：

```text
$LAB_VISUAL/prepare/report.json
$LAB_VISUAL/prepare/identity.json
$LAB_VISUAL/prepare/connection.json
```

游戏软件加载可能耗时数分钟。不要并发运行两个准备命令。游戏进程重新启动后重新准备身份与连接，旧身份回执不能当作当前进程证明。

`android-boot / waiting` 表示尚未读到 `sys.boot_completed=1`，默认总预算为 600 秒。ADB 连接或该属性读取出现单次超时，准备工具会记录到 `boot.jsonl`，在剩余预算内继续只读检查；不会因此自动重启 VM、ADB 服务或游戏。总预算耗尽才以 `Android boot budget exceeded` 结束。若准备失败后确认原 VM 仍运行，应使用新的 `LAB_VISUAL` 目录重试准备，不必重新启动 VM。

## 3. 启动原生浏览器服务

继续使用刚才同一终端中的 `LAB_VISUAL`。把连接文件明确传给服务，不再手工拼第二套探针端口：

```bash
nice -n 10 .venv-cloud/bin/python tools/cloud/native_browser.py serve \
  --connection "$LAB_VISUAL/prepare/connection.json" \
  --output "$LAB_VISUAL/browser" \
  --archives outputs/cloud/native-archives \
  --host 127.0.0.1 --port 8772 \
  --adb .cache/native-tools/bin/adb --adb-port 5038 \
  --serial 127.0.0.1:15555 --root-shell su0 \
  --capture-ticks 20 --frame-wait 1.1 --max-tick 7200
```

也可把同一连接路径放入 `ROYALE_NATIVE_CONNECTION` 后省略 `--connection`；环境变量只影响当前终端及其子进程。教程采用显式参数，便于核对。

保持这个终端运行。在 VS Code 的 **Ports／端口** 中打开 **8772**，进入 `/native`。首次状态应提示连接核验中，完成后提示可新建对局。**启动 HTTP 服务不会自动创建对局。** `--output` 使用新目录，归档根目录可以复用；最新 CLI 也允许省略 `--output`，此时按 UTC 时间自动建立新日志目录，本教程显式指定以便追踪。

页面只向 worker 提交任务，GET 状态与图片不主动推进引擎。单个原生 worker 串行执行 RPC；多个浏览器共享同一对局，请约定一位操作人。下牌等 POST 使用当前服务的会话令牌，页面会自动附带。

### 在 rendered 与 headless 之间切换

浏览器入口请求原生渲染；`native_loop.py` 的默认采集／训练闭环使用 headless。**不能在活局中切换模式**，也不能让浏览器和训练器同时占用同一个原生会话。

从浏览器切回 headless 时，先暂停并保存，再对浏览器服务按 `Ctrl+C`，等待 worker 关闭。确需切换游戏进程模式时，在新准备目录中显式请求重启：

```bash
LAB_MODE="outputs/cloud/native-mode-$(date -u +%Y%m%dT%H%M%SZ)"
.venv-cloud/bin/python tools/cloud/native_prepare.py \
  --manifest outputs/native_loop_20261020/local-runtime-version-001.json \
  --output "$LAB_MODE/prepare" --restart-app \
  --adb .cache/native-tools/bin/adb --adb-port 5038 \
  --serial 127.0.0.1:15555 --root-shell su0
```

只有报告到 `prepared` 后才把新身份连接交给后续命令：

```bash
export ROYALE_NATIVE_CONNECTION="$LAB_MODE/prepare/connection.json"
```

随后按[原生闭环教程](NATIVE_LOOP_ZH.md)执行 `tools/native_loop.py` 的 headless 命令。旧进程的身份回执和连接不能复用。反向从 headless 进入 rendered 时，也可先结束原有任务，按上例显式重启，再把新连接传给浏览器服务。

`--restart-app` 会 force-stop 当前固定客户端并结束当前局，是用户主动选择的模式切换操作；默认准备流程不会重启应用，也不会把它当作网络失败后的自动恢复。此选项没有解决本轮外部 SIGTERM 或全黑问题的成功证据。

## 4. 手动操作一局

1. 选择随机种子、控制方和对手策略。当前固定坐标中 owner 0 在上半场，owner 1 在下半场。
2. 点击“新建／重置原生局”。已有局会被替换，需要时先保存。
3. 对局初始化包含 90 tick 预热，通常首先显示 **tick 90，即 4.5 秒**。它不是从错误时间开始；原生重演的最早可达 tick 也取自该局初始化值。
4. 先保持暂停，检查实际 Android 图像、己方圣水和手牌。图片为全黑或内容尚未就绪时，页面明确显示视觉内容未验证，不能据此宣布画面通过。
5. 选择可负担的手牌。右侧“落点输入器（不是游戏画面）”提供 18×32 格坐标盘：选牌后点格子直接提交，未选牌时仅填写坐标；默认吸附格子中心，可关闭后精确点选。它不显示兵或塔状态，只输入原生坐标。若以后有已核验的像素战场标定，也可直接点击实际截图；当前缺少标定时，实际图片点击下牌禁用。
6. 输入 `x=0..17999`、`y=0..31999`。例如 owner 1 的普通部队应从下半场候选位置开始；法术和特殊卡的实际合法性仍以引擎为准。标记仅表示请求点，不是部署点真值。
7. 提交后查看“动作与实际消费”。“请求已返回”“已登记”和“已确认卡牌消费”分别表示不同阶段；未知反馈不得当作成功。
8. 用“精确单步推进”推进 1–200 tick，或点“连续运行”。20 tick 为一秒游戏时间。连续运行允许你在推进间隙继续操作，但软件 Android 不保证现实时间实时速度。
9. 点击“暂停”后，等待当前小段计算与截图结束。自然终局或到达推进预算后不得再下牌；后者标为截断，不能冒充自然终局。
10. 点击“保存当前局与回放证据”，记下返回的归档编号。

连接中断后先“重新读取状态”，核对最后动作与消费。本页不会自动重发下牌、重置或重演命令，避免重复操作。

## 5. 保存图像密度与完整性

`--capture-ticks` 按游戏 tick 控制 PNG 保存间隔：

| 参数 | 游戏时间中的采集密度 | 含义与成本 |
|---|---|---|
| `--capture-ticks 20` | 约 1 FPS | 默认研究检查；每一秒游戏时间约保留一张，并非流畅录像 |
| `--capture-ticks 10` | 约 2 FPS | 增加动作过程画面，也增加截图与存储开销 |
| `--capture-ticks 1` | 20 Hz 保存请求 | 逐 tick 请求保存；TCG 需要大量截图、等待和磁盘空间，先用短局验证，不代表实际画面以 20 Hz 刷新 |

创建、动作和暂停等节点可能额外强制截图，同 tick 也可能有多张。默认截图从完成初始化的 **tick 90** 开始，不包含 Android 开机、客户端加载过程或最初 90 tick 的录像。播放器保留原始 tick 间隔，不插值或补出未采集画面。

逻辑采样密度、Android 显示刷新率和现实播放速度是三个不同指标。当前软件 VM 启动参数包含 `androidboot.redroid_fps=1`；把 `capture-ticks` 改为 1，不会自动把 Android 画面升级成 20 FPS，可能仍保存重复或延迟画面。要声称逐 tick 新鲜画面，还须验证 Android 渲染设置、像素变化和 tick 对齐。

`--frame-wait` 是冻结逻辑 tick 后等待 Android 画面呈现的现实秒数；截图前后 tick 相同，不等于证明像素零延迟。

保存结果通常位于：

```text
outputs/cloud/native-archives/<归档编号>/
  manifest.json
  native/journal.json
  native/initialization.json
  native/states.jsonl
  native/states-bound.jsonl
  native/native_result.json
  native/execution_feedback.json
  frames/index.jsonl
  frames/000000.png ...
```

`manifest.json` 区分以下字段：

| 字段 | 实际含义 |
|---|---|
| `natural_terminal` | 是否保存到自然终局；截断不算自然终局 |
| `frame_count`、`capture_ticks` | 保存文件数和逻辑采样间隔 |
| `complete_frame_coverage` | 指定密度从初始化至自然终局的采样覆盖 |
| `full_20hz_samples` | 自然终局覆盖完整且 `capture_ticks=1`，仅描述逐 tick 采样文件 |
| `full_20hz_frames` | 当前固定为 false，未证明每秒 20 张新鲜原生画面 |
| `fresh_frame_rate_verified` | 当前固定为 false，真实呈现帧率尚未验收 |
| `frame_rate_limitation` | 记录逐 tick 采样不能证明 Android 每秒呈现 20 张新鲜画面 |

**覆盖完整指指定采样密度从初始化至自然终局的覆盖**：默认从 tick 90 起按 20 tick 采样，即使覆盖完整，也不等于 20 Hz、不等于从客户端加载画面开始录像、不等于所有动作视觉过程均被保留。还需检查 `frame_pixels_non_black`、真实 PNG 和显示对齐证据。

## 6. 只读回看实际保存画面

切换到“原生引擎回放”，选择归档后点“只读查看保存画面”。展开播放器：

- 拖动保存帧滑条或使用上一帧／下一帧。
- 按原始游戏 tick 间隔播放，支持 0.5×、1×、2×、4×。
- 缺少原始 tick 时禁用按时间自动播放，不猜测原速。
- 左侧标为“保存画面”，不显示未经保存的圣水等数值。
- 点击“返回当前引擎画面”后，才允许重新操作当前局。

这一功能只读取归档 PNG，不恢复或修改引擎，所以 Android VM 停止后仍可回看。HTTP 服务必须保持运行。关闭原生服务和 VM 后，使用独立的离线入口：

```bash
LAB_REVIEW="outputs/cloud/native-review-$(date -u +%Y%m%dT%H%M%SZ)"
.venv-cloud/bin/python tools/cloud/native_browser.py serve --offline \
  --output "$LAB_REVIEW" --archives outputs/cloud/native-archives \
  --host 127.0.0.1 --port 8772
```

重新打开 8772，选择归档并查看保存画面。`--offline` 不启动 ADB、不校验运行资源、不获取原生会话锁，也不连接引擎；页面禁用原生建局和重演，但保留保存图片读取。需要重新计算原生局时，停止离线服务，再按前文准备并启动在线服务。

## 7. 真正的原生引擎回放

先准备好同版本 Android 与新身份连接，然后在页面选择归档，点击“打开并从头重演”。这一步会替换当前活动对局：

1. 校验归档来源文件 SHA256 与原生版本指纹。
2. 按原始初始化、种子和固定卡组创建原生局。
3. 在每个原始登记 tick 注册命令，在执行 tick 观察消费。
4. 与保存的状态检查点核对。请求与实际消费、归一化 generation／事件计数起点分别处理。
5. 拖动原生时间线并松开，或输入目标 tick。回退会重置并重演前缀，页面显示处理中；不会假装瞬间倒退。
6. 长时间重演时可点“暂停”，请求在安全检查点停止，保留已完成前缀。暂停不是强制杀死当前 RPC；等待服务报告实际停下的 tick，再继续。

可用范围为该局 `initialization_tick` 到保存终点，当前六局历史初始化为 tick 90，不能要求回到 tick 0。回放中的手动下牌关闭，避免污染来源轨迹。若核对失败，保留差异报告，不能称为确定性重演通过。

要登记以前只保存了数值和命令的原生局，先执行：

```bash
.venv-cloud/bin/python tools/cloud/native_browser.py import \
  --source outputs/native_loop_20261020/native-run-002/evaluation/game-000/native \
  --archives outputs/cloud/native-archives
```

该命令只读登记已有来源，不连接 Android，也不补造历史图片。页面刷新归档列表后可尝试引擎重演；只有实际重演并截图后，才会产生新的图像记录。

## 8. 导出保存画面动画

先核对保存 PNG 与状态绑定，再导出保留原 tick 间隔的 APNG：

```bash
.venv-cloud/bin/python tools/cloud/native_browser.py export \
  --archive "outputs/cloud/native-archives/<归档编号>"
```

把 `<归档编号>` 替换为保存结果中的真实编号，不要连尖括号一起执行。输出 `animation.png` 与 `export-report.json`。此命令不连接原生，不把数值状态画成游戏画面，不承诺 MP4。原始 PNG 保留，动画也不增加未保存的帧。

## 9. 停止与故障定位

先暂停、保存，再对服务终端按 `Ctrl+C`，等待 worker 关闭。最后停止 VM：

```bash
.venv-cloud/bin/python tools/cloud/software_android_vm.py stop \
  --cache .cache/native-tools/android-recovery-001
```

| 现象 | 检查与处理 |
|---|---|
| 8772 未连接 | 检查服务终端是否运行、Ports 是否转发 8772，不要打开 8771 代替 |
| `start` 提示 `Recorded PID is alive` | 先对同一 cache 执行 `status` 核对身份与磁盘；若确认是已有同一 VM 且 QMP 为 `running`，复用它并继续 prepare，不要再次 start。若身份不匹配，保留现场检查，不直接杀 PID 或删除记录 |
| 准备阶段 `adb-server exit 255`／`cannot start server on remote host` | 旧准备入口用 `-H 127.0.0.1` 启动 daemon，导致新 daemon 无法启动；当前已改用 `adb -P 5038 start-server`，设备客户端仍显式使用 `-H`／`-P`。真实冷启动已确认仅监听 `127.0.0.1:5038`、协议 `0029`，5037 未监听，见 [ADB 验证报告](../outputs/cloud/native-adb-local-start-20261024-001/report.json)；相关 22 项模拟测试通过。复用现有正常 VM，在新目录重跑 prepare，无需重启 VM；这些结果不代表 Android boot、游戏或视觉验收通过 |
| 资源／进程身份失败 | 查看 `prepare/report.json` 和对应 stdout／stderr；进程重启后重新准备连接 |
| `getprop sys.boot_completed` 单次超时 | 慢速 TCG 或短暂传输阻塞可能造成超时。先确认 VM 仍运行；当前工具会在总预算内继续只读检查。早期脚本会在此直接退出，更新后用新输出目录重试准备，保留旧失败日志 |
| `game-start exit 2` | 查看 `prepare/start/report.json` 的精确 `error`；当前准备工具会把子报告原因带到顶层，并保留 `start_command_accepted`。已接受启动不等于进程已验证，失败时不自动重发 |
| 启动后出现空 shell 回包或 root 提示 | 空回包不能证明缺少 root。2026-10-24 手动实例 `20261024T052053.185400Z-vm` 在 `05:31:49 UTC` 收到 SIGTERM，随后 ADB 空回包导致旧脚本误报 root；此前已经读到 UID0。先查 VM 存活和 `qemu.log`，不要直接修改权限或重复启动 |
| tick 变化但截图全黑 | 逻辑可运行不能证明渲染可运行；保存 PNG 和探针状态，检查内容加载与 Android 图形路径 |
| 无法点击战场 | 查看 `frame.mapping`；未标定时用原生坐标，不猜屏幕映射 |
| 动作未消费 | 核对 generation、槽、卡牌 ID、圣水、坐标和原生反馈；不要自动重发 |
| 回退较慢 | 正在重置并计算前缀；单台 TCG 不能承诺瞬时随机跳转 |
| 历史归档无图片 | 可登记并尝试引擎重演，原历史录屏仍不存在 |
| 画面密集模式很慢 | 先 `capture-ticks 20` 验证短流程，再提高密度；不要并发训练或另开 VM |
| VM 再次收到 SIGTERM | 先检查宿主可用内存。[已上传日志](CODESPACES_MEMORY_DIAGNOSIS_20261024_ZH.md)显示六次退出与低可用内存相关，存在 earlyoom；64 MiB 缓存没有解决资源余量问题。先确认 16 GB 升配实际生效，不自动循环重启 |

如果已确认 VM 收到 SIGTERM，而仓库日志没有发送者，请先导出 Codespaces 运行日志：在 VS Code 按 `Ctrl+Shift+P`，执行 **Codespaces: Export Logs**，把日志 ZIP 放在仓库的 `outputs/cloud/`，再核对终止时刻附近记录。该入口见 [GitHub 官方日志说明](https://docs.github.com/en/codespaces/troubleshooting/github-codespaces-logs)。它可能提供容器／会话层线索，不保证包含宿主发信号的完整证据；不能在来源未明时直接归因于 GitHub 或某个扩展。

本轮结论是**真实视觉链路未通过**，仍需实际验证游戏非黑画面、浏览器动作消费、前后向原生重演、停止 VM 后读取真实保存帧、真实帧 APNG 导出及覆盖完整性。当前全黑 PNG 仅证明截图通路能返回图像文件。页面契约证据见 `outputs/cloud/native-ui-contract-20261024-001/report.json`，其夹具范围与上述实际失败证据明确分开。

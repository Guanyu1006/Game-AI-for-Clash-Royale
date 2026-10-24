# Codespaces VM 终止日志核查与 16 GB 恢复步骤

用户上传 `outputs/cloud/logs.zip` 后，本轮只读分析了覆盖目标时间的运行日志，并与六次 QEMU 终止记录对齐。原始 ZIP 保留，没有启动 VM、ADB、游戏或训练。

**结论：六次终止高度关联宿主低可用内存；宿主存在 earlyoom，内存保护是目前最有证据支持的解释。** 尚缺少 earlyoom 实际配置及直接写明受害进程的服务日志，所以没有把具体信号发送者写成已证实。

**当前机型已经升配并由实际 doctor 确认。** 用户手动调整现有 Codespace、重连后提供的检查显示 `logical_cpus=4`，`memory.total_gib=15.61874008178711`（约 **15.62 GiB**），当次 `available_gib=12.462543487548828`（约 **12.46 GiB**），572 项共享资产校验通过，另有[本地 doctor 与恢复预检证据](../outputs/cloud/codespaces-memory-upgrade-20261024-001/report.json)。可用内存是当次瞬时值，不保证后续实验始终可用。下文的 2 核／约 7.76 GiB 描述上传历史日志所对应的旧机器；升配确认不代表 VM 稳定性或真实视觉验收已经通过。

## 事实与推断

压缩包包含 55 个成员，压缩后 14,946,626 字节，声明解压后约 129 MB。本轮按成员流式读取相关运行日志，跳过凭据与环境导出成员，没有执行批量解压。[ZIP 清单与 SHA256](../outputs/cloud/codespaces-log-audit-20261024-001/zip-manifest.json)保存了输入身份。

`20261024_034702_VmAgent.log` 覆盖 `03:47:02–05:41:20 UTC`。外层时间没有时区后缀，但其中带 `Z` 的结构化时间与外层对齐。六次 QEMU 的 SIGTERM 时刻附近均有宿主内存采样；下表保留原始内核 kB 单位，即 1024 字节，采样不等于终止瞬间的最低值。

| SIGTERM 时刻（UTC） | 终止前最近 MemAvailable（kB） | 终止后最近 MemAvailable（kB） |
|---|---:|---:|
| 04:28:45.966 | 833,628 | 3,586,708 |
| 04:34:56.386 | 876,848 | 3,548,156 |
| 04:41:29.823 | 847,028 | 3,488,852 |
| 04:48:35.480 | 984,116 | 3,412,844 |
| 05:01:52.800 | 966,888 | 2,328,784 |
| 05:31:49.698 | 821,540 | 2,489,868 |

来源行号、采样时间和单位见[内存对照结论](../outputs/cloud/codespaces-log-audit-20261024-001/vmagent-conclusions.json)与[六次 VM 时间线](../outputs/cloud/codespaces-log-audit-20261024-001/vm-timeline.json)。历史终止前的可用内存约 800–960 MiB，旧机器总内存为 7,942.273 MiB，无 swap；这不是升配后当前内存状态。

最后一次的关键原始成员行号为：

- `VmAgent.log:791569`：05:31:47.660，`MemAvailable: 821540 kB`。
- `VmAgent.log:791877`：05:31:49.721，`796.0 avail Mem`，swap 为 0。
- `VmAgent.log:792043`：05:31:49.930，宿主 PID 650 的进程名为 `earlyoom`。
- `VmAgent.log:792252`：05:31:52.751，`MemAvailable: 2489868 kB`。

原始成员完整名称为 `20261024_034702_VmAgent.log`，脱敏摘录见[内存证据](../outputs/cloud/codespaces-log-audit-20261024-001/vmagent-memory-evidence.jsonl)。QEMU 自身的[终止日志](../outputs/cloud/avm/20261024T052053.185400Z-vm/qemu.log)先于 ADB 空回包和准备脚本失败；此前已读到 UID0，因此不能把随后的空内容解释为缺少 root。

宿主在最后一次终止前还运行约 968 MiB RSS 的 `clamscan`；容器内有多个约 432–679 MiB RSS 的 `MainThr+` 进程，QEMU 约 1.6 GiB。容器和宿主视图中的同一 QEMU 不能相加计算两份内存，也不能把 RSS 相加当作精确独占内存。详见[资源快照](../outputs/cloud/codespaces-log-audit-20261024-001/vmagent-resource-summary.json)。

earlyoom 是用户态内存保护程序，在可用内存与 swap 低于配置阈值时发送 SIGTERM；默认阈值为 10%，实际可配置。[项目维护者手册](https://github.com/rfjakob/earlyoom/blob/master/MANPAGE.md)描述了该行为。本机的配置未出现在上传日志里，附近采样也未必捕获最低值。六次一致的低内存关联、内存回升及该进程的存在，构成高可信推断，仍不等于直接确认发送者。

此前容器 `memory.events` 的 `oom_kill=0` 不能排除这种情况：该计数不能证明宿主上的用户态保护程序没有发过 SIGTERM。

## 其它日志能说明什么

- 最新 VS Code 日志在 05:31:43 有成功活动心跳，05:31:52 仍有隧道消息。目标前后一分钟内没有客户端断线／崩溃记录。[客户端审计](../outputs/cloud/codespaces-log-audit-20261024-001/client-summary.json)。
- Tunnel 在目标附近继续通信，存储统计跨目标时间连续。SSHMonitor 的 `sshd.pid` 缺失是约每 10 秒重复的长期模式，不能当作新发生的容器死亡。[平台审计](../outputs/cloud/codespaces-log-audit-20261024-001/platform-summary.json)。
- 旧 server/terminal 日志只覆盖到 04:28:51，不能用于认定 05:31 的发送者。日志里的 03:46 journald SIGTERM 也与本次 QEMU 不同。
- 已审阅的仓库路径中没有发现固定 600 秒终止当前 VM 的逻辑；六次实际存活时长约 250–652 秒，也不支持固定时限解释。

这些事实使“仅因当前网页断线或空闲而停止整台 Codespace”的解释缺少支持，但不排除日志没有覆盖的其它宿主行为。十月二十日的两次整个窗口中断没有在本轮被重新确定根因。

## 已补充的开发检查

`tools/cloud/workspace.py doctor` 现在增加只读 `memory` 字段，由 [`memory_resources.py`](../tools/cloud/memory_resources.py)读取可见 Linux `/proc/meminfo`：

- `total_bytes`、`available_bytes`、`swap_total_bytes`、`swap_free_bytes`。
- `total_gib`、`available_gib`、来源和范围说明。
- 缺字段／读取失败保持 `null`；实际无 swap 则明确为 0。

它描述可见宿主内存，不描述 Android guest 内存，也不替代 cgroup 限额或原生验收。7 项相关测试通过，实际 doctor 和哈希见[检查报告](../outputs/cloud/codespaces-log-audit-20261024-001/memory-check-report.json)。新增检查不会启动任何原生进程。

## 当前恢复状态：升配已确认，等待手动恢复与重新验收

升配后的只读恢复预检通过：最新记录仍为 `outputs/cloud/avm/20261024T052053.185400Z-vm`，原有安装磁盘仍在；没有运行中的原生 VM，旧 PID 39827 已失效，15555／15556 端口空闲。这些条件说明可以继续按记录恢复原磁盘，不代表 Android 已启动、内容就绪或画面成功。

以下保留已经完成的机型变更方法；当前无需再次执行第 1–4 步升配，只需在后续变更或重连时用 doctor 复核：

1. 在编辑器执行“保存全部”。当前分支为 `dev/main-start-20261020`，源码和已安装磁盘都在仓库目录中。
2. 打开 [Codespaces 列表](https://github.com/codespaces)，找到当前仓库与分支对应的现有 Codespace。
3. 点击该 Codespace 右侧 `…` → **Change machine type**，选择界面显示的 **4 cores / 16 GB RAM**，再点 **Update codespace**。也可在 VS Code 命令面板执行 **Codespaces: Change Machine Type**。
4. 等待变更。如果界面提示下次重启生效，停止这个现有 Codespace，再重新打开它。变更／重启会暂时断开当前窗口。
5. 重连同一个 Codespace 后，只先执行下面的检查，把 `logical_cpus` 与 `memory` 输出用于确认；不要直接认定机型已生效。

```bash
cd /workspaces/Game-AI-for-Clash-Royale
git branch --show-current
.venv-cloud/bin/python tools/cloud/workspace.py doctor
```

本次实际结果已经达到 `logical_cpus=4`、`memory.total_gib≈15.62`。机器显示名、可用机型和生效方式以账户界面为准。GitHub 官方说明：同存储容量的变更可能等到下一次重启才生效，不同容量的变更会暂时停止 Codespace；机型有不同计费档位。[机型变更说明](https://docs.github.com/en/codespaces/customizing-your-codespace/changing-the-machine-type-for-your-codespace?tool=webui)。停止后重开会保留已保存工作，删除 Codespace 则不同；上述步骤只修改并重开现有实例。[生命周期说明](https://docs.github.com/en/codespaces/about-codespaces/understanding-the-codespace-lifecycle)。

升配确认与只读恢复预检已完成，下一步由用户按手动 `start`／`status` 步骤串行恢复同一安装磁盘，再按[原生视觉指南](NATIVE_VISUAL_ZH.md)验证。guest 先保持 1536 MiB／1 vCPU，不因宿主升配就同时扩大所有实验。继续记录宿主可用内存，验证持续稳定性、内容就绪、原生渲染短检查、自然终局和重演。升配旨在缓解已观测的内存压力，不是原生完整画面或棋力提升已通过的证明；本次没有因此自动启动 VM、ADB、游戏或训练。

后续进展：16 GB 升配后，用户手动恢复 VM 并完成连接准备，[本次报告](../outputs/cloud/native-visual-20261024T061421Z/prepare/report.json)于 2026-10-24 06:27:20 UTC（北京时间 14:27:20）记录 `prepared`、`content_ready=true`、`identity_verified=true`，新 `connection.json` 已生成。核对时 VM 3394 仍运行、宿主可用约 10.87 GiB，8772 尚未监听，浏览器服务与新局尚未启动。这仅证明本次内容及身份连接准备成功，真实建局、截图和视觉验收仍待执行，也不能据此承诺永久稳定。

此后浏览器首次建局失败。新[只读 Android 诊断](../outputs/cloud/native-render-diagnostic-20261024-001/report.json)明确记录：游戏 PID 1978 于 06:27:36 UTC（北京时间 14:27:36）因 ANR 被 Android 结束，VM 3394 仍运行、宿主可用约 10.69 GiB。这是 guest 界面响应超时，不能继续归因于旧宿主内存问题。图形线程正在软件 LLVM 编译，焦点事件最终耗时约 84 秒，超过该轮约 50 秒输入超时。当前改为[2 vCPU／倍率 20 的手动恢复试验](NATIVE_VISUAL_ZH.md#当前故障准备成功后游戏因-anr-退出)，保留 guest RAM 与安装磁盘；后续仍须存活、画面、动作与回放验收。

# Codespaces 软件 Android 实验路线

这是一条 **experimental** 的运行时准备路线：把 Debian Linux 内核与 Android 12 根文件系统放进 QEMU TCG 软件虚拟机。它不依赖 Codespaces 宿主机提供 Binder/KVM，也不在宿主机安装软件或加载内核模块。运行时会较慢；Android 能启动仍不代表 ARM64 游戏、原生探针和训练闭环已经通过。

**2026 年 10 月 20 日已实机跑通有限闭环：4 场原生采集、2 轮 GRU 训练和 2 场新进程模型评估，6/6 自然终局。** [原始闭环报告](../outputs/native_loop_20261020/native-run-002/report.json)、[独立验收](../outputs/native_loop_20261020/native-run-002-acceptance/report.json)分别保存执行和结果复核；2424 条转移、24194 个推进 tick、202 次控制方实际消费，连续 generation 1–6。原生像素、普通单位公平可见性、PPO 与棋力提升仍不在本次验收范围。

本次恢复通过 [源码启动记录](../outputs/cloud/avm/20261020T134453.037427Z-vm/process.json)显式复用旧私有盘，不再复制数 GB 磁盘。ADB 核对 BOOT=1、system_server／SurfaceFlinger；`su 0` shell 为 UID0，adbd 本身仍是 UID2000。[内容等待回执](../outputs/native_loop_20261020/readiness-wait-recovery-002/report.json)在约 7 分半钟后返回 true；[identity-check-005](../outputs/native_loop_20261020/identity-check-005/report.json)重新核验实际 PID1814 与全部资源，随后执行六局。之前中断局与早到的身份检查均保留，未计入六局。

**当前 VM 已在验收后停止**，见[安全停止回执](../outputs/cloud/avm/20261020T134453.037427Z-vm/stop.json)，端口释放，避免与浏览器／训练叠加负载。下一次从已停止的最新 attempt 恢复，再等待内容和刷新身份；不能把保存的 PID1814 回执当作新进程认证。

从零下载、解包、建盘到游戏对局的整套通用流程尚未在新 cache 完整复验；静态及 CLI 检查范围见[源码检查报告](../outputs/cloud/software-android-source-checks/report.json)。历史排障文件位于 `outputs/native_loop_20261020/software_runtime/`。窗口中断的事实、原因边界与降低负载措施见[核查与恢复说明](WINDOW_RESTART_20261020_ZH.md)。

## 可复现的源码与资源身份

- [`software_android.py`](../tools/cloud/software_android.py)：检查条件、下载、解包和构建 ext4 镜像。
- [`software_android_vm.py`](../tools/cloud/software_android_vm.py)：建立独立 VM attempt、生成 initramfs、启动、查看和停止已记录的 QEMU。
- [`software_android.lock.json`](../configs/native/software_android.lock.json)：43 个 Debian 包与一层 Redroid 镜像的固定版本、尺寸和 SHA256。总压缩下载为 **739,861,565 bytes**；不随 `latest` 标签自动升级。

Debian 包的 SHA256 来源是当次已验证签名的 bookworm Packages 索引；提交的锁保存该索引 SHA256 与各包身份。后续复现直接校验这些锁值。若上游已移除某个固定版本，保留失败证据，不自动换成新版本。Redroid 使用匿名公共下载授权，授权只留在进程内存，不保存账号凭据。

项目没有重新分发 Android 镜像、内核和 native bridge 二进制；各组件保留其原有许可。游戏 APK、探针、固定内容资源的获取与身份检查仍使用[原生闭环指南](NATIVE_LOOP_ZH.md)中的独立入口。

## 1. 准备运行时

仓库路径中使用显式解释器；当前入口支持 Linux x86_64、Debian bookworm 兼容宿主用户空间。宿主需要已有 `dpkg-deb`、`mke2fs` 和 `cp`。脚本不会为缺失工具执行全局安装。

```bash
.venv-cloud/bin/python tools/cloud/software_android.py doctor
.venv-cloud/bin/python tools/cloud/software_android.py download
.venv-cloud/bin/python tools/cloud/software_android.py unpack
.venv-cloud/bin/python tools/cloud/software_android.py image
```

下载总预算固定不超过 2 GiB，准备与启动前要求仓库文件系统至少 10 GiB 空闲。默认缓存是 `.cache/native-tools/software-android/`，准备日志是 `outputs/cloud/software-android/<时间戳>-<操作>/`。

解包器先写普通文件，最后创建链接，不会在宿主机跟随 Android 归档中的绝对链接；归档 UID/GID/mode 单独保存。ext4 构建使用 `mke2fs -E no_copy_xattrs`；UID/GID 与模式在 VM 内批量恢复。Binder 和其他模块仅由 VM 内的 initramfs 加载。

已有不同的下载文件、部分下载、解包目录与镜像均保留。重复解包或构建不会覆盖历史；需要重新尝试时选择仓库内新的 `--cache` 路径，并在每条命令保持一致。上游网络中断产生 `.partial` 时，先查看该次 `report.json`，不要把部分文件改名当成完整资源。

## 2. 启动并检查

以下默认命令用于之后从默认 cache 准备的新 VM。已停止的最新恢复实例使用 cache `.cache/native-tools/android-recovery-001`、attempt `outputs/cloud/avm/20261020T134453.037427Z-vm`；其 PID5340 已通过保护入口停止。PID 可能被系统复用，后续操作须由同一 cache 的进程身份检查决定。相同端口占用时通用工具会保留现有服务并退出。

```bash
.venv-cloud/bin/python tools/cloud/software_android_vm.py start
.venv-cloud/bin/python tools/cloud/software_android_vm.py status
```

查看记录时必须显式选择同一 cache；当前已停止，status 拒绝操作不存在的 PID 属预期：

```bash
.venv-cloud/bin/python tools/cloud/software_android_vm.py status \
  --cache .cache/native-tools/android-recovery-001
```

本次实际恢复启动采用 `nice -n 10`、`--vcpus 1 --timeout-multiplier 10`；核验、模拟和训练串行进行。发生环境重建时先确认旧实例已停止，使用下述显式恢复入口。不要用默认 cache 或早期实验的 `vm_control.py` 操作恢复实例。

中断后，复用已停止实例的私有磁盘，避免再次复制数 GB：

```bash
nice -n 10 .venv-cloud/bin/python tools/cloud/software_android_vm.py start \
  --cache .cache/native-tools/android-recovery-001 --output outputs/cloud/avm \
  --vcpus 1 --timeout-multiplier 10 \
  --resume-attempt outputs/cloud/avm/20261020T134453.037427Z-vm
```

`--resume-attempt` 校验旧记录、磁盘路径、PID、QMP、端口和占盘进程，拒绝路径逃逸、链接及异常历史链。它复用旧 attempt 的 **可变磁盘**，在新 attempt 保存日志与 initramfs；原始底盘与旧日志不改。恢复余量最低 1 GiB，新建盘仍要求 10 GiB。连续恢复时指定最新 process.json 对应 attempt，工具跟踪 `resumed_from` 至最早私有盘。中断的游戏不续接，验证标志与 RPC 计数重新开始。

默认只用 **1536 MiB 内存、1 vCPU、TCG 单线程**。每次启动在 `outputs/cloud/android-vm/<时间戳>-vm/` 保存独立磁盘副本、initramfs、命令、进程身份、QMP socket 和日志，不修改准备阶段的 ext4 底盘。`status` 中 QEMU 显示 `running` 只证明虚拟机进程在运行。

若实际 CPU 负载和 Android Watchdog 日志表明 1 vCPU 不足，可在下一次启动时选择 `start --vcpus 2`。该选项同时使用 TCG 多线程与 guest cpuset `0-1`，内存仍保持 1536 MiB；默认不自动提高资源。

`--timeout-multiplier` 接受 1 到 20 的整数，默认 1；首次慢速 TCG 冷启动可选 `start --timeout-multiplier 10`。它把 `ro.hw_timeout_multiplier` 传给早期 Android init，只对下一次冷启动生效，不修改当前 VM。依据[本地 framework 字节码与 AOSP 核实记录](../outputs/native_loop_20261020/software_runtime/watchdog-timeout-research.json)，这个 Android 12 镜像的 Watchdog 超时为 `60000 × multiplier` 毫秒，检查间隔为超时的一半；取 10 时分别为 600 秒、300 秒。该设置还影响其他读取该属性的组件；它不能证明启动完成，也不能修复死锁。新启动后仍须实际读取属性并核对日志。

网络限定为 QEMU `restrict=on`；ADB 转发为 `127.0.0.1:15555`，串口为 `127.0.0.1:15556`，不对公网监听。启动前检查两个端口以及之前记录的 PID，不替换已运行服务。VM 唯一共享目录为 `.cache/native-tools/android-share/`，以只读 9p 挂到 guest 的 `/data/local/tmp/royale-staging/`；该宿主目录内不得包含符号链接。不要将整个仓库、用户主目录或 SSH 目录作为共享目录。

启动过程使用独立 mount/PID namespace 与 `switch_root`。此前普通 `chroot` 触发 Android 对 `/proc/self/root` 的检查失败；直接从 initramfs `pivot_root` 返回 EINVAL。因此当前代码使用排障中的 `switch_root` 路线，并在独立目录保留 BusyBox 控制入口。必要的 virtio、ext4、Binder 和 9p 模块加载失败时会输出明确标记并停留在 guest shell。

另有两项来自实际排障的配置已固化：加载 guest FUSE 模块并挂载 `/sys/fs/fuse/connections`，避免 vold 因连接目录不可用而退出；在 Android 共用的 guest 网络 namespace 内，每 10 秒检查 `pref 100 lookup main` 规则并恢复 `eth0` 默认路由，避免 netd 改写规则后 ADB hostfwd 失联。初始化时先加规则再设置默认路由。该维护使用 guest 静态 BusyBox，不依赖 ADB；QEMU 的 `restrict=on` 仍然阻止 guest 对外网络。所有这些操作只发生在 VM 内。

早期实验曾手动恢复路由；当前恢复实例已使用包含上述 FUSE 和路由保活逻辑的源码入口。具体生成的 guest 初始化脚本与串口日志保存在当前 attempt，网络问题仍须核对实际日志。

查看 `serial.log` 和 `qemu.log`，依次确认 Linux、根盘挂载、归档 ownership、Android init 等标记。**必须实际读取 `sys.boot_completed=1`，并检查 Android 服务与 ADB，才可记录 Android 启动成功。** 脚本生成的启动记录始终初始化为 `android_boot_verified=false`，不会凭进程存在自动提升结论。

可向已记录 VM 的串口输入 guest 命令；命令与响应会单独保存。串口响应受当前前台进程影响，不保证 shell 已就绪。

```bash
.venv-cloud/bin/python tools/cloud/software_android_vm.py console \
  --cache .cache/native-tools/android-recovery-001 --text 'ps'
```

原生 RPC 和 APK 安装不由这个入口自动执行。若需要 ADB，先使用仓库已有的锁定安装入口，再按原生连接指南选择明确设备，避免混用旧设备状态。

## 3. 停止与保留证据

停止当前恢复实例的入口如下；仅在结束当前验收、需要停止时使用：

```bash
.venv-cloud/bin/python tools/cloud/software_android_vm.py stop \
  --cache .cache/native-tools/android-recovery-001
```

所有由通用入口创建的 VM 均使用对应 cache 的 `software_android_vm.py stop`。该入口核对 PID 的启动时间和命令，再向该 attempt 的 QMP 发送 `quit`，等待进程退出与端口释放；不会按名字批量结束其他 QEMU。若进程已退出或记录不匹配，保留现场并报告，不操作碰巧复用相同 PID 的其他进程。默认新建 VM 的磁盘副本互相独立；不加 resume 时从选定 cache 的底盘创建，不自动继承上一 attempt 的修改。显式 resume 则复用已停止实例的可变盘，不能把该盘当作不变历史工件。本次恢复明确采用保留的已安装底盘，不能据此假定普通新建底盘已有游戏。

要验收真正的原生研究闭环，还需分别证明：固定 APK/内容/探针身份、ARM64 库可加载、真实 RPC reset/step/下牌反馈、自然终局与再次 reset、完整可训练轨迹、模型重新加载后的真实对局，以及对应回放。运行时准备成功不能替代这些证据。

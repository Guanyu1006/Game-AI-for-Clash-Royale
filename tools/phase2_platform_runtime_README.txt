第二阶段原生平台生命周期入口（2026-10-18）

在项目 PowerShell 中：
  .\.venv-phase2-core\Scripts\python.exe -B tools\phase2_platform_runtime.py status
  .\.venv-phase2-core\Scripts\python.exe -B tools\phase2_platform_runtime.py up
  .\.venv-phase2-core\Scripts\python.exe -B tools\phase2_platform_runtime.py down
  .\.venv-phase2-core\Scripts\python.exe -B tools\phase2_platform_runtime.py self-test

up 只使用已下载/已配置的项目内 Linux qcow2、WHPX QEMU、Redroid 和原生应用。
它不会安装软件、修改驱动、防火墙或全局网络，也不下载文件。
没有依赖时会明确失败，不会悄悄切换到第一阶段的近似模拟器。

启动步骤：
  1. 用 CIM 和 TCP listener 检查确认 VM/隧道的身份与端口所有者。
  2. 复用已存在的正确项目 VM，或者启动 -accel whpx、restrict=on 的后台 VM。
  3. 若项目 known_hosts 已有唯一有效 ED25519 pin，先用严格 SSH 验证并只读 boot_id。
     成功时记录 existing_project_known_hosts_strict_auth，不要求 cloud-init 再输出公钥。
     没有 pin 或认证未成功时，才从已核验 QEMU 的本地 serial.log 提取本次启动唯一
     ED25519 公钥，核对 SHA256 fingerprint 与 cloud-init/QEMU 创建时间。
     需要变更 pin 时，先在本次日志目录保存完整旧文件，再原子更新目标项。
     接着等待 SSH；启动 Android；等待 getprop=1。
  4. 重试 ADB connect，确认 ADB getprop=1；仅当应用未运行才启动应用。
  5. 设置 guest ADB 26789 转发；复用或建立 host SSH 26789 转发。
  6. 仅读取原生 status，等待 contentReady=true，再检查所有者。
本入口不会调用 reset/configure/advance/step/pause/resume，也不会修改战斗版本锁。
up 复用现有应用时会保留它当前的渲染/无画面模式和对局状态。
SSH 固定 StrictHostKeyChecking=yes、HostKeyAlgorithms=ssh-ed25519、UpdateHostKeys=no。
UserKnownHostsFile 使用 ASCII 相对路径 .cache/phase2/secondary_runtime/known_hosts，
避免 OpenSSH 把含空格的中文绝对路径错误解析成多个文件。工作目录固定为项目根目录。
现有 pin 通过严格 SSH 认证后可继续信任；主机公钥的变更只信任已核验项目 QEMU 的
本地 serial 输出，不使用 ssh-keyscan，不关闭主机身份验证；旧公钥历史保存在
known_hosts.before-local-serial-bootstrap。
cloud-init 重新生成 host key 时这条路径可以安全更新本地 pin；验证不通过时停止操作。
其他工具仍须在单独的独占运行窗口中操作一场对局。
原生探针只允许一个会话所有者。Doctor/status 发现活动对局控制器时会返回 busy，
列出控制器进程并跳过 RPC；native_ready=null 表示未重新探测。up 复用这样的完整
运行时时也返回 busy，不与控制器争抢会话。busy 的诊断退出码为 0。

端口仅绑定 127.0.0.1：
  22225 Linux SSH；22226 QEMU QMP；55555 Android ADB；26789 原生探针。
若端口由未知进程或非 loopback listener 占用，入口拒绝继续，不杀进程。
如果 Windows CIM / Get-NetTCPConnection 被当前终端权限或 Codex 沙盒限制，
入口会保存原始 stderr 并报告 inspection_failed；它不绕过限制。
这种失败不是 VM 启动成功。应在被授权、具有宿主进程检查权限的终端运行。

停止步骤：
down 首先拒绝在 phase2_replay/evaluate_native/view/cohort/batch 等控制器运行时停机；
也拒绝 phase2_platform.py --mode agent/replay，Archive 观察模式不阻止停机。
随后调用 tools/phase2_platform_guest_shutdown.py 的源代码，只在已验证的 Linux guest 中
准备有界关机：临时停用 apt 定时器，检查实际 apt/dpkg POSIX 锁，给三个更新服务
写 /run 下的 15 秒停止超时，并 daemon-reload 和读回属性核验。这些设置在重启消失，
不更改 /etc、不安装更新、不更改宿主。包锁被占用时默认拒绝关机并保存诊断。
然后 SSH sync && sudo shutdown -h now，等待原 VM 退出，再只结束经过身份复核的隧道。
该准备步骤处理隔离 guest 的 unattended-upgrades 关机 helper 原有 30 分钟等待；
它不承诺所有可能的 guest 服务都能在 90 秒内停止，未知故障仍保留日志。
默认不强制杀 VM。down --force 仅在优雅关闭超时后结束经过二次核验的项目 VM；
这可能丢失尚未同步的 guest 状态，应只用于恢复确实卡死的项目运行时。
若 guest 已进入关闭流程而无法完成准备，显式 --force 会保留准备失败并继续有界回收。
它不会结束其他 QEMU/SSH，也不会删除/重建 qcow2。

日志：
  outputs/platform_next/runtime/<UTC>-<action>-<PID>/report.json
  同目录 commands.json、逐命令 stdout/stderr、VM 或 tunnel 的后台日志。
  outputs/platform_next/runtime/latest.json 是最近一次调用的副本。
  outputs/platform_next/runtime/vm.json 与 tunnel.json 是本入口启动的元信息。
  原有 outputs/phase2/secondary 的历史元信息不被覆盖。
  lifecycle.lock 使用操作系统文件锁，进程退出/崩溃会释放，不靠猜测清理 PID。
  所有 child TEMP/TMP 都指定到项目 .tmp/platform_next/runtime。

关机修复实际验收应在新的冷启动 guest 上运行默认 down：
  guest_bounded_shutdown_prepare.stdout.log 中 status=prepared；
  after.<service>.TimeoutStopUSec=15s；
  最终 status=stopped 且报告没有 forced_vm_stop。
已有卡死 guest 仅使用 down --force --shutdown-timeout 10 恢复，再冷启动验证默认路径。
修复前失败日志应保留，不能把强制恢复当成优雅停机通过。

实现依据（systemd 官方文档）：
  https://www.freedesktop.org/software/systemd/man/258/systemd.service.html
    TimeoutStopSec 与 TimeoutStopFailureMode=kill 定义服务停止超时行为。
  https://www.freedesktop.org/software/systemd/man/258/systemctl.html
    mask --runtime 只持续至下次重启。
  https://www.freedesktop.org/software/systemd/man/258/systemd.unit.html
    /run/systemd/system 支持运行时 service drop-in。

集成验证必须由唯一的运行时所有者顺序执行：
  A. self-test；status（此阶段只读，不修改战斗）。
  B. up；确认 status=ready、native_status.contentReady=true、restrict=on 和已核验 PID。
  C. 再次 up；确认 vm_reused/tunnel_reused/app_reused=true，PID 和对局状态保持。
  D. 运行画面/回放/AI控制器，只能有一个控制器调用原生变更命令。
  E. 停止控制器后 down；确认 status=stopped，4 个端口关闭。
  F. 若需要，重新 up 验证冷恢复。不可与 formal batch / replay 并发执行。

QEMU 的 -L/-drive/-serial 使用固定项目工作目录下的 ASCII 相对路径，避免 Windows
QEMU 文件参数无法打开中文绝对路径；exe 仍由 Python 使用 Unicode 绝对路径启动。
启动后 1 秒检查退出码；后续等待每轮检查本次后台子进程，发现退出立即报告日志。

本实现子任务的实际验证：--help 和 self-test 通过；self-test 覆盖 18 项身份/端口/路径控制，
没有启动 VM。2026-10-18 沙盒内 status 检查到 4 端口均关闭，但 CIM 被拒绝；
原始证据 outputs/platform_next/runtime/20261018T034715.898396Z-status-36436/。
实际 up/down 与原生 contentReady 需由主代理在授权宿主窗口独占验证。

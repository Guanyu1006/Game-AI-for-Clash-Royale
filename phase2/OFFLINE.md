# 阶段B：隔离后的冷启动、真实回放与checkpoint对战

执行入口：`tools/phase2_offline_smoke.py`。当前工具已编写并通过Python编译检查，**尚未运行该完整冷启动验收**；主代理正式100局仍独占RPC，不能把此前运行时准备、源码存在或历史正控制直接记作本轮B通过。

## 具体执行顺序与预计时间

1. 仅在其他对局控制器释放后启动。检查项目内BC checkpoint、weights SHA与历史准备阶段同URL HTTP200原始证据，依赖必须提前取得。
2. 停止经PID/实际命令行核实属于本项目的SSH探针隧道；通过guest SSH执行sync和正常关机。等待旧QEMU完整退出，不强行启动第二份相同磁盘。
3. 从项目qcow2冷启动QEMU-WHPX，明确使用 `restrict=on`。通过当前宿主进程的实际CommandLine核实参数，而不只信任一份声明成功的JSON。guest前后 `boot_id` 必须不同。
4. 等待SSH与Android `sys.boot_completed=1`，启动已有Android容器；对固定公开URL重测直连、原host代理和两个无需DNS的外网TCP地址。URL/代理不得成功，SSH作为本机正控制必须成功。
5. 冷启动真实App并重建ADB与本地probe隧道，确认原生内容在隔离中初始化，重核652项installed内容SHA。
6. **新Python进程**运行真实回放CLI，保留43条真实动作、原始RPC、minimal状态与自然终局；脚本逐条核验实际消费与文件hash，不读取某个硬编码passed字符串作为结果。
7. **另一个新Python进程**加载固定checkpoint，在真实原生环境完成1局agent对战。验证真实model预测、对应模型动作消费事件、自然终局以及checkpoint/weights前后SHA不变。
8. 对同URL和直连外网地址再次测隔离，生成 `report.json`、每命令argv/退出码/日志SHA及产物清单。

预计VM/Android冷启1–3分钟、真实ordinary回放约30–60秒、单局模型对战约1–3分钟，总计约4–8分钟；按实际日志计时，不把估算作为保证。整个过程不修改宿主全局防火墙、代理、虚拟化驱动或账号设置。

## 运行命令

仅输出计划，不连接VM/ADB/RPC：

```powershell
.venv\Scripts\python.exe -X utf8 tools/phase2_offline_smoke.py --checkpoint models/phase2/bc/checkpoint.json --output outputs/phase2/offline/cold-smoke-001
```

主代理授予独占窗口，且真实BC已产出后：

```powershell
.venv\Scripts\python.exe -X utf8 tools/phase2_offline_smoke.py --run --exclusive-runtime --checkpoint models/phase2/bc/checkpoint.json --output outputs/phase2/offline/cold-smoke-001
```

输出目录要求尚不存在，避免覆盖失败证据。`--app-only` 可诊断仅App冷启动，但明确记录该较窄范围，不能用它冒称VM完整冷启动。默认使用完整VM关机/开机。

真实回放在项目 `.venv-phase2-core` 新进程执行 `tools/phase2_replay.py 85c0a48b6a1d47cc8057710e05bf7773`；agent在项目 `.venv` 新进程执行 `tools/phase2_evaluate_native.py --episodes 1 --offline-smoke --checkpoint ...`。后者是明确的B单局模式，永久 **不计入G要求的10局分母**；G仍须另跑正常10局。全WAIT、动作未确认、看门狗截断或没有实际自然终局均保留为失败，不强制代替模型出牌。

## 隔离证据边界

原生Android战斗计算与资源位于QEMU guest，usernet `restrict=on` 阻断其外部路由，仅保留显式loopback SSH/ADB转发。第一次依赖准备时已实际取得同固定GitHub URL经既有host代理HTTP200的日志：

`outputs/phase2/secondary/network_preparation_positive_control.stdout.log`

该原日志的SHA与URL/状态被锁入本轮报告；验收不会为了补一个正控制临时开放已冷启动的guest。负控制是在本轮隔离中真实发起请求，另有1.1.1.1/8.8.8.8:443实际TCP探测，避免只凭DNS不可用下结论。本地SSH和真实probe初始化/动作/终局构成独立的本机通信正控制。

Python控制器运行在宿主机。为收紧其网络边界，回放与agent两个新进程还经过项目内wrapper：禁止非loopback Python DNS/connect/connect_ex/sendto，并真实尝试外部canary确认拦截。只允许127.0.0.1/::1/localhost，随后才执行原CLI；保存独立network_guard.json。这个guard不是内核沙箱，不能声称覆盖任意恶意原生扩展绕行；它覆盖本项目已审计的Python控制路径，原生引擎本身由guest隔离。其他宿主应用的网络不会被修改。

没有网络异常日志、没登录账号、某个status显示ready、或只有一张截图，都不能单独证明B通过。

## 验收读取哪些实际数据

| 证据 | 验证内容 |
|---|---|
| `commands.json`与每条stdout/stderr | 真正执行的argv、退出码、时间、原日志SHA |
| guest boot_id前后 | 默认必须是新的完整guest启动 |
| 实际QEMU进程CommandLine | 必须含restrict=on，不接受restrict=off |
| `isolated_network_actual.*`和after_games日志 | 相同固定URL负控制、无DNS外网TCP拒绝 |
| `installed_content.json` | 真实Android内652项SHA与精确版本 |
| `replay/episode.json`与文件锁 | 源ID/源SHA、全动作结果与不可变输入 |
| `replay/wire.jsonl`、`states.jsonl` | 原生configure/advance、实际CardPlay事件与自然终局 |
| `agent/report.json`、`game-000/*` | 新进程加载的checkpoint/weights/version、model预测、每条模型消费事件、原始终局 |
| 两份network_guard.json | 本轮子进程实际外连canary拒绝及目标脚本hash/退出码 |

脚本会从raw `cr-minimal.v4`响应重建消费事件集合，核对每条模型反馈的card_event实际存在于原始wire，模型预测数量与确认消费数量一致；还从agent独立states日志交叉核实终局。只要必要证据缺失，就记录错误并返回非零，不回退到旧模拟器或猜测状态。

## 状态保存与隐私

guest关机、启动、网络、模型或重放任一步失败都会保存已经产生的commands、日志与报告；不会在不确定的动作结果上自动重试变更。只对启动期间的只读SSH/property/status执行有界等待。发生失败时先读该运行目录，下一轮使用新的输出目录。

全部新增guest数据保存在项目qcow2，Python环境/临时文件/模型与日志均在项目内。公开分享包排除 `.cache/phase2/secondary_runtime`（私钥、cloud-init user-data、VM磁盘、运行时）、`.cache/phase2/viewer`二进制缓存、专有APK/libg/assets与签名密钥。命令日志可引用私钥的项目内路径，但不包含私钥内容。

B完成后仍需F的完整真实episode viewer/录像与五锚点验证，入口见 `phase2/VIEWER.md`；它们必须串行使用同一probe。冷启动验收保留headless进程，viewer验证退出时可冷重启恢复headless；不把进程切换称为snapshot/restore。

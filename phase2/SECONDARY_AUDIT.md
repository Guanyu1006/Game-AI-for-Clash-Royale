# Scroll 原生候选与本机 Android 运行条件审计

审计日期：2026-10-15。以下区分源码观察、已取得依赖和本机实测；Android 启动不等于完成原生对局验收。主后端 FirstLight/Jason 的比赛与回放证据由主报告另列。

## 结论

Scroll 确实调用配套客户端 `libg.so` 计算战斗，包含建局、命令入队、逐 tick 推进与 checksum；其 Rust 部分主要提供会话和原生函数封装。它不是只有大厅/转发的项目。不过固定版本为 **1.3.2 / build85 / ARMv7**，目前公开接口偏训练 NPC，缺现代回放所需版本和完整结构化状态契约，因此保留为第二条原生候选，不能用它重放本阶段现代数据。

本机 WSL 实际不可用，已有 VMware 启动实测失败；项目内便携 **QEMU-WHPX → Ubuntu24.04.5 → Redroid Android12** 已成功启动，ADB 已报告 `device`，`sys.boot_completed=1`，zygote/surfaceflinger 均 running。官方固定 Redroid 文件系统实际包含 ARM64 Bionic 和 `libndk_translation.so`，提供现代 ARM64 APK 的本机执行切口。原生 APK 的初始化、对局与终局必须继续单独验收。

## 固定来源与取得结果

| 项目 | 固定值/实际结果 |
|---|---|
| Scroll 源码 | [上游仓库](https://git.xeondev.com/Supercell/Scroll)，commit `d897141dd5fb6c09753ff29761ef95c051c4655f`，42 个跟踪文件 |
| 源码目录 | `third_party/phase2/scroll`；仓库未见 LICENSE，不能推定 MIT/Apache 授权 |
| Release | [0.1 下载页](https://git.xeondev.com/Supercell/Scroll/releases)，API403，但公开 HTML/APK 实际下载成功 |
| Client APK | 95,409,307字节；SHA256 `6e4eb28057032699e33f4196d87c4adfcf90bf67f0781ae8964e320bc6b88d85` |
| Server APK | 94,250,140字节；SHA256 `bde6392c21e7fa8b3ae0423d410855e48e09c1bcaa93504d5c6eb912d5355db9` |
| APK 版本 | 二者均 versionName1.3.2、versionCode85，minSDK15/targetSDK23；全 ZIP CRC 实测通过 |
| ABI | APK 中 ELF 均为 ELF32/ARM，`armeabi-v7a`；不是 ARM64 或 Windows DLL |
| 原始 libg | 4,410,156字节；server libg 与 client libg.so.bak 相同，SHA256 `f73b70d714d1423c6a8ef520982ce49d870e2db027021ebdffdd755efece6a80` |
| 修改 client libg | SHA256 `7d6ea4370ecc4e06fef40ea34375186de2bd7cfa90f5859d446ea086ab544e0a` |
| server libserver | 2,551,596字节；SHA256 `4a5e9fd0c5e5bab76c91862a3e3643733162455ff91c7b75655cea3e65394483` |
| 资源 | 两 APK 各629项 assets，约89MB；APK/原生库/资源的权利独立于封装源码，不进入公开分享包 |

完整清单见 `outputs/phase2/secondary/scroll_sources_manifest.json`、`scroll_apk_audit.json`。文件保存在 `third_party/phase2/scroll_assets/`，只用于本地研究；未从下载成功推导再分发权限。

## 真实战斗计算调用链

1. `libserver/src/lib.rs::JNI_OnLoad → server_main → resources::init`：在 Android 已加载 `libg.so` 后启动 Rust 服务，监听 **127.0.0.1:9339**，每连接一个线程。
2. `ffi_util.rs` 从 `/proc/self/maps` 寻找 libg 基址，`import!` 以基址加固定 RVA 调原函数；Thumb 地址含 `+1`。资源初始化利用配套 GameMain/ResourceManager 和 CSV loader。
3. `session/mod.rs::PlayerSession::start_mission` 创建 `LogicGameMode::new(true)`，配置 NPC、训练地图与 arena54000001，将卡组/avatars 交给原生逻辑。
4. `session/handlers/home.rs::on_end_client_turn_message` 按客户端请求 tick 循环推进，在 execute_tick 把命令交给原生 `LogicCommandManager.add_command`，再调用 `LogicGameMode.update_one_tick`。
5. `logic/mode.rs` 实际指向原生构造器 RVA0x119DAC、update0x11A718、checksum0x11A85C、encode0x11A8D4、destruct0x11A034（均 ARM Thumb `+1`）。逐次 checksum 与客户端比较，差异产生 out-of-sync 消息。

这些函数调用是实质原生战斗切口，但本次尚未在 ARMv7 Android 完成训练战斗；源码存在不等于该构建本机已运行。

## 七项能力边界

| 问题 | 已确认 | 未确认/缺口 |
|---|---|---|
| 初始化依赖 | 需 Android 宿主、相同 libg 与资源；公开 APK 已取得 | 不能 Windows 直接加载；现代 ARM64 容器不提供32位 Android 用户态 |
| 重置/步进/动作 | new/destruct、原生逐tick、命令队列封装存在 | 尚无稳定公共 headless API；技能/现代形态不适用该旧版本 |
| 状态/终局 | 原生 checksum 与网络 sector encode 存在 | `game_object_manager` 仍 usize/TODO；没有已验证实体/终局 JSON 接口 |
| 离线性 | 源码监听 loopback，会话/训练由本地服务计算 | 未实测 Android 首次资源初始化；不能只凭 README 宣称完全离线 |
| 批处理/并行 | 不绘图的原生 update 调用切口存在 | 全局资源/单例、每连接线程，隔离、线程安全、内存与吞吐尚未实测 |
| 画面 | 配套 client APK 负责原生渲染，server APK 按说明黑屏 | 同局显示与自动控制尚未实测 |
| snapshot/fork | home/avatar 存档与 battle encode 均有源码 | home存档不是完整战斗恢复；未证明 battle decode/restore，未证明 seed 与确定性 |

## 已执行的最小构建/加载尝试

`tools/phase2_secondary_apk_audit.py` 实际执行 Rust 构建命令 `cargo ndk -t armeabi-v7a build --locked --release`，本机缺 cargo/NDK，因此 FileNotFound；没有安装全局 Rust/SDK 或接受 SDK 协议。实际 `ctypes.CDLL(libg/libserver)` 均得到 Windows **WinError193**，这是 ABI/OS 格式拒绝，不能记成原生库成功初始化。原先 `adb devices -l` 为工具不存在；后续 ADB 安装在项目磁盘内的 Linux guest，未安装 Windows 全局 ADB。

随后将 Scroll server APK按上表SHA校验传入真实 Android12_64only，实际安装返回 **`INSTALL_FAILED_NO_MATCHING_ABIS`，res=-113，exit1**，证据 `scroll_android_install_result.{json,stdout.log,stderr.log}`。该64位运行时没有 ARMv7用户态/32位 zygote，必须另选含32位支持的固定 Android镜像才能运行 Scroll；未为非主候选再下载一套大镜像。旧包未启动，不污染主候选游戏会话。

## 本机运行条件：失败与成功证据

| 路径 | 实际证据 | 结论 |
|---|---|---|
| WSL | `wsl --status` exit50；version/list exit1；无分发版 | exe 存在不代表 WSL 可用；不启用系统组件 |
| 已有 Android 模拟器 | 标准安装位置与卸载项未发现 MuMu/BlueStacks/Nox；PATH 无 ADB/emulator | 未全盘搜索，不断言不存在任何未登记目录；未安装 MuMu/接受专有协议 |
| 已有 VMware17.6.3 | vmrun list 可执行，项目VMX实际 start 失败；MonitorModeULM 后0xc0000005 | 保留日志，不修改驱动/系统设置；未把 vmrun存在算guest运行 |
| 便携 QEMU | 11.1.0，公开 Windows NSIS包仅7zip解包；实际 WHPX guest SSH成功 | 不执行安装器，不改全局PATH/驱动 |
| Linux guest | Ubuntu24.04.5，glibc2.39，kernel6.8.0-146；4vCPU/4GB；10GB sparseqcow2 | 全部新增磁盘/配置位于项目 `.cache/phase2/secondary_runtime` |
| Android | 固定 Redroid12_64only amd64，boot_completed1、zygote/surfaceflinger running、ADB device | 主ABI x86_64，候选 ARM64 翻译执行需要逐 APK 实测 |

主机 CPU 的 CIM 虚拟化标志与 HypervisorPresent 不一致；实际 WHPX 启动成功更有判别力。没有据隐藏的 CPU 标志建议启用 BIOS。主机清单只保留相关字段，不保留完整 systeminfo 的个人字段。

## Linux/Android 固定运行时

- QEMU：[官方列出的 Windows 构建站](https://qemu.weilnetz.de/w64/)，`qemu-w64-setup-20260811.exe`，206,615,928字节，与上游 SHA512匹配；QEMU及依赖的各自许可证随解包保存。
- Ubuntu：[固定 minimal cloud image](https://cloud-images.ubuntu.com/minimal/releases/noble/release-20261008/)，264,503,296字节，SHA256 `2f119f50bf29fe40e73b69ae2a736e8210d8146796f036e0324315b6b3a777df`，与公开 SHA256SUMS匹配。
- Redroid：[官方文档](https://github.com/remote-android/redroid-doc)，commit `eba9a489990f3f558b7115f25fdc782d610ee8ad`。固定 OCI amd64 manifest `sha256:e6f799d56b9a9a2bbc6224b5b7a6dc744c9b4d878ac856f27f0c4ec793ef55d3`，单层646,059,401字节，SHA256 `5c010910096953e774cdbb3687e00a315c8d59101fa240b3789d4274db03fe9b`，解包内容约1.52GB。实际镜像有 ARM64 linker/Bionic/GLES 与 `libndk_translation.so`，并非仅引用 README。
- 该层经 SSH流式导入guest Docker，显式保持 upstream `/init qemu=1 androidboot.hardware=redroid` entrypoint；Docker import 会改变 config/imageID，已记录为 `c89c186289a3645320445f3beeb8554ac91f00c8a56e8e41e8100281ecce828d`，未冒称 OCI 原 config。
- Redroid 主项目 Apache2、Linux kernel GPLv2；ARM 翻译器是公开镜像预置的单独组件，不能推定为开源或可自由再分发。镜像含系统 NOTICE，公共分享包排除镜像/专有库/APK/密钥。

ADB34.0.4、Docker29.1.3、containerd2.2.1 和 binder 内核模块均实际装在 guest 内。Linux无ashmem时使用 memfd。运行时脚本见 `tools/phase2_secondary_guest_prepare.sh`、`phase2_secondary_guest_runtime_services.sh`。

## 实际修复及可继续命令

1. Windows QEMU 对中文绝对固件路径报 invalid argument，全部命令改为项目 cwd 下 ASCII 相对路径，无 subst/全局配置。
2. NoCloud 需 SMBIOS `serial=ds=nocloud`；DHCP在网络模式切换时未及时完成，改固定 guest10.0.2.15，密钥仅SSH，无密码。
3. binderfs 新节点最初0600导致 Android服务 Permission denied，改0666；Docker `--device` 创建新 mknod 丢失 binderfs inode 私有状态导致 ENXIO，改为真正 bind mount 后 Android启动成功。
4. VM 启动器加入 QMP占用与进程立即退出检查，避免把尚未完成关机时端口冲突的进程当成功启动。
5. 正式隔离冷启动的 network-online 曾等待120秒；等待结束后SSH仍可用，已在 guest 内禁用该可选等待服务，不影响固定地址与外网隔离。失败与成功启动证据分别保留。

```powershell
.venv-audit\Scripts\python.exe tools/phase2_secondary_vm.py qemu-start --accel whpx --isolated
.venv-audit\Scripts\python.exe tools/phase2_secondary_guest_probe.py --label check --command "sudo phase2-android-start; adb connect 127.0.0.1:5555; adb devices -l"
.venv-audit\Scripts\python.exe tools/phase2_secondary_guest_exec.py tools/phase2_secondary_guest_network.sh --label network_isolated_control --timeout 60
```

正式模式 QEMU usernet `restrict=on`，仅显式开放主机 loopback22225→guest SSH22、loopback55555→guest adbd5555；准备模式 `restrict=off` 用于公开依赖取得。准备模式经本机已有代理可访问固定 GitHub文件，已取得 HTTP200正控制；正式隔离模式对同URL请求 DNS失败、对同host代理请求 Connection refused，同时SSH正控制仍成功，证据为 `network_preparation_positive_control.stdout.log` 与 `network_isolated_control.stdout.log`。这验证当前路由隔离，未声称对所有未来配置作形式证明。

## 现代 ARM64 切口实际进展

`outputs/phase2/native/offline-native-smoke.apk` 经 SSH 流式拷入 guest，主机/guest SHA256 均为 `97bad257c5d6e0499d2fe7e674f1f4de24fc28c19a8456c574dd8a8e8ea42c9a`。真实 Android `adb install -r` 返回 **Success**，包为 `nullsroyale.rel.free`。切换并验证正式隔离后，`am start -n nullsroyale.rel.free/com.supercell.clashroyale.GameApp` 成功；实际 pid1585，logcat 显示 GameApp Displayed 和 ndk_translation EGL调用，Android TCP127.0.0.1:26789实际 LISTEN。

`adb forward tcp:26789 tcp:26789` 加项目 SSH loopback隧道后，主机127.0.0.1:26789可供原生适配器调用。**RPC由 native_engine 子代理独占**，此审计不同时执行 reset/advance，以免污染同局。初次启动尚无 .86 update，必须明确是基包15.535.3资源诊断；精确资源更新、attest与真实对局结果由原生报告后续记录，不能仅凭端口监听宣称完成。

`outputs/phase2/secondary/native_first_frame.png` / `native_loading_frame.png` 是实际 `adb exec-out screencap -p` 原始帧，已用图像查看工具检查为游戏加载画面，包含原生客户端角色与95%加载条，不是平台自行重绘。第一张有 Android 首次全屏提示；仅点击系统 GOT IT 后取第二张，未点击游戏下牌/联网按钮。截图保留来源和SHA，加载画面不代表已经显示同场战斗。

## 完整15.535.86资源安装与隔离冷启动

主代理随后取得651项完整delta资源，native_engine生成 `outputs/phase2/native/runtime-update.tar`，加入 canonical fingerprint 后共652文件/124,875,056内容字节；archive125,655,040字节，SHA256 `2d067ac41de200b849a381030a89fe83505d579bcc819a9429cfc0820ed1785d`。主机与guest传输后SHA一致，guest暂存区先校652项，再经 Android root命名空间流式tar安装到app update目录。

首次 `docker cp` 到 Android运行中动态挂载的 `/data/user/0` 路径被daemon报不存在；该失败不等于Android内实际目录缺失。改为 `docker exec -i ... tar` 在真实mount namespace内安装，失败日志 `native_update86_install.*` 和成功日志 `native_update86_install_namespace.*` 均保留。仅在native_engine确认其base对局与RPC已结束后force-stop，未中断运行中的对局。

旧update保存为 `/data/user/0/nullsroyale.rel.free/update.before-20261015T032934Z`。新目录设置appUID/GID10070:10070并执行restorecon；真实installed **652/652 SHA256通过**，不是仅校传输包。`installed_update86_hashes.json` 保存逐文件实际SHA与预期SHA；唯一 `assets.scdb` 的公共SHA1元数据不符例外沿用精确固定上游SHA256，其他文件不放宽验证。

安装后 fingerprint SHA256为 `e0cb2fb9eb2fdd0df1afcb00df3e3ba61cf25aa8b24e39c17cc391d952a4e339`，content version `15.535.86`，content manifest SHA1 `41d8df308b22bf144c2a4f2b75f6e6fbf69a0c82`。在保持QEMU `restrict=on` 条件下冷启App，新pid2132，26789实际LISTEN；新进程 maps与installed libg/probe SHA仍匹配，证据 `android_update86_loaded_identity.stdout.log`。`network_update86_cold_start.stdout.log` 再次对同URL/hostproxy验证隔离。RPC已交回native_engine独占，.86对局结果仍以其原始wire/state报告为准。

## 真实原生战斗画面验证

native_engine随后实际运行`.86`渲染器，报告 native-render/ready，将同一原生对局停在tick120后释放RPC。此审计抓取 `outputs/phase2/native/render/runtime86_first/frame.png` 并实际使用图像查看工具核验：完整原生竞技场、河流双桥、六座塔与已下牌单位均可见，已不再是加载屏；SHA256 `d6370329fb44ea6a1c0d8917161e398369422bdf35f41287df1e1b968fb16dc8`。实际截图来源为 Android framebuffer，未自行绘制或修改像素。

原生渲染RPC报告/命令日志保存在同目录。单张静态帧不证明视频帧率；该最小原生battle view没有完整手牌HUD。同场状态/tick与动作消费仍须结合其raw wire/state证据，不由截图推断。截图完成后经native_engine授权force-stop并冷启，恢复新进程pid2418/loopback26789；渲染中的战斗状态没有跨进程保留，RPC交回进行新的headless验证。

运行文件/证据目录：`outputs/phase2/secondary`。私钥、guest user-data、VM磁盘和 APK 均不得放入分享包。原 VMware 崩溃留下的2GB orphan vmem 已在确认无 vmware-vmx进程、核对绝对路径位于项目后删除；失败日志和VM配置保留。

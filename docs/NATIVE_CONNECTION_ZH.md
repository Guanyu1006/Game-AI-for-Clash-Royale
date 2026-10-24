# 原生后端连接与验收边界

连接设置已经接入保留的 NativeBattleBackend。它支持本机 loopback TCP，也支持将用户自有原生主机的 probe 经 SSH 转发到 Codespaces loopback；配置不会创建 Android 或补齐游戏资源。

本次已经在 Codespaces 内实际准备软件 Android、固定资源和已安装游戏，并验证过原生推进；当前步骤与证据见 [原生指南](NATIVE_LOOP_ZH.md) 和 [软件 Android 指南](SOFTWARE_ANDROID_TCG_ZH.md)。在其他没有对应资源或 Android 实例的环境，预检仍应返回 unavailable。普通 TCP 服务、接口测试夹具、近似战场和历史身份回执均不能替代实际原生验收。

## 独立复核固定更新资源

固定更新资源有独立、可重复的入口，默认只读、零网络：

    .venv-cloud/bin/python tools/cloud/native_content.py inspect \
      --output outputs/cloud/native-content-inspect-001

它仅核验清单中的 paired_content 与 content_fingerprint，当前共有 652 项。ready_content 表示这批资源逐项 SHA256、大小正确；Android、APK、probe 和实际原生对局还需分别验收。本次完整恢复与独立复核证据在 outputs/native_loop_20261020/content_setup/local-audit-final.json。

以后缺少这些资源时，可显式恢复到清单原路径，并使用新的证据目录：

    .venv-cloud/bin/python tools/cloud/native_content.py fetch \
      --output outputs/cloud/native-content-fetch-001 --max-mib 160 --workers 2

fetch 只访问已审查的固定静态 CDN，不执行下载内容，不访问账号或对局服务。缓存也会重新核验强哈希；不同的已有文件不会覆盖。最多两并发、单文件最多 32 MiB，连续两次失败后停止安排新请求，已经在途的请求会保留结果；不会自动重试或更换 IP。fingerprint 仅接受已锁定的原文及精确派生表示，两者都校验 SHA256。已有证据目录会拒绝复用。

## 先运行零 RPC 预检

仓库根目录执行：

    .venv-cloud/bin/python tools/cloud/native_connection.py check

默认不连接，不创建 VM，不 reset、不推进对局；报告包括缺失资产数量、固定版本校验和 Android 身份回执。ready_assets 仅表示本地资产及回执通过现有严格校验，不代表当前有可运行原生对局。失败返回码为 1，报告保留实际原因。

需要明确选择端口或身份回执时，创建独立配置：

    .venv-cloud/bin/python tools/cloud/native_connection.py init \
      --output outputs/cloud/native/connection.json \
      --port 26789 \
      --timeout 10 \
      --manifest data/phase2/version_manifest.json
    export ROYALE_NATIVE_CONNECTION=outputs/cloud/native/connection.json
    .venv-cloud/bin/python tools/cloud/native_connection.py check \
      --config outputs/cloud/native/connection.json

init 仅写文件；已有同名配置会拒绝覆盖。清单字节 SHA256 被保存到配置，更换清单后必须创建经过审查的新配置。路径必须位于当前仓库。现有 Windows 入口没有设置该环境变量时继续使用原默认值。

程序接口：

    from royale_phase2.connection import check_connection, load_connection
    from royale_phase2.backend import NativeBattleBackend

    report = check_connection("outputs/cloud/native/connection.json")  # 0 RPC
    settings = load_connection("outputs/cloud/native/connection.json")  # 0 RPC
    backend = NativeBattleBackend(connection_config="outputs/cloud/native/connection.json")

构造 backend 会校验本地全部 manifest 资产和记录的 Android 身份回执，尚不连接 probe；显式 reset 才开始原生对局。连接配置不能绕过缺失 APK、libg、资源或身份回执。

## 为新构建版本采集 Android 身份回执

新生成的离线 APK 有独立 SHA256，旧身份脚本硬编码的 APK 哈希不能授权它。先用 native_version.py 创建独立版本清单；当前构建清单为 outputs/native_loop_20261020/local-runtime-version-001.json。设备已安装该清单对应的 APK、已放置更新资源、启动应用且 status 的 contentReady=true 以后，在有该设备 ADB 连接的主机执行：

    .venv-cloud/bin/python tools/cloud/native_identity.py \
      --manifest outputs/native_loop_20261020/local-runtime-version-001.json \
      --serial YOUR_DEVICE_SERIAL \
      --adb .cache/native-tools/bin/adb \
      --adb-port 5038 \
      --output outputs/cloud/android-identity-check-001

将 YOUR_DEVICE_SERIAL 换成用户明确选择的已连接设备，不会自动列举、扫描或连接设备。项目内 ADB 使用 5038；用户自有主机的现有 ADB 通常使用 5037，需显式选择本机实际端口和可执行文件。无可用设备或资源尚未映射时，此命令返回 unavailable 并保存日志，不生成成功回执。本次软件 Android serial 为 127.0.0.1:15555、root-shell 为 su0；不要在初始化仅几十秒时采集身份。

默认要求 adbd 的 shell 已经是 root。若用户原生设备已具备 su 0，可显式添加 --root-shell su0。工具不执行 adb root、不启动或停止 ADB daemon、不安装 APK、不推文件、不重启游戏、不连接 probe。它先用只读 host:version 核对既有 ADB server 协议，避免 adb 客户端的版本不一致重启行为；设备命令固定使用显式 -H 127.0.0.1 和 -s serial。[Android ADB 客户端实现](https://android.googlesource.com/platform/packages/modules/adb/+/refs/tags/android-14.0.0_r27/client/adb_client.cpp)

采集过程核验安装包路径、当前 PID 和进程启动时间、实际 maps 中的 APK/libg/probe/update assets.scdb；随后单个只读脚本对 APK、两份已加载库和全部 652 个更新文件计算 SHA256 与大小，再核对进程、映射路径和安装路径没有变化。所有命令、脚本、stdout/stderr 及其哈希保存在新输出目录中。

只有实际设备证据全部符合显式新清单，才会写入清单指定的 phase2-native-adb-identity.v1 回执。已有回执不会覆盖；可用 --identity-output 指定新的仓库内路径，并在连接配置的 identity_path 中选择它。回执是这次进程和文件检查的记录，不是 live probe attest，也不代表对局已经验收；进程、安装包或更新资源变动后必须重新采集。测试注入的 fake ADB runner 一律标记 interface_only，不生成 backend 可接受的成功身份。

## 可选的外部原生主机

以下保留 Windows／外部 Android 的连接方法。用户已有对应主机时可以使用；本次项目内软件 Android 不需要外部主机或 SSH。端口转发本身仍不能生成 Android 系统。

原本完整 Windows 项目可继续使用：

    .\run_platform.ps1 -Mode Doctor
    .\.venv-phase2-core\Scripts\python.exe -B tools/phase2_platform_runtime.py up

该项目已有启动器会管理已配置的项目 VM、应用和本机转发。不要同时启动 Agent、Replay、Human 或其他采集进程争用同一原生 RPC。运行时内部的应用启动及转发命令可在 tools/phase2_platform_runtime.py 中审查。

若用户的原生主机直接运行已配置的 Android 设备，且原 probe 只监听设备 loopback，可在该原生主机终端按实际设备 serial 设置转发：

    adb -s ANDROID_SERIAL forward tcp:26789 tcp:26789

这要求 Android 应用、probe、资源已安装并启动；adb forward 本身只建立端口转发。serial 必须由用户选择实际设备，工具不会扫描网络或默认接管其他设备。

如原生主机已运行 SSH 服务，且当前用户已有正常登录方式，在 Codespaces 另开终端：

    ssh -o ExitOnForwardFailure=yes -o ServerAliveInterval=15 -N \
      -L 127.0.0.1:26789:127.0.0.1:26789 USER@NATIVE_HOST

将 USER@NATIVE_HOST 换为用户自己的主机。保持 probe 和隧道均绑定 loopback，不需要公开 probe 端口；由用户在正常 SSH 流程中选择身份与确认主机。此工具不读取 SSH 私钥、不更改 SSH 配置、不建立隧道。

同一原生服务没有其他控制者时，显式发一次只读 status：

    .venv-cloud/bin/python tools/cloud/native_connection.py check \
      --config outputs/cloud/native/connection.json \
      --connect \
      --output outputs/cloud/native/connection-status-001.json

此操作获取项目原生锁，只发送协议握手 session-v1 和一次 status。不会自动重试、reset、下牌、推进、重启应用或回退到近似模拟器。rpc_calls=1 指尝试的一次 status 请求，协议握手另计。连接失败保留具体错误。

## 必须区分的证据

- manifest_sha256 与 version_fingerprint：本地清单及锁定文件的身份。
- Android identity receipt：曾经在具体进程和文件上完成的检查；变更运行时后需要重新取证。不能复制旧回执就声称新主机已通过。
- contentReady=true：当前 status 表示内容已初始化；这个最小 probe 没有 live identity attest 命令，不能据此证明远端加载的是哪一份 libg。
- 实际原生验收：还必须执行 reset、双方下牌、原生消费确认、完整对局或明确截断、终止后再 reset，以及保存模型在同一环境中的完整运行。通过这些以前，连接检查的 native_runtime_verified 和 native_battle_execution_verified 均保持 false。

新增连接测试用很小的合成 ELF 文件和本机 TCP 接口夹具验证配置、拒绝、握手和断线行为；没有游戏物理，也不计作原生对局。运行：

    .venv-cloud/bin/python -m unittest tests_cloud.test_native_connection -v

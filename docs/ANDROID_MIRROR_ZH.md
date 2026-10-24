# 在自己电脑打开云端 Android 镜像窗口

Android 与固定原生引擎仍运行在 Codespaces；**scrcpy 图形窗口运行在你自己的 Windows 电脑**。在远程终端运行 Windows 脚本不会弹出本机窗口。项目新增入口通过 GitHub CLI 建立私有 SSH 隧道，默认只看屏幕、关闭声音和触摸控制，不创建 VM、不重启 ADB、不建局或下牌。

这是一条已实现并经过静态／计划测试的入口，尚未在用户 Windows 电脑验收。当前软件 Android 的启动配置为 360×640、1 FPS、软件 GPU；镜像帧率上限不等于源画面帧率。Android 黑屏时，scrcpy 也可能显示黑屏；是否有可用视频编码器仍需实际连接核实。

## 1. 只做一次的本机准备

打开**本机 PowerShell**。镜像脚本是单文件，不依赖 Python：可以在本机仓库使用 `tools/android_mirror.ps1`，也可以在原生页面下载已经写入当前 Codespace 名称的 `android_mirror.ps1`，将它放到专用工作目录。仓库入口以项目目录为根；单独下载时以脚本同目录为根。若已在 VS Code 连接 Codespace，可从 Windows 开始菜单另开 PowerShell；不要把远程 `/workspaces/...` 路径当作本机目录。

1. 从 [scrcpy 官方 Windows 页面](https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md)进入官方 Releases，下载适合本机架构的 portable ZIP。将包内文件解压到本机项目 `.cache/native-tools/scrcpy/`，确认该目录直接包含 `scrcpy.exe`、`adb.exe` 和 `scrcpy-server`，保留同包 DLL。无需全局安装，也不要只复制一个 EXE。
2. 本机已有 GitHub CLI 可直接使用；没有时从 [GitHub CLI 官方 Releases](https://github.com/cli/cli/releases/latest)下载 Windows ZIP，解压到本机项目 `.cache/native-tools/github-cli/`，确认存在 `bin/gh.exe`。本机还需要 Windows OpenSSH 的 `ssh.exe`；脚本只检查，不替你修改 Windows 系统功能。
3. 用户自行在本机使用已有 GitHub CLI 登录。portable 入口示例为 `& .\.cache\native-tools\github-cli\bin\gh.exe auth login`。工具不会读取、复制或输出凭据、私钥；认证和 SSH 身份由用户自己的官方 GitHub CLI 管理。官方说明 `gh codespace ssh` 在没有可用身份时可能自行创建 SSH 密钥，因此不能把该官方工具自身的账号状态声称为项目产物或擅自搬入 Git。

全部项目下载、日志与生成计划放在仓库内被忽略的 `.cache/`、`outputs/` 中；不要提交 portable 二进制或账号状态。已安装的本机工具和旧 Windows Python 环境保持各自用途，镜像脚本不安装 Python 包。

## 2. 获得准确的 Codespace 名称

在当前 **Codespaces 远程终端**运行：

```bash
.venv-cloud/bin/python tools/cloud/android_mirror.py plan
```

它只读 `CODESPACE_NAME` 并打印计划，不连接 ADB、RPC 或 GitHub。复制其中的 `powershell_command` 到本机 PowerShell，便可避免手填当前名称。没有该环境变量时，显式加 `--codespace 真实名称`。可通过 `--output outputs/cloud/android-mirror-plan.json` 新建计划文件，已有文件不覆盖。

## 3. 本机一条命令打开镜像

原生页面下载的单文件已经填入当前实例名，在它所在目录运行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\android_mirror.ps1
```

它默认寻找同目录下的 `.cache/native-tools/scrcpy/` 和 `.cache/native-tools/github-cli/bin/gh.exe`；也可用下述参数指定已有本机工具。尚未下载 portable 工具时，脚本会指出缺少的文件与文档，不会自动全局安装。

本机项目根目录：

```powershell
.\tools\android_mirror.ps1 -Codespace '这里填当前名称'
```

脚本先核对工具与本机端口，再建立隧道、查询云端 ADB 的协议版本，版本相同后才启动只读 scrcpy。关闭镜像窗口会结束本次脚本创建的 gh／SSH 进程树；Android VM、游戏和其它终端服务继续保留。

如果 scrcpy 已解压在项目内的其它目录：

```powershell
.\tools\android_mirror.ps1 -Codespace '这里填当前名称' `
  -ScrcpyDir '.cache/native-tools/scrcpy-win64' `
  -GhPath '.cache/native-tools/github-cli/bin/gh.exe'
```

只检查本机文件或只打印计划：

```powershell
.\tools\android_mirror.ps1 -Codespace '这里填当前名称' -Action Doctor
.\tools\android_mirror.ps1 -Codespace '这里填当前名称' -Action Plan
```

如果 PowerShell 因执行策略拒绝脚本，可在**本机**单独运行以下进程级入口，不改机器级策略：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\tools\android_mirror.ps1 -Codespace '这里填当前名称'
```

## 4. 它连接了什么

```text
本机 scrcpy GUI
  ├─ 本机 127.0.0.1:15038 ──私有 SSH── 云端 127.0.0.1:5038 ADB server
  └─ 本机 127.0.0.1:27184 ──私有 SSH── 云端 127.0.0.1:27183 视频转发
                                         └─ Android 设备 127.0.0.1:15555
```

默认使用 `--force-adb-forward --port 27183 --tunnel-port 27184 --no-control --no-audio --no-power-on`，只在脚本期间设置本机 `ADB_SERVER_SOCKET`，结束后恢复旧值。不会把 ADB 改成公网监听，也不会改变默认本机 5037 daemon。脚本先用只读 `host:version` 协议核对远端 daemon，版本不同即退出，避免 ADB 客户端为兼容性而重启云端服务。

需要的云端前提是：VM 与 Android 已运行，仓库 ADB server 已在 5038 服务，正确设备已经连接。镜像入口不会补建这些前提。`contentReady` 只是内容可用；要显示原生战场，还应在 [8772 原生页面](NATIVE_VISUAL_ZH.md)点击新建 rendered 对局。headless 的数值采集局不会自动变成可见战场。镜像读取不会推进原生引擎，暂停中的战场会保持静止。

## 5. 常见情况

| 现象 | 下一步 |
|---|---|
| 找不到 `scrcpy.exe`／`adb.exe` | 核对 portable ZIP 的解压层级，或用 `-ScrcpyDir` 指向同时包含二者的目录 |
| 找不到 `gh.exe` | 按前文放置 portable GitHub CLI，或用 `-GhPath` 指定已有本机工具 |
| gh 报未登录 | 用户自行在本机完成官方 CLI 登录，不向聊天发送 token |
| 隧道失败／超时 | 查看本机 `outputs/android-mirror/<时间>/tunnel.stderr.log`；检查 Codespace 存活、访问权限和 sshd，不重启原生 VM |
| 本机端口被占用 | 例如加 `-LocalAdbPort 15039 -LocalVideoPort 27185`，保留已有服务 |
| 云端 ADB 协议版本不同 | 使用兼容的官方 portable ADB；不要执行 `adb kill-server` 结束当前原生会话 |
| scrcpy 编码失败 | 在本机运行同入口 `-Action Encoders`，保存日志；它会连接已有设备并列编码器，不创建对局。尚未证实本 VM 可编码，不猜测编码器名称 |
| 窗口全黑 | 先区分 Android 系统界面与游戏画面；镜像不能修复 EGL／软件渲染／内容加载问题。保留 PNG 和实际日志 |
| 显示慢或画面静止 | 当前源显示约 1 FPS，且 TCG 不保证现实实时速度；暂停局静止是预期。`-MaxFps 15` 只改传输上限，不改 VM、GPU 或源 FPS |

需要分开查看两侧日志时，在两个**本机**终端分别运行 `-Action Tunnel` 与 `-Action View`，参数保持一致。Tunnel 终端结束时按 Ctrl+C。

本次仅完成入口实现与离线检查；成功打开本机窗口、编码性能、游戏非黑画面与事件回放分别验收。不会把计划输出或 scrcpy 官方功能描述算成本项目已运行成功。

官方依据：[Redroid 查看屏幕](https://github.com/remote-android/redroid-doc#getting-started)、[scrcpy 私有隧道](https://github.com/Genymobile/scrcpy/blob/master/doc/tunnels.md)、[只读控制](https://github.com/Genymobile/scrcpy/blob/master/doc/control.md#read-only)、[编码器与帧率](https://github.com/Genymobile/scrcpy/blob/master/doc/video.md)、[gh codespace ssh 参数](https://cli.github.com/manual/gh_codespace_ssh)。

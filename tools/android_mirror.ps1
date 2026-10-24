# 在用户电脑运行。工具只镜像现有 Android，不创建 VM、建局或下牌。
[CmdletBinding()]
param(
    [ValidatePattern('^[A-Za-z0-9][A-Za-z0-9-]{0,120}$')][string]$Codespace = '__ROYALE_CODESPACE__',
    [ValidateSet('Start', 'Plan', 'Doctor', 'Tunnel', 'View', 'Encoders')][string]$Action = 'Start',
    [string]$ScrcpyDir = '.cache/native-tools/scrcpy',
    [string]$GhPath = '',
    [ValidateRange(1024, 65535)][int]$LocalAdbPort = 15038,
    [ValidateRange(1024, 65535)][int]$LocalVideoPort = 27184,
    [ValidateRange(1024, 65535)][int]$RemoteAdbPort = 5038,
    [ValidateRange(1024, 65535)][int]$RemoteVideoPort = 27183,
    [ValidatePattern('^127\.0\.0\.1:[0-9]{1,5}$')][string]$Serial = '127.0.0.1:15555',
    [ValidateRange(1, 60)][int]$MaxFps = 5,
    [ValidateRange(5, 180)][int]$ConnectTimeout = 45
)
$ErrorActionPreference = 'Stop'
$RepoRoot = $PSScriptRoot
if ((Split-Path -Leaf $PSScriptRoot) -eq 'tools') { $RepoRoot = Split-Path -Parent $PSScriptRoot }
if ($Codespace -notmatch '^[A-Za-z0-9][A-Za-z0-9-]{0,120}$') {
    throw '请从当前原生页面重新下载已写入实例名的镜像脚本，或使用 -Codespace 显式指定名称。'
}
if ($LocalAdbPort -eq $LocalVideoPort -or $RemoteAdbPort -eq $RemoteVideoPort) {
    throw '同一侧 ADB 与视频端口必须不同。'
}
$DevicePort = [int]($Serial.Split(':')[-1])
if ($DevicePort -lt 1 -or $DevicePort -gt 65535) { throw 'Android device 端口超出范围。' }

$TunnelArgs = @('codespace', 'ssh', '--codespace', $Codespace, '--', '-N', '-T',
    '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15', '-o', 'ServerAliveCountMax=3',
    '-L', "127.0.0.1:${LocalAdbPort}:127.0.0.1:${RemoteAdbPort}",
    '-L', "127.0.0.1:${LocalVideoPort}:127.0.0.1:${RemoteVideoPort}")
$ViewArgs = @('--serial', $Serial, '--force-adb-forward', '--port', "$RemoteVideoPort",
    '--tunnel-host', '127.0.0.1', '--tunnel-port', "$LocalVideoPort", '--no-control', '--no-audio',
    '--no-power-on', '--no-clipboard-autosync', '--video-codec=h264', '--max-size=640',
    "--max-fps=$MaxFps", '--video-bit-rate=1M')

if ($Action -eq 'Plan') {
    [ordered]@{ run_on = '本机 Windows PowerShell'; codespace = $Codespace;
        tunnel_argv = @('gh') + $TunnelArgs; mirror_argv = @('scrcpy') + $ViewArgs;
        ADB_SERVER_SOCKET = "tcp:127.0.0.1:$LocalAdbPort";
        read_only = $true; network_calls = 0; live_mirror_verified = $false } | ConvertTo-Json -Depth 5
    return
}
if ($env:OS -ne 'Windows_NT' -or $env:CODESPACES -eq 'true') {
    throw '请在你电脑的 Windows PowerShell 运行；Codespaces 终端不能弹出你电脑的镜像窗口。云端可用 android_mirror.py plan。'
}

function Resolve-Tool([string]$Explicit, [string]$Name) {
    if ($Explicit) {
        if (-not (Test-Path -LiteralPath $Explicit -PathType Leaf)) {
            throw "工具不存在：$Explicit。scrcpy请从 https://github.com/Genymobile/scrcpy/releases/latest 下载官方Windows ZIP，把全部内容放到脚本工作目录.cache/native-tools/scrcpy/（含scrcpy.exe与adb.exe），或用-ScrcpyDir指定目录。gh请从 https://github.com/cli/cli/releases/latest 下载portable ZIP，放到.cache/native-tools/github-cli/bin/gh.exe或用-GhPath指定。"
        }
        return (Resolve-Path -LiteralPath $Explicit).Path
    }
    $Found = Get-Command $Name -CommandType Application -ErrorAction SilentlyContinue
    if ($Found) { return $Found.Source }
    throw "缺少 $Name。GitHub CLI官方portable ZIP：https://github.com/cli/cli/releases/latest；放到.cache/native-tools/github-cli/bin/gh.exe或使用-GhPath。Windows还需ssh.exe。本入口不会全局安装。"
}
if (-not [System.IO.Path]::IsPathRooted($ScrcpyDir)) { $ScrcpyDir = Join-Path $RepoRoot $ScrcpyDir }
if (-not $GhPath) {
    $PortableGh = Join-Path $RepoRoot '.cache/native-tools/github-cli/bin/gh.exe'
    if (Test-Path -LiteralPath $PortableGh -PathType Leaf) { $GhPath = $PortableGh }
}
$GhExe = Resolve-Tool $GhPath 'gh.exe'
$null = Resolve-Tool '' 'ssh.exe'
$ScrcpyExe = Resolve-Tool (Join-Path $ScrcpyDir 'scrcpy.exe') 'scrcpy.exe'
$AdbExe = Resolve-Tool (Join-Path $ScrcpyDir 'adb.exe') 'adb.exe'
if ($Action -eq 'Doctor') {
    [ordered]@{ gh = $GhExe; ssh = 'ssh.exe'; scrcpy = $ScrcpyExe; adb = $AdbExe;
        codespace = $Codespace; network_calls = 0; adb_calls = 0;
        note = '只检查本机文件；尚未验证 gh 登录、云端编码器或实际画面' } | ConvertTo-Json
    return
}

function Assert-PortFree([int]$Port) {
    $Listener = New-Object System.Net.Sockets.TcpListener -ArgumentList ([System.Net.IPAddress]::Loopback), $Port
    try { $Listener.Start() }
    catch { throw "本机端口 $Port 已占用；请指定新的 -LocalAdbPort / -LocalVideoPort，不结束现有服务。" }
    finally { $Listener.Stop() }
}
function Read-Exact($Stream, [int]$Count) {
    $Buffer = New-Object byte[] $Count
    $Offset = 0
    while ($Offset -lt $Count) {
        $Read = $Stream.Read($Buffer, $Offset, $Count - $Offset)
        if ($Read -eq 0) { throw '云端 ADB server 连接提前结束。' }
        $Offset += $Read
    }
    return [System.Text.Encoding]::ASCII.GetString($Buffer)
}
function Get-RemoteAdbVersion {
    # ADB host:version 是只读协议探测；不启动/重启任何 daemon。
    $Client = New-Object System.Net.Sockets.TcpClient
    try {
        $Pending = $Client.ConnectAsync('127.0.0.1', $LocalAdbPort)
        if (-not $Pending.Wait(700)) { throw '本机 ADB 隧道尚未连通。' }
        $Pending.GetAwaiter().GetResult()
        $Stream = $Client.GetStream(); $Stream.ReadTimeout = 1500; $Stream.WriteTimeout = 1500
        $Request = [System.Text.Encoding]::ASCII.GetBytes('000chost:version')
        $Stream.Write($Request, 0, $Request.Length)
        if ((Read-Exact $Stream 4) -ne 'OKAY') { throw '云端没有返回 ADB server 版本。' }
        $Length = [Convert]::ToInt32((Read-Exact $Stream 4), 16)
        if ($Length -lt 1 -or $Length -gt 16) { throw 'ADB server 版本响应长度异常。' }
        return [Convert]::ToInt32((Read-Exact $Stream $Length), 16)
    }
    finally { $Client.Dispose() }
}

if ($Action -eq 'Tunnel') {
    Assert-PortFree $LocalAdbPort; Assert-PortFree $LocalVideoPort
    Write-Host '私有隧道运行中；保持此终端打开。另一本机终端用 -Action View。结束时 Ctrl+C。'
    & $GhExe @TunnelArgs
    if ($LASTEXITCODE -ne 0) { throw "gh 隧道退出：$LASTEXITCODE。请检查本机 gh 登录和 Codespace SSH 服务。" }
    return
}

$TunnelProcess = $null
$MirrorProcess = $null
$OldSocket = $env:ADB_SERVER_SOCKET
$OldAdb = $env:ADB
$RunDir = Join-Path $RepoRoot ('outputs/android-mirror/' + [DateTime]::UtcNow.ToString('yyyyMMddTHHmmss.fffffffZ'))
$null = New-Item -ItemType Directory -Path $RunDir
try {
    if ($Action -in @('Start', 'Encoders')) {
        Assert-PortFree $LocalAdbPort; Assert-PortFree $LocalVideoPort
        Write-Host "正在建立到 $Codespace 的私有隧道；日志：$RunDir"
        # 参数均为经过校验的名字、整数与固定 SSH 选项，无 shell 拼接或外部命令字符串。
        $TunnelProcess = Start-Process -FilePath $GhExe -ArgumentList $TunnelArgs -PassThru -NoNewWindow `
            -RedirectStandardOutput (Join-Path $RunDir 'tunnel.stdout.log') `
            -RedirectStandardError (Join-Path $RunDir 'tunnel.stderr.log')
    }
    $Deadline = [DateTime]::UtcNow.AddSeconds($ConnectTimeout)
    $RemoteVersion = $null
    do {
        if ($TunnelProcess -and $TunnelProcess.HasExited) { throw "gh 隧道已退出；查看 $RunDir/tunnel.stderr.log。用户可自行在本机运行 gh auth login。" }
        try { $RemoteVersion = Get-RemoteAdbVersion }
        catch {
            if ([DateTime]::UtcNow -ge $Deadline) { throw "ADB 私有隧道等待超时；不重启云端 ADB。$($_.Exception.Message)" }
            Start-Sleep -Milliseconds 400
        }
    } while ($null -eq $RemoteVersion)
    $LocalVersionText = (& $AdbExe version 2>&1 | Out-String)
    if ($LASTEXITCODE -ne 0 -or $LocalVersionText -notmatch 'Android Debug Bridge version 1\.0\.(\d+)') {
        throw '无法读取 portable adb 的本地协议版本；未启动 scrcpy。'
    }
    if ([int]$Matches[1] -ne $RemoteVersion) {
        throw "本机 ADB 协议版本 $($Matches[1]) 与云端 $RemoteVersion 不同；已拒绝启动，避免客户端重启现有云端 daemon。"
    }
    $env:ADB_SERVER_SOCKET = "tcp:127.0.0.1:$LocalAdbPort"
    $env:ADB = $AdbExe
    if ($Action -eq 'Encoders') {
        $ViewArgs = @('--serial', $Serial, '--force-adb-forward', '--port', "$RemoteVideoPort",
            '--tunnel-host', '127.0.0.1', '--tunnel-port', "$LocalVideoPort", '--no-control', '--no-audio', '--list-encoders')
    }
    Write-Host '打开本机只读 Android 镜像；关闭窗口后只关闭本次创建的 SSH 隧道，云端 VM 与对局继续保留。'
    $MirrorProcess = Start-Process -FilePath $ScrcpyExe -ArgumentList $ViewArgs -PassThru -NoNewWindow `
        -RedirectStandardOutput (Join-Path $RunDir 'scrcpy.stdout.log') `
        -RedirectStandardError (Join-Path $RunDir 'scrcpy.stderr.log')
    while (-not $MirrorProcess.HasExited) { Start-Sleep -Milliseconds 400 }
    $MirrorProcess.WaitForExit()
    $MirrorExit = $MirrorProcess.ExitCode
    if ($Action -eq 'Encoders') {
        Get-Content -LiteralPath (Join-Path $RunDir 'scrcpy.stdout.log')
        Get-Content -LiteralPath (Join-Path $RunDir 'scrcpy.stderr.log')
    }
    if ($MirrorExit -ne 0) { throw "scrcpy 退出：$MirrorExit。日志：$RunDir/scrcpy.stderr.log；编码器问题可用 -Action Encoders 检查。" }
}
finally {
    $env:ADB_SERVER_SOCKET = $OldSocket
    $env:ADB = $OldAdb
    if ($MirrorProcess -and -not $MirrorProcess.HasExited) {
        & "$env:SystemRoot/System32/taskkill.exe" /PID $MirrorProcess.Id /T /F 2>$null | Out-Null
    }
    if ($TunnelProcess -and -not $TunnelProcess.HasExited) {
        # 只处理本脚本创建的进程树；不按名称结束任何其他 gh/ssh/adb。
        & "$env:SystemRoot/System32/taskkill.exe" /PID $TunnelProcess.Id /T /F 2>$null | Out-Null
    }
}

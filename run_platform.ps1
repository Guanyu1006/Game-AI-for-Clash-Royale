param(
    [ValidateSet('Archive','Replay','Agent','Human','Doctor','Stop')][string]$Mode='Archive',
    [switch]$AutoPlay,
    [switch]$Record,
    [ValidateRange(0,10)][double]$Speed=1,
    [ValidateRange(1,100)][int]$Quantum=20,
    [int]$Seed=20002,
    [string]$PolicyFile='',
    [ValidateSet('auto','player','battlefield','oracle')][string]$InformationMode='auto',
    [string]$Checkpoint='outputs/phase2/learning_pipeline/model_g_variant/checkpoint.json',
    [string]$ArchiveFolder='outputs/phase2/viewer/acceptance-001/rendered',
    [int]$Port=8770
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
# Windows PowerShell 5.1 otherwise decodes captured native UTF-8 stdout using
# its console code page. Encoding it as UTF-8 afterwards cannot repair it.
$utf8NoBom=[System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding=$utf8NoBom
$OutputEncoding=$utf8NoBom
$env:PYTHONUTF8='1';$env:PYTHONDONTWRITEBYTECODE='1'
$env:PYTHONIOENCODING='utf-8'
$env:TEMP=Join-Path $PSScriptRoot '.tmp/platform_next';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Path $env:TEMP -Force | Out-Null
$python=Join-Path $PSScriptRoot '.venv-phase2-core/Scripts/python.exe'
if ($Mode -in @('Agent','Human')) { $python=Join-Path $PSScriptRoot '.venv-phase2-data/Scripts/python.exe' }
if (!(Test-Path -LiteralPath $python)) { throw "Project Python missing: $python" }
if ($Mode -eq 'Doctor') { & $python -B tools/phase2_platform_runtime.py status;exit $LASTEXITCODE }
if ($Mode -eq 'Stop') { & $python -B tools/phase2_platform_runtime.py down;exit $LASTEXITCODE }
if ($Mode -ne 'Archive') {
    Write-Host 'Preparing project native runtime. Logs: outputs/platform_next/runtime/'
    $upOutput=& $python -B tools/phase2_platform_runtime.py up
    $upExit=$LASTEXITCODE
    [System.IO.File]::WriteAllText((Join-Path $PSScriptRoot 'outputs/platform_next/runtime/last_launcher_up.log'),(($upOutput -join [Environment]::NewLine)+[Environment]::NewLine),$utf8NoBom)
    if ($upExit -ne 0) { throw 'Runtime startup failed. See outputs/platform_next/runtime/latest.json' }
    $runtimeReport=Get-Content -LiteralPath 'outputs/platform_next/runtime/latest.json' -Raw -Encoding UTF8 | ConvertFrom-Json
    if ($runtimeReport.status -eq 'busy') { throw 'An existing native controller is active. Close it before starting another match.' }
    Write-Host "Native runtime: $($runtimeReport.status). Detailed report: $($runtimeReport.report_path)"
}
$cli=@('-B','tools/phase2_platform.py','--mode',$Mode.ToLower(),'--port',"$Port",'--speed',"$Speed",'--quantum',"$Quantum",'--seed',"$Seed",'--information-mode',$InformationMode,'--checkpoint',$Checkpoint,'--archive-folder',$ArchiveFolder)
if ($AutoPlay) {$cli+='--autoplay'}
if ($Record) {$cli+='--record'}
if ($PolicyFile) {$cli+=@('--policy-file',$PolicyFile)}
Write-Host "Open http://127.0.0.1:$Port in your browser. Keep this terminal open."
& $python @cli
exit $LASTEXITCODE

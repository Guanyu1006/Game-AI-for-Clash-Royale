param([string]$Python='python', [string]$Environment='.venv', [switch]$Rebuild)
$ErrorActionPreference='Stop'
$taskRoot=$PSScriptRoot
Set-Location -LiteralPath $taskRoot
$env:PYTHONUTF8='1'
$env:TEMP=Join-Path $taskRoot '.tmp'
$env:TMP=$env:TEMP
$env:PIP_CACHE_DIR=Join-Path $taskRoot '.cache\pip'
New-Item -ItemType Directory -Force -Path $env:TEMP,$env:PIP_CACHE_DIR | Out-Null
$environmentPath=[IO.Path]::GetFullPath((Join-Path $taskRoot $Environment))
if(-not $environmentPath.StartsWith($taskRoot+'\')) { throw 'Environment must stay in project' }
$pythonExe=Join-Path $environmentPath 'Scripts\python.exe'
if(-not (Test-Path -LiteralPath $pythonExe)) {
    & $Python -m venv $environmentPath
    if($LASTEXITCODE -ne 0) { throw 'Python environment creation failed. Use existing Python 3.14 x64.' }
}
& $pythonExe -c 'import sys; assert sys.version_info[:2] == (3,14), "This Windows snapshot is validated on CPython 3.14 x64"'
if($LASTEXITCODE -ne 0) { throw 'Python version mismatch. Native extension needs matching ABI.' }
& $pythonExe -m pip install --disable-pip-version-check -r requirements.lock
if($LASTEXITCODE -ne 0) { throw 'Dependency installation failed; inspect proxy settings or cached wheels' }
& $pythonExe -m pip install --no-deps --no-build-isolation -e .
if($LASTEXITCODE -ne 0) { throw 'Editable package installation failed' }
if($Rebuild) { & (Join-Path $taskRoot 'tools\build_native.ps1'); if($LASTEXITCODE -ne 0) { throw 'Native build failed' } }
& $pythonExe -m royale_lab info
if($LASTEXITCODE -ne 0) { throw 'Native engine verification failed. Close service and retry -Rebuild.' }
Write-Output "Ready: $pythonExe"

param([string]$Python="py", [switch]$WithData)
$ErrorActionPreference="Stop"
Set-Location -LiteralPath $PSScriptRoot
$env:PIP_CACHE_DIR=Join-Path $PSScriptRoot ".cache\friend-pip"
$env:TEMP=Join-Path $PSScriptRoot ".tmp\friend"
$env:TMP=$env:TEMP
$env:PYTHONDONTWRITEBYTECODE="1"
$env:PYTHONUTF8="1"
$env:OPENBLAS_NUM_THREADS="1"
New-Item -ItemType Directory -Force -Path $env:PIP_CACHE_DIR,$env:TEMP | Out-Null
if(-not(Test-Path -LiteralPath ".venv-friend\Scripts\python.exe")){
    if($Python -eq "py"){ & py -3.12 -m venv .venv-friend }
    else { & $Python -m venv .venv-friend }
    if($LASTEXITCODE -ne 0){throw "Python 3.12 is required; pass -Python <python.exe>."}
}
$taskPython=Join-Path $PSScriptRoot ".venv-friend\Scripts\python.exe"
& $taskPython -m pip install -r requirements-friend.txt
if($LASTEXITCODE -ne 0){throw "Dependency installation failed."}
if($WithData){
    & $taskPython -m pip install -r tools\phase2_learning_requirements.lock.txt
    if($LASTEXITCODE -ne 0){throw "Optional data dependencies failed."}
}
& $taskPython -B VERIFY_PACKAGE.py
if($LASTEXITCODE -ne 0){throw "Package integrity failed."}
& $taskPython -B SMOKE_TEST.py
if($LASTEXITCODE -ne 0){throw "Offline smoke failed."}

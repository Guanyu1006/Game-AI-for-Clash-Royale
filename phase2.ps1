param(
    [ValidateSet('doctor','status','verify-version','probe-status','baseline-check','verify-data','compatibility','audit-data','fetch-data','replay','batch-replay','repeatability','export','train-bc','evaluate-model','evaluate','view','cohort','benchmark','offline-smoke','acceptance')]
    [string]$Action='doctor',
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$CommandArgs
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8='1'
$env:PYTHONDONTWRITEBYTECODE='1'
$env:TEMP=Join-Path $PSScriptRoot '.tmp\phase2';$env:TMP=$env:TEMP
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$phase2Python=Join-Path $PSScriptRoot '.venv-phase2-core\Scripts\python.exe'
if(-not(Test-Path -LiteralPath $phase2Python)) { $phase2Python=Join-Path $PSScriptRoot '.venv\Scripts\python.exe' }
& $phase2Python -m royale_phase2 $Action @CommandArgs
exit $LASTEXITCODE

param(
    [Parameter(Mandatory=$true)][ValidateSet('doctor','baseline-audit','data-audit','convert','collect','export','train','evaluate','predict-model','play','acceptance')][string]$Action,
    [Parameter(ValueFromRemainingArguments=$true)][string[]]$Options
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8='1';$env:PYTHONDONTWRITEBYTECODE='1'
& (Join-Path $PSScriptRoot '.venv/Scripts/python.exe') -B tools/phase3.py $Action @Options
exit $LASTEXITCODE

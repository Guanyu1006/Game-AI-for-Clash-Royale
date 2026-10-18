param([ValidateSet('bootstrap','demo','tests','evaluate','train-bc','train-ppo','research','audit')][string]$Action='demo')
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
$env:PYTHONUTF8='1'
$env:TEMP=Join-Path $PSScriptRoot '.tmp'; $env:TMP=$env:TEMP
$env:PIP_CACHE_DIR=Join-Path $PSScriptRoot '.cache\pip'
$pythonExe=Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
switch($Action) {
    'bootstrap' { & (Join-Path $PSScriptRoot 'bootstrap.ps1') }
    'demo' { if(-not (Test-Path $pythonExe)) { & (Join-Path $PSScriptRoot 'bootstrap.ps1') }; & $pythonExe -m royale_lab serve }
    'tests' { & $pythonExe -m unittest discover -s tests -v; if($LASTEXITCODE -ne 0) { throw 'MVP regression failed' }; & $pythonExe -m unittest discover -s tests_platform -v }
    'evaluate' { & $pythonExe -m royale_lab evaluate --games 100 --workers 4 }
    'train-bc' { & $pythonExe -m royale_lab train-bc --episodes 12 --epochs 20 }
    'train-ppo' { & $pythonExe -m royale_lab train-ppo --steps 4096 }
    'research' { & $pythonExe -B tools\research_verify.py }
    'audit' { & $pythonExe tools\native_smoke.py; & $pythonExe tools\native_coverage.py }
}
if($LASTEXITCODE -ne 0) { throw "Action failed: $Action" }

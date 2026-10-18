param(
    [string]$PolicyFile='examples/human_ai_policy.py',
    [int]$Port=8770,
    [int]$Seed=20002,
    [ValidateRange(0,10)][double]$Speed=1,
    [ValidateRange(1,20)][int]$Quantum=4
)
$ErrorActionPreference='Stop'
Set-Location -LiteralPath $PSScriptRoot
Write-Host 'Human vs AI: you are bottom owner1; AI is top owner0.'
Write-Host 'Open the printed URL. Select or drag a card onto the arena.'
& (Join-Path $PSScriptRoot 'run_platform.ps1') -Mode Human -PolicyFile $PolicyFile -InformationMode battlefield -Speed $Speed -Quantum $Quantum -Seed $Seed -Port $Port
exit $LASTEXITCODE

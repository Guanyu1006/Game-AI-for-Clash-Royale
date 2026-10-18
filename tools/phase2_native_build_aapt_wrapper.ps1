$ErrorActionPreference = 'Stop'
$taskRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$out = Join-Path $taskRoot '.cache/phase2/native_tools'
$source = Join-Path $taskRoot 'tools/phase2_native_src/phase2_aapt_wrapper.cpp'
$env:TEMP = Join-Path $taskRoot '.tmp/phase2/java'
$env:TMP = $env:TEMP
New-Item -ItemType Directory -Force -Path $out,$env:TEMP | Out-Null
$vcvars = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat'
$binary = Join-Path $out 'aapt_wrapper.exe'
$object = Join-Path $out 'aapt_wrapper.obj'
$command = 'call "' + $vcvars + '" >nul && cl /nologo /std:c++17 /O2 /EHsc /utf-8 /MD "' + $source + '" /Fo:"' + $object + '" /Fe:"' + $binary + '"'
cmd /d /c $command
if ($LASTEXITCODE -ne 0) { throw 'AAPT2 relative-path wrapper build failed' }

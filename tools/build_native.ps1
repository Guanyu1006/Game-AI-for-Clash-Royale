param([switch]$Tests,[string]$OutputFolder='')
$ErrorActionPreference = 'Stop'
$taskRoot = Split-Path -Parent $PSScriptRoot
$env:TEMP = Join-Path $taskRoot '.tmp'
$env:TMP = $env:TEMP
$env:PIP_CACHE_DIR = Join-Path $taskRoot '.cache/pip'
New-Item -ItemType Directory -Force -Path $env:TEMP | Out-Null
$engine = Join-Path $taskRoot 'third_party/ClashRoyaleAi'
$build = Join-Path $taskRoot '.cache/native_direct'
$logPrefix = 'native_engine'
if ($OutputFolder) {
    $resolvedOutput = [System.IO.Path]::GetFullPath((Join-Path $taskRoot $OutputFolder))
    $rootPrefix = [System.IO.Path]::GetFullPath($taskRoot).TrimEnd('\') + '\'
    if (-not $resolvedOutput.StartsWith($rootPrefix,[System.StringComparison]::OrdinalIgnoreCase)) { throw 'OutputFolder must stay inside the project' }
    $build = Join-Path $resolvedOutput 'build'
    $logPrefix = 'native_engine_numeric'
    New-Item -ItemType Directory -Force -Path $resolvedOutput | Out-Null
}
New-Item -ItemType Directory -Force -Path $build | Out-Null
$python = Join-Path $taskRoot '.venv/Scripts/python.exe'
$pythonBase = & $python -c 'import sys; print(sys.base_prefix)'
$pybind = Join-Path $taskRoot '.cache/native_pybind11/pybind11/include'
if (-not (Test-Path $pybind)) {
    & $python -m pip install --target (Join-Path $taskRoot '.cache/native_pybind11') pybind11==3.0.1
    if ($LASTEXITCODE -ne 0) { throw 'pybind11 dependency download failed' }
}
$vcvars = 'C:\Program Files\Microsoft Visual Studio\2022\Community\VC\Auxiliary\Build\vcvars64.bat'
if (-not (Test-Path $vcvars)) { throw "Existing MSVC is required; not found: $vcvars" }
$incs = @((Join-Path $engine 'include/core'), (Join-Path $engine 'include/entities'), (Join-Path $engine 'include/rendering'), $pybind, (Join-Path $pythonBase 'include')) | ForEach-Object { '/I "' + $_ + '"' }
$moduleSuffix = & $python -c "import sysconfig; print(sysconfig.get_config_var('EXT_SUFFIX'))"
$pythonLib = & $python -c "import sys; print('python' + str(sys.version_info.major) + str(sys.version_info.minor) + '.lib')"
$out = Join-Path $engine ('python_ai/clash_royale_env' + $moduleSuffix)
if ($OutputFolder) { $out = Join-Path $resolvedOutput ('clash_royale_env' + $moduleSuffix) }
$src = Join-Path $engine 'src/bindings.cpp'
$obj = Join-Path $build 'bindings.obj'
$command = 'call "' + $vcvars + '" >nul && cl /nologo /std:c++17 /O2 /EHsc /bigobj /utf-8 /MD /LD ' + ($incs -join ' ') + ' "' + $src + '" /Fo:"' + $obj + '" /link /LIBPATH:"' + (Join-Path $pythonBase 'libs') + '" ' + $pythonLib + ' /OUT:"' + $out + '" /IMPLIB:"' + (Join-Path $build 'clash_royale_env.lib') + '"'
Push-Location $build
try {
    cmd /d /c $command 2>&1 | Tee-Object -FilePath (Join-Path $taskRoot ('outputs/' + $logPrefix + '_build.log'))
    if ($LASTEXITCODE -ne 0) { throw "Native module build failed: $LASTEXITCODE" }
} finally { Pop-Location }
Write-Output "Built $out"
if ($Tests) {
    $catch = Join-Path $taskRoot '.cache/native_catch2'
    if (-not (Test-Path (Join-Path $catch 'catch_amalgamated.cpp'))) {
        & $python (Join-Path $taskRoot 'tools/native_dependencies.py')
        if ($LASTEXITCODE -ne 0) { throw 'Pinned Catch2 dependency download failed' }
    }
    $testSrc = @(Get-ChildItem -Path (Join-Path $engine 'tests/core/*.cpp'), (Join-Path $engine 'tests/entities/*.cpp') | ForEach-Object FullName)
    $testSrc += Join-Path $catch 'catch_amalgamated.cpp'
    $testExe = Join-Path $build 'ClashRoyaleTests.exe'
    $args = '/nologo /std:c++17 /O2 /EHsc /bigobj /utf-8 /MD /MP8 ' + ($incs -join ' ') + ' /I "' + $catch + '" /I "' + (Join-Path $engine 'tests/entities') + '" ' + (($testSrc | ForEach-Object { '"' + $_ + '"' }) -join ' ') + ' /Fe:"' + $testExe + '"'
    $rsp = Join-Path $build 'tests.rsp'
    Set-Content -LiteralPath $rsp -Value $args -Encoding utf8
    $command = 'call "' + $vcvars + '" >nul && cl @"' + $rsp + '"'
    Push-Location $build
    try {
        cmd /d /c $command 2>&1 | Tee-Object -FilePath (Join-Path $taskRoot ('outputs/' + $logPrefix + '_tests_build.log'))
        if ($LASTEXITCODE -ne 0) { throw "Native tests build failed: $LASTEXITCODE" }
        & $testExe --reporter junit --out (Join-Path $taskRoot ('outputs/' + $logPrefix + '_tests.xml'))
        if ($LASTEXITCODE -ne 0) { throw "Native tests failed: $LASTEXITCODE" }
    } finally { Pop-Location }
}

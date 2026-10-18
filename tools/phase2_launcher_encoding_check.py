"""Real Windows PowerShell 5.1 UTF-8 regression, with no VM or native RPC.

The fixture contains the real Chinese project path. The PowerShell subprocess
executes only JSON reads and a synthetic Python stdout producer. Its UTF-8
initialization, JSON-read and log-write statements come from run_platform.ps1.
"""
from __future__ import annotations
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
DEFAULT = 'outputs/platform_next/fixes/20261018_launcher_encoding'
POWERSHELL = Path('C:/Windows/System32/WindowsPowerShell/v1.0/powershell.exe')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=DEFAULT)
    args = parser.parse_args()
    folder = (ROOT / args.output).resolve()
    if not folder.is_relative_to(ROOT):
        parser.error('Output must stay inside the project')
    folder.mkdir(parents=True, exist_ok=True)
    relative = folder.relative_to(ROOT).as_posix()
    source = (ROOT / 'run_platform.ps1').read_text(encoding='utf-8-sig')
    # Use the actual launcher statements, so this checks its implementation
    # rather than a separately maintained UTF-8 fix in the test.
    initialization = '\n'.join(line for line in source.splitlines()
        if line.startswith(('$utf8NoBom=', '[Console]::OutputEncoding=', '$OutputEncoding=',
                            "$env:PYTHONUTF8=", "$env:PYTHONIOENCODING=")))
    read_statement = next(line.strip() for line in source.splitlines()
                          if line.strip().startswith('$runtimeReport=Get-Content'))
    write_statement = next(line.strip() for line in source.splitlines()
                           if 'last_launcher_up.log' in line and 'WriteAllText' in line)
    fixture = {'status': 'ready', 'report_path': str(ROOT / 'outputs/platform_next/runtime/中文报告/report.json'),
               'vm_started': {'command': [str(ROOT / '.cache/phase2/secondary_runtime/qemu/qemu-system-x86_64.exe')]},
               'unicode_note': '研究生：中文路径、已有原生画面'}
    fixture_bytes = (json.dumps(fixture, ensure_ascii=False, indent=2) + '\n').encode('utf-8')
    (folder / 'fixture.utf8-no-bom.json').write_bytes(fixture_bytes)
    (folder / 'fixture.utf8-bom.json').write_bytes(b'\xef\xbb\xbf' + fixture_bytes)
    producer = folder / 'native_stdout_fixture.py'
    producer.write_text("import sys\nfrom pathlib import Path\nsys.stdout.buffer.write(Path(__file__).with_name('fixture.utf8-no-bom.json').read_bytes())\n", encoding='utf-8')
    script = r'''$ErrorActionPreference='Stop'
$root=[IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../../../..'))
Set-Location -LiteralPath $root
$report=[ordered]@{schema='phase2-launcher-encoding-regression.v1';vm_started=$false;native_rpc_used=$false;real_launcher_invoked=$false;native_producer='synthetic fixture only';ps_version=$PSVersionTable.PSVersion.ToString();ps_edition=$PSVersionTable.PSEdition;ansi_code_page=[Text.Encoding]::Default.CodePage;console_code_page_before=[Console]::OutputEncoding.CodePage;launcher_sha256='__SOURCE_SHA__'}
$fixture='__REL__/fixture.utf8-no-bom.json'
$expected=[Text.Encoding]::UTF8.GetString([Convert]::FromBase64String('__EXPECTED__'))
try {
    $old=Get-Content -LiteralPath $fixture -Raw | ConvertFrom-Json
    $report.old_file_read=[ordered]@{parse_success=$true;exact_chinese_path=($old.vm_started.command[0] -ceq $expected);actual_path=$old.vm_started.command[0]}
} catch {
    $report.old_file_read=[ordered]@{parse_success=$false;exact_chinese_path=$false;error=$_.Exception.Message}
}
$python=Join-Path $root '.venv-phase2-core/Scripts/python.exe'
$oldNative=& $python -B '__REL__/native_stdout_fixture.py'
try {
    $oldNativeJSON=($oldNative -join [Environment]::NewLine)|ConvertFrom-Json
    $report.old_native_capture=[ordered]@{parse_success=$true;exact_chinese_path=($oldNativeJSON.vm_started.command[0] -ceq $expected);actual_path=$oldNativeJSON.vm_started.command[0]}
} catch {
    $report.old_native_capture=[ordered]@{parse_success=$false;exact_chinese_path=$false;error=$_.Exception.Message}
}
# Also exercise an actual legacy Chinese console page. The test invocation may
# inherit 65001 from Codex; that does not cover a student's default CP936 shell.
[Console]::OutputEncoding=[Text.Encoding]::GetEncoding(936)
$OutputEncoding=[Text.Encoding]::GetEncoding(936)
$legacy936=& $python -B '__REL__/native_stdout_fixture.py'
try {
    $legacy936JSON=($legacy936 -join [Environment]::NewLine)|ConvertFrom-Json
    $report.legacy_936_native_capture=[ordered]@{parse_success=$true;exact_chinese_path=($legacy936JSON.vm_started.command[0] -ceq $expected);actual_path=$legacy936JSON.vm_started.command[0]}
} catch {
    $report.legacy_936_native_capture=[ordered]@{parse_success=$false;exact_chinese_path=$false;error=$_.Exception.Message}
}
__INITIALIZATION__
__READ_STATEMENT__
$report.new_file_read=[ordered]@{parse_success=$true;exact_chinese_path=($runtimeReport.vm_started.command[0] -ceq $expected);actual_path=$runtimeReport.vm_started.command[0]}
$bom=Get-Content -LiteralPath '__REL__/fixture.utf8-bom.json' -Raw -Encoding UTF8|ConvertFrom-Json
$report.new_bom_file_exact=($bom.vm_started.command[0] -ceq $expected)
$upOutput=& $python -B '__REL__/native_stdout_fixture.py'
$nativeExit=$LASTEXITCODE
$decoded=($upOutput -join [Environment]::NewLine)|ConvertFrom-Json
$report.new_native_capture=[ordered]@{exit_code=$nativeExit;parse_success=$true;exact_chinese_path=($decoded.vm_started.command[0] -ceq $expected);actual_path=$decoded.vm_started.command[0];console_code_page=[Console]::OutputEncoding.CodePage;pipe_code_page=$OutputEncoding.CodePage;python_io_encoding=$env:PYTHONIOENCODING}
__WRITE_STATEMENT__
$saved=[IO.File]::ReadAllText((Join-Path $root '__REL__/new_launcher_native.stdout.log'),[Text.UTF8Encoding]::new($false,$true))|ConvertFrom-Json
$report.saved_log_roundtrip_exact=($saved.vm_started.command[0] -ceq $expected)
$tokens=$null;$parseErrors=$null
[Management.Automation.Language.Parser]::ParseFile((Join-Path $root 'run_platform.ps1'),[ref]$tokens,[ref]$parseErrors)|Out-Null
$report.launcher_parse_errors=@($parseErrors|ForEach-Object{$_.ToString()})
$report.legacy_failure_reproduced=(-not $report.old_file_read.parse_success)
$report.legacy_936_native_corruption_reproduced=(-not $report.legacy_936_native_capture.exact_chinese_path)
$passed=($PSVersionTable.PSVersion.Major -eq 5 -and $PSVersionTable.PSVersion.Minor -eq 1 -and $report.legacy_failure_reproduced -and $report.legacy_936_native_corruption_reproduced -and $report.new_file_read.exact_chinese_path -and $report.new_bom_file_exact -and $report.new_native_capture.exact_chinese_path -and $report.saved_log_roundtrip_exact -and $parseErrors.Count -eq 0)
$report.status=if($passed){'pass'}else{'fail'}
[IO.File]::WriteAllText((Join-Path $root '__REL__/powershell5.report.json'),($report|ConvertTo-Json -Depth 12),[Text.UTF8Encoding]::new($false))
Write-Host "PowerShell5 encoding regression: $($report.status)"
if(-not $passed){exit 1}
'''
    expected = fixture['vm_started']['command'][0]
    substitutions = {
        '__SOURCE_SHA__': hashlib.sha256((ROOT / 'run_platform.ps1').read_bytes()).hexdigest(),
        '__EXPECTED__': base64.b64encode(expected.encode('utf-8')).decode('ascii'),
        '__INITIALIZATION__': initialization,
        '__READ_STATEMENT__': read_statement.replace('outputs/platform_next/runtime/latest.json', relative + '/fixture.utf8-no-bom.json'),
        '__WRITE_STATEMENT__': write_statement.replace('$PSScriptRoot', '$root').replace('outputs/platform_next/runtime/last_launcher_up.log', relative + '/new_launcher_native.stdout.log'),
        '__REL__': relative,
    }
    for needle, value in substitutions.items():
        script = script.replace(needle, value)
    test_script = folder / 'powershell5.regression.ps1'
    test_script.write_text(script, encoding='utf-8-sig')
    command = [str(POWERSHELL), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', str(test_script)]
    try:
        run = subprocess.run(command, cwd=ROOT, capture_output=True, timeout=40)
        (folder / 'powershell5.stdout.log').write_bytes(run.stdout)
        (folder / 'powershell5.stderr.log').write_bytes(run.stderr)
        result = {'exit_code': run.returncode, 'command': command,
                  'powershell_report': relative + '/powershell5.report.json',
                  'no_vm_or_native_rpc': True, 'full_live_entry_test': 'reserved for the sole runtime owner'}
    except Exception as exc:
        result = {'exit_code': None, 'error': f'{type(exc).__name__}: {exc}', 'no_vm_or_native_rpc': True}
    (folder / 'test_invocation.json').write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    report_path = folder / 'powershell5.report.json'
    if report_path.is_file():
        report = json.loads(report_path.read_text(encoding='utf-8-sig'))
        print(json.dumps({'status': report['status'], 'ps_version': report['ps_version'],
                          'legacy_failure_reproduced': report['legacy_failure_reproduced'],
                          'legacy_936_native_corruption_reproduced': report['legacy_936_native_corruption_reproduced'],
                          'new_file_exact': report['new_file_read']['exact_chinese_path'],
                          'new_native_capture_exact': report['new_native_capture']['exact_chinese_path'],
                          'saved_log_roundtrip_exact': report['saved_log_roundtrip_exact'],
                          'report': str(report_path.relative_to(ROOT))}, ensure_ascii=False, indent=2))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get('exit_code') == 0 else 1


if __name__ == '__main__':
    raise SystemExit(main())

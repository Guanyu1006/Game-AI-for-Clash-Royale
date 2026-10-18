"""Read-only host/runtime probes. No installation, feature toggle or license acceptance."""
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    env=os.environ.copy();env['TEMP']=str(ROOT/'.tmp');env['TMP']=str(ROOT/'.tmp')
    records=[]
    commands=[
        ['C:/Windows/System32/wsl.exe','--status'],
        ['C:/Windows/System32/wsl.exe','--version'],
        ['C:/Windows/System32/wsl.exe','--list','--verbose'],
        ['powershell','-NoProfile','-Command','Get-CimInstance Win32_Processor | Select-Object Name,VirtualizationFirmwareEnabled,SecondLevelAddressTranslationExtensions,VMMonitorModeExtensions | ConvertTo-Json -Compress'],
        ['powershell','-NoProfile','-Command','Get-CimInstance Win32_ComputerSystem | Select-Object HypervisorPresent | ConvertTo-Json -Compress'],
        ['powershell','-NoProfile','-Command','Get-Service LxssManager,WslService,vmcompute,vmms -ErrorAction SilentlyContinue | Select-Object Name,Status | ConvertTo-Json -Compress'],
        ['powershell','-NoProfile','-Command',"Get-AppxPackage '*WindowsSubsystemForLinux*' | Select-Object Name,Version,InstallLocation | ConvertTo-Json -Compress"],
        ['powershell','-NoProfile','-Command',"systeminfo.exe | Select-String 'Virtualization-based security|Hyper-V Requirements|hypervisor has been detected|Base Virtualization|Status: Running' | ForEach-Object {$_.Line}"],
    ]
    for i,cmd in enumerate(commands):
        start=time.perf_counter()
        try:
            result=subprocess.run(cmd,cwd=ROOT,env=env,capture_output=True,timeout=12)
            def decode(blob):
                if b'\x00' in blob[:100]:return blob.decode('utf-16le',errors='replace').lstrip('\ufeff')
                for encoding in ('utf-8','gb18030'):
                    try:return blob.decode(encoding)
                    except UnicodeDecodeError:pass
                return blob.decode('utf-8',errors='replace')
            record={'command':cmd,'exit_code':result.returncode,'stdout':decode(result.stdout),'stderr':decode(result.stderr)}
        except subprocess.TimeoutExpired as exc:record={'command':cmd,'timeout_seconds':12,'stdout':str(exc.stdout),'stderr':str(exc.stderr)}
        except Exception as exc:record={'command':cmd,'error':repr(exc)}
        record['wall_seconds']=time.perf_counter()-start;records.append(record)
        (OUT/f'host_probe_{i:02}.json').write_text(json.dumps(record,ensure_ascii=False,indent=2),encoding='utf-8')
    paths=['C:/Users/63047/AppData/Local/Android/Sdk','C:/Program Files/Android','C:/Program Files/qemu',
           'C:/Windows/System32/wsl.exe','C:/Windows/System32/lxss','C:/Windows/System32/bash.exe']
    report={'host':platform.platform(),'machine':platform.machine(),'python':sys.executable,
            'tools':{k:shutil.which(k) for k in ['adb','emulator','qemu-system-arm','qemu-system-aarch64','qemu-system-x86_64','docker','cargo','rustc']},
            'paths':{p:Path(p).exists() for p in paths},'probes':records,
            'scope':'Read-only queries. No WSL/Hyper-V install/enable, SDK license acceptance or Android/official account operation.'}
    (OUT/'host_runtime_probe.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__':main()

"""Read only installed Android/VMware inventory relevant to the phase-two runtime."""
import json
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]


def main():
    commands=[
      "Get-ItemProperty 'HKLM:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKLM:\\SOFTWARE\\WOW6432Node\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*','HKCU:\\SOFTWARE\\Microsoft\\Windows\\CurrentVersion\\Uninstall\\*' -ErrorAction SilentlyContinue | Where-Object {$_.DisplayName -match 'MuMu|BlueStacks|Android|MEmu|VMware|VirtualBox|Nox|雷电|夜神'} | Select-Object DisplayName,DisplayVersion,InstallLocation | ConvertTo-Json -Compress",
      "Get-Service VMAuthdService,VMUSBArbService,VMnetDHCP,'VMware NAT Service' -ErrorAction SilentlyContinue | Select-Object Name,Status | ConvertTo-Json -Compress",
    ]
    records=[]
    for cmd in commands:
        run=subprocess.run(['powershell','-NoProfile','-Command',cmd],capture_output=True,timeout=15)
        records.append({'command':cmd,'exit_code':run.returncode,'stdout':run.stdout.decode('utf-8',errors='replace'),'stderr':run.stderr.decode('utf-8',errors='replace')})
    paths=['C:/Program Files/Netease','C:/Program Files (x86)/Netease','C:/Program Files/MuMu','C:/Program Files/MuMuVMM','D:/MuMu','D:/Program Files/Netease','D:/Program Files/MuMuPlayer-12.0','C:/Program Files (x86)/VMware/VMware Workstation/vmrun.exe']
    report={'probes':records,'paths':{p:Path(p).exists() for p in paths},'scope':'Registry and standard-location inventory, not an exhaustive scan of every unregistered application.'}
    (ROOT/'outputs/phase2/secondary/optional_host_inventory.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))


if __name__=='__main__':main()

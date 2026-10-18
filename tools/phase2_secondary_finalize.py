"""Rebuild evidence index for the secondary audit; never infer battle success."""
import ctypes
from datetime import datetime,timezone
import hashlib,json,platform
from pathlib import Path
import subprocess

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
def read(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def file_row(path):
 p=ROOT/path
 with p.open('rb') as handle:digest=hashlib.file_digest(handle,'sha256').hexdigest()
 return {'path':str(path).replace('\\','/'),'bytes':p.stat().st_size,'sha256':digest}
def main():
 qemu=read('qemu_vm_process.json')
 if platform.system()=='Windows':
  kernel=ctypes.windll.kernel32
  kernel.OpenProcess.restype=ctypes.c_void_p
  kernel.OpenProcess.argtypes=[ctypes.c_ulong,ctypes.c_bool,ctypes.c_ulong]
  kernel.GetExitCodeProcess.argtypes=[ctypes.c_void_p,ctypes.POINTER(ctypes.c_ulong)]
  kernel.CloseHandle.argtypes=[ctypes.c_void_p]
  handle=kernel.OpenProcess(0x1000,False,qemu['pid'])
  if handle:
   code=ctypes.c_ulong();kernel.GetExitCodeProcess(handle,ctypes.byref(code));kernel.CloseHandle(handle);qemu['process_still_running']=code.value==259
  else:qemu['process_still_running']=False
 prep=json.JSONDecoder().raw_decode((OUT/'network_preparation_positive_control.stdout.log').read_text())[0]
 isolation_log=OUT/'network_update86_cold_start.stdout.log'
 if not isolation_log.exists():isolation_log=OUT/'network_isolated_control.stdout.log'
 iso=json.JSONDecoder().raw_decode(isolation_log.read_text())[0]
 before=next(c for c in prep['checks'] if c['route']=='existing_host_proxy')
 after=next(c for c in iso['checks'] if c['route']=='existing_host_proxy')
 identity_path=OUT/'android_update86_loaded_identity.stdout.log'
 if not identity_path.exists():identity_path=OUT/'android_loaded_native_identity.stdout.log'
 identity=identity_path.read_text()
 expected={'libg.so':'110aa2b5cac391c498645e072b0d88729428c2c2845e7e2737ca8ee979059783',
           'libcrprobe.so':'d526ed884bd449fbd360a32f22cfd3f524efe35ee06755dc7d0cee3e10e97190'}
 verified={name:any(line.startswith(sha+' ') and line.endswith('/'+name) for line in identity.splitlines()) for name,sha in expected.items()}
 native_maps={name:any(name in line and ('r--p' in line or 'r-xp' in line) for line in identity.splitlines()) for name in ['libg.so','libcrprobe.so','libndk_translation.so']}
 current_content=None
 if (OUT/'installed_update86_hashes.json').exists():
  installed=read('installed_update86_hashes.json')
  current_content={key:installed[key] for key in ['fingerprint_version','fingerprint_sha256','content_manifest_sha1','file_count','all_verified','archive_sha256']}
  current_content['evidence']='outputs/phase2/secondary/installed_update86_hashes.json'
 files=['phase2/SECONDARY_AUDIT.md']+[str(p.relative_to(ROOT)) for p in OUT.glob('*.json') if p.name!='secondary_acceptance.json']
 files += [str(p.relative_to(ROOT)) for p in OUT.glob('*.stdout.log')]
 result={'schema':'phase2-secondary-runtime-audit.v1','generated_utc':datetime.now(timezone.utc).isoformat(),
 'scope':'Secondary Scroll audit and Windows-hosted Linux/Android execution conditions. This is not a native battle/replay acceptance.',
 'scroll':{'source_commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT/'third_party/phase2/scroll',text=True).strip(),
           'source_license':'unknown; no LICENSE in fixed checkout','android_install_exit':read('scroll_android_install_result.json')['exit_code'],
           'android_install_error':'INSTALL_FAILED_NO_MATCHING_ABIS res=-113','battle_executed':False},
 'runtime':{'qemu':qemu,'android_boot_completed':read('linux_guest_android_bind_mount_boot.json')['stdout'].splitlines()[0]=='1',
            'android_primary_abi':'x86_64','advertised_abis':['x86_64','arm64-v8a'],'arm64_native_bridge':'libndk_translation.so',
            'installed_native_sha_verified':verified,'native_process_maps_verified':native_maps,
            'native_identity_evidence':str(identity_path.relative_to(ROOT)),'installed_content':current_content,
            'signed_apk_install_success':'Success' in read('linux_guest_android_apk_install.json')['stdout'],
            'native_battle_success':'Not claimed here; see native agent raw RPC match reports.'},
 'network':{'preparation':before,'formal':after,'qemu_restrict_on':qemu.get('isolated') is True,
            'same_url_positive_negative_control':before['url']==after['url'] and before['result'].get('status')==200 and 'error' in after['result'],
            'ssh_survived_in_isolation':read('linux_guest_android_isolated_launch.json')['ssh_exit_code']==0,
            'public_bindings':'Only127.0.0.1 SSH22225, Android55555, QMP22226, probe26789; no LAN listener.'},
 'exclusions':['.cache/phase2/secondary_runtime (VM disks, SSH private keys, user-data, all runtime binaries)',
               'third_party/phase2/scroll_assets (proprietary APK/libg/assets)',
               'outputs/phase2/native/*.apk (local modified proprietary APK)'],
 'evidence_files':[file_row(Path(path)) for path in sorted(set(files))]}
 (OUT/'secondary_acceptance.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
 print(json.dumps({k:result[k] for k in ['scroll','network']},ensure_ascii=True,indent=2))
 print('indexed_evidence_files='+str(len(result['evidence_files'])))
if __name__=='__main__':main()

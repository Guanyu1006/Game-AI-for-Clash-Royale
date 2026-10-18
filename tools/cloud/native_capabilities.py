"""Read-only host capabilities; never installs modules or starts native RPC."""
import os
from pathlib import Path
import platform
import shutil
import subprocess

def inspect():
    devices={str(p):p.exists() for p in (Path('/dev/binder'),Path('/dev/binderfs/binder-control'),Path('/dev/ashmem'))}
    docker=shutil.which('docker'); info=None
    if docker:
        try:
            p=subprocess.run([docker,'info','--format','{{json .ServerVersion}}'],capture_output=True,text=True,timeout=8)
            info={'exit_code':p.returncode,'stdout':p.stdout[:500],'stderr':p.stderr[:1000]}
        except (OSError,subprocess.TimeoutExpired) as e:info={'error':str(e)}
    return {'system':platform.system(),'machine':platform.machine(),'binder_devices':devices,'docker':docker,'docker_info':info,
      'native_runtime_verified':False,'rpc_calls':0,'privileged_containers_tested':False,
      'reason':'This read-only check does not prove Android/probe/assets compatibility. Native deployment requires a separate actual reset/play/terminal acceptance.'}

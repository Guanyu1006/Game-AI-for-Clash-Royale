"""Stream the verified official single-layer filesystem into guest Docker.

No second compressed-layer copy in guest. Imported config is explicitly recorded;
the filesystem layer retains the verified upstream digest.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import time

ROOT=Path(__file__).resolve().parents[1]


def main():
    out=ROOT/'outputs/phase2/secondary';report=json.loads((out/'redroid_layer_audit.json').read_text())
    path=ROOT/report['path'];assert report['verified'] and hashlib.sha256(path.read_bytes()).hexdigest()==report['expected_sha256']
    ssh=['C:/Windows/System32/OpenSSH/ssh.exe','-i',str(ROOT/'.cache/phase2/secondary_runtime/guest-key'),'-p','22225','-o','BatchMode=yes',
         '-o','StrictHostKeyChecking=no','-o','UserKnownHostsFile=.cache/phase2/secondary_runtime/known_hosts','researcher@127.0.0.1',
         'sudo docker import --change \'ENTRYPOINT ["/init","qemu=1","androidboot.hardware=redroid"]\' - phase2-redroid:pinned12']
    t=time.perf_counter()
    with path.open('rb') as handle,(out/'redroid_import.stdout.log').open('wb') as stdout,(out/'redroid_import.stderr.log').open('wb') as stderr:
        run=subprocess.run(ssh,stdin=handle,stdout=stdout,stderr=stderr,cwd=ROOT,timeout=600)
    result={'source_layer_sha256':report['expected_sha256'],'source_oci_manifest':'sha256:e6f799d56b9a9a2bbc6224b5b7a6dc744c9b4d878ac856f27f0c4ec793ef55d3',
            'source_bytes':path.stat().st_size,'imported_tag':'phase2-redroid:pinned12','exit_code':run.returncode,'wall_seconds':time.perf_counter()-t,
            'config_change':'Import sets the same upstream /init,qemu=1,androidboot.hardware=redroid entrypoint; imported Docker image ID differs from original OCI config digest.'}
    (out/'redroid_import.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps(result,indent=2))
    if run.returncode:raise SystemExit(run.returncode)


if __name__=='__main__':main()

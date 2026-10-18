set -eu
python3 - <<'PY'
from datetime import datetime,timezone
import hashlib,json,subprocess
from pathlib import Path
receipt=json.loads(Path('/home/researcher/runtime-update.tar.json').read_text())
prefix='/data/user/0/nullsroyale.rel.free/update/'
paths=[prefix+row['file'] for row in receipt['files']]
cmd=['sudo','docker','exec','phase2-android','sha256sum']+paths
result=subprocess.run(cmd,check=True,capture_output=True,text=True)
actual={}
for line in result.stdout.splitlines():
 digest,separator,path=line.partition('  ')
 assert separator and path.startswith(prefix)
 actual[path[len(prefix):]]=digest
assert len(actual)==652
rows=[]
for expected in receipt['files']:
 name=expected['file'];sha=actual[name]
 assert sha==expected['sha256'],name
 rows.append({'file':name,'actual_installed_sha256':sha,'expected_sha256':expected['sha256'],'verified':True})
fp=subprocess.check_output(['sudo','docker','exec','phase2-android','cat',prefix+'fingerprint.json'])
parsed=json.loads(fp)
print(json.dumps({'schema':'phase2-installed-update-sha256.v1','generated_utc':datetime.now(timezone.utc).isoformat(),
 'source_receipt_sha256':hashlib.sha256(Path('/home/researcher/runtime-update.tar.json').read_bytes()).hexdigest(),
 'archive_sha256':receipt['archive_hashes']['sha256'],'file_count':len(rows),'all_verified':True,
 'fingerprint_sha256':hashlib.sha256(fp).hexdigest(),'fingerprint_version':parsed['version'],'content_manifest_sha1':parsed['sha'],
 'sha1_metadata_exception':next(r for r in receipt['files'] if r['file']=='assets.scdb'),
 'files':rows},indent=2))
PY

set -eu
python3 - <<'PY'
from datetime import datetime,timezone
import hashlib,json,pathlib,subprocess,tarfile

base=pathlib.Path('/home/researcher')
archive=base/'runtime-update.tar'
receipt=json.loads((base/'runtime-update.tar.json').read_text())
expected='2d067ac41de200b849a381030a89fe83505d579bcc819a9429cfc0820ed1785d'
assert hashlib.file_digest(archive.open('rb'),'sha256').hexdigest()==expected
assert receipt['content_version']=='15.535.86' and receipt['file_count']==652
stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
stage=base/('phase2-update-'+stamp)
stage.mkdir(exist_ok=False)
allowed={'update/'+row['file'] for row in receipt['files']}|{'SHA256SUMS','manifest.json'}
with tarfile.open(archive) as tar:
 members=tar.getmembers()
 assert len(members)==len(allowed) and {m.name for m in members}==allowed
 for member in members:
  assert member.isfile() and not member.issym() and not member.islnk()
  assert not pathlib.PurePosixPath(member.name).is_absolute() and '..' not in pathlib.PurePosixPath(member.name).parts
 tar.extractall(stage,filter='data')
for row in receipt['files']:
 path=stage/'update'/row['file']
 assert path.is_file() and path.stat().st_size==row['bytes']
 assert hashlib.file_digest(path.open('rb'),'sha256').hexdigest()==row['sha256'],row['file']
print(json.dumps({'stage':str(stage),'all_652_guest_staging_hashes_verified':True,'archive_sha256':expected}),flush=True)

container='phase2-android'
app='nullsroyale.rel.free'
appbase='/data/user/0/'+app
stagedir='update.stage-'+stamp
backup='update.before-'+stamp
def run(args,**kwargs):
 print('COMMAND '+json.dumps(args),flush=True)
 return subprocess.run(args,check=True,**kwargs)
run(['sudo','docker','exec',container,'am','force-stop',app])
run(['sudo','docker','exec',container,'mkdir','-p',appbase+'/'+stagedir])
producer=subprocess.Popen(['tar','-cf','-','-C',str(stage/'update'),'.'],stdout=subprocess.PIPE)
try:
 run(['sudo','docker','exec','-i',container,'tar','-xf','-','-C',appbase+'/'+stagedir],stdin=producer.stdout)
finally:
 producer.stdout.close()
assert producer.wait()==0
script='''set -eu
base="$1"; stagedir="$2"; backup="$3"
test "$base" = /data/user/0/nullsroyale.rel.free
test -d "$base/$stagedir"
owner=$(stat -c '%u:%g' "$base")
echo APP_OWNER=$owner
chown -R "$owner" "$base/$stagedir"
if [ -e "$base/update" ]; then
  test ! -e "$base/$backup"
  mv "$base/update" "$base/$backup"
  echo PREVIOUS_UPDATE_BACKUP="$base/$backup"
else
  echo PREVIOUS_UPDATE_ABSENT=true
fi
mv "$base/$stagedir" "$base/update"
restorecon -R "$base/update"
'''
run(['sudo','docker','exec','-i',container,'sh','-s','--',appbase,stagedir,backup],input=script.encode())
run(['sudo','docker','exec','-i',container,'tee','/data/local/tmp/phase2-v86-SHA256SUMS'],input=(stage/'SHA256SUMS').read_bytes(),stdout=subprocess.DEVNULL)
run(['sudo','docker','exec',container,'sh','-c','cd /data/user/0/nullsroyale.rel.free/update && sha256sum -c /data/local/tmp/phase2-v86-SHA256SUMS'])
installed=subprocess.check_output(['sudo','docker','exec',container,'cat',appbase+'/update/fingerprint.json'])
fp=json.loads(installed)
print(json.dumps({'installed_fingerprint_sha256':hashlib.sha256(installed).hexdigest(),'installed_fingerprint_sha1':hashlib.sha1(installed).hexdigest(),
                  'fingerprint_version':fp.get('version'),'fingerprint_sha':fp.get('sha'),'fingerprint_file_count':len(fp.get('files',[])),
                  'all_652_installed_sha256_checks_passed':True,'installed_bytes':receipt['bytes'],
                  'archive_sha256':expected,'stage':str(stage),'backup_candidate':appbase+'/'+backup}),flush=True)
PY

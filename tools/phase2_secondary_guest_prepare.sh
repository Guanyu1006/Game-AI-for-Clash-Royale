set -eu
export DEBIAN_FRONTEND=noninteractive
echo '=== Pre-install Linux network probe ==='
python3 - <<'PY'
import json,urllib.request,time
rows=[]
url='https://cloud-images.ubuntu.com/minimal/releases/noble/release-20261008/SHA256SUMS'
for proxy in [None,'http://10.0.2.2:17890']:
 start=time.perf_counter()
 try:
  opener=urllib.request.build_opener(urllib.request.ProxyHandler({} if proxy is None else {'https':proxy,'http':proxy}))
  with opener.open(url,timeout=8) as r: result={'status':r.status,'bytes':len(r.read())}
 except Exception as exc:result={'error':repr(exc)}
 rows.append({'proxy':proxy,'seconds':time.perf_counter()-start,**result})
print(json.dumps(rows))
PY
echo '=== Install guest-only Android container prerequisites ==='
sudo apt-get -o Acquire::http::Proxy=http://10.0.2.2:17890 -o Acquire::https::Proxy=http://10.0.2.2:17890 update
sudo apt-get -o Acquire::http::Proxy=http://10.0.2.2:17890 -o Acquire::https::Proxy=http://10.0.2.2:17890 install -y --no-install-recommends linux-modules-extra-$(uname -r) docker.io adb
sudo modprobe binder_linux devices=binder,hwbinder,vndbinder
ls -l /dev/binder /dev/hwbinder /dev/vndbinder
sudo systemctl start docker
sudo docker version
adb version
df -B1 /
echo PHASE2_GUEST_ANDROID_PREREQUISITES_READY

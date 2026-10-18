set -eu
python3 - <<'PY'
import json,socket,time,urllib.request
url='https://raw.githubusercontent.com/remote-android/redroid-doc/eba9a489990f3f558b7115f25fdc782d610ee8ad/README.md'
checks=[]
for label,opener in [('direct',urllib.request.build_opener(urllib.request.ProxyHandler({}))),('existing_host_proxy',urllib.request.build_opener(urllib.request.ProxyHandler({'https':'http://10.0.2.2:17890'})))]:
 t=time.perf_counter()
 try:
  with opener.open(url,timeout=7) as r:result={'status':r.status,'prefix':r.read(64).decode(errors='replace')}
 except Exception as exc:result={'error':type(exc).__name__+': '+str(exc)}
 checks.append({'route':label,'url':url,'result':result,'wall_seconds':time.perf_counter()-t})
print(json.dumps({'checks':checks},indent=2))
PY
sudo docker exec phase2-android getprop sys.boot_completed
sudo docker exec phase2-android getprop ro.product.cpu.abi
sudo docker exec phase2-android getprop ro.product.cpu.abilist
sudo docker exec phase2-android getprop ro.dalvik.vm.native.bridge

set -eu
sudo mkdir -p /usr/local/libexec
sudo tee /usr/local/libexec/phase2-binder.py >/dev/null <<'PY'
import fcntl,os,subprocess
subprocess.run(['modprobe','binder_linux'],check=True)
os.makedirs('/dev/binderfs',exist_ok=True)
if subprocess.run(['mountpoint','-q','/dev/binderfs']).returncode:
 subprocess.run(['mount','-t','binder','binder','/dev/binderfs'],check=True)
fd=os.open('/dev/binderfs/binder-control',os.O_RDONLY)
try:
 for name in ['binder','hwbinder','vndbinder']:
  if not os.path.exists('/dev/binderfs/'+name):
   data=bytearray(264);data[:len(name)]=name.encode();fcntl.ioctl(fd,0xC1086201,data,True)
  if not os.path.lexists('/dev/'+name):os.symlink('/dev/binderfs/'+name,'/dev/'+name)
  os.chmod('/dev/binderfs/'+name,0o666)
finally:os.close(fd)
PY
sudo tee /etc/systemd/system/phase2-binder.service >/dev/null <<'UNIT'
[Unit]
Description=Project phase-two Android binderfs nodes
Before=docker.service
[Service]
Type=oneshot
RemainAfterExit=yes
ExecStart=/usr/bin/python3 /usr/local/libexec/phase2-binder.py
[Install]
WantedBy=multi-user.target
UNIT
sudo mkdir -p /etc/systemd/system/docker.service.d
sudo tee /etc/systemd/system/docker.service.d/phase2-binder.conf >/dev/null <<'UNIT'
[Unit]
Requires=phase2-binder.service
After=phase2-binder.service
UNIT
sudo systemctl daemon-reload
sudo systemctl enable --now phase2-binder.service
systemctl status phase2-binder.service --no-pager
sudo tee /usr/local/bin/phase2-android-start >/dev/null <<'SH'
#!/bin/sh
set -eu
if docker container inspect phase2-android >/dev/null 2>&1; then
  docker start phase2-android
else
  mkdir -p /var/lib/phase2-android-data
  docker run -d --name phase2-android --restart unless-stopped --privileged \
    -v /dev/binderfs/binder:/dev/binder \
    -v /dev/binderfs/hwbinder:/dev/hwbinder \
    -v /dev/binderfs/vndbinder:/dev/vndbinder \
    -v /var/lib/phase2-android-data:/data -p 5555:5555 \
    phase2-redroid:pinned12 androidboot.use_memfd=1 androidboot.redroid_gpu_mode=guest \
    androidboot.redroid_width=720 androidboot.redroid_height=1280 androidboot.redroid_fps=15 \
    ro.dalvik.vm.native.bridge=libndk_translation.so \
    ro.enable.native.bridge.exec=1 ro.product.cpu.abilist=x86_64,arm64-v8a \
    ro.product.cpu.abilist64=x86_64,arm64-v8a
fi
SH
sudo chmod 755 /usr/local/bin/phase2-android-start
sudo systemctl disable systemd-networkd-wait-online.service
echo PHASE2_RUNTIME_SERVICES_READY

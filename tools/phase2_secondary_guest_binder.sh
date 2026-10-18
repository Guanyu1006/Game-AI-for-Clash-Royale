set -eu
sudo modprobe binder_linux
sudo mkdir -p /dev/binderfs
if ! mountpoint -q /dev/binderfs; then sudo mount -t binder binder /dev/binderfs; fi
sudo python3 - <<'PY'
import fcntl,os,struct
fd=os.open('/dev/binderfs/binder-control',os.O_RDONLY)
try:
 for name in ['binder','hwbinder','vndbinder']:
  if os.path.exists('/dev/binderfs/'+name):continue
  data=bytearray(264);data[:len(name)]=name.encode()
  fcntl.ioctl(fd,0xC1086201,data,True)
  print(name,struct.unpack_from('II',data,256))
finally:os.close(fd)
PY
for dev in binder hwbinder vndbinder; do
  if [ ! -e /dev/$dev ]; then sudo ln -s /dev/binderfs/$dev /dev/$dev; fi
done
ls -l /dev/binderfs /dev/binder /dev/hwbinder /dev/vndbinder
sudo systemctl start docker
sudo docker version
adb version
echo PHASE2_BINDER_DOCKER_ADB_READY

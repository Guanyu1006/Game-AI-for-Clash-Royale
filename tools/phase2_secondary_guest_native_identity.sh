set -eu
sudo docker exec phase2-android id
sudo docker exec phase2-android sh -c 'find /data/app -type f \( -name libg.so -o -name libcrprobe.so -o -name base.apk \) -exec sha256sum {} \;'
sudo docker exec phase2-android sh -c 'pid=$(pidof nullsroyale.rel.free); echo NATIVE_PID=$pid; cat /proc/$pid/maps'
sudo docker exec phase2-android sh -c 'find /data/user/0/nullsroyale.rel.free -maxdepth 2 -type f -name fingerprint.json -exec sha256sum {} \;'

set +e
adb -s 127.0.0.1:5555 install -r /home/researcher/scroll-server.apk
code=$?
echo INSTALL_EXIT=$code
exit $code

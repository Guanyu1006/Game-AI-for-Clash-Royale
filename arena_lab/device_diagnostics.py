"""Bounded, read-only Android loading diagnostics; never launches/restarts apps."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
import shutil
import subprocess
import time

from .device import write

PACKAGE = "nullsroyale.rel.free"
SCRIPT = r'''
game_pid=$(pidof nullsroyale.rel.free)
case "$game_pid" in ''|*[!0-9]*) echo "No unique current game PID"; exit 21;; esac
echo "GAME_PID=$game_pid"
cat /proc/$game_pid/cmdline
echo
for leaf in stat status io schedstat; do
  echo "PROCESS=$leaf"
  cat /proc/$game_pid/$leaf
done
echo MAPS_COUNT
wc -l /proc/$game_pid/maps
echo MEMORY
cat /proc/meminfo
echo PRESSURE
cat /proc/pressure/cpu /proc/pressure/io /proc/pressure/memory
echo DATA_FILESYSTEM
df -k /data
for task in /proc/$game_pid/task/*; do
  echo "THREAD=$task"
  cat "$task/comm" "$task/stat" "$task/wchan" "$task/syscall" "$task/schedstat"
  echo
done
echo GAME_FILE_MAPPINGS
grep -E 'libg.so|libndk_translation|vulkan|pastel|base.apk' /proc/$game_pid/maps
echo SURFACE_LAYERS
dumpsys SurfaceFlinger --list
echo SURFACE_GLOBAL_LATENCY
dumpsys SurfaceFlinger --latency
echo GRAPHICS_FRAMESTATS
dumpsys gfxinfo nullsroyale.rel.free framestats
echo CURRENT_WINDOW
dumpsys window windows | grep -E 'mCurrentFocus|mFocusedApp|Surface|hasSurface'
echo PROCESS_IDENTITY_END
cat /proc/$game_pid/stat
'''


def collect(args):
    if not args.serial or args.serial.startswith("-") or any(c.isspace() for c in args.serial):
        raise ValueError("Explicit ADB serial required")
    if not 1 <= args.samples <= 3 or not 0 <= args.interval <= 60 or not 1 <= args.timeout <= 180:
        raise ValueError("Require 1–3 samples, interval <=60 seconds, timeout <=180 seconds")
    root = Path(args.output); root.mkdir(parents=True, exist_ok=False)
    (root/"read-only-guest-script.sh").write_text(SCRIPT)
    report = {"schema": "arena-device-loading-diagnostics.v1", "package": PACKAGE,
              "started_utc": datetime.now(timezone.utc).isoformat(),
              "mutation_commands": [], "root_cause": "unknown", "samples": []}
    write(root/"report.json", report)
    prefix = [str(args.adb), "-H", "127.0.0.1", "-P", str(args.port), "-s", args.serial,
              "shell", "-T", "su", "0", "sh", "-s"]
    for index in range(args.samples):
        if index: time.sleep(args.interval)
        start = time.monotonic_ns()
        entry = {"index": index, "started_ns": start}
        try:
            entry["host_disk_free_bytes"] = shutil.disk_usage(root).free
            for name, source in (("host-meminfo", "/proc/meminfo"), ("host-memory-events", "/sys/fs/cgroup/memory.events")):
                if Path(source).exists(): (root/f"{index}-{name}.txt").write_text(Path(source).read_text())
            result = subprocess.run(prefix, input=SCRIPT.encode(), capture_output=True, timeout=args.timeout)
            stdout, stderr = result.stdout, result.stderr
            entry.update(returncode=result.returncode, timeout=False)
        except subprocess.TimeoutExpired as error:
            stdout, stderr = error.stdout or b"", error.stderr or b""
            entry.update(returncode=None, timeout=True)
        except OSError as error:
            stdout, stderr = b"", str(error).encode()
            entry.update(returncode=None, timeout=False, error=f"{type(error).__name__}: {error}")
        (root/f"{index}-guest.txt").write_bytes(stdout)
        (root/f"{index}-stderr.txt").write_bytes(stderr)
        entry["finished_ns"] = time.monotonic_ns()
        report["samples"].append(entry); write(root/"report.json", report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--adb", default=".cache/native-tools/bin/adb")
    parser.add_argument("--serial", required=True)
    parser.add_argument("--port", type=int, default=5038)
    parser.add_argument("--output", required=True)
    parser.add_argument("--samples", type=int, default=2)
    parser.add_argument("--interval", type=float, default=30)
    parser.add_argument("--timeout", type=float, default=90)
    collect(parser.parse_args())


if __name__ == "__main__": main()

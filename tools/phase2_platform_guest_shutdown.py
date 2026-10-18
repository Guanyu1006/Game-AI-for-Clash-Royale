"""Bound package-update shutdown waits only inside the pinned project guest.

Invoked over the already verified loopback SSH connection by the host launcher.
All configuration changes are in guest /run and disappear on reboot. This does
not install/update packages and never changes host services or configuration.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import traceback

SERVICES = ('unattended-upgrades.service', 'apt-daily.service', 'apt-daily-upgrade.service')
TIMERS = ('apt-daily.timer', 'apt-daily-upgrade.timer')
LOCKS = ('/var/lib/dpkg/lock', '/var/lib/dpkg/lock-frontend',
         '/var/lib/apt/lists/lock', '/var/cache/apt/archives/lock')
DROPIN = '[Service]\nTimeoutStopSec=15s\nTimeoutStopFailureMode=kill\nSendSIGKILL=yes\n'
PROPERTIES = ('LoadState', 'ActiveState', 'SubState', 'TimeoutStopUSec',
              'TimeoutStopFailureMode', 'SendSIGKILL')


def parse_properties(text):
    return dict(line.split('=', 1) for line in text.splitlines() if '=' in line)


def require_bounded(properties):
    if properties.get('TimeoutStopUSec') not in ('15s', '15000000'):
        raise RuntimeError('The guest did not apply TimeoutStopSec=15s: ' + repr(properties))
    if properties.get('TimeoutStopFailureMode') != 'kill' or properties.get('SendSIGKILL') != 'yes':
        raise RuntimeError('The guest did not apply the bounded stop failure policy: ' + repr(properties))


def package_lock_status():
    # dpkg/apt use POSIX byte-range locks, so flock() would be the wrong test.
    # Opening without O_CREAT/O_TRUNC never creates or rewrites lock files.
    import fcntl
    rows = []
    for name in LOCKS:
        path = Path(name)
        if not path.exists():
            rows.append({'path': name, 'status': 'absent'})
            continue
        descriptor = os.open(name, os.O_RDWR)
        try:
            try:
                fcntl.lockf(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                rows.append({'path': name, 'status': 'held_by_another_process'})
            else:
                rows.append({'path': name, 'status': 'available'})
                fcntl.lockf(descriptor, fcntl.LOCK_UN)
        finally:
            os.close(descriptor)
    return rows


def prepare(report):
    if sys.platform != 'linux' or os.geteuid() != 0 or not Path('/usr/local/bin/phase2-android-start').is_file():
        raise RuntimeError('Refusing to modify a system without the root Linux project-guest marker')
    started = time.monotonic()
    def run(argv, required=True):
        remaining = 50 - (time.monotonic() - started)
        if remaining <= 0:
            raise TimeoutError('Guest shutdown preparation exceeded its 50s total budget')
        row = {'argv': argv}
        report['commands'].append(row)
        try:
            result = subprocess.run(argv, capture_output=True, text=True, timeout=min(8, remaining))
            row.update(exit_code=result.returncode, stdout=result.stdout, stderr=result.stderr)
        except Exception as exc:
            row['error'] = f'{type(exc).__name__}: {exc}'
            raise
        if required and result.returncode:
            raise RuntimeError('Guest command failed: ' + repr(row))
        return result.stdout
    before = {}
    for unit in SERVICES + TIMERS:
        before[unit] = parse_properties(run(['systemctl', 'show', unit] + ['--property=' + p for p in PROPERTIES], required=False))
    report['before'] = before
    timers = [unit for unit in TIMERS if before[unit].get('LoadState') not in (None, 'not-found')]
    if timers:
        run(['systemctl', '--runtime', 'mask', '--now'] + timers)
    report['package_locks'] = package_lock_status()
    if any(row['status'] == 'held_by_another_process' for row in report['package_locks']):
        report['status'] = 'refused_package_operation_active'
        report['reason'] = 'Real apt/dpkg locks are held; do not impose a short kill timeout during package writes'
        return 3
    installed = [unit for unit in SERVICES if before[unit].get('LoadState') not in (None, 'not-found')]
    report['runtime_dropins'] = []
    for unit in installed:
        path = Path('/run/systemd/system') / (unit + '.d') / '90-phase2-stop-bound.conf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(DROPIN, encoding='utf-8')
        report['runtime_dropins'].append({'path': str(path), 'sha256': hashlib.sha256(DROPIN.encode()).hexdigest()})
    run(['systemctl', 'daemon-reload'])
    after = {}
    for unit in installed:
        after[unit] = parse_properties(run(['systemctl', 'show', unit] + ['--property=' + p for p in PROPERTIES]))
        require_bounded(after[unit])
    report.update(status='prepared', after=after, package_updates_started=False,
                  runtime_only=True, expires='next guest reboot', stop_bound_seconds_per_update_service=15,
                  wall_seconds=time.monotonic() - started)
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--apply', action='store_true')
    parser.add_argument('--self-test', action='store_true')
    args = parser.parse_args()
    if args.self_test:
        assert parse_properties('LoadState=loaded\nTimeoutStopUSec=15s\n') == {'LoadState': 'loaded', 'TimeoutStopUSec': '15s'}
        require_bounded({'TimeoutStopUSec': '15s', 'TimeoutStopFailureMode': 'kill', 'SendSIGKILL': 'yes'})
        try:
            require_bounded({'TimeoutStopUSec': '30min', 'TimeoutStopFailureMode': 'terminate', 'SendSIGKILL': 'yes'})
        except RuntimeError:
            pass
        else:
            raise AssertionError('An unbounded observed stop policy was accepted')
        print(json.dumps({'status': 'pass', 'guest_modified': False, 'checks': ['systemctl_properties_parse', 'bounded_policy_readback', 'old_30min_policy_rejected']}))
        return 0
    if not args.apply:
        parser.error('--apply is required; this script runs only in the project guest')
    report = {'schema': 'phase2-guest-shutdown-preparation.v1', 'status': 'failed', 'commands': [],
              'scope': 'Already identity-verified project Linux guest; /run runtime configuration only',
              'host_modified': False, 'package_updates_started': False}
    try:
        code = prepare(report)
    except Exception as exc:
        report.update(error=f'{type(exc).__name__}: {exc}', traceback=traceback.format_exc())
        code = 1
    print(json.dumps(report, ensure_ascii=True, indent=2), flush=True)
    return code


if __name__ == '__main__':
    raise SystemExit(main())

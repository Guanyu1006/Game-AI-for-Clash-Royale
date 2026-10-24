"""Local artifact/namespace preparation contracts; no VM, ADB or game calls."""
import gzip
import hashlib
import io
import json
import os
from contextlib import ExitStack, redirect_stdout, redirect_stderr
from pathlib import Path
from types import SimpleNamespace
import tarfile
import tempfile
import unittest
from unittest.mock import patch

from tools.cloud import software_android as prepare
from tools.cloud import software_android_vm as vm


class SoftwareAndroidArtifacts(unittest.TestCase):
    def setUp(self):
        (prepare.ROOT / '.tmp').mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(prefix='software-android-test-', dir=prepare.ROOT / '.tmp')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def spec(self, content):
        return {'filename': 'artifact.bin', 'url': 'https://example.invalid/locked',
                'bytes': len(content), 'sha256': hashlib.sha256(content).hexdigest()}

    def archive(self, members):
        path = self.root / 'fixture.tar.gz'
        with tarfile.open(path, 'w:gz') as archive:
            for name, content, link in members:
                info = tarfile.TarInfo(name)
                info.uid, info.gid, info.mode = 0, 2000, 0o755
                if link is not None:
                    info.type, info.linkname = tarfile.SYMTYPE, link
                    archive.addfile(info)
                else:
                    info.size = len(content)
                    archive.addfile(info, io.BytesIO(content))
        return path

    def test_existing_different_artifact_is_preserved_without_network(self):
        path = self.root / 'artifact.bin'
        path.write_bytes(b'historical evidence')
        with patch.object(prepare.urllib.request, 'urlopen') as network:
            with self.assertRaisesRegex(ValueError, 'existing artifact preserved'):
                prepare.download_one(self.spec(b'expected'), path)
        network.assert_not_called()
        self.assertEqual(path.read_bytes(), b'historical evidence')

    def test_verified_cache_never_fetches_again(self):
        path = self.root / 'artifact.bin'
        path.write_bytes(b'locked public bytes')
        with patch.object(prepare.urllib.request, 'urlopen') as network:
            result = prepare.download_one(self.spec(path.read_bytes()), path)
        network.assert_not_called()
        self.assertTrue(result['cached'])

    def test_truncated_download_retains_partial_and_never_seals_artifact(self):
        path = self.root / 'artifact.bin'
        with patch.object(prepare.urllib.request, 'urlopen', return_value=io.BytesIO(b'short')):
            with self.assertRaisesRegex(ValueError, 'SHA256/size mismatch'):
                prepare.download_one(self.spec(b'a longer signed artifact'), path)
        self.assertFalse(path.exists())
        self.assertEqual(path.with_name('artifact.bin.partial').read_bytes(), b'short')

    def test_absolute_android_symlink_is_stored_without_dereferencing(self):
        layer = self.archive([('system/bin/test', b'guest executable', None), ('bin', b'', '/system/bin')])
        destination = self.root / 'rootfs'
        ownership = self.root / 'ownership.tsv'
        result = prepare.extract_rootfs(layer, destination, ownership)
        self.assertEqual((destination / 'system/bin/test').read_bytes(), b'guest executable')
        self.assertTrue((destination / 'bin').is_symlink())
        self.assertEqual(os.readlink(destination / 'bin'), '/system/bin')
        self.assertFalse(result['archive_links_followed'])
        self.assertIn('0|2000|755|system/bin/test', ownership.read_text())

    def test_archive_escape_is_rejected_without_writing_outside_rootfs(self):
        layer = self.archive([('../outside', b'escaped', None)])
        with self.assertRaisesRegex(ValueError, 'Unsafe archive path'):
            prepare.extract_rootfs(layer, self.root / 'rootfs', self.root / 'ownership.tsv')
        self.assertFalse((self.root / 'outside').exists())

    def test_deferred_parent_symlink_cannot_redirect_regular_file_writes(self):
        outside = self.root / 'outside'
        outside.mkdir()
        layer = self.archive([('redirect', b'', str(outside)), ('redirect/payload', b'guest data', None)])
        with self.assertRaises(FileExistsError):
            prepare.extract_rootfs(layer, self.root / 'rootfs', self.root / 'ownership.tsv')
        self.assertEqual(list(outside.iterdir()), [])

    def test_cpio_records_links_without_embedding_target_file_contents(self):
        outside = self.root / 'private-fixture'
        outside.write_bytes(b'LOCAL_TEST_SENTINEL_DO_NOT_EMBED')
        guest = self.root / 'initramfs'
        guest.mkdir()
        (guest / 'link').symlink_to(outside)
        (guest / 'init').write_bytes(b'#!/bin/sh\nexit 0\n')
        target = self.root / 'initramfs.cpio.gz'
        vm.cpio(guest, target)
        archive = gzip.decompress(target.read_bytes())
        self.assertIn(b'070701', archive)
        self.assertIn(str(outside).encode(), archive)
        self.assertNotIn(outside.read_bytes(), archive)

    def test_pid_reuse_guard_blocks_stop_before_any_qmp_connection(self):
        cache = self.root / 'cache'
        attempt = self.root / 'attempt'
        cache.mkdir()
        attempt.mkdir()
        record = {'schema': 'experimental-software-android-vm.v1',
                  'attempt': attempt.relative_to(prepare.ROOT).as_posix(),
                  'qmp_socket': str(attempt / 'qmp.sock'), 'pid': os.getpid(),
                  'process_starttime': 'recorded-original-process',
                  'adb_endpoint': '127.0.0.1:15555', 'serial_endpoint': '127.0.0.1:15556'}
        (cache / 'active-vm.json').write_text(json.dumps(record))
        with patch.object(vm, 'process_identity', return_value='different-process'), \
                patch.object(vm, 'qmp') as transport:
            with self.assertRaisesRegex(ValueError, 'refusing any other process'):
                vm.stop(cache)
        transport.assert_not_called()

    def recovery_fixture(self):
        cache, attempt = self.root / 'cache', self.root / 'original-vm'
        cache.mkdir()
        attempt.mkdir()
        (cache / 'android.ext4').write_bytes(b'preserved-base')
        disk = attempt / 'android.ext4'
        disk.write_bytes(b'installed-game')
        (cache / 'ownership.tsv').write_text('guest ownership fixture')
        image = {'bytes': disk.stat().st_size, 'lock_sha256': prepare.sha256(prepare.LOCK),
                 'source_ownership_sha256': prepare.sha256(cache / 'ownership.tsv')}
        (cache / 'image.json').write_text(json.dumps(image))
        record = {'schema': 'experimental-software-android-vm.v1',
                  'attempt': attempt.relative_to(prepare.ROOT).as_posix(),
                  'qmp_socket': str(attempt / 'qmp.sock'), 'pid': 900001,
                  'process_starttime': '123', 'adb_endpoint': '127.0.0.1:15555',
                  'serial_endpoint': '127.0.0.1:15556',
                  'android_boot_verified': True, 'native_game_execution_verified': True,
                  'command': [str(cache / 'packages/usr/bin/qemu-system-x86_64'),
                              '-qmp', 'unix:' + str(attempt / 'qmp.sock') + ',server=on,wait=off',
                              '-drive', 'file=' + str(disk) + ',format=raw,if=virtio,cache=writeback']}
        (attempt / 'process.json').write_text(json.dumps(record))
        (attempt / 'serial.log').write_text('historical boot evidence')
        (cache / 'active-vm.json').write_text(json.dumps(record))
        return cache, attempt, disk, image, record

    def test_resume_records_a_new_run_and_reuses_disk_across_two_restarts(self):
        cache, original, disk, _, _ = self.recovery_fixture()
        old_process = (original / 'process.json').read_bytes()
        old_serial = (original / 'serial.log').read_bytes()
        base = (cache / 'android.ext4').read_bytes()
        state = {'live': None, 'next': 900002}

        def fake_launch(*args, **kwargs):
            state['live'] = state['next']
            state['next'] += 1
            return SimpleNamespace(pid=state['live'])

        def fake_initramfs(cache, lock, attempt, vcpus, timeout, display_fps=1):
            path = attempt / 'new-initramfs.gz'
            path.write_bytes(b'new boot namespace')
            (attempt/'generated-launch-android.sh').write_text(vm.launch_android_script(timeout, display_fps))
            return path, ['fixture-module']

        def fake_attempt(output, operation):
            path = self.root / ('r' + str(state['next']))
            path.mkdir()
            return path

        with ExitStack() as stack:
            stack.enter_context(patch.object(vm, 'STAGING', self.root / 'staging'))
            stack.enter_context(patch.object(prepare, 'check_platform'))
            full_space = stack.enter_context(patch.object(prepare, 'check_space'))
            stack.enter_context(patch.object(vm.shutil, 'disk_usage', return_value=SimpleNamespace(free=2 * 1024 ** 3)))
            ports = stack.enter_context(patch.object(vm, 'wait_ports_free'))
            stack.enter_context(patch.object(vm, 'disk_unused'))
            stack.enter_context(patch.object(vm, 'make_initramfs', side_effect=fake_initramfs))
            stack.enter_context(patch.object(prepare, 'new_attempt', side_effect=fake_attempt))
            copy = stack.enter_context(patch.object(vm.subprocess, 'run'))
            stack.enter_context(patch.object(vm.subprocess, 'Popen', side_effect=fake_launch))
            stack.enter_context(patch.object(vm, 'process_identity',
                                             side_effect=lambda pid: '234' if pid == state['live'] else None))
            first = vm.start(cache, {'kernel_version': 'fixture'}, self.root / 'runs',
                             timeout_multiplier=10, resume_attempt=original)
            state['live'] = None  # Simulated external stop/restart, with receipts preserved.
            second = vm.start(cache, {'kernel_version': 'fixture'}, self.root / 'runs',
                              vcpus=2, timeout_multiplier=10, resume_attempt=first['attempt'], tb_size_mib=128,
                              memory_mib=3072, display_fps=5)
        copy.assert_not_called()
        full_space.assert_not_called()
        self.assertEqual(ports.call_count, 2)
        self.assertNotEqual(first['attempt'], second['attempt'])
        self.assertEqual(second['resumed_from'], first['attempt'])
        self.assertEqual(first['mutable_disk'], disk.relative_to(prepare.ROOT).as_posix())
        self.assertEqual(second['mutable_disk'], first['mutable_disk'])
        self.assertEqual(second['disk_owner_attempt'], original.relative_to(prepare.ROOT).as_posix())
        self.assertFalse(second['disk_copied_from_base'])
        self.assertFalse(second['android_boot_verified'])
        self.assertFalse(second['native_game_execution_verified'])
        self.assertEqual(second['native_rpc_calls'], 0)
        self.assertEqual(second['required_free_bytes'], 1024 ** 3)
        self.assertEqual(first['tb_size_mib'], 64)
        self.assertEqual(second['tb_size_mib'], 128)
        self.assertEqual(first['command'][first['command'].index('-accel') + 1], 'tcg,thread=single,tb-size=64')
        self.assertEqual(second['command'][second['command'].index('-accel') + 1], 'tcg,thread=multi,tb-size=128')
        for result in (first, second):
            expected_memory, expected_fps = (1536, 1) if result is first else (3072, 5)
            self.assertEqual(result['memory_mib'], expected_memory)
            self.assertEqual(result['display_fps'], expected_fps)
            self.assertEqual(result['command'][result['command'].index('-m') + 1], str(expected_memory))
            saved = json.loads((prepare.ROOT / result['attempt'] / 'process.json').read_text())
            for field in ('tb_size_mib', 'memory_mib', 'display_fps'):
                self.assertEqual(saved[field], result[field])
            script = (prepare.ROOT/result['attempt']/'generated-launch-android.sh').read_text()
            self.assertIn('androidboot.redroid_fps='+str(expected_fps)+' ', script)
        self.assertEqual((original / 'process.json').read_bytes(), old_process)
        self.assertEqual((original / 'serial.log').read_bytes(), old_serial)
        self.assertEqual((cache / 'android.ext4').read_bytes(), base)
        self.assertFalse((prepare.ROOT / second['attempt'] / 'android.ext4').exists())

    def test_invalid_tcg_cache_is_rejected_before_platform_lock_or_disk_access(self):
        cache, attempt, disk, _, _ = self.recovery_fixture()
        snapshots = {path: path.read_bytes() for path in
                     (cache / 'active-vm.json', cache / 'android.ext4', attempt / 'process.json', disk)}
        for value in (0, 63, 65, 1024, -64, True, 64.0, '64', None):
            with self.subTest(tb_size_mib=value), patch.object(prepare, 'check_platform') as platform, \
                    patch.object(vm, 'start_lock') as lease, patch.object(vm.subprocess, 'Popen') as launch:
                with self.assertRaisesRegex(ValueError, 'translation cache'):
                    vm.start(cache, {}, self.root / 'runs', resume_attempt=attempt, tb_size_mib=value)
                platform.assert_not_called()
                lease.assert_not_called()
                launch.assert_not_called()
        self.assertFalse((self.root / 'runs').exists())
        for path, content in snapshots.items():
            self.assertEqual(path.read_bytes(), content)

    def test_cli_tcg_cache_default_explicit_choices_and_invalid_value(self):
        for arguments, expected in (([], 64), (['--tb-size-mib', '128'], 128),
                                    (['--tb-size-mib', '256'], 256), (['--tb-size-mib', '512'], 512)):
            with self.subTest(arguments=arguments), patch('sys.argv', ['software_android_vm.py', 'start', *arguments]), \
                    patch.object(prepare, 'load_lock', return_value={}), \
                    patch.object(vm, 'start', return_value={'fixture_only': True}) as launch, \
                    redirect_stdout(io.StringIO()):
                vm.main()
                self.assertEqual(launch.call_args.args[-3], expected)
        with patch('sys.argv', ['software_android_vm.py', 'start', '--tb-size-mib', '1024']), \
                patch.object(vm, 'start') as launch, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            vm.main()
        launch.assert_not_called()

    def test_guest_memory_and_fps_reject_invalid_values_before_platform_or_disk_access(self):
        for name, bad_values in (('memory_mib', (0, 1024, 4097, True, 3072.0, '3072', None)),
                                 ('display_fps', (0, 2, 60, True, 5.0, '5', None))):
            for value in bad_values:
                with self.subTest(name=name, value=value), patch.object(prepare, 'check_platform') as platform, \
                        patch.object(vm, 'start_lock') as lock, patch.object(vm.subprocess, 'Popen') as launch:
                    with self.assertRaises(ValueError):
                        vm.start(self.root/'never-created', {}, self.root/'runs', **{name: value})
                    platform.assert_not_called(); lock.assert_not_called(); launch.assert_not_called()
        self.assertFalse((self.root/'never-created').exists())

    def test_display_fps_changes_only_the_generated_guest_launch_script(self):
        default = vm.launch_android_script()
        self.assertIn('androidboot.redroid_fps=1 ', default)
        for fps in (1, 5, 10, 15, 30):
            script = vm.launch_android_script(20, fps)
            self.assertIn('androidboot.redroid_fps='+str(fps)+' ', script)
            self.assertIn('ro.hw_timeout_multiplier=20 ', script)
            self.assertNotIn('__DISPLAY_FPS__', script)
            self.assertNotIn('__HW_TIMEOUT_MULTIPLIER__', script)
        self.assertIn('__DISPLAY_FPS__', vm.LAUNCH_ANDROID)

    def test_cli_guest_memory_and_display_fps_defaults_explicit_and_invalid_values(self):
        for arguments, expected in (([], (1536, 1)),
                                    (['--memory-mib', '2048', '--display-fps', '10'], (2048, 10)),
                                    (['--memory-mib', '3072', '--display-fps', '5'], (3072, 5)),
                                    (['--memory-mib', '4096', '--display-fps', '30'], (4096, 30))):
            with self.subTest(arguments=arguments), patch('sys.argv', ['software_android_vm.py', 'start', *arguments]), \
                    patch.object(prepare, 'load_lock', return_value={}), \
                    patch.object(vm, 'start', return_value={'fixture_only': True}) as launch, \
                    redirect_stdout(io.StringIO()):
                vm.main()
                self.assertEqual(launch.call_args.args[-2:], expected)
        for flag, value in (('--memory-mib', '8192'), ('--display-fps', '60')):
            with self.subTest(flag=flag), patch('sys.argv', ['software_android_vm.py', 'start', flag, value]), \
                    patch.object(vm, 'start') as launch, redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
                vm.main()
            launch.assert_not_called()

    def test_resume_refuses_symlinks_and_repository_escape(self):
        cache, attempt, disk, image, _ = self.recovery_fixture()
        link = self.root / 'redirect'
        link.symlink_to(attempt, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            vm.resume_disk(cache, link, image)
        with self.assertRaisesRegex(ValueError, 'below the current repository'):
            vm.unlinked_local('/tmp/outside-vm')
        disk.unlink()
        disk.symlink_to(cache / 'android.ext4')
        with self.assertRaisesRegex(ValueError, 'symlinks'):
            vm.resume_disk(cache, attempt, image)

    def test_resume_refuses_changed_drive_record(self):
        cache, attempt, _, image, record = self.recovery_fixture()
        record['command'][-1] = 'file=/tmp/unrecorded.ext4,format=raw,if=virtio,cache=writeback'
        (attempt / 'process.json').write_text(json.dumps(record))
        with self.assertRaisesRegex(ValueError, 'disk/QMP command differs'):
            vm.resume_disk(cache, attempt, image)

    def test_resume_refuses_base_hardlink_instead_of_private_disk(self):
        cache, attempt, disk, image, _ = self.recovery_fixture()
        disk.unlink()
        disk.hardlink_to(cache / 'android.ext4')
        with self.assertRaisesRegex(ValueError, 'private, unchanged-size'):
            vm.resume_disk(cache, attempt, image)

    def test_resume_refuses_alive_pid_before_disk_reuse(self):
        cache, attempt, _, image, _ = self.recovery_fixture()
        with patch.object(vm, 'process_identity', return_value='reused-or-original'), \
                self.assertRaisesRegex(ValueError, 'Recorded PID is alive'):
            vm.resume_disk(cache, attempt, image)

    def test_resume_refuses_live_qmp_socket_even_when_pid_is_gone(self):
        import socket
        cache, attempt, _, image, _ = self.recovery_fixture()
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
            server.bind(str(attempt / 'qmp.sock'))
            server.listen(1)
            with patch.object(vm, 'process_identity', return_value=None), \
                    self.assertRaisesRegex(ValueError, 'QMP socket is active'):
                vm.resume_disk(cache, attempt, image)

    def test_start_refuses_changed_active_receipt_before_launch(self):
        cache, attempt, _, _, record = self.recovery_fixture()
        record['pid'] += 1
        (cache / 'active-vm.json').write_text(json.dumps(record))
        with patch.object(prepare, 'check_platform'), patch.object(vm.subprocess, 'Popen') as launch, \
                self.assertRaisesRegex(ValueError, 'Active VM receipt differs'):
            vm.start(cache, {}, self.root / 'runs', resume_attempt=attempt)
        launch.assert_not_called()

    def test_resume_refuses_broken_chain(self):
        cache, attempt, disk, image, record = self.recovery_fixture()
        later = self.root / 'later-vm'
        later.mkdir()
        record.update(attempt=later.relative_to(prepare.ROOT).as_posix(),
                      qmp_socket=str(later / 'qmp.sock'),
                      mutable_disk=disk.relative_to(prepare.ROOT).as_posix())
        record['command'][2] = 'unix:' + str(later / 'qmp.sock') + ',server=on,wait=off'
        (later / 'process.json').write_text(json.dumps(record))
        with patch.object(vm, 'process_identity', return_value=None), \
                self.assertRaisesRegex(ValueError, 'Missing VM disk recovery history'):
            vm.resume_disk(cache, later, image)

    def test_resume_minimum_space_is_one_gib_and_leaves_no_new_run_on_failure(self):
        cache, attempt, _, _, _ = self.recovery_fixture()
        with patch.object(prepare, 'check_platform'), patch.object(vm, 'process_identity', return_value=None), \
                patch.object(vm, 'wait_ports_free'), patch.object(vm, 'disk_unused'), \
                patch.object(vm.shutil, 'disk_usage', return_value=SimpleNamespace(free=1024 ** 3 - 1)), \
                patch.object(vm.subprocess, 'Popen') as launch, \
                self.assertRaisesRegex(ValueError, 'At least 1 GiB'):
            vm.start(cache, {}, self.root / 'runs', resume_attempt=attempt)
        launch.assert_not_called()
        self.assertFalse((self.root / 'runs').exists())

    def test_default_new_copy_keeps_original_ten_gib_space_check(self):
        cache, _, _, _, _ = self.recovery_fixture()
        with patch.object(prepare, 'check_platform'), patch.object(vm, 'process_identity', return_value=None), \
                patch.object(vm, 'wait_ports_free'), \
                patch.object(prepare, 'check_space', side_effect=ValueError('At least 10 GiB')) as space, \
                patch.object(vm.subprocess, 'Popen') as launch, \
                self.assertRaisesRegex(ValueError, 'At least 10 GiB'):
            vm.start(cache, {}, self.root / 'runs')
        space.assert_called_once_with()
        launch.assert_not_called()

    def test_resume_refuses_occupied_ports_without_starting_a_process(self):
        cache, attempt, _, _, _ = self.recovery_fixture()
        with patch.object(prepare, 'check_platform'), patch.object(vm, 'process_identity', return_value=None), \
                patch.object(vm, 'wait_ports_free', side_effect=ValueError('loopback port is occupied')), \
                patch.object(vm.subprocess, 'Popen') as launch, \
                self.assertRaisesRegex(ValueError, 'port is occupied'):
            vm.start(cache, {}, self.root / 'runs', resume_attempt=attempt)
        launch.assert_not_called()

    def test_concurrent_start_lock_prevents_second_launch(self):
        cache, attempt, _, _, _ = self.recovery_fixture()
        with vm.start_lock(cache), patch.object(prepare, 'check_platform'), \
                patch.object(vm.subprocess, 'Popen') as launch, \
                self.assertRaisesRegex(ValueError, 'Another VM start'):
            vm.start(cache, {}, self.root / 'runs', resume_attempt=attempt)
        launch.assert_not_called()

    def test_disk_still_named_by_another_qemu_is_preserved(self):
        cache, _, disk, _, _ = self.recovery_fixture()
        entry = self.root / '12345'
        entry.mkdir()
        (entry / 'cmdline').write_bytes(
            str(cache / 'packages/usr/bin/qemu-system-x86_64').encode() + b'\0-drive\0file='
            + str(disk).encode() + b',format=raw,if=virtio,cache=writeback\0')
        with patch.object(Path, 'iterdir', return_value=iter([entry])), \
                self.assertRaisesRegex(ValueError, 'Another QEMU still uses'):
            vm.disk_unused(disk)

    def test_resume_refuses_cycle_in_history(self):
        cache, _, disk, image, record = self.recovery_fixture()
        later = self.root / 'cycle-vm'
        later.mkdir()
        record.update(attempt=later.relative_to(prepare.ROOT).as_posix(),
                      qmp_socket=str(later / 'qmp.sock'),
                      mutable_disk=disk.relative_to(prepare.ROOT).as_posix(),
                      resumed_from=later.relative_to(prepare.ROOT).as_posix())
        record['command'][2] = 'unix:' + str(later / 'qmp.sock') + ',server=on,wait=off'
        (later / 'process.json').write_text(json.dumps(record))
        with patch.object(vm, 'process_identity', return_value=None), \
                self.assertRaisesRegex(ValueError, 'Cycle in VM disk recovery history'):
            vm.resume_disk(cache, later, image)


if __name__ == '__main__':
    unittest.main()

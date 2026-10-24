"""生成本机 Android 镜像计划；不连接网络、ADB、VM 或原生 RPC。"""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil

ROOT = Path(__file__).resolve().parents[2]


def _port(value, label):
    if type(value) is not int or not 1024 <= value <= 65535:
        raise ValueError(f'{label} 必须为 1024..65535 的整数')
    return value


def powershell_quote(value):
    """Literal PowerShell argument; never an interpolated command expression."""
    if not isinstance(value, str) or any(ord(c) < 32 for c in value):
        raise ValueError('参数不能包含控制字符')
    return "'" + value.replace("'", "''") + "'"


def mirror_plan(codespace, *, local_adb_port=15038, local_video_port=27184,
                remote_adb_port=5038, remote_video_port=27183,
                serial='127.0.0.1:15555', max_fps=5):
    if not isinstance(codespace, str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9-]{0,120}', codespace):
        raise ValueError('请用 --codespace 指定 Codespace 名称；不能填写 URL、空格或命令')
    if not isinstance(serial, str) or not re.fullmatch(r'127\.0\.0\.1:[0-9]{1,5}', serial):
        raise ValueError('device 必须是现有 loopback Android 设备，例如 127.0.0.1:15555')
    if not 1 <= int(serial.rsplit(':', 1)[1]) <= 65535:
        raise ValueError('Android 设备端口超出范围')
    for value, label in ((local_adb_port, '本机 ADB 端口'), (local_video_port, '本机视频端口'),
                         (remote_adb_port, '云端 ADB 端口'), (remote_video_port, '云端视频端口')):
        _port(value, label)
    if local_adb_port == local_video_port or remote_adb_port == remote_video_port:
        raise ValueError('同一侧的 ADB 与视频端口必须不同')
    if type(max_fps) is not int or not 1 <= max_fps <= 60:
        raise ValueError('max_fps 必须为 1..60 的整数')
    tunnel = ['gh', 'codespace', 'ssh', '--codespace', codespace, '--', '-N', '-T',
              '-o', 'ExitOnForwardFailure=yes', '-o', 'ServerAliveInterval=15',
              '-o', 'ServerAliveCountMax=3',
              '-L', f'127.0.0.1:{local_adb_port}:127.0.0.1:{remote_adb_port}',
              '-L', f'127.0.0.1:{local_video_port}:127.0.0.1:{remote_video_port}']
    view = ['scrcpy', '--serial', serial, '--force-adb-forward',
            '--port', str(remote_video_port), '--tunnel-host', '127.0.0.1',
            '--tunnel-port', str(local_video_port), '--no-control', '--no-audio',
            '--no-power-on', '--no-clipboard-autosync', '--video-codec=h264',
            '--max-size=640', f'--max-fps={max_fps}', '--video-bit-rate=1M']
    invocation = ['&', powershell_quote(r'.\tools\android_mirror.ps1'), '-Codespace',
                  powershell_quote(codespace), '-LocalAdbPort', str(local_adb_port),
                  '-LocalVideoPort', str(local_video_port), '-RemoteAdbPort', str(remote_adb_port),
                  '-RemoteVideoPort', str(remote_video_port), '-Serial', powershell_quote(serial),
                  '-MaxFps', str(max_fps)]
    return {'schema': 'android-mirror-plan.v1', 'codespace': codespace,
            'run_on': '用户本机 Windows PowerShell；不是 Codespaces 远程终端',
            'powershell_command': ' '.join(invocation), 'tunnel_argv': tunnel,
            'mirror_argv': view, 'mirror_environment': {'ADB_SERVER_SOCKET': f'tcp:127.0.0.1:{local_adb_port}'},
            'read_only_mirror': True, 'audio': False, 'network_calls': 0,
            'adb_calls': 0, 'vm_started': False, 'native_rpc_calls': 0,
            'live_mirror_verified': False,
            'prerequisites': ['本机官方 portable scrcpy（包含 adb.exe）与 GitHub CLI；用户自行登录 gh',
                              '云端现有 Android 与 5038 ADB server；本工具不创建它们',
                              '云端已经成功渲染；本工具不修复黑屏或提高源显示帧率'],
            'references': {'scrcpy': 'https://github.com/Genymobile/scrcpy/blob/master/doc/windows.md',
                           'tunnels': 'https://github.com/Genymobile/scrcpy/blob/master/doc/tunnels.md',
                           'gh': 'https://cli.github.com/manual/gh_codespace_ssh'}}


def write_plan(output, value):
    path = Path(output)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    path.relative_to(ROOT)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as stream:
        json.dump(value, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    return path


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=['plan', 'doctor'], nargs='?', default='plan')
    parser.add_argument('--codespace', default=os.environ.get('CODESPACE_NAME'))
    parser.add_argument('--local-adb-port', type=int, default=15038)
    parser.add_argument('--local-video-port', type=int, default=27184)
    parser.add_argument('--remote-adb-port', type=int, default=5038)
    parser.add_argument('--remote-video-port', type=int, default=27183)
    parser.add_argument('--serial', default='127.0.0.1:15555')
    parser.add_argument('--max-fps', type=int, default=5)
    parser.add_argument('--output', help='可选：新建仓库内计划 JSON，拒绝覆盖')
    args = parser.parse_args(argv)
    try:
        result = mirror_plan(args.codespace, local_adb_port=args.local_adb_port,
                             local_video_port=args.local_video_port, remote_adb_port=args.remote_adb_port,
                             remote_video_port=args.remote_video_port, serial=args.serial, max_fps=args.max_fps)
        if args.command == 'doctor':
            result['current_host_tools'] = {name: shutil.which(name) for name in ('gh', 'scrcpy', 'ssh')}
            result['current_host_scope'] = '仅检查当前进程的 PATH；云端缺少 scrcpy 不说明用户电脑缺少它'
        if args.output:
            write_plan(args.output, result)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    raise SystemExit(main())

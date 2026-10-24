"""跨平台阶段三入口；离线研究与原生运行分别验收。"""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from royale_phase3.common import configure

COMMANDS = ('doctor', 'baseline-audit', 'data-audit', 'convert', 'collect',
            'export', 'train', 'evaluate', 'predict-model', 'play', 'acceptance',
            'native-loop', 'native-connection')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('command', choices=COMMANDS)
    # REMAINDER ensures e.g. `train --help` reaches the training parser.
    parser.add_argument('arguments', nargs=argparse.REMAINDER,
                        help='Arguments forwarded unchanged to the selected command')
    args = parser.parse_args(argv)
    configure()
    os.environ['TMPDIR'] = str(ROOT / '.tmp/phase3')
    rest = args.arguments
    if args.command == 'doctor':
        from royale_phase3.doctor import doctor
        doctor_parser = argparse.ArgumentParser(prog=f'{parser.prog} doctor')
        doctor_parser.add_argument('--output', default='outputs/phase3/doctor.json')
        selected = doctor_parser.parse_args(rest)
        print(json.dumps(doctor(selected.output), ensure_ascii=False, indent=2))
        return 0
    if args.command == 'baseline-audit':
        return subprocess.call([sys.executable, '-B', '-m', 'royale_phase3.evaluation', *rest], cwd=ROOT)
    if args.command in ('native-loop', 'native-connection'):
        script = ROOT/'tools'/('native_loop.py' if args.command == 'native-loop' else 'cloud/native_connection.py')
        return subprocess.call([sys.executable, '-B', str(script), *rest], cwd=ROOT)
    if args.command == 'play':
        if '-h' in rest or '--help' in rest:
            play_parser = argparse.ArgumentParser(prog=f'{parser.prog} play',
                description='保留 Windows 原生入口；Codespaces 使用离线研究页和近似教学模拟器。')
            play_parser.add_argument('native_arguments', nargs='*', help='Windows run_play.ps1 arguments')
            play_parser.print_help()
            return 0
        shell = shutil.which('powershell.exe') if os.name == 'nt' else None
        if not shell:
            print(json.dumps({'status': 'unsupported', 'native_runtime_verified': False,
                'reason': '当前入口需要 Windows PowerShell 与已配置的 Android/Binder、固定版本游戏资源；云端离线环境未验收原生运行。',
                'local_windows_command': r'.\run_play.ps1',
                'cloud_command': '.venv-cloud/bin/python tools/cloud/workspace.py serve',
                'native_rpc_calls': 0}, ensure_ascii=False), file=sys.stderr)
            return 2
        return subprocess.call([shell, '-NoProfile', '-File', str(ROOT/'run_play.ps1'), *rest], cwd=ROOT)
    scripts = {'collect': 'phase3_collect.py', 'export': 'phase3_export.py',
        'data-audit': 'phase3_export.py', 'train': 'phase3_train.py',
        'evaluate': 'phase3_train.py', 'predict-model': 'phase3_world.py',
        'convert': 'phase3_sources.py', 'acceptance': 'phase3_acceptance.py'}
    prefix = {'export': ['export'], 'data-audit': ['audit'],
              'train': ['train'], 'evaluate': ['evaluate']}.get(args.command, [])
    script = ROOT/'tools'/scripts[args.command]
    if not script.is_file():
        parser.error('Implementation not available: '+str(script))
    return subprocess.call([sys.executable, '-B', str(script), *prefix, *rest], cwd=ROOT)


if __name__ == '__main__':
    raise SystemExit(main())

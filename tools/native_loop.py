"""原生小规模闭环；资源缺失时明确 unavailable，训练命令可以离线运行。"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from royale_phase3.common import configure, write
from royale_phase3.native_loop import (export_trajectories, run_batch, run_loop,
                                       runtime_preflight, train_offline)


def main(argv=None):
    configure()
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    doctor = sub.add_parser('doctor', help='只读资产/身份检查；不连接RPC')
    doctor.add_argument('--output')
    for command in ('collect', 'evaluate', 'run'):
        p = sub.add_parser(command)
        p.add_argument('--output', required=True)
        p.add_argument('--games', type=int, default=4 if command != 'evaluate' else 2)
        p.add_argument('--seed', type=int, default=510000)
        p.add_argument('--max-tick', type=int, default=7200)
        p.add_argument('--wall-seconds', type=float, default=300)
        if command == 'run':
            p.add_argument('--evaluation-games', type=int, default=2)
            p.add_argument('--epochs', type=int, default=2)
            p.add_argument('--hidden', type=int, default=32)
            p.add_argument('--play-weight', type=float, default=8., help='显式记录出牌标签权重；不接触test')
        else:
            p.add_argument('--opponent', choices=['cycle', 'random', 'wait', 'alternate'], default='alternate')
            p.add_argument('--rendered', action='store_true', help='请求原生renderer；不生成合成游戏截图')
            if command == 'collect':
                p.add_argument('--policy', choices=['cycle', 'random', 'wait', 'manual', 'checkpoint'], default='random')
                p.add_argument('--checkpoint')
                p.add_argument('--actions', help='manual JSONL: game_index/tick/action；未指定时刻明确WAIT')
            else:
                p.add_argument('--checkpoint', required=True)
    p = sub.add_parser('export', help='导出可训练/可回放的数据，不连接原生')
    p.add_argument('--source', required=True)
    p.add_argument('--output', required=True)
    p = sub.add_parser('train', help='对保存原生来源数据进行真实CPU GRU优化；不连接RPC')
    p.add_argument('--metadata', required=True)
    p.add_argument('--output', required=True)
    p.add_argument('--epochs', type=int, default=2)
    p.add_argument('--hidden', type=int, default=32)
    p.add_argument('--seed', type=int, default=51001)
    p.add_argument('--play-weight', type=float, default=8., help='显式记录出牌标签权重；不接触test')
    args = vars(parser.parse_args(argv))
    command = args.pop('command')
    try:
        if command == 'doctor':
            output = args.pop('output')
            report = runtime_preflight(connect=False)
            if output:
                write(output, report)
            code = 0 if report['ready'] else 2
        elif command == 'export':
            report = export_trajectories(**args)
            code = 0
        elif command == 'train':
            report = train_offline(**args)
            code = 0
        elif command == 'run':
            report = run_loop(**args)
            code = 0 if report['native_closed_loop_verified'] else 2
        else:
            if command == 'evaluate':
                args.update(policy='checkpoint', paired=True)
            report = run_batch(**args)
            code = 0 if report['status'] == 'completed' else 2
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return code
    except (ValueError, FileExistsError, FileNotFoundError, RuntimeError) as exc:
        print(json.dumps({'status': 'failed', 'error': f'{type(exc).__name__}: {exc}',
                          'native_closed_loop_verified': False, 'fallback_used': False},
                         ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

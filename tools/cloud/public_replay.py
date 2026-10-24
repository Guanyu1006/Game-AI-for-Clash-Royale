"""只读转换公开出牌事件，保存独立导入档案；不启动 Android 或原生对局。"""
import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.public_replay import MAX_SOURCE_BYTES, PublicReplayError, _decode, import_public_replay
from royale_phase2.runtime import local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('source', help='仓库内单局事件 JSON：payload_json 行、battle/events 或 source episode')
    parser.add_argument('--output', help='全新导入目录；默认按 UTC 时间建立')
    args = parser.parse_args(argv)
    try:
        path = local(args.source)
        if path.stat().st_size > MAX_SOURCE_BYTES:
            raise PublicReplayError('单局 JSON 不能超过 2 MiB。')
        raw = path.read_bytes()
        output = args.output or 'outputs/cloud/public-replays/import-'+datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S.%fZ')
        result = import_public_replay(_decode(raw), output, filename=path.name, source_bytes=raw)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError) as exc:
        print(json.dumps({'status': 'failed', 'error': str(exc), 'native_rpc_calls': 0}, ensure_ascii=False), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())

"""Configure a loopback/SSH-forwarded native probe; connect only with --connect."""
import argparse
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
from royale_phase2.connection import NativeConnection, check_connection
from royale_phase2.runtime import local


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    create = commands.add_parser('init', help='Write settings without contacting any endpoint')
    create.add_argument('--output', default='outputs/cloud/native/connection.json')
    create.add_argument('--port', type=int, default=26789)
    create.add_argument('--timeout', type=float, default=10)
    create.add_argument('--manifest', default='data/phase2/version_manifest.json')
    create.add_argument('--identity')
    check = commands.add_parser('check', help='Inspect evidence; --connect makes one explicit status request')
    check.add_argument('--config')
    check.add_argument('--connect', action='store_true')
    check.add_argument('--output')
    args = parser.parse_args(argv)
    if args.command == 'init':
        digest = hashlib.sha256(local(args.manifest).read_bytes()).hexdigest()
        settings = NativeConnection(port=args.port, timeout=args.timeout, version_manifest=args.manifest,
                                    identity_path=args.identity, manifest_sha256=digest)
        path = local(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(settings.as_dict(), stream, ensure_ascii=False, indent=2)
            stream.write('\n')
        print(json.dumps({'configuration': str(path.relative_to(ROOT)), 'rpc_calls': 0}, ensure_ascii=False))
        return 0
    if args.connect:
        from royale_phase3.common import native_lock
        with native_lock():
            report = check_connection(args.config, connect=True)
    else:
        report = check_connection(args.config)
    if args.output:
        path = local(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('x', encoding='utf-8') as stream:
            json.dump(report, stream, ensure_ascii=False, indent=2)
            stream.write('\n')
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] != 'unavailable' else 1


if __name__ == '__main__':
    raise SystemExit(main())

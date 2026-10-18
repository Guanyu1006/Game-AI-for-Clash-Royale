"""Bounded, pinned public-source fetch; all files stay in project external/raw."""
from __future__ import annotations
import argparse, hashlib, json, pathlib, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
BASE = ROOT / 'data/phase3/external'
PINS = {'cochon': ('Cochon123/clash-royale-replays', 'bee52be3d0d6463f6e4826da314ca2b6ec7ad007'),
        'tv': ('chrisrca/clash-royale-tv-replays', 'f3a750881e008e0daf237afd45736231b51a955a')}

def fetch(url, path, limit):
    path = (BASE / path).resolve()
    path.relative_to(BASE.resolve())
    if path.exists():
        data = path.read_bytes()
    else:
        with urllib.request.urlopen(url, timeout=90) as response:
            data = response.read(limit + 1)
        if len(data) > limit:
            raise ValueError(f'budget exceeded {len(data)} > {limit}: {url}')
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open('xb') as f:
            f.write(data)
    row = {'url': url, 'path': str(path.relative_to(ROOT)), 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
    print(json.dumps(row, ensure_ascii=True))
    return data, row

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', choices=['metadata', 'file', 'tree'])
    parser.add_argument('--source', choices=PINS)
    parser.add_argument('--path')
    parser.add_argument('--max-bytes', type=int, default=8_000_000)
    args = parser.parse_args()
    if args.max_bytes > 32_000_000:
        raise ValueError('per-file maximum 32 MB; no full snapshot downloads')
    if args.mode == 'metadata':
        for name, (repo, rev) in PINS.items():
            fetch(f'https://huggingface.co/api/datasets/{repo}/tree/{rev}', f'discovery/{name}_tree.json', 2_000_000)
            fetch(f'https://huggingface.co/datasets/{repo}/resolve/{rev}/README.md', f'raw/{name}/{rev}/README.md', 500_000)
    elif args.mode == 'tree':
        repo, rev = PINS[args.source]
        if not args.path or '..' in pathlib.PurePosixPath(args.path).parts:
            raise ValueError('safe tree path is required')
        fetch(f'https://huggingface.co/api/datasets/{repo}/tree/{rev}/{args.path}', f'discovery/{args.source}_{args.path.replace("/", "_")}_tree.json', 2_000_000)
    else:
        repo, rev = PINS[args.source]
        if not args.path or '..' in pathlib.PurePosixPath(args.path).parts or args.path.endswith('.tar.gz'):
            raise ValueError('a specific safe non-archive file is required')
        _, row = fetch(f'https://huggingface.co/datasets/{repo}/resolve/{rev}/{args.path}', f'raw/{args.source}/{rev}/{args.path}', args.max_bytes)
        manifest = BASE / 'download_receipts.jsonl'
        with manifest.open('a', encoding='utf-8') as f:
            f.write(json.dumps(row, ensure_ascii=True) + '\n')

if __name__ == '__main__':
    main()

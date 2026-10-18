"""Read only selected Parquet columns through bounded, archived HTTP ranges."""
from __future__ import annotations
import hashlib, io, json, pathlib, urllib.request
import pyarrow.parquet as pq

ROOT = pathlib.Path(__file__).resolve().parents[1]
REV = 'f3a750881e008e0daf237afd45736231b51a955a'
OUT = ROOT / 'data/phase3/external/raw/tv' / REV / 'range_samples'
TOTAL_BUDGET = 64_000_000
TOTAL_USED = 0

def json_scalar(value):
    """PyArrow half-float -> JSON number without image/object execution."""
    if type(value).__module__ == 'numpy' and hasattr(value, 'item'):
        return value.item()
    raise TypeError(f'Unsupported JSON value {type(value).__name__}')

class BoundedRemote(io.RawIOBase):
    def __init__(self, path, size, tag):
        super().__init__()
        self.url = f'https://huggingface.co/datasets/chrisrca/clash-royale-tv-replays/resolve/{REV}/{path}'
        self.size, self.pos, self.used, self.tag = size, 0, 0, tag
        self.folder = OUT / tag
        self.folder.mkdir(parents=True, exist_ok=True)
        self.receipts = []
    def readable(self): return True
    def seekable(self): return True
    def tell(self): return self.pos
    def seek(self, offset, whence=0):
        self.pos = offset if whence == 0 else self.pos + offset if whence == 1 else self.size + offset
        if not 0 <= self.pos <= self.size: raise ValueError('seek out of bounds')
        return self.pos
    def read(self, size=-1):
        global TOTAL_USED
        if size < 0: size = self.size - self.pos
        size = min(size, self.size - self.pos)
        if size == 0: return b''
        if size > 8_000_000 or self.used + size > 24_000_000 or TOTAL_USED + size > TOTAL_BUDGET:
            raise ValueError('range budget would be exceeded')
        start = self.pos
        local = self.folder / f'{start:012d}-{start+size-1:012d}.bin'
        if local.exists():
            data = local.read_bytes()
            content_range = f'bytes {start}-{start+size-1}/{self.size}'
        else:
            req = urllib.request.Request(self.url, headers={'Range': f'bytes={start}-{start+size-1}'})
            with urllib.request.urlopen(req, timeout=90) as response:
                content_range = response.headers.get('Content-Range')
                if response.status != 206 or content_range != f'bytes {start}-{start+size-1}/{self.size}':
                    raise ValueError(f'no exact bounded HTTP range: {response.status} {content_range}')
                data = response.read(size + 1)
            if len(data) != size: raise ValueError('range length mismatch')
            with local.open('xb') as f: f.write(data)
        self.receipts.append({'url': self.url, 'range': content_range, 'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest(), 'local': str(local.relative_to(ROOT))})
        self.pos += len(data); self.used += len(data); TOTAL_USED += len(data)
        return data

def main():
    OUT.mkdir(parents=True, exist_ok=True)
    jobs = [
      ('raw_frame_metadata', 'arena_01/01600528-75d9-429a-bb4b-7c5f062600a5/frames.parquet', 488889400),
      ('processed_hand_elixir', 'initial_training_hand_elixir.parquet', 1388750868),
      ('processed_offset1', 'training_offset_1.parquet', 10016106287),
    ]
    report = []
    for tag, path, size in jobs:
        remote = BoundedRemote(path, size, tag)
        row = {'tag': tag, 'source_path': path, 'revision': REV, 'source_bytes': size}
        try:
            pf = pq.ParquetFile(remote)
            row.update({'schema': str(pf.schema_arrow), 'rows': pf.metadata.num_rows, 'row_groups': pf.metadata.num_row_groups})
            allowed = {'frame_id', 'hash', 'card', 'hand', 'elixir', 'x', 'y', 'arena', 'replay', 'frame', 'offset'}
            columns = [n for n in pf.schema_arrow.names if n in allowed]
            row['selected_columns'] = columns
            print(tag, row, flush=True)
            table = pf.read_row_group(0, columns=columns)
            derived = OUT / f'{tag}.metadata_v2.jsonl'
            encoded = ''.join(json.dumps({'source_row_group': 0, 'source_row_in_group': idx, **item},
                ensure_ascii=True, allow_nan=False, default=json_scalar) + '\n' for idx, item in enumerate(table.to_pylist()))
            if derived.exists():
                if derived.read_text(encoding='utf-8') != encoded:
                    raise ValueError('immutable derived sample differs; use a new version')
            else:
                with derived.open('x', encoding='utf-8') as f: f.write(encoded)
            row.update({'selected_rows': table.num_rows, 'selected_path': str(derived.relative_to(ROOT)), 'status': 'converted_selected_columns'})
        except Exception as exc:
            row.update({'status': 'blocked', 'error': repr(exc)})
        row.update({'requested_bytes': remote.used, 'receipts': remote.receipts})
        report.append(row)
        (OUT / 'range_manifest_v2.json').write_text(json.dumps(report, ensure_ascii=True, indent=2), encoding='utf-8')
    print(json.dumps(report, ensure_ascii=True, indent=2))

if __name__ == '__main__': main()

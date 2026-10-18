"""Fetch one statically attested public package bootstrap, without executing it.

All three URL components are APK constants (package/version/channel), not user
or device identifiers. See outputs/phase2/assets/dex_analysis.json and the ELF
argument flow. This is preparation only, never the offline battle runtime.
"""
from pathlib import Path
import hashlib
import json
import time
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
URL = 'https://wildcat-api.dnull.xyz/v2/script/nullsroyale.rel.free/15.535.13/release'


def main():
    out = ROOT / 'outputs/phase2/assets'; out.mkdir(parents=True, exist_ok=True)
    dest = ROOT / 'third_party/phase2/runtime-assets/public-bootstrap.bin'
    dest.parent.mkdir(parents=True, exist_ok=True)
    request = urllib.request.Request(URL, headers={'User-Agent': 'phase2-public-asset-audit/1.0'})
    record = {'url': URL, 'purpose': 'public static package resource discovery',
              'script_executed': False, 'native_execution': False,
              'parameters_source': ['dex_analysis.json', 'bootstrap_argument_flow.json'],
              'user_or_device_identifiers': False}
    start = time.monotonic()
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
        with opener.open(request, timeout=30) as response:
            body = response.read(4 * 1024 * 1024 + 1)
            if len(body) > 4 * 1024 * 1024:
                raise ValueError('Bootstrap exceeds 4MiB budget')
            dest.write_bytes(body)
            record.update(status='downloaded', http_status=response.status, final_url=response.url,
                          headers=dict(response.headers), bytes=len(body), sha256=hashlib.sha256(body).hexdigest(),
                          path=dest.relative_to(ROOT).as_posix())
    except Exception as exc:
        record.update(status='failed', error=f'{type(exc).__name__}: {exc}')
    record['wall_seconds'] = time.monotonic() - start
    (out / 'public_bootstrap_fetch.json').write_text(json.dumps(record, indent=2), encoding='utf-8')
    print(json.dumps(record, indent=2))
    if record['status'] != 'downloaded':
        raise SystemExit(2)


if __name__ == '__main__':
    main()

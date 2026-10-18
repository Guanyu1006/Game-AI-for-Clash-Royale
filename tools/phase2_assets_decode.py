"""Read the bootstrap wire envelope for strings; never evaluate its bytecode.

The wire XOR algorithm is transcribed from exact-build libwildcat instructions
0x56c614..0x56c6e4. Signature validity is deliberately not claimed by this
static inspection. It provides no native battle or resource-version evidence.
"""
from pathlib import Path
import hashlib
import json
import re
import struct

ROOT = Path(__file__).resolve().parents[1]


def main():
    path = ROOT / 'third_party/phase2/firstlight/user-provided/lib/arm64-v8a/libwildcat.so'
    lib = path.read_bytes()
    expected = json.loads((ROOT / 'outputs/phase2/assets/bootstrap_static.json').read_text(encoding='utf-8'))['sha256']
    if hashlib.sha256(lib).hexdigest() != expected:
        raise ValueError('Repeat static algorithm audit for changed library')
    phoff = struct.unpack_from('<Q', lib, 32)[0]
    ents, count = struct.unpack_from('<HH', lib, 54)
    constant = None
    for i in range(count):
        seg = struct.unpack_from('<IIQQQQQQ', lib, phoff + i * ents)
        if seg[0] == 1 and seg[3] <= 0x11dac8 < seg[3] + seg[5]:
            off = seg[2] + 0x11dac8 - seg[3]
            constant = lib[off:off + 8]
    if constant is None:
        raise ValueError('Envelope constant not mapped')
    source = ROOT / 'third_party/phase2/runtime-assets/public-bootstrap.bin'
    wire = source.read_bytes()
    if len(wire) < 77:
        raise ValueError('Truncated bootstrap')
    length = int.from_bytes(bytes(a ^ b for a, b in zip(wire[:4], bytes.fromhex('12391337'))), 'big')
    if length != len(wire) - 12:
        raise ValueError('Envelope length differs from static decoder')
    key = bytes(wire[4 + i] ^ wire[i % 4] ^ constant[i] for i in range(8))
    decoded = bytes(value ^ key[i % 8] for i, value in enumerate(wire[12:]))
    dest = source.with_name('public-bootstrap.decoded.bin'); dest.write_bytes(decoded)
    strings = [m.group().decode('ascii') for m in re.finditer(rb'[ -~]{5,}', decoded)]
    out = ROOT / 'outputs/phase2/assets'
    (out / 'bootstrap_strings.json').write_text(json.dumps(strings, indent=2), encoding='utf-8')
    report = {'input_sha256': hashlib.sha256(wire).hexdigest(), 'decoded_sha256': hashlib.sha256(decoded).hexdigest(),
              'length_bytes': length, 'signature_validation': 'not_performed',
              'script_executed': False, 'native_execution': False,
              'wire_format': '12-byte length/key envelope; decoded 64-byte signature then bytecode',
              'bytecode_version': decoded[64], 'strings': len(strings),
              'resource_cdn_found': False,
              'interpretation': 'Static strings include royale.server.dnull.xyz; no asset CDN or pinned content manifest was found.'}
    (out / 'bootstrap_decode.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()

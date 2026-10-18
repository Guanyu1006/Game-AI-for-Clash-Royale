"""Read-only public asset bootstrap discovery in the acquired matching APK.

This inspects native bytes; it never executes or downloads the embedded script.
"""
from pathlib import Path
import argparse
import hashlib
import json
import re
import struct
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / '.cache/phase2/native_deps'))
from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--region', action='append', default=[], help='virtual address:length in hex')
    args = parser.parse_args()
    path = ROOT / 'third_party/phase2/firstlight/user-provided/lib/arm64-v8a/libwildcat.so'
    data = path.read_bytes()
    phoff = struct.unpack_from('<Q', data, 32)[0]
    ents, count = struct.unpack_from('<HH', data, 54)
    segments = [struct.unpack_from('<IIQQQQQQ', data, phoff + i * ents) for i in range(count)]

    def offset(address):
        for seg in segments:
            if seg[0] == 1 and seg[3] <= address < seg[3] + seg[5]:
                return seg[2] + address - seg[3]
        raise ValueError(f'Unmapped address {address:#x}')

    def address(off):
        for seg in segments:
            if seg[0] == 1 and seg[2] <= off < seg[2] + seg[5]:
                return seg[3] + off - seg[2]
        return None

    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    regions = []
    for region in args.region:
        start, size = (int(x, 16) for x in region.split(':'))
        if size > 8192:
            raise ValueError('Use bounded regions <=8192 bytes')
        body = data[offset(start):offset(start) + size]
        regions.append({'address': hex(start), 'instructions': [
            f'{i.address:#x} {i.mnemonic} {i.op_str}' for i in md.disasm(body, start)]})
    if regions:
        print(json.dumps(regions, indent=2))
        return
    strings = []
    targets = {}
    for match in re.finditer(rb'[ -~]{7,}', data):
        value = match.group().decode('ascii')
        if 'v2/script/' in value or value.startswith('wildcat4.'):
            row = {'offset': match.start(), 'address': address(match.start()), 'value': value}
            strings.append(row)
            targets[row['address']] = value
    xrefs = []
    for seg in segments:
        if seg[0] != 1 or not seg[1] & 1:
            continue
        pages = {}
        for i in md.disasm(data[seg[2]:seg[2] + seg[5]], seg[3]):
            if i.mnemonic == 'adrp':
                reg, imm = i.op_str.split(', ')
                pages[reg] = (int(imm.removeprefix('#'), 16), i.address)
            elif i.mnemonic == 'add':
                ops = i.op_str.split(', ')
                if len(ops) == 3 and ops[1] in pages and ops[2].startswith('#'):
                    page, origin = pages[ops[1]]
                    if i.address - origin <= 48:
                        dest = page + int(ops[2][1:], 0)
                        if dest in targets:
                            start = max(seg[3], origin - 48)
                            body = data[offset(start):offset(i.address) + 64]
                            xrefs.append({'target': targets[dest], 'instruction': hex(i.address),
                                          'context': [f'{j.address:#x} {j.mnemonic} {j.op_str}' for j in md.disasm(body, start)]})
    report = {'schema_version': 'royale-phase2-static-bootstrap.v1',
              'library': path.relative_to(ROOT).as_posix(), 'sha256': hashlib.sha256(data).hexdigest(),
              'strings': strings, 'references': xrefs,
              'native_execution': False, 'script_executed': False}
    out = ROOT / 'outputs/phase2/assets'; out.mkdir(parents=True, exist_ok=True)
    (out / 'bootstrap_static.json').write_text(json.dumps(report, indent=2), encoding='utf-8')
    print(json.dumps({'strings': strings, 'references': len(xrefs), 'evidence': 'outputs/phase2/assets/bootstrap_static.json'}))


if __name__ == '__main__':
    main()

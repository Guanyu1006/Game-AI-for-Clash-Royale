"""Read exact ARM64 ELF registration/initialization evidence; never execute it."""
import hashlib
import json
from pathlib import Path
import struct
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'.cache/phase2/native_deps'))
from capstone import Cs,CS_ARCH_ARM64,CS_MODE_ARM

def main():
    path=ROOT/'third_party/phase2/firstlight/user-provided/lib/arm64-v8a/libg.so'
    data=path.read_bytes();phoff=struct.unpack_from('<Q',data,32)[0]
    ents,count=struct.unpack_from('<HH',data,54)
    segments=[struct.unpack_from('<IIQQQQQQ',data,phoff+i*ents) for i in range(count)]
    def off(address):
        for p in segments:
            if p[0]==1 and p[3]<=address<p[3]+p[5]:return p[2]+address-p[3]
        raise ValueError(f'Unmapped {address:#x}')
    dynamic=next(p for p in segments if p[0]==2)
    tags=dict(struct.iter_unpack('<qQ',data[dynamic[2]:dynamic[2]+dynamic[5]]))
    relocations={}
    for offset in range(off(tags[7]),off(tags[7])+tags[8],24):
        address,info,addend=struct.unpack_from('<QQq',data,offset)
        if info&0xffffffff==1027:relocations[address]=addend
    def text(address):
        try:start=off(address);end=data.index(0,start,start+300)
        except (ValueError,IndexError):return None
        try:return data[start:end].decode('ascii')
        except UnicodeDecodeError:return None
    natives=[]
    for address,pointer in relocations.items():
        signature=text(relocations.get(address+8,0))
        target=relocations.get(address+16)
        if signature and signature.startswith('(') and ')' in signature and target:
            name=text(pointer)
            if name and name.isascii() and name.replace('_','').isalnum():
                natives.append({'name':name,'signature':signature,'target':target,'target_hex':hex(target),'table_address':hex(address)})
    probe=ROOT/'third_party/phase2/firstlight/native_runner/probe/cr_replay_probe.cpp'
    source=probe.read_text(encoding='utf-8')
    import re
    fingerprints=[]
    definitions=dict(re.findall(r'constexpr std::uintptr_t (k\w+Offset) = (0x[0-9a-f]+);',source))
    for match in re.finditer(r'constexpr std::uint8_t (k\w+)Prologue\[16\] = \{([^}]+)\}',source):
        key=match[1]+'Offset'
        if key not in definitions:continue
        virtual=int(definitions[key],16)
        expected=bytes(int(x,16) for x in re.findall(r'0x([0-9a-f]+)',match[2]))
        actual=data[off(virtual):off(virtual)+16]
        fingerprints.append({'name':match[1],'address':hex(virtual),'expected':expected.hex(),'actual':actual.hex(),'matches':expected==actual})
    targets={'JNI_OnLoad':20774516,'start':7690096}
    for row in natives:
        if any(word in row['name'].lower() for word in ['init','update','create','asset','surface','start']):targets[row['name']]=row['target']
    md=Cs(CS_ARCH_ARM64,CS_MODE_ARM);disassembly=[]
    for label,target in targets.items():
        try:body=data[off(target):off(target)+512]
        except ValueError:continue
        disassembly.append({'name':label,'address':hex(target),'instructions':[
            {'address':hex(i.address),'mnemonic':i.mnemonic,'operands':i.op_str} for i in md.disasm(body,target)]})
    report={'library':str(path.relative_to(ROOT)).replace('\\','/'),'sha256':hashlib.sha256(data).hexdigest(),
        'relative_relocations':len(relocations),'jni_methods':natives,'core_probe_hook_fingerprints':fingerprints,
        'disassembly':disassembly,'interpretation':'Static ELF/JNI address discovery only. No native instruction was executed.'}
    (ROOT/'outputs/phase2/native/arm64_initialization_audit.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'relative_relocations':len(relocations),'jni_methods':natives,
        'hook_fingerprints':{'matches':sum(r['matches'] for r in fingerprints),'total':len(fingerprints)}}))

if __name__=='__main__':main()

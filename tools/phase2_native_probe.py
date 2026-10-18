"""Real loader/API attempts and ELF evidence; unavailable runtime never passes."""
from __future__ import annotations
import ctypes
import hashlib
import importlib
import json
from pathlib import Path
import platform
import struct
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/native'

def elf_info(path):
    data=path.read_bytes()
    if data[:4]!=b'\x7fELF' or data[5]!=1:raise ValueError('Expected little-endian ELF')
    bits=64 if data[4]==2 else 32
    machine=struct.unpack_from('<H',data,18)[0]
    if bits==64:
        phoff,shoff=struct.unpack_from('<QQ',data,32)
        phsize,phnum,shsize,shnum,shnames=struct.unpack_from('<HHHHH',data,54)
        phfmt='<IIQQQQQQ';shfmt='<IIQQQQIIQQ';dynfmt='<qQ';symfmt='<IBBHQQ'
    else:
        phoff,shoff=struct.unpack_from('<II',data,28)
        phsize,phnum,shsize,shnum,shnames=struct.unpack_from('<HHHHH',data,42)
        phfmt='<IIIIIIII';shfmt='<IIIIIIIIII';dynfmt='<iI';symfmt='<IIIBBH'
    sections=[struct.unpack_from(shfmt,data,shoff+i*shsize) for i in range(shnum)]
    section_strings=data[sections[shnames][4]:sections[shnames][4]+sections[shnames][5]]
    def string(raw,offset):
        return raw[offset:raw.find(b'\x00',offset)].decode('utf-8','replace')
    names={string(section_strings,s[0]):s for s in sections}
    needed=[];imports=[];exports=[];tags={}
    strings=names.get('.dynstr');strings=data[strings[4]:strings[4]+strings[5]] if strings else b''
    dynamic=names.get('.dynamic')
    if dynamic:
        entry=struct.calcsize(dynfmt)
        for offset in range(dynamic[4],dynamic[4]+dynamic[5],entry):
            tag,value=struct.unpack_from(dynfmt,data,offset)
            if tag==1:needed.append(string(strings,value))
            tags[tag]=value
    symbols=names.get('.dynsym')
    if symbols is None and 6 in tags:
        # Null's strips the .dynsym section header but its PT_LOAD mapping,
        # DT_SYMTAB and GNU hash remain valid for the runtime linker.
        phdrs=[struct.unpack_from(phfmt,data,phoff+i*phsize) for i in range(phnum)]
        def file_offset(address):
            for p in phdrs:
                if p[0]!=1:continue
                offset,virtual,size=(p[2],p[3],p[5]) if bits==64 else (p[1],p[2],p[4])
                if virtual<=address<virtual+size:return offset+address-virtual
            raise ValueError(f'Unmapped ELF pointer {address:#x}')
        if 0x6ffffef5 in tags:
            gh=file_offset(tags[0x6ffffef5]);buckets,first,blooms,_=struct.unpack_from('<IIII',data,gh)
            bucket_offset=gh+16+blooms*(bits//8)
            starts=struct.unpack_from('<'+'I'*buckets,data,bucket_offset)
            last=max(starts) if starts else first
            chain=bucket_offset+4*buckets+4*max(0,last-first)
            if last>=first:
                while True:
                    value=struct.unpack_from('<I',data,chain)[0];last+=1;chain+=4
                    if value&1:break
            count=max(first,last)
        elif 4 in tags:count=struct.unpack_from('<II',data,file_offset(tags[4]))[1]
        else:raise ValueError('No supported dynamic symbol count metadata')
        symbols=(0,11,0,0,file_offset(tags[6]),count*tags[11],0,0,0,tags[11])
    if symbols:
        for offset in range(symbols[4],symbols[4]+symbols[5],symbols[9] or struct.calcsize(symfmt)):
            s=struct.unpack_from(symfmt,data,offset);name=string(strings,s[0])
            if not name:continue
            section=s[3] if bits==64 else s[5]
            value=s[4] if bits==64 else s[1]
            (exports if section else imports).append({'name':name,'value':value})
    return {'path':str(path.relative_to(ROOT)).replace('\\','/'),'bytes':len(data),
        'sha256':hashlib.sha256(data).hexdigest(),'elf_bits':bits,'elf_machine':machine,
        'architecture':{183:'AArch64',40:'ARM',62:'x86_64',3:'x86'}.get(machine,'unknown'),
        'needed':needed,'imports':imports,'exports':exports,
        'section_names':list(names)}

def run():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'platform':platform.platform(),'machine':platform.machine(),'python':sys.version,
        'started_unix':time.time(),'runtime_succeeded':False,'steps':[],'elf_artifacts':[]}
    sys.path.insert(0,str(ROOT/'third_party/phase2'))
    try:
        module=importlib.import_module('jason.engine')
        engine=module.Engine(timeout=1)
        try:
            state=engine.reset(seed=1)
            report['steps'].append({'name':'arm64_probe_reset','passed':True,'tick':state.tick})
        except Exception as exc:
            report['steps'].append({'name':'arm64_probe_reset','passed':False,'error':f'{type(exc).__name__}: {exc}'})
        finally:engine.close()
    except Exception as exc:
        report['steps'].append({'name':'arm64_probe_import','passed':False,'error':f'{type(exc).__name__}: {exc}'})
    try:
        module=importlib.import_module('jason.clapha')
        instance=module.ClaphaEngine(str(ROOT/'third_party/phase2/clapha'),workdir=str(ROOT/'.cache/phase2/native_work'))
        state=instance.reset(seed=1)
        report['steps'].append({'name':'clapha_in_process_reset','passed':True,'tick':state.tick})
    except Exception as exc:
        report['steps'].append({'name':'clapha_in_process_reset','passed':False,'error':f'{type(exc).__name__}: {exc}'})
    artifacts=[ROOT/'third_party/phase2/jason/prebuilt/libcrprobe.so']
    artifacts+=list((ROOT/'third_party/phase2/firstlight/user-provided/lib').rglob('libg.so'))
    for path in artifacts:
        if not path.exists():continue
        info=elf_info(path);report['elf_artifacts'].append(info)
        try:
            ctypes.CDLL(str(path))
            loaded=True;error=None
        except Exception as exc:loaded=False;error=f'{type(exc).__name__}: {exc}'
        report['steps'].append({'name':'host_native_loader','artifact':info['path'],'passed':loaded,'error':error,
            'interpretation':'A loader success alone would still not prove match execution.'})
    (OUT/'native_probe.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    print(json.dumps({'runtime_succeeded':False,'steps':report['steps'],
        'elf':[{k:x[k] for k in ['path','architecture','needed','sha256']} for x in report['elf_artifacts']]},indent=2))

if __name__=='__main__':run()

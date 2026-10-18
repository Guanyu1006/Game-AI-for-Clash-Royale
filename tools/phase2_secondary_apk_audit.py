"""Inspect paired Scroll APKs and actually attempt native load/build on this host."""
import ctypes
import hashlib
import json
import os
from pathlib import Path
import struct
import subprocess
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'outputs/phase2/secondary'
ASSETS=ROOT/'third_party/phase2/scroll_assets'


def digest(blob):return hashlib.sha256(blob).hexdigest()


def elf(blob):
    assert blob[:4]==b'\x7fELF'
    bits=32 if blob[4]==1 else 64;endian='<' if blob[5]==1 else '>'
    fmt='HHIIIIIHHHHHH' if bits==32 else 'HHIQQQIHHHHHH'
    h=struct.unpack_from(endian+fmt,blob,16)
    shoff,shsize,shnum,shstrings=h[5],h[10],h[11],h[12]
    shfmt='IIIIIIIIII' if bits==32 else 'IIQQQQIIQQ'
    sections=[struct.unpack_from(endian+shfmt,blob,shoff+i*shsize) for i in range(shnum)]
    strings=sections[shstrings];section_names=blob[strings[4]:strings[4]+strings[5]]
    cstr=lambda data,index:data[index:].split(b'\x00',1)[0].decode('utf-8',errors='replace')
    named={cstr(section_names,s[0]):s for s in sections}
    dyn=named.get('.dynamic');needed=[];soname=None
    if dyn:
        strsec=sections[dyn[6]];table=blob[strsec[4]:strsec[4]+strsec[5]]
        step=8 if bits==32 else 16
        for i in range(dyn[4],dyn[4]+dyn[5],step):
            tag,value=struct.unpack_from(endian+('iI' if bits==32 else 'qQ'),blob,i)
            if tag==1:needed.append(cstr(table,value))
            if tag==14:soname=cstr(table,value)
    symbols=[]
    for section in sections:
        if section[1]!=11:continue
        stringsec=sections[section[6]];table=blob[stringsec[4]:stringsec[4]+stringsec[5]]
        for i in range(section[4],section[4]+section[5],section[9] or (16 if bits==32 else 24)):
            if bits==32:name,value,size,info,other,index=struct.unpack_from(endian+'IIIBBH',blob,i)
            else:name,info,other,index,value,size=struct.unpack_from(endian+'IBBHQQ',blob,i)
            text=cstr(table,name)
            if text and ('JNI_OnLoad' in text or 'LogicGameMode' in text or (index==0 and any(k in text for k in ['android','JNI','AAsset','egl','GL','log_']))):
                symbols.append({'name':text,'value':hex(value),'undefined':index==0})
    return {'bits':bits,'machine':h[1],'machine_name':{40:'ARM',62:'x86_64',183:'AArch64'}.get(h[1],'unknown'),
            'endianness':'little' if endian=='<' else 'big','type':h[0],'flags':hex(h[6]),'soname':soname,
            'needed':needed,'selected_dynamic_symbols':symbols,'sha256':digest(blob),'bytes':len(blob)}


def manifest(blob):
    strings=[];result=[];pos=8
    while pos+8<=len(blob):
        kind,header,size=struct.unpack_from('<HHI',blob,pos)
        if size<8 or pos+size>len(blob):break
        if kind==1:
            count,style,flags,start,styles=struct.unpack_from('<IIIII',blob,pos+8)
            offsets=struct.unpack_from('<'+'I'*count,blob,pos+header)
            def length8(offset):
                first=blob[offset]
                return (((first&127)<<8)|blob[offset+1],offset+2) if first&128 else (first,offset+1)
            for offset in offsets:
                at=pos+start+offset
                if flags&0x100:
                    _,at=length8(at);length,at=length8(at);text=blob[at:at+length].decode('utf-8',errors='replace')
                else:
                    length=struct.unpack_from('<H',blob,at)[0];at+=2
                    if length&0x8000:length=((length&0x7fff)<<16)|struct.unpack_from('<H',blob,at)[0];at+=2
                    text=blob[at:at+length*2].decode('utf-16le',errors='replace')
                strings.append(text)
        if kind==0x102:
            namespace,name,attrstart,attrsize,count=struct.unpack_from('<IIHHH',blob,pos+16)
            tag=strings[name];attrs={}
            for i in range(count):
                at=pos+16+attrstart+i*attrsize
                ns,key,raw=struct.unpack_from('<III',blob,at);typ=blob[at+15];value=struct.unpack_from('<I',blob,at+16)[0]
                attrs[strings[key]]=strings[raw] if raw!=0xffffffff else strings[value] if typ==3 else bool(value) if typ==0x12 else value
            if tag in ('manifest','uses-sdk','application','activity','service','uses-permission','action','category'):result.append({'tag':tag,'attributes':attrs})
        pos+=size
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    report={'apks':[],'attempts':[]}
    for path in sorted(ASSETS.glob('*.apk')):
        with zipfile.ZipFile(path) as archive:
            bad=archive.testzip();assert bad is None,bad
            record={'path':str(path.relative_to(ROOT)),'sha256':digest(path.read_bytes()),'bytes':path.stat().st_size,
                    'zip_crc_all_entries_passed':True,'manifest':manifest(archive.read('AndroidManifest.xml')),'native_libraries':[],
                    'assets':[],'strings_loadLibrary':[]}
            for entry in archive.infolist():
                if entry.filename.startswith('lib/') and not entry.is_dir():
                    blob=archive.read(entry);meta=elf(blob);meta['apk_path']=entry.filename
                    record['native_libraries'].append(meta)
                    target=ASSETS/path.stem/entry.filename;target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(blob)
                if entry.filename.startswith('assets/') and not entry.is_dir():
                    blob=archive.read(entry);record['assets'].append({'name':entry.filename,'bytes':entry.file_size,'sha256':digest(blob),
                        'header_hex':blob[:12].hex() if '.csv' in entry.filename else None})
                if entry.filename.endswith('.dex'):
                    blob=archive.read(entry)
                    record['strings_loadLibrary'].append({'dex':entry.filename,'loadLibrary_offsets':[i for i in range(len(blob)) if blob.startswith(b'loadLibrary',i)][:20],
                        'server_strings':[(s.decode(),blob.find(s)) for s in [b'libserver',b'server',b'Scroll',b'SupercellNativeActivity']]})
            report['apks'].append(record)
    env=os.environ.copy();env['TEMP']=str(ROOT/'.tmp');env['TMP']=str(ROOT/'.tmp');env['CARGO_HOME']=str(ROOT/'.cache/phase2/secondary_cargo');env['RUSTUP_HOME']=str(ROOT/'.cache/phase2/secondary_rustup')
    build=['cargo','ndk','-t','armeabi-v7a','build','--locked','--release']
    try:
        run=subprocess.run(build,cwd=ROOT/'third_party/phase2/scroll',env=env,capture_output=True,text=True,timeout=20)
        report['attempts'].append({'command':build,'cwd':'third_party/phase2/scroll','exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
    except Exception as exc:report['attempts'].append({'command':build,'error':repr(exc),'missing_toolchain':'cargo/rustc/cargo-ndk/Android NDK not installed'})
    for name in ('libg.so','libserver.so'):
        path=next((ASSETS/'clash-royale-server-aligned-debugSigned/lib/armeabi-v7a').glob(name))
        try:ctypes.CDLL(str(path));status={'unexpectedly_loaded':True}
        except Exception as exc:status={'error_type':type(exc).__name__,'error':repr(exc),'winerror':getattr(exc,'winerror',None)}
        report['attempts'].append({'action':'ctypes.CDLL','file':str(path.relative_to(ROOT)),**status})
    try:
        run=subprocess.run(['adb','devices','-l'],cwd=ROOT,env=env,capture_output=True,text=True,timeout=10)
        report['attempts'].append({'command':['adb','devices','-l'],'exit_code':run.returncode,'stdout':run.stdout,'stderr':run.stderr})
    except Exception as exc:report['attempts'].append({'command':['adb','devices','-l'],'error':repr(exc)})
    report['license']='Pinned wrapper tree has no LICENSE. Native game code/assets are separate proprietary material; excluded from public sharing.'
    report['runtime_scope']='Windows load attempts intentionally exercise OS format/ABI check only, not a successful Android initialization or battle.'
    (OUT/'scroll_apk_audit.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'apks':[{k:v for k,v in r.items() if k not in ('assets','strings_loadLibrary')} for r in report['apks']],
                      'attempts':report['attempts']},ensure_ascii=True,indent=2))


if __name__=='__main__':main()

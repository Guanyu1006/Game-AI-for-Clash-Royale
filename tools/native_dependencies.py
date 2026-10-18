"""Recover pinned native-test build dependencies into project-local cache."""
from pathlib import Path
import hashlib
import json
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'.cache/native_catch2'
OUT.mkdir(parents=True,exist_ok=True)
items=[]
for name in ['catch_amalgamated.hpp','catch_amalgamated.cpp','LICENSE.txt']:
    path=OUT/name
    url='https://raw.githubusercontent.com/catchorg/Catch2/v3.7.1/'+('extras/' if name.startswith('catch_') else '')+name
    if not path.exists():
        errors=[]
        for attempt in range(4):
            try:
                opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if attempt % 2 else urllib.request.build_opener()
                path.write_bytes(opener.open(url,timeout=30).read());break
            except Exception as exc:errors.append(f'{type(exc).__name__}: {exc}')
        else:raise RuntimeError(f'Cannot recover {name}: {errors}')
    items.append({'name':name,'url':url,'version':'v3.7.1','path':str(path.relative_to(ROOT)),'bytes':path.stat().st_size,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
(ROOT/'outputs/native_engine_dependencies.json').write_text(json.dumps({'catch2':items,'license':'Boost Software License 1.0','pybind11':'3.0.1 (BSD-3-Clause; .cache/native_pybind11)'},indent=2),encoding='utf-8')
print('Native Catch2 dependencies available:',len(items))

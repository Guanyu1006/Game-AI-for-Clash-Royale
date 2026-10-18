"""Read-only local platform inventory. Network/native tests are explicit elsewhere."""
import ctypes
import importlib.util
import os
import platform
import shutil
import sys
from .common import local,now,reference,write

def doctor(output):
    class Memory(ctypes.Structure):
        _fields_=[('length',ctypes.c_ulong),('load',ctypes.c_ulong)]+[(n,ctypes.c_ulonglong) for n in ('total_physical','available_physical','total_page','available_page','total_virtual','available_virtual','extended')]
    memory=Memory();memory.length=ctypes.sizeof(memory)
    available=False
    if sys.platform=='win32':available=bool(ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(memory)))
    report={'schema':'phase3-doctor.v1','time':now(),'python':sys.version,'executable':sys.executable,
        'platform':platform.platform(),'logical_cpus':os.cpu_count(),'disk':dict(zip(('total','used','free'),shutil.disk_usage(local('.')))),
        'memory':{'available':available,'total_bytes':memory.total_physical,'free_bytes':memory.available_physical},
        'modules':{name:bool(importlib.util.find_spec(name)) for name in ('numpy','torch','pyarrow','gymnasium')},
        'project_manifest':reference('data/phase2/version_manifest.json'),'network_connections':0,
        'native_rpc_calls':0,'global_configuration_changes':0}
    if report['modules']['torch']:
        import torch
        report['torch']={'version':torch.__version__,'cuda_available':torch.cuda.is_available(),'device':torch.cuda.get_device_name(0) if torch.cuda.is_available() else 'cpu'}
    write(output,report);return report

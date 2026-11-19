# -*- coding: utf-8 -*-
import json, os, shutil, sys, ctypes
out = {}
try:
    total, used, free = shutil.disk_usage(os.path.abspath(os.sep))
    out['disk'] = {'total_gb': round(total/2**30,1), 'used_pct': round(used/total*100,1), 'free_gb': round(free/2**30,1)}
except Exception as e:
    out['disk'] = str(e)
try:
    class MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [('dwLength', ctypes.c_ulong), ('dwMemoryLoad', ctypes.c_ulong),
                    ('ullTotalPhys', ctypes.c_ulonglong), ('ullAvailPhys', ctypes.c_ulonglong),
                    ('ullTotalPageFile', ctypes.c_ulonglong), ('ullAvailPageFile', ctypes.c_ulonglong),
                    ('ullTotalVirtual', ctypes.c_ulonglong), ('ullAvailVirtual', ctypes.c_ulonglong),
                    ('ullAvailExtendedVirtual', ctypes.c_ulonglong)]
    m = MEMORYSTATUSEX(); m.dwLength = ctypes.sizeof(MEMORYSTATUSEX)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    out['memory'] = {'load_pct': m.dwMemoryLoad, 'total_gb': round(m.ullTotalPhys/2**30,1), 'avail_gb': round(m.ullAvailPhys/2**30,1)}
except Exception as e:
    out['memory'] = str(e)
wd = os.getcwd()
size = 0; n = 0
for root, dirs, files in os.walk(wd):
    for f in files:
        try:
            size += os.path.getsize(os.path.join(root, f)); n += 1
        except OSError:
            pass
out['workdir'] = {'path': wd, 'files': n, 'size_mb': round(size/2**20,1)}
print(json.dumps(out, ensure_ascii=False, indent=2))

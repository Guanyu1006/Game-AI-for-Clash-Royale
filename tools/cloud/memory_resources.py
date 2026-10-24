"""Read-only visible Linux host memory; no subprocess, guest or policy checks."""
from pathlib import Path
import re

MEMINFO_FIELDS = {'MemTotal': 'total_bytes', 'MemAvailable': 'available_bytes',
                  'SwapTotal': 'swap_total_bytes', 'SwapFree': 'swap_free_bytes'}
SCOPE = ('Visible host /proc/meminfo only; not Android guest allocation, '
         'container cgroup memory limit, or proof that a requested machine upgrade completed')


def parse_meminfo(text):
    """Linux meminfo kB means 1024 bytes; absent/invalid values stay unknown."""
    result = {name: None for name in MEMINFO_FIELDS.values()}
    seen = set()
    for line in text.splitlines():
        key, separator, value = line.partition(':')
        if not separator or key not in MEMINFO_FIELDS:
            continue
        field = MEMINFO_FIELDS[key]
        match = re.fullmatch(r'\s*(\d+)\s+kB\s*', value)
        if key in seen:
            result[field] = None  # Ambiguous duplicate data is not an observation.
        else:
            result[field] = int(match[1]) * 1024 if match else None
        seen.add(key)
    result.update(total_gib=result['total_bytes'] / 1024**3 if result['total_bytes'] is not None else None,
                  available_gib=result['available_bytes'] / 1024**3 if result['available_bytes'] is not None else None,
                  scope=SCOPE)
    known = sum(result[name] is not None for name in MEMINFO_FIELDS.values())
    result['status'] = 'available' if known == 4 else 'partial' if known else 'unknown'
    return result


def inspect_memory(path='/proc/meminfo'):
    """Read one known kernel text file; missing observations never become zero."""
    try:
        result = parse_meminfo(Path(path).read_text(encoding='ascii'))
    except (OSError, UnicodeError) as exc:
        result = parse_meminfo('')
        result['error'] = type(exc).__name__ + ': ' + str(exc)
    result['source'] = str(path)
    return result

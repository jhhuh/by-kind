#!/usr/bin/env python3
"""Attribute TinyEMU PC samples to qemu-user text / JIT cache / kernel.

Reads the PROFE dumps from a profiling run plus the /proc/pid/maps captured
from the same process, and reports where the emulated instructions went.
Uses the last N windows, which are the steady-state syscall loop rather than
boot or process startup.
"""
import re, sys, collections, pathlib

WINDOWS = int(sys.argv[1]) if len(sys.argv) > 1 else 10
SYSMAP = ('/nix/store/jic7iqrqd8gmrd0jlx8rialdiy6p89rq-'
          'linux-riscv64-tinyemu-6.12.77-riscv64-unknown-linux-gnu/System.map')

regions = []
for line in open('/tmp/profile-syscall.maps'):
    m = re.match(r'([0-9a-f]+)-([0-9a-f]+) (\S+)\s+(\S+)\s+\S+\s+\S+\s*(.*)', line.strip())
    if m:
        regions.append((int(m.group(1), 16), int(m.group(2), 16),
                        m.group(3), int(m.group(4), 16), m.group(5).strip()))

def where(pc):
    for lo, hi, perm, off, name in regions:
        if lo <= pc < hi:
            if name.endswith('qemu-x86_64') and 'x' in perm:
                return 'qemu-user text', pc - lo + off
            if name.endswith('qemu-x86_64'):
                return 'qemu-user data', None
            if not name and 'x' in perm and 'w' in perm:
                return 'JIT code cache', None
            if name in ('[stack]', '[heap]', '[vdso]', '[vvar]'):
                return name, None
            if name.startswith('/mnt/') and 'bench' in name:
                return 'guest x86_64 binary', None
            return 'other mapping', None
    return 'unmapped', None

# split the stderr into per-dump windows
windows, cur = [], None
for line in open('/tmp/profile-syscall.stderr', errors='replace'):
    if line.startswith('PROF total='):
        cur = []
        windows.append(cur)
    elif line.startswith('PROFE ') and cur is not None:
        _, priv, pc, cnt = line.split()
        cur.append((priv, int(pc, 16), int(cnt)))

use = [w for w in windows if w][-WINDOWS:]
print(f'{len(windows)} windows total, using last {len(use)}')

buckets = collections.Counter()
qemu_off = collections.Counter()
total = 0
for w in use:
    for priv, pc, cnt in w:
        total += cnt
        if priv == 'S' or priv == 'M':
            buckets[f'{priv}-mode (kernel/firmware)'] += cnt
            continue
        name, off = where(pc)
        buckets[name] += cnt
        if off is not None:
            qemu_off[off] += cnt

print(f'\n=== where {total:,} user+kernel samples went ===')
for name, cnt in buckets.most_common():
    print(f'  {100.0*cnt/total:6.2f}%  {name}')

print('\n=== hottest offsets inside qemu-x86_64 (file offset) ===')
for off, cnt in qemu_off.most_common(20):
    print(f'  {100.0*cnt/total:6.3f}%  0x{off:06x}')

# contiguous hot clusters, which is what identifies a function without symbols
offs = sorted(qemu_off)
clusters, cur = [], None
for o in offs:
    if cur and o - cur[-1] <= 64:
        cur.append(o)
    else:
        cur = [o]; clusters.append(cur)
scored = sorted(((sum(qemu_off[o] for o in c), c[0], c[-1], len(c)) for c in clusters),
                reverse=True)
print('\n=== hottest contiguous regions in qemu-x86_64 ===')
for cnt, lo, hi, n in scored[:12]:
    print(f'  {100.0*cnt/total:6.3f}%  0x{lo:06x}-0x{hi:06x}  ({hi-lo+4:5d} bytes, {n:4d} PCs)')

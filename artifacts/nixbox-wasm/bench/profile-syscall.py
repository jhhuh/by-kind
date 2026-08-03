#!/usr/bin/env python3
"""Profile where our stack spends time during an emulated x86_64 syscall.

Runs qemu-user in the background inside the guest, dumps its /proc/pid/maps
(qemu-x86_64 is a stripped static-PIE, so the load base is only knowable at
runtime), and lets TinyEMU's PC sampler run over the workload. Attribution is
done offline against those maps.
"""
import os, pty, re, select, subprocess, sys, time

HERE = os.path.dirname(os.path.abspath(__file__))
TEMU = os.path.join(HERE, '..', 'tinyemu-2019-12-21', 'temu')
CFG = os.path.join(HERE, 'rv-native.cfg')
BIN = sys.argv[1] if len(sys.argv) > 1 else 'syscallbench-500000'

mfd, sfd = pty.openpty()
errf = open('/tmp/profile-syscall.stderr', 'wb')
p = subprocess.Popen([TEMU, '-append', 'init=/bin/sh', CFG],
                     stdin=sfd, stdout=sfd, stderr=errf, close_fds=True)
os.close(sfd)

buf, sent, done = b'', False, False
deadline = time.monotonic() + 600
while time.monotonic() < deadline:
    r, _, _ = select.select([mfd], [], [], 0.2)
    if r:
        try:
            d = os.read(mfd, 4096)
        except OSError:
            break
        if not d:
            break
        buf += d
        if not sent and buf.endswith(b'/ # '):
            sent = True
            os.write(mfd, (
                'mount -t proc none /proc\n'
                'mount -t 9p -o trans=virtio,version=9p2000.L,msize=131072 /dev/root /mnt\n'
                f'/mnt/qemu-x86_64 /mnt/{BIN} &\n'
                'sleep 3\n'
                'P=; for d in /proc/[0-9]*; do [ "$(cat $d/comm)" = qemu-x86_64 ] && P=${d#/proc/}; done\n'
                # split literals so the pty's echo of the command cannot
                # match the markers we scan for
                'echo MAPS\'\'TART; cat /proc/$P/maps; echo MAP\'\'END\n').encode())
    if b'MAPEND' in buf and re.search(rb'(?:CK|SC)=[0-9a-f]{16}', buf):
        break
p.kill(); p.wait(); errf.close()

text = buf.decode('utf8', 'replace')
m = re.search(r'MAPSTART(.*?)MAPEND', text, re.S)
open('/tmp/profile-syscall.maps', 'w').write(m.group(1) if m else '')
print(m.group(1).strip() if m else 'NO MAPS CAPTURED')

#!/usr/bin/env python3
"""Count riscv64 instructions our stack spends per emulated x86_64 syscall.

Runs three workloads back to back in one native temu boot and interpolates
insn_counter (from TLBSTAT, which carries both t= wall ms and insn=) at each
marker. Both clocks are CLOCK_MONOTONIC, so the console timestamps taken here
and the emulator's t= are directly comparable.

  usage: count-insn.py <bin0> <binN> <rounds>
"""
import os, pty, re, select, subprocess, sys, time

BIN0, BINN, ROUNDS = sys.argv[1], sys.argv[2], int(sys.argv[3])
HERE = os.path.dirname(os.path.abspath(__file__))
TEMU = os.path.join(HERE, '..', 'tinyemu-2019-12-21', 'temu')
CFG = os.path.join(HERE, 'rv-native.cfg')

mfd, sfd = pty.openpty()
errf = open('/tmp/count-insn.stderr', 'wb')
p = subprocess.Popen([TEMU, '-append', 'init=/bin/sh', CFG],
                     stdin=sfd, stdout=sfd, stderr=errf, close_fds=True)
os.close(sfd)

buf, marks, sent = b'', [], False
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
        for m in re.finditer(rb'(?:CK|SC)=([0-9a-f]{16})', buf):
            if not any(x[0] == m.start() for x in marks):
                marks.append((m.start(), time.monotonic(), m.group(1).decode()))
        if not sent and buf.endswith(b'/ # '):
            sent = True
            os.write(mfd, (
                'mount -t 9p -o trans=virtio,version=9p2000.L,msize=131072 /dev/root /mnt\n'
                f'/mnt/qemu-x86_64 /mnt/{BIN0}\n'
                f'/mnt/qemu-x86_64 /mnt/{BIN0}\n'
                f'/mnt/qemu-x86_64 /mnt/{BINN}\n').encode())
    if len(marks) >= 3:
        break
p.kill(); p.wait(); errf.close()

samples = []
for line in open('/tmp/count-insn.stderr', errors='replace'):
    m = re.search(r'TLBSTAT t=(\d+) .*insn=(\d+)', line)
    if m:
        samples.append((int(m.group(1)) / 1000.0, int(m.group(2))))

def insn_at(t):
    """linear interpolation between the two bracketing TLBSTAT samples"""
    prev = None
    for ts, n in samples:
        if ts >= t:
            if prev is None:
                return n
            (t0, n0) = prev
            if ts == t0:
                return n
            return n0 + (n - n0) * (t - t0) / (ts - t0)
        prev = (ts, n)
    return samples[-1][1] if samples else None

if len(marks) < 3 or not samples:
    print(f'incomplete: {len(marks)} markers, {len(samples)} TLBSTAT samples')
    sys.exit(1)

i = [insn_at(t) for _, t, _ in marks]
startup = i[1] - i[0]
run = i[2] - i[1]
per = (run - startup) / ROUNDS
print(f'checksums      {[m[2] for m in marks]}')
print(f'startup        {startup:,.0f} riscv64 insns (qemu-user start, no work)')
print(f'workload       {run:,.0f} riscv64 insns (startup + {ROUNDS:,} rounds)')
print(f'per round      {per:,.1f} riscv64 instructions')

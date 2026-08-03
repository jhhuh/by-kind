#!/usr/bin/env python3
"""Run a guest command sequence and time it from OUTSIDE the guest.

Every console byte is timestamped on the host as it arrives, so elapsed time
between two guest-printed markers is real wall time, not guest clock.

  usage: timed-run.py <cfg> <label> [--timeout N] -- <cmd> [<cmd> ...]
"""
import os, pty, re, select, subprocess, sys, time

cfg, label = sys.argv[1], sys.argv[2]
rest = sys.argv[3:]
timeout = 120.0
if rest and rest[0] == '--timeout':
    timeout = float(rest[1]); rest = rest[2:]
assert rest[0] == '--', "expected -- before commands"
cmds = rest[1:]

TEMU = os.environ.get('TEMU') or os.path.join(
    os.path.dirname(os.path.abspath(__file__)), '..', 'tinyemu-2019-12-21', 'temu')

mfd, sfd = pty.openpty()
err = open(f'/tmp/timed-{label}.stderr', 'wb')
p = subprocess.Popen([TEMU, '-append', 'init=/bin/sh', cfg],
                     stdin=sfd, stdout=sfd, stderr=err, close_fds=True)
os.close(sfd)

t0 = time.monotonic()
buf, line, events, sent = b'', b'', [], 0
deadline = t0 + timeout
prompt_seen = False

while time.monotonic() < deadline:
    r, _, _ = select.select([mfd], [], [], 0.2)
    if r:
        try:
            data = os.read(mfd, 4096)
        except OSError:
            break
        if not data:
            break
        buf += data
        for ch in data:
            if ch in (10, 13):
                if line:
                    events.append((time.monotonic() - t0, line.decode('utf8', 'replace')))
                line = b''
            else:
                line += bytes([ch])
        # the shell prompt "/ # " has no newline, so match on the raw buffer
        if not prompt_seen and buf.endswith(b'/ # '):
            prompt_seen = True
            events.append((time.monotonic() - t0, '<<prompt>>'))
    if prompt_seen and sent < len(cmds):
        # feed the next command once the previous one's marker has been seen.
        # match on exact line equality: the echoed command line also contains
        # the marker text, and long commands wrap across lines.
        want = f'MARK{sent - 1}' if sent else None
        if want is None or any(t.strip() == want for _, t in events):
            os.write(mfd, (cmds[sent] + '\n').encode())
            events.append((time.monotonic() - t0, f'<<sent {sent}>>'))
            sent += 1
    if sent == len(cmds) and any(t.strip() == f'MARK{len(cmds)-1}'
                                 for _, t in events):
        break

p.kill(); p.wait(); err.close()

print(f'=== {label} ===')
for t, txt in events:
    if txt.startswith('<<') or 'MARK' in txt or 'DONE' in txt or 'real' in txt:
        print(f'  {t:8.2f}s  {txt}')
if sent < len(cmds) or not any(f'MARK{len(cmds)-1}' in t for _, t in events):
    print(f'  TIMEOUT after {timeout}s (sent {sent}/{len(cmds)} commands)')
    print('  last console lines:')
    for t, txt in events[-6:]:
        print(f'    {t:8.2f}s  {txt}')

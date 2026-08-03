#!/usr/bin/env bash
# Measure idle instruction burn: boot to a shell prompt, send nothing, and
# report instructions executed per wall second from TIMERSTAT (wall-clock keyed,
# read from OUTSIDE the guest).
#   usage: idle-burn.sh <temu-binary> <cfg> [seconds] [label]
set -u
TEMU="${1:?usage: idle-burn.sh <temu> <cfg> [secs] [label]}"
CFG="${2:?}"
SECS="${3:-50}"
LABEL="${4:-$(basename "$TEMU")}"

ERR=$(mktemp)
timeout $((SECS + 15)) "$TEMU" -append 'init=/bin/sh' "$CFG" >/dev/null 2>"$ERR" &
PID=$!
sleep "$SECS"
kill -TERM $PID 2>/dev/null
wait $PID 2>/dev/null

# TIMERSTAT t=<ms wall> insn=<count> ...  -- take the first and last samples
# after the guest has reached the prompt (drop the first two, boot is not idle).
python3 - "$ERR" "$LABEL" <<'EOF'
import re, sys
rows = []
for line in open(sys.argv[1], errors='replace'):
    m = re.search(r'TIMERSTAT t=(\d+) insn=(\d+) mtip=(\d+) .*powerdown=(\d)', line)
    if m:
        rows.append(tuple(int(x) for x in m.groups()))
if len(rows) < 3:
    print(f"{sys.argv[2]}: only {len(rows)} TIMERSTAT samples -- boot failed?")
    sys.exit(1)
a, b = rows[1], rows[-1]          # drop sample 0: still booting
dt   = (b[0] - a[0]) / 1000.0
dins = b[1] - a[1]
dmt  = b[2] - a[2]
print(f"{sys.argv[2]}: idle {dt:.1f}s wall  insn={dins:,}  "
      f"{dins/dt/1e6:.2f} Minsn/s  mtip={dmt}  "
      f"{dins/dmt if dmt else float('nan'):,.0f} insn/wakeup  powerdown={b[3]}")
EOF
rm -f "$ERR"

# Devlog: browser Linux with x86_64 nix packages

Topic-specific journal, split off from `devlog.md` (which covers the by-kind
classifier). Newest entries at the bottom. Entry point for the whole thread is
[`HANDOFF.md`](HANDOFF.md).

---

## 2026-08-03 — the "idle spin" finding was a measurement artifact

Resumed on the one open blocker: 6.12 burning ~940,000 instructions per timer
wakeup while sitting idle at a shell prompt.

**It does not.** The kernel idles correctly, and better than 4.15 does.

### What went wrong

`TLBSTAT` was keyed on `insn_counter` and carried no wall-clock stamp. The
original measurement was "sit at a prompt for 45 seconds, read the last
instruction count" — which counts **boot** instructions and attributes them all
to idle. Adding `t=<CLOCK_MONOTONIC ms>` to the same dump made it visible
immediately:

```
6.12 BOOT   200,046,013 insn in   1.10 s wall  = 181 Minsn/s
6.12 IDLE     1,850,784 insn in  35.0  s wall  =  52.9 Kinsn/s
```

The 220 M instructions are the boot, and the boot takes about a second. The old
"220 M while idle" number and the "45 seconds" it was divided by were measuring
two different things.

Idle, measured over a wall-clock-keyed window on both kernels:

```
            insn/s idle   timer rate   insn/wakeup   powerdown
4.15         166 K         100 Hz         1,663          1
6.12          53 K         5.5 Hz         9,640          1
```

6.12 burns **three times fewer** instructions per second at idle. It is more
expensive per wakeup (5.8×) but wakes 18× less often, which is exactly what a
tickless kernel should look like. Both reach WFI.

### What this retracts

- **"180 million instructions while idle."** No — that is the boot.
- **"~100× more work every time the guest wakes up."** No — 5.8×, and the
  earlier figure came from dividing boot instructions by idle wakeups.
- **"It never settles, and every wall-clock second goes to spinning rather than
  to the workload. That is what starves qemu-user."** This was the only
  mechanism I had for the real blocker, and it is wrong. The blocker's cause is
  once again unknown.

The two hypotheses previously recorded as *disproven* (MMU thrashing, timer
storm) stay disproven. What changes is that the observation I had recorded as
*established* — the per-wakeup cost — was also an artifact.

That is three measurement-scope errors of the same kind now (host pty I/O
counted as emulation, guest clock counted as wall clock, boot counted as idle).
The common failure is reading a counter whose key is not the axis being claimed.
`TLBSTAT`/`TIMERSTAT` now both carry `t=` in wall-clock ms for this reason.

### Incidental

- `temu` no longer builds against this sandbox's curl 8.18 headers
  (`multi.h:429: expected identifier before '__extension__'`) or SDL 1.x. Native
  builds now use `make temu CONFIG_SDL= CONFIG_FS_NET=`. Neither affects the
  riscv64 interpreter; `-fs0` local 9p and local disk images still work, only
  the HTTP-backed vfsync filesystem is gone from the native binary.
- `TIME_CACHE_INSNS` is now overridable at build time (`-DTIME_CACHE_INSNS=N`)
  so the `rdtime` cache granularity can be varied without editing source.
- `riscv64-kernel-minimal.nix` now installs `System.map` and `vmlinux`, needed
  to resolve profiler samples to kernel symbols.

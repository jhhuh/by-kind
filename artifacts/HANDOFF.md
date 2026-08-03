# Hand-off: browser Linux with x86_64 nix packages

Written 2026-08-03. Read this first if you are resuming cold.

## Where this came from

`by-kind` (https://jhhuh.github.io/by-kind/) classifies nixpkgs `by-name` packages. This
is a **separate exploration** living alongside it: clicking a package should open a
terminal with that package usable — a browser microVM. Everything here is notes and
experiments; no by-kind code is touched.

## The design

```
browser → wasm → TinyEMU riscv64 interpreter → riscv64 Linux
                                                 ├─ binfmt_misc → qemu-x86_64 (a riscv64 binary)
                                                 └─ x86_64 nix packages over 9p
```

The riscv64 layer exists **only for fork/exec**. Nothing else in wasm provides them —
Blink stubs `wait4`, WASI has no processes. The architecture we emulate and the
architecture the packages are built for deliberately differ: riscv64 nixpkgs binary-cache
coverage is 0%, x86_64 is ~100%.

## What works

**Nested x86_64, natively and in wasm.** Same md5 digest as the native path throughout.

```
                    native temu    wasm temu
riscv64 md5sum 16MB    1.21 s        1.41 s
x86_64 via qemu-user   5.32 s        6.40 s
translation cost       4.21x         4.35x     <- unchanged by host
```

**TinyEMU builds to wasm** from the 2019 source on emscripten 4.0.10 — only three retired
link flags needed dropping. 159,877 bytes. Boots to a shell as PID 1 in **574 ms**, about
2.0x native (290.8 ms). Cold fetch is ~2.0 MB gzipped with the rootfs still lazy, against
qemu-wasm's ~119 MB and ~30 s.

**Transparent execution via binfmt_misc**, on the 4.15 guest:

```
/ # echo NATIVE_OK; /mnt/busybox echo TRANSPARENT_x86_64_OK; /mnt/busybox uname -m
NATIVE_OK
TRANSPARENT_x86_64_OK
x86_64
```

## Five TinyEMU bugs found and fixed

Patches in [`nixbox-wasm/`](nixbox-wasm/):

- **`tinyemu-canonical-isa-string.patch`** — TinyEMU emitted `riscv,isa` alphabetically
  (`rv64acdfim`); Linux does `strncasecmp(isa, "rv64ima", 7)`, rejects every CPU node,
  then `BUG_ON(!found_boot_cpu)`. Probably why nobody runs modern kernels on TinyEMU: the
  panic points at SMP, not at the device tree.
- **`tinyemu-time-csr.patch`** — CSR `0xc01` was never implemented (Bellard expected
  firmware to trap `rdtime`; 6.12's vDSO reads it from U-mode), and `COUNTEREN_MASK`
  lacked TM. Includes a 1024-instruction cache, because the naive version called
  `clock_gettime()` once per guest instruction.
- **`tinyemu-dt-rng-seed.patch`** — no entropy source at all, so Linux ≥5.x blocks
  the first `getrandom()` forever. See the blocker section below.
- **`tinyemu-fdt-reserve-firmware.patch`** — empty FDT memory reservation map, so
  Linux overwrites BBL and any M-mode trap spins forever. See below.
- **`tinyemu-virtqueue-size.patch`** — `MAX_QUEUE_NUM` was **16**, and
  `VRING_DESC_F_INDIRECT` is defined but never used, so ring length capped
  scatter-gather size. One line to 256: `msize=131072` works and a 3.76 MB load went
  **~200 s → 1 s**.

## The blocker is closed (2026-08-03)

x86_64 workloads now run on the 6.12 guest at 4.15's speed. Timed from outside the
guest, with host-side timestamps on every console byte
([`timed-run.py`](nixbox-wasm/timed-run.py)):

```
                                   6.12 before   6.12 after   4.15
qemu-x86_64 busybox true           never >380s      0.29 s    0.22 s
qemu-x86_64 busybox uname -m       never            0.40 s    0.33 s
qemu-x86_64 busybox md5sum (1 MB)  never            0.95 s      -
```

Digest verified against the host, `uname -m` reports `x86_64`, and 4.15 is unchanged.
It was **two more TinyEMU bugs**, neither related to emulation speed:

- **`tinyemu-dt-rng-seed.patch`** — TinyEMU has no entropy source of any kind, and
  Linux ≥5.x blocks `getrandom()` in `wait_for_random_bytes()` until the CRNG is
  seeded, so the first caller hangs forever. Caught by asking the guest:
  `SYSCALL=278` (`__NR_getrandom`), `WCHAN=wait_for_random_bytes`. Fixed by putting
  an `rng-seed` in the device tree's `/chosen`, the standard bootloader contract.
- **`tinyemu-fdt-reserve-firmware.patch`** — `fdt_output()` wrote an *empty* memory
  reservation map, so Linux allocated over BBL's text. Silent until the first trap
  the firmware still owns (`medeleg` delegates neither illegal-instruction nor access
  faults), whereupon `mtvec` jumps into heap data and the machine spins in M-mode at
  240 Minsn/s forever. Caught by the PC profiler: 100% M-mode in a 7-instruction loop
  at `0x80000004`, with the memory there no longer matching `bbl64.bin`.

Three earlier hypotheses were **disproven by measurement** and are worth not
revisiting: not the MMU (page-walk rates unremarkable); not a timer storm (6.12 fires
20× *fewer* interrupts, correctly tickless); and not an idle spin — that last one was
my own measurement artifact, retracted below. A fourth, that U-mode `rdtime` was
trapping, died on inspection: `mcounteren`/`scounteren` are both `0x7`.

### Retracted 2026-08-03: the "idle spin" result

`TLBSTAT` was keyed on `insn_counter` with no wall-clock stamp, so "sit at a prompt for
45 s and read the instruction count" was counting the **boot** and attributing it to
idle. With `t=` added to the same dump:

```
6.12 BOOT   200,046,013 insn in   1.10 s wall
6.12 IDLE     1,850,784 insn in  35.0  s wall  = 52.9 Kinsn/s

               insn/s idle   timer rate   insn/wakeup
  4.15          166 K         100 Hz         1,663
  6.12           53 K         5.5 Hz         9,640
```

6.12 idles **three times more cheaply** than 4.15 in absolute terms. It never spun. The
figures `220,041,906 insn / 234 wakeups = 940,350` and "~100x more work per wakeup" came
from dividing boot instructions by idle wakeups and should not be used.

### What replaced it

Re-timing the workload from outside showed the guest was **blocked, not spinning** —
`powerdown=1` at ~135 Kinsn/s, with virtio kicks, completions, IRQ raises and acks all
frozen, so there was no outstanding I/O either. That reframing is what led to
`getrandom()`, and from there to the overwritten firmware. Both are fixed above.

## Reading order

1. [`experiment-results-2026-08-02.md`](experiment-results-2026-08-02.md) — the measurements
2. [`devlog_browser-linux.md`](devlog_browser-linux.md) — the running journal, and the
   only place the two 2026-08-03 bugs are written up in full
3. [`modern-kernel-on-tinyemu.md`](modern-kernel-on-tinyemu.md) — kernel build, the first
   three bugs, and the wrong turns
4. [`prior-art-browser-x86-emulators.md`](prior-art-browser-x86-emulators.md) — landscape
5. [`js-wasm-boundary-patterns.md`](js-wasm-boundary-patterns.md) — JS↔wasm plumbing,
   written to be self-contained
6. [`nixbox-wasm/README.md`](nixbox-wasm/README.md) — how to build and how to measure

## Gotchas that cost hours

- **`MODULARIZE=1` is mandatory** — otherwise the glue's `var Module` hoists and silently
  shadows any injected config.
- `vm_start` takes **7** args in the 2019 source; today's `jslinux.js` passes 8.
- The config path **must be a URL** — `load_file()` is `abort()` under Emscripten.
- The disk must be in **split format**; pass width/height `0` without a framebuffer.
- The symbol is **`9P_FS`**, not `V9FS_FS`. My first minimal kernel had the 9p protocol
  and no filesystem driver.
- **`HVC_RISCV_SBI` must stay off** — it claims `hvc0`, and TinyEMU's SBI console is
  output-only, so the guest prints perfectly and accepts no input.
- binfmt magic must be **escaped text**, not raw bytes — NULs truncate it so it matches
  every ELF and every exec ELOOPs, including native ones. Use Alpine's conf verbatim.
- **Mount `/proc` before believing `ps`** — without it `ps` prints a header and no rows
  and `/proc/[0-9]*` does not glob, which looks like "no processes" rather than "no
  procfs". `/proc/<pid>/syscall` and `wchan` are what actually named both 2026-08-03 bugs.
- The **remote builder fails on cross derivations** (`/setup: No such file or directory`
  from `source-stdenv.sh` on `zhao.coati-bebop.ts.net`); use `--builders ''`.

## Four of my measurements were wrong

Worth knowing when reading older commits, all corrected in place:

- "25 ms native boot" was process-spawn overhead; the boot happened inside a `sleep` and
  was never timed. Real figure 290.8 ms.
- "55% of boot is console logging" was host pty I/O under `script`, not emulation.
- "1 s vs 370 s" came from `date +%s` **inside the guest** — guest clock, not wall clock.
- "180 M instructions while idle on 6.12" was the boot, counted by an
  instruction-keyed dump with no wall clock.

All four are the same failure: reading a counter whose key is not the axis being claimed.
`TLBSTAT` and `TIMERSTAT` now both carry `t=` in `CLOCK_MONOTONIC` ms, and
`timed-run.py` timestamps console bytes on the host. Measure externally, and check what
the x-axis actually is.

## Not done

- Kernel is 15.6 MB / 4.2 MB gzipped (down from 44.6 MB) — acceptable, but Bellard's is
  1.9 MB.
- Never run in a real browser, only node. Same V8, but that is an inference.
- The nix side — NAR fetching, closure walk, OPFS caching — is proven natively
  ([`spike_nixbox/`](spike_nixbox/)) but not wired into the wasm guest.

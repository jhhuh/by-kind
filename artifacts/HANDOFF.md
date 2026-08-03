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

## Three TinyEMU bugs found and fixed

Patches in [`nixbox-wasm/`](nixbox-wasm/):

- **`tinyemu-canonical-isa-string.patch`** — TinyEMU emitted `riscv,isa` alphabetically
  (`rv64acdfim`); Linux does `strncasecmp(isa, "rv64ima", 7)`, rejects every CPU node,
  then `BUG_ON(!found_boot_cpu)`. Probably why nobody runs modern kernels on TinyEMU: the
  panic points at SMP, not at the device tree.
- **`tinyemu-time-csr.patch`** — CSR `0xc01` was never implemented (Bellard expected
  firmware to trap `rdtime`; 6.12's vDSO reads it from U-mode), and `COUNTEREN_MASK`
  lacked TM. Includes a 1024-instruction cache, because the naive version called
  `clock_gettime()` once per guest instruction.
- **`tinyemu-virtqueue-size.patch`** — `MAX_QUEUE_NUM` was **16**, and
  `VRING_DESC_F_INDIRECT` is defined but never used, so ring length capped
  scatter-gather size. One line to 256: `msize=131072` works and a 3.76 MB load went
  **~200 s → 1 s**.

## The open blocker

**x86_64 emulation is unusable on the 6.12 guest** — under 0.5 s wall on 4.15, does not
complete in 360 s on 6.12. Same emulator, same binaries, no binfmt and no 9p involved.

Two of my hypotheses were **disproven by measurement**:

- *Not the MMU.* Page-walk rates unremarkable.
- *Not a timer storm — the reverse.* 6.12 fires 20x **fewer** interrupts (correctly
  tickless), and both kernels reach WFI with `powerdown=1`.

What the data does say:

```
4.15    40,000,578 insn / 4500 wakeups =     8,889 instructions per timer event
6.12   220,041,906 insn /  234 wakeups =   940,350 instructions per timer event
```

**~100x more work per wakeup.** I am not offering a third mechanism without data, having
guessed wrong twice here. The instrumentation patches
(`tinyemu-tlb-instrumentation.patch`, `tinyemu-timer-instrumentation.patch`) are
committed and reusable; the next step is finding *what* runs during those 940K
instructions — e.g. logging PC ranges after wakeup.

## Reading order

1. [`experiment-results-2026-08-02.md`](experiment-results-2026-08-02.md) — the measurements
2. [`modern-kernel-on-tinyemu.md`](modern-kernel-on-tinyemu.md) — kernel build, the three
   bugs, the open blocker, and both wrong turns
3. [`prior-art-browser-x86-emulators.md`](prior-art-browser-x86-emulators.md) — landscape
4. [`js-wasm-boundary-patterns.md`](js-wasm-boundary-patterns.md) — JS↔wasm plumbing,
   written to be self-contained
5. [`nixbox-wasm/README.md`](nixbox-wasm/README.md) — how to reproduce the build

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
- The **remote builder fails on cross derivations** (`/setup: No such file or directory`
  from `source-stdenv.sh` on `zhao.coati-bebop.ts.net`); use `--builders ''`.

## Three of my measurements were wrong

Worth knowing when reading older commits, all corrected in place:

- "25 ms native boot" was process-spawn overhead; the boot happened inside a `sleep` and
  was never timed. Real figure 290.8 ms.
- "55% of boot is console logging" was host pty I/O under `script`, not emulation.
- "1 s vs 370 s" came from `date +%s` **inside the guest** — guest clock, not wall clock.

Measure externally before trusting a timing.

## Not done

- Kernel is 15.6 MB / 4.2 MB gzipped (down from 44.6 MB) — acceptable, but Bellard's is
  1.9 MB.
- Never run in a real browser, only node. Same V8, but that is an inference.
- The nix side — NAR fetching, closure walk, OPFS caching — is proven natively
  ([`spike_nixbox/`](spike_nixbox/)) but not wired into the wasm guest.

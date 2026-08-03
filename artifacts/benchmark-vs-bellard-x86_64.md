# How much would a real x86_64 core in TinyEMU actually buy?

Our design emulates **riscv64** and runs x86_64 binaries under `qemu-x86_64` inside it,
because TinyEMU's `x86_cpu.c` is a stub. The obvious question is what we are paying for
that extra layer, and therefore whether implementing `x86_cpu_interp` is worth it.

Bellard publishes a working x86_64 emulator compiled to wasm for
[jslinux](https://bellard.org/jslinux/). It is **not redistributable**, so it cannot be
deployed — but it can be measured, and it is the best available answer to "what if
TinyEMU had a real x86_64 core".

**Answer: it depends entirely on the workload, by a factor of ten.** On compute-bound
code the nested design costs ~7%. On syscall-bound code it costs ~11×.

## Numbers

Two freestanding x86_64 binaries, no libc (`-nostdlib`, raw syscalls), so the same bytes
run in every configuration and nothing depends on which libc a guest has. Each is built
twice — `ROUNDS=0` and the real count — and the zero build's time is subtracted, which
removes process startup, 9p load and (on Bellard's side) kernel boot.

Everything is timed **on the host** as console bytes arrive. Both emulators run in wasm
under the same node on the same machine.

```
compute-bound: 20,000,000 rounds of a dependent xorshift/multiply chain

                                  time      vs native
  native x86_64                     35 ms      1.0x
  Bellard x86_64 in wasm         2,373 ms     67.8x
  ours: TinyEMU riscv64 + qemu-user in wasm
                                 2,546 ms     72.7x      <- 1.07x Bellard


syscall-bound: 500,000 getpid()

                                  time      vs native
  native x86_64                    137 ms      1.0x
  Bellard x86_64 in wasm           798 ms       5.8x
  ours: TinyEMU riscv64 + qemu-user in wasm
                                 8,961 ms      65.4x      <- 11.2x Bellard
```

Every configuration produced identical checksums — `cebcc5dad359ec3e` and
`000000000007a120` — so all three are doing the same work.

## Why the two workloads disagree

`qemu-user` translates a hot loop **once** into riscv64, after which TinyEMU is
interpreting straight-line riscv64. Bellard's emulator decodes x86_64 on every
instruction. Those two effects very nearly cancel, which is why the compute case is a
wash.

A syscall gets no such amortisation. Each one traps out of translated code, is marshalled
by `qemu-user`, and becomes a real riscv64 syscall into the emulated kernel — a long path
executed interpreted, every time. Bellard's guest issues one syscall that its kernel
handles directly.

This also reconciles with the earlier measurement that `qemu-user` costs 4.35× over
native riscv64 code *inside our own emulator*: if the nested stack still matches Bellard
on compute despite paying that 4.35×, then TinyEMU's riscv64 core is roughly 4× cheaper
per instruction than his x86_64 core — which is what you would expect from the relative
decode complexity of the two ISAs.

## What this means for the project

**Implementing an x86_64 core in TinyEMU is not the lever it looked like.** For
compute-bound work it buys essentially nothing. It is worth up to ~11× only for
syscall-bound work.

That matters here because running nix packages *is* closer to the syscall-bound end:
short-lived processes, `exec`, path lookups, lots of small file I/O. So the honest
statement is that the ceiling is real but workload-specific, and cheaper things should be
tried first — `qemu-user` syscall path costs, and the emulated kernel's own syscall entry
cost, are both attackable without writing an x86_64 interpreter.

## Does his interpreter special-case `syscall`?

A fair worry: if his emulator shortcut the `syscall` instruction — dispatching it in the
emulator instead of running the guest kernel's entry path — the syscall column would be
measuring two different things. The instruction counts say it does not.

Both loops were disassembled to get exact guest instruction counts per round: **17** for
the compute loop, **8** for the syscall loop (7 ALU plus one `syscall`).

*His side*, derived from the compute rate. 20,000,000 × 17 = 340 M guest instructions in
2,373 ms is **143 Minsn/s**. The syscall run's 500,000 × 8 = 4 M loop instructions
therefore account for ~28 ms of the 798 ms, leaving **~220 emulated instructions per
syscall round trip**. That is an ordinary cost for a Linux `entry_SYSCALL_64` →
`sys_getpid` → `sysretq` path in a kernel built without PTI and retpolines, and it is far
too expensive to be a shortcut — a paravirtual dispatch would be a handful of
instructions, not a couple of hundred. (This is an upper bound on the count: kernel entry
uses `swapgs`, pushes and MSR access, which likely cost his interpreter more per
instruction than the ALU ops the 143 Minsn/s figure came from, so the true count is
probably lower still — but lower for that reason, not because of special-casing.)

*Our side*, measured exactly rather than derived, using TinyEMU's own instruction counter
([`bench/count-insn.py`](nixbox-wasm/bench/count-insn.py) interpolates `insn=` from the
wall-clock-keyed `TLBSTAT` dumps at each marker):

```
compute   30.8 riscv64 instructions per round of 17 x86 instructions
                                        = 1.81 riscv64 insns per emulated x86 insn
syscall  1787.0 riscv64 instructions per round of 8 x86 instructions
                                        = ~1,774 for the `syscall` alone
```

So `qemu-user` turns ordinary x86_64 into riscv64 at **1.81 instructions each** — very
good, and why the compute case is a wash — but a single guest syscall costs **~1,774
riscv64 instructions**, roughly **980× an ordinary instruction**.

That is the whole of the 11×, and no special-casing on his side is needed to explain it.
Our stack pays for two kernel entries' worth of work where his pays for one: `qemu-user`
must leave the translated block, save guest state, run its `do_syscall` dispatch, and
then issue a *real* riscv64 syscall into the emulated riscv64 kernel — whose own entry
path is itself interpreted — before restoring and re-entering translated code.

### Splitting the 1,774

The same loop was cross-built for **riscv64** and run directly in the guest with no
`qemu-user` in the path ([`bench/rvsyscallbench.c`](nixbox-wasm/bench/rvsyscallbench.c)),
which measures our kernel's entry cost on its own. Its loop is 7 riscv64 instructions,
one being `ecall`:

```
239.6 riscv64 instructions per native round
  - 6 for the loop itself
  = ~234 for a native riscv64 syscall (kernel entry, sys_getpid, exit)
```

So:

```
  x86_64 syscall through qemu-user      1,774 riscv64 instructions
  native riscv64 syscall                  234
  ------------------------------------------
  qemu-user marshalling                 1,540      <- 87% of the cost
```

**The emulated kernel is not the problem; `qemu-user` is.** Our riscv64 kernel's entry
path costs ~234 interpreted instructions, which is in the same range as the ~220 derived
for Bellard's x86_64 kernel entry — two lean Linux syscall paths, as expected, and a
useful consistency check on that derived figure. Everything above that is `qemu-user`
leaving the translation block, saving and restoring guest CPU state, and running its
`do_syscall` dispatch.

That makes the 11× attackable **without writing an x86_64 CPU**. As a bound: if
`qemu-user`'s per-syscall overhead vanished, the syscall path would go from 1,774 to 234
instructions — 7.6× — which would put our syscall-bound figure near 8.6× native against
Bellard's 5.8×, i.e. most of the gap closed.

Two caveats on that bound. `getpid` is the cheapest syscall there is, so ~1,540 is
essentially `qemu-user`'s *fixed* per-syscall cost; for syscalls that do real work the
relative overhead is smaller, and 11× is therefore a worst case rather than a typical
one. And where those 1,540 instructions actually go inside `qemu-user` has not been
profiled — the PC profiler can attribute user-mode samples, so that is the next step, not
a conclusion.

## What we know about his kernel, and what we do not

His side runs **his** kernel, and that is an uncontrolled variable. What could be
established from the image:

```
Linux version 6.19.3 (bellard@gpu-server4)
  (gcc 13.3.1 20240611 (Red Hat 13.3.1-2), GNU ld 2.40-21.el8)
  #17 PREEMPT_DYNAMIC Mon Mar 9 17:12:35 CET 2026
```

- **It is genuinely x86_64**, and this does not rest on the filename: the benchmarks are
  `ELF 64-bit LSB, x86-64, statically linked`, his emulator ran them, and the checksums
  match native exactly. A 32-bit kernel could not have executed them.
- The bzImage is **uncompressed**, and there is no `IKCFG_ST`, so `CONFIG_IKCONFIG` is
  off and **his `.config` cannot be read**.
- A string scan turns up **no modification markers** — every "patch" hit is ordinary
  upstream microcode/text-patching text. That is weak evidence: absence of markers is
  not proof of an unmodified tree.

**So we cannot show his kernel is vanilla.** It is also version **6.19.3 against our
6.12.77**, with an unknown config, so the two sides differ in kernel version and
configuration as well as in emulator and ISA.

This is not fatal, because it does not touch both results equally:

- **The compute-bound number (1.07×) is essentially immune.** It is a userspace loop
  that issues one `write` and one `exit`; the kernel is barely involved.
- **The syscall-bound number (11.2×) is exposed.** Kernel entry cost is a large part of
  what it measures, so some unknown share of that 11× could be kernel version or config
  rather than emulator. Treat 11.2× as an upper bound on the *emulator* difference, not
  as a measurement of it.

Controlling this properly means running the same kernel version and config on both sides,
which is not directly possible across ISAs; the nearest approach is booting a vanilla
x86_64 kernel of a known version under his emulator. Not done.

**Two further caveats, both of which make the payoff look smaller than the table
suggests:**

- Bellard's core is closed and hand-optimised over many years. A core we write is
  unlikely to match it, so 11× is an **upper** bound on what implementing
  `x86_cpu_interp` would recover.
- The comparison conflates two things — the extra `qemu-user` layer, and his core's
  quality versus TinyEMU's. The decomposition above separates them, but only for these
  two workloads.

## Reproducing

Nothing from bellard.org is vendored in this repository. To repeat the measurement:

```sh
# our side needs no download; Bellard's side needs these three, which are his:
curl -O https://bellard.org/jslinux/kernel-x86_64-new.bin
#   x86_64emu.wasm and x86_64emu-wasm.js come from the jslinux page itself
#   (https://bellard.org/jslinux/vm.html?cpu=x86_64&url=alpine-x86_64.cfg&mem=256)

# build the workloads (host is x86_64, so no cross-compiler needed)
gcc -O2 -static -nostdlib -nostartfiles -DROUNDS=$N -o cpubench-$N cpubench.c

# publish them as a vfsync tree, which is what both emulators mount over 9p
build_filelist <dir-of-binaries> <tree>

node bench/run-x86_64.cjs <base> http://local/x86-bench.cfg 256   # his
node bench/run-riscv.cjs  <base> http://local/rv-bench.cfg   256   # ours
```

Sources in [`nixbox-wasm/bench/`](nixbox-wasm/bench/). Bellard's x86_64 config boots our
own tree with `init=/cpubench-N`, so no userland is needed on his side at all.

**Build the benchmark `temu` without `-DRISCV_PROF_ENABLE`.** The PC profiler adds a
branch to every interpreted instruction; the first version of this measurement had it
compiled in, against a production build of Bellard's, which is not a fair comparison.

**The same `var Module` hoisting trap applies to his glue** as to TinyEMU's, and it is
worse there because his build is not `MODULARIZE`d at all: `var Module = typeof Module !=
"undefined" ? Module : {}` hoists `Module` as a module-local, so `typeof` sees the local
and any injected config is silently dropped. The object `require()` returns *is* the
glue's `Module`, and `createWasm()` is async, so hooks attached immediately after
`require` still land in time. The wasm path is resolved from `scriptDirectory`, so the
file must be named `x86_64emu-wasm.wasm` next to the JS.

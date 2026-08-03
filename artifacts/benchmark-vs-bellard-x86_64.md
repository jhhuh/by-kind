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

**Two caveats, both of which make the payoff look smaller than the table suggests:**

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

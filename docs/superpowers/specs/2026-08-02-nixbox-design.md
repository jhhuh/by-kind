# nixbox — an x86_64 CPU for TinyEMU, and a nix-store filesystem for it

**Status:** design, not yet implemented
**Date:** 2026-08-02
**Repo:** subproject of `cat-nixpkgs` (by-kind), directory `nixbox/`

## Goal

Click a package on https://jhhuh.github.io/by-kind/ and get an overlay terminal
with a real shell and that package on `PATH`, with no server and no boot wait.

## Why this shape

Prior investigation (`artifacts/spike_nixbox/README.md`) proved the nix half
natively: closure walking, NAR parsing, and a real `bash` with `PATH` into a store
assembled from nothing but HTTP. It also established the constraints:

| fact | consequence |
|---|---|
| `cache.nixos.org` sends `access-control-allow-origin: *` | browser JS can fetch NARs directly |
| per-package delta over a cached base is 0.04–2.2 MB | "instant" is achievable |
| riscv64 / armv7l nixpkgs cache coverage is **0%** | those architectures have nothing to run |
| i686 coverage is **8%** | every 32-bit emulator is ruled out |
| x86_64 ~100%, aarch64 95% | only these two are viable targets |
| Blink's Emscripten build has no `fork` | user-mode emulation cannot host a shell |

A real shell therefore requires 64-bit **full-system** emulation. TinyEMU is the
right host for it, for reasons established below.

## What TinyEMU actually is

Verified against `tinyemu-2019-12-21.tar.gz`, not documentation.

- `x86_machine.c` — **2,569 lines, complete.** i440FX/PIIX3, PIC, PIT, RTC, IDE,
  PS/2, VGA, PCI, VirtIO. Descended from QEMU: `ps2.c` and `pckbd.c` still carry
  *"QEMU PS/2 keyboard/mouse emulation, Copyright (c) 2003 Fabrice Bellard"* in
  their headers. Relicensed MIT, which Bellard could do because he wrote them.
- `x86_cpu.c` — **a 2,538-byte stub.** Every function empty; `x86_cpu_init` prints
  `"x86 emulator is not supported"` and exits. x86 runs via KVM or not at all.
- `x86_cpu.h` — a clean 14-function CPU interface, consumed at **29 call sites**.
- `fs_net.c` — **2,910 lines**, the largest file in the tree: *"Networked
  Filesystem using HTTP"*, vfsync-compatible, lazy per-file fetch, `.preload`
  archive batching, already `#if defined(EMSCRIPTEN)`-aware.

**Nobody has ever filled the stub.** Every public fork carries it byte-identical:

```
dearchap/tinyemu           x86_cpu.c 2538 bytes  stub
a3f/TinyEMU                x86_cpu.c 2538 bytes  stub
yoshijava/TinyEMU          x86_cpu.c 2538 bytes  stub
corwin-of-amber/tinyemu    x86_cpu.c 2538 bytes  stub
```

The likely reason is licensing rather than difficulty: every x86 component that
*could* be lifted from QEMU was lifted, and `target/i386` is the one piece that
couldn't, because it has had hundreds of contributors since 2003 and is
irrevocably GPLv2. An MIT tree cannot absorb it.

TEMU and TinyEMU are the same project — the JSLinux page's column header links to
`/tinyemu`, and `Makefile` builds `PROGS+= temu$(EXE)`. So JSLinux's live x86_64
VM *is* this codebase, with a CPU core that has never been published.

## Two independent tracks

Track B does not depend on Track A. `fs_net.c` has no CPU or machine dependency —

```
$ grep -i 'x86|riscv|cpu|machine' fs_net.c
  (nothing)
```

— so the filesystem work compiles and runs against `riscv_machine.c`, which has a
real CPU, while the x86_64 core is still being written. Neither track blocks.

---

# Track A — `x86_cpu.c`

## The decisive scope cut: long mode only

`x86_machine.c` boots a Linux kernel with **no BIOS and no real mode**. Its own
comment says so: `/* map PCI interrupts (no BIOS, so we must do it) */`. Today it
sets `CR0.PE`, loads a flat GDT, and enters the kernel's *32-bit* entry point:

```c
val = x86_cpu_get_reg(s->cpu_state, X86_CPU_REG_CR0);
x86_cpu_set_reg(s->cpu_state, X86_CPU_REG_CR0, val | (1 << 0));   /* PE */
x86_cpu_set_seg(s->cpu_state, X86_CPU_SEG_CS, &sd);               /* flat 32-bit */
x86_cpu_set_reg(s->cpu_state, X86_CPU_REG_EIP, load_address);
x86_cpu_set_reg(s->cpu_state, 6, KERNEL_PARAMS_ADDR);             /* esi */
```

We switch it to the **64-bit boot protocol** instead, which the kernel documents
(`Documentation/arch/x86/boot.rst`):

> the kernel is started by jumping to the 64-bit kernel entry point, which is the
> start address of loaded 64-bit kernel plus 0x200. At entry, the CPU must be in
> 64-bit mode with paging enabled. … a GDT must be loaded with the descriptors for
> selectors `__BOOT_CS(0x10)` and `__BOOT_DS(0x18)`; both descriptors must be 4G
> flat segment … interrupt must be disabled; `%rsi` must hold the base address of
> the struct boot_params.

The *machine* builds an identity-mapped PML4 in guest RAM, sets
`CR4.PAE | EFER.LME | CR0.PG`, loads a flat 64-bit GDT, and enters at
`load_address + 0x200`.

**Therefore the CPU never implements:** real mode, 16-bit protected mode, 32-bit
protected mode, virtual-8086 mode, 32-bit paging, PAE-32 paging, hardware task
switching, or call gates. Long mode only, with one 4-level paging format.

Compatibility mode (32-bit userspace under a 64-bit kernel) is also out of scope:
every `x86_64-linux` binary in nixpkgs is 64-bit.

## Interface v2

The published `x86_cpu.h` cannot express long mode — `uint32_t` register values,
`X86_CPU_REG_EIP`, eight GPRs, 32-bit segment bases. It gets replaced:

| v1 | v2 |
|---|---|
| `uint32_t x86_cpu_get_reg(s, int)` | `uint64_t x86_cpu_get_reg(s, int)` |
| `X86_CPU_REG_EIP` (8) | `X86_CPU_REG_RIP`, 16 GPRs |
| `CR0`, `CR2` | + `CR3`, `CR4`, `EFER`, `MSR` accessors |
| `X86CPUSeg { u16 sel, flags; u32 base, limit; }` | `{ u16 sel, flags; u64 base; u32 limit; }` |

29 call sites in `x86_machine.c` change, of which 12 are one mechanical block
(a 32-bit register shuffle for an interrupt callback) and 6 are the boot path
being rewritten anyway.

## Instruction coverage

Target **x86-64-v1**: base 64-bit integer plus SSE2, which is the mandatory
x86_64 baseline and what nixpkgs builds against. CPUID is ours to control, so we
advertise no AVX/BMI/etc. and glibc's runtime dispatch takes SSE2 paths.

Systems instructions the kernel requires: `syscall`/`sysret`, `swapgs`,
`rdmsr`/`wrmsr` (subset), `cpuid`, `rdtsc`, `cli`/`sti`/`hlt`, `in`/`out`,
`lgdt`/`lidt`/`ltr`, `mov` to/from CR and DR, `invlpg`, `iretq`, `lock` prefix,
`cmpxchg`/`cmpxchg16b`, `xchg`, and the `rep` string operations.

x87 is deferred — glibc's `long double` needs it eventually, but nothing on the
path to a working shell does.

## Correctness strategy

This is the part that decides whether the project succeeds. An x86_64 core with a
subtle flag bug does not fail loudly; it produces a kernel that misbehaves ten
million instructions later. Two mechanisms, both in place before the kernel is
ever booted:

**1. Differential fuzzing against the host CPU.** A native harness takes
(GPRs, RFLAGS, XMM, a memory window, instruction bytes), executes the instruction
for real in a controlled frame, and dumps the resulting state. The same input runs
through our interpreter and every architectural bit is compared. Instruction bytes
are generated from the decoder's own tables, so coverage tracks what we claim to
implement. The host CPU is the most authoritative reference obtainable, and it is
free. Blink (ISC, x86_64, already builds here) serves as a second opinion on
flag-heavy cases.

**2. Trace divergence against QEMU.** QEMU emits a full execution trace
(`-d in_asm,cpu`). We run the identical kernel image and compare traces
instruction by instruction. The first divergence *is* the bug, with its exact
instruction and register state. This converts "the kernel hangs somewhere" — the
failure mode that makes writing a CPU intractable — into a bisect with a precise
answer. It is the single most important tool in the project and it is built in
M1, not when trouble starts.

## Milestones

Each is a verifiable gate, not a checkpoint.

| # | deliverable | verified by |
|---|---|---|
| M0 | interface v2, machine 64-bit entry, CPU skeleton | riscv64 build and tests still green; x86 target links |
| M1 | decoder + integer ALU; **both test harnesses working** | 10⁷ differential-fuzz cases with zero mismatches |
| M2 | 4-level paging, exceptions, interrupts | hand-written long-mode test kernel runs to completion |
| M3 | enough to boot | Linux reaches `earlyprintk=serial` output |
| M4 | userspace | boots to an init shell in a nix-built initramfs |
| M5 | SSE2 + `syscall`/`sysret` | `bash` from the nix store runs over 9p, with pipes and subshells |
| M6 | browser | emscripten build boots in a page |

M3 is the point of highest risk and the point at which trace divergence pays for
itself.

## Performance

An interpreter, not a JIT. TinyEMU's RISC-V core is an interpreter and JSLinux is
usably fast with it. A Wasm JIT is a possible later optimisation and explicitly
not a milestone. If interactive latency proves unacceptable at M6, that is the
moment to revisit — not before.

---

# Track B — the nix store as a vfsync filesystem

## The insight

**A NAR is a `.preload2` archive.** TinyEMU already batches many small files into
a single archive fetched in one HTTP request, because a round-trip per file is
fatal to boot time. Nix store paths are immutable, content-addressed units at
exactly that granularity. The two mechanisms are the same idea, and one is already
implemented.

## Wire format

From `build_filelist.c`, a plain-text manifest served off any static host:

```
Version: 1
Revision: 1
NextFileID: <hex>
FSFileCount: <n>
FSSize: <bytes>
FSMaxSize: <bytes>
Key:
RootID: <hex>
```

followed by a recursive directory listing — `%06o uid gid [size] [mtime] name
[file_id]` per entry, `.` closing each directory. Regular files are fetched
individually as flat blobs named by `file_id` under the base URL
(`file_id_to_filename()` → `compose_path(base_url, fname)`).

## Design

- Generate the manifest from `narinfo` metadata plus NAR listings. Closure walking
  and NAR parsing are already proven in `artifacts/spike_nixbox/fetchstore.py`.
- On first touch of any file in a store path, fetch that path's `.nar.zst` once,
  decompress, and populate every file it contains — lazy at store-path
  granularity, which is the natural unit.
- Root filesystem is 9p with no disk image, exactly as JSLinux does it:
  ```
  cmdline: "... root=root rootfstype=9p rootflags=trans=virtio,msize=8192 ro"
  fs0: { file: "https://<host>/<manifest>" }
  ```
- Cache in OPFS so the shared base closure is fetched once, ever.

## Testing without x86_64

`fs_net.c` is CPU-independent, so this track is developed and validated against
**TinyEMU riscv64**, which has a real CPU today, using Bellard's own `fs_net.c` as
the live reference implementation. The 0% riscv64 nixpkgs cache coverage prevents
*shipping* nix packages there; it does not prevent validating the manifest
generator, the archive batching, or the lazy-fetch path.

---

# Repository layout

```
nixbox/
  README.md
  vendor/tinyemu/        upstream 2019-12-21 + our patches
  src/x86_cpu/           decode.c  interp.c  mmu.c  sse.c  cpu.h
  src/vfs/               manifest generator (Python)
  tests/difftest/        differential fuzzer + native reference harness
  tests/trace/           QEMU trace comparison
```

The subproject shares by-kind's flake and CI.

# Non-goals

- SMP. Single CPU.
- Graphics. Serial console only.
- Guest networking. Deferred; nothing on the path to a shell needs it.
- x87, AVX, and everything above the x86-64-v1 baseline.
- 32-bit compatibility mode.
- A JIT.

# Risks

| risk | mitigation |
|---|---|
| Silent miscompilation surfaces only as kernel misbehaviour | trace divergence against QEMU, built at M1 |
| Scope creep into 32-bit/real mode | 64-bit boot protocol makes it structurally unnecessary |
| Emscripten build flags in `Makefile.js` are from 2018 | port at M6; the Blink build proved emscripten works here |
| Interpreter too slow to be interactive | measure at M6; JIT is a known escape hatch |
| The project is long and by-kind should not wait | Track B ships independently and is not blocked |

# Licensing

TinyEMU is MIT. Our `x86_cpu.c` must be written from scratch — **no code may be
taken from QEMU's `target/i386`**, which is GPLv2 and cannot be relicensed. Intel
SDM and AMD APM are the specifications; QEMU may be used as a *reference oracle*
for behaviour (running it and comparing traces) but never as a source of code.

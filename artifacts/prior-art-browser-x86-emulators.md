# Prior art: x86 / x86_64 emulation in the browser

Survey as of 2026-08-02. Companion note: [`js-wasm-boundary-patterns.md`](js-wasm-boundary-patterns.md)
covers *how* each of these wires JavaScript to WebAssembly.

Everything below was checked against source or a live artifact. Where a claim is
inference rather than measurement it says so.

## The constraint that shapes all of them

WebAssembly is W^X by construction: linear memory is never executable and the code
section is immutable after instantiation. A classic JIT — emit native code into a
buffer, jump to it — is impossible. Kohei Tokunaga states it directly in the FOSDEM
2025 slides (slide 12): *"Wasm can't execute code generated on memory."*

The only runtime codegen available is `new WebAssembly.Module(bytes)` followed by
`new WebAssembly.Instance(...)`. Every JIT-capable emulator here converges on that
same workaround, independently.

## The landscape

| project | guest | engine | license | state | 64-bit |
|---|---|---|---|---|---|
| **qemu-wasm** (ktock) | x86_64, aarch64, riscv64 | QEMU TCG → Wasm modules, MTTCG | GPLv2 | active, FOSDEM 2025 | **yes** |
| **TinyEMU / TEMU** (Bellard) | x86, x86_64, riscv | own interpreter | MIT source, **binary ungranted** | src frozen 2019-12-21, site live | **yes**, unreleased |
| **v86** (copy) | x86 only | Rust CPU → Wasm modules | BSD-2 | active | **no** |
| **container2wasm** (ktock) | x86_64, riscv64 | wraps Bochs or qemu-wasm | Apache-2.0 | active | yes (via backends) |
| **Bochs c2w-wasm** (ktock fork) | x86_64 | pure interpreter | LGPL-2.1 | 2024-08-27 | yes |
| **CheerpX / WebVM** | x86 | proprietary engine | WebVM shell Apache-2.0, **engine closed** | very active (17k stars) | no |
| **Qemu.js** | 32-bit only | QEMU TCG → Wasm | NOASSERTION | **dead**, last push 2019-05-12 | no |
| **Blink** (jart) | x86_64 user-mode | interpreter | ISC | active | yes, but user-mode |

### Why most of these are out

- **32-bit guests are unusable for nixpkgs.** Measured: i686 binary-cache coverage is
  ~8% on a 45-package sample, and the three hits were all libraries pulled in as
  dependencies. That eliminates v86, CheerpX, and Qemu.js on availability of
  *binaries*, regardless of their technical merit. v86's own Readme is unambiguous:
  *"Linux works pretty well. 64-bit kernels are not supported."*
- **User-mode emulation cannot host a shell.** Blink's Emscripten build has no
  `fork` — `configure` fails the `HAVE_FORK` probe, and the syscall table registers
  `fork`/`vfork`/`wait4`/`kill` only under `#ifdef HAVE_FORK`. Verified by building
  it: the generated `config.h` contains `DISABLE_THREADS` and `DISABLE_VFS` and no
  `HAVE_FORK` at all.
- **Qemu.js is dead** (2019) and never supported 64-bit guests.
- **CheerpX's engine is proprietary.** The 17k-star WebVM repo is the shell around it.

### The two that remain

**qemu-wasm** and **TinyEMU**. They are the only projects that combine a 64-bit guest
with a real kernel and a real shell.

## TinyEMU: the CPU is not in the box

The published tarball (`tinyemu-2019-12-21.tar.gz`) contains a complete x86 PC —
`x86_machine.c` is 2,569 lines of i440FX/PIIX3, PIC, PIT, RTC, IDE, PS/2, VGA, PCI and
VirtIO — and a 14-function CPU interface in `x86_cpu.h`, consumed at 29 call sites.

What it does not contain is a CPU:

```c
/*
 * x86 CPU emulator stub
 */
X86CPUState *x86_cpu_init(PhysMemoryMap *mem_map)
{
    fprintf(stderr, "x86 emulator is not supported\n");
    exit(1);
}
```

All 14 functions are empty. x86 runs under KVM or not at all. And nobody has ever
filled it — every public fork carries the identical 2,538-byte stub:

```
dearchap/tinyemu           x86_cpu.c 2538 bytes  stub
a3f/TinyEMU                x86_cpu.c 2538 bytes  stub
yoshijava/TinyEMU          x86_cpu.c 2538 bytes  stub
corwin-of-amber/tinyemu    x86_cpu.c 2538 bytes  stub
```

**The likely reason is licensing, not difficulty.** Every x86 component that could be
lifted from QEMU was lifted — `ps2.c` and `pckbd.c` still carry *"QEMU PS/2
keyboard/mouse emulation, Copyright (c) 2003 Fabrice Bellard"* in their headers, and
the 2003 dates are QEMU's first release year. `target/i386` is the one piece that
could not follow, because it has had hundreds of contributors since 2003 and is
irrevocably GPLv2, which an MIT tree cannot absorb.

Also note the published interface is structurally 32-bit — `uint32_t` register values,
`X86_CPU_REG_EIP`, eight GPRs, 32-bit segment bases. There is nowhere to put long mode.

### Confirmed by building it: no x86 guest at either bitness

The obvious follow-up is whether the *32-bit* emulator — the one Bellard does ship as a
compiled `x86emu-wasm.wasm` — can be rebuilt from source. It cannot. Built from the
released tarball and run against the demo's own configs:

```console
$ make CONFIG_FS_NET= CONFIG_SDL=          # compiles and links cleanly,
                                           # including x86_machine.o ide.o ps2.o vga.o

$ ./temu root-x86.cfg
KVM not available
x86 emulator is not supported              # <- x86_cpu_init stub, exit(1)

$ ./temu root-riscv64.cfg                  # control: same binary
[    0.164421] NET: Registered protocol family 17
[    0.164656] 9pnet: Installing 9P2000 support     # boots Linux
```

The riscv64 control rules out a broken build. `CONFIG_X86EMU=y` compiles fine; the
failure is purely the runtime stub.

The emscripten build system does not even attempt x86:

```make
PROGS=js/riscvemu32.js js/riscvemu32-wasm.js js/riscvemu64.js js/riscvemu64-wasm.js
```

So **`x86emu-wasm.wasm` (369 KB, 32-bit) and `x86_64emu-wasm.wasm` (1.5 MB) were both
built from a private tree.** The x86 CPU has never been released in any form except as
compiled binaries, at either bitness. What the public source provides is everything
around it: the complete PC machine, IDE, PS/2, VGA, PCI, VirtIO, and the vfsync
network filesystem.

### TEMU is TinyEMU

Same project; `temu` is the binary name (`Makefile`: `PROGS+= temu$(EXE)`), and the
JSLinux page's "TEMU" column links to `/tinyemu`. So the live x86_64 JSLinux runs this
codebase with a CPU core that has never been published as source.

### What *is* distributed

`jslinux-2019-12-21.tar.gz` ships a compiled **32-bit** x86 emulator —
`x86emu-wasm.wasm`, 369 KB — plus a 32-bit Linux 4.12 bzImage (`xloadflags = 0x0`, so
no `XLF_KERNEL_64` entry point). The 64-bit build, `x86_64emu-wasm.wasm` (1,545,305
bytes), exists only as a file served from bellard.org and is in no tarball.

Two independent blockers on using it:

```
$ curl -sSI https://bellard.org/jslinux/x86_64emu-wasm.wasm
Server: Apache
Content-Type: application/wasm
Content-Length: 1545305
          ← no Access-Control-Allow-Origin at all
```

- **CORS.** Cannot be fetched from another origin. `vfsync.org` is likewise pinned to
  `Access-Control-Allow-Origin: https://bellard.org`. The whole setup is deliberately
  same-origin.
- **Licence.** `MIT-LICENSE.txt` covers the source tree. The demo tarball's
  `readme.txt` grants nothing and `index.html` carries a bare
  `© 2017-2019 Fabrice Bellard`.

Asking Bellard for redistribution permission is the only route, and would be cheap to
try. Everything technical about using it is otherwise straightforward — see the
companion note.

## qemu-wasm: what the FOSDEM 2025 talk establishes

[Slides](https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6290-running-qemu-inside-browser/slides/238760/slides_1dDtpcS.pdf),
Kohei Tokunaga, NTT. The talk is effectively the design document for the
[`qemu-wasm-demo`](https://github.com/ktock/qemu-wasm-demo) repo.

- **Slide 6** — x86_64, AArch64, RISCV64 guests; TCG JIT, mount and networking.
- **Slide 12** — the W^X problem and its workaround: `WebAssembly.Module` compiles,
  `WebAssembly.Instance` makes it executable. Emscripten pthreads enable MTTCG.
- **Slide 13** — each Translation Block becomes one Wasm module. *64-bit IR
  instructions were added to TCG* to enable 64-bit guests and MTTCG. QEMU's memory and
  helper functions are imported into the TB module.
- **Slide 14** — TBs run on the TCI interpreter by default; only blocks executed many
  times (~1500) are compiled to Wasm. **This is why first boot is slow.**
- **Slide 16** — pigz on an emulated x86_64 guest, Chrome 130, i7-10510U:
  Bochs ~40, QEMU single-threaded ~13, **QEMU 4-thread MTTCG ~7.5**. So qemu-wasm is
  ~5× Bochs, and MTTCG is ~1.7× single-threaded.
- **Slide 24** — c2w sits *above* qemu-wasm: *"container2wasm is a converter of a
  container to a Wasm blob. Provides `--to-js` flag for enabling QEMU Wasm (>= v0.8)."*
- **Slide 26, future work** — *"Accessing package repos (e.g. apk, apt, …) and
  container registries from browser (w/ CORS restriction)."* Our use case, listed as
  not done by the author.

Slide 13 is the one that matters for build-vs-buy: adding 64-bit IR to TCG so that a
browser can host a 64-bit guest is the hard part of this whole problem space, and it
is already done and upstreamable.

## Two project families, three layers

The stacks are parallel, but ktock's has a rung Bellard's does not:

```
                  Bellard                        ktock
  ─────────────────────────────────────────────────────────────────────
  web app         jslinux                        qemu-wasm-demo
                  jslinux.js, term.js,           container2wasm-demo
                  *.cfg, index.html              index.html, worker.js, module.js

  packaging       build_filelist, splitimg       container2wasm (c2w)
                   — filesystem prep only         — builds the whole artifact

  emulator        TinyEMU (binary: temu)         qemu-wasm
                  MIT; x86_cpu.c is a stub       GPLv2; QEMU fork, Wasm TCG backend
```

c2w has no Bellard counterpart: it is a build tool that takes a container image and
emits a Wasm blob with kernel, rootfs and emulator baked in. Because it sits above the
emulator it can pick a backend — Bochs by default, qemu-wasm with `--to-js`. Bellard
has one emulator and no such choice.

## Architecture: where the machine lives

The deepest split in this landscape is not language or licence, it is **how much of
the machine is JavaScript**.

| | CPU | devices | filesystem |
|---|---|---|---|
| TinyEMU | wasm | wasm | wasm (`fs_net.c` fetches over HTTP itself) |
| qemu-wasm | wasm | wasm | wasm, bridged to JS via emscripten `FS` + `-virtfs` |
| **v86** | **wasm** | **JavaScript** | **JavaScript** |

v86 is the outlier and it is the interesting one. `src/rust/` (34,710 lines) is the CPU
— JIT, paging, modrm, regs — and everything else is JS: `acpi.js`, `ide.js`, `pci.js`,
`virtio.js`, `vga.js`, `ne2k.js`, `pit.js`, `rtc.js`, `ps2.js`, `uart.js`, plus
`lib/9p.js` (1,170 lines) and `lib/filesystem.js` (1,969 lines).

That is exactly the "factor the CPU out from system emulation" shape, already built.
It also has the best-documented customization surface of anything here — an 892-line
`v86.d.ts` including a first-class custom-filesystem hook:

```ts
/**
 * A function that will be called for each 9p request.
 * If specified, this will back Virtio9p instead of a filesystem.
 * Use this to build or connect to a custom 9p server.
 */
handle9p?: (reqbuf: Uint8Array, reply: (replybuf: Uint8Array) => void) => void;
```

and its `FS` methods are `async` (`FS.prototype.Read = async function`), so lazy
network-backed files need no `Atomics.wait` gymnastics.

**And it is 32-bit, so none of it is usable for nixpkgs.** This is the sharpest tension
in the landscape: the project with by far the best customization story is on the wrong
side of the one hard constraint.

## Cache coverage, which decides architecture

Measured against `cache.nixos.org` on a 45-package sample drawn from `by-name`:

| system | evaluates | cached | coverage |
|---|---:|---:|---:|
| x86_64-linux | — | — | ~100% (reference) |
| aarch64-linux | 43 | 41 | **95%** |
| i686-linux | 37 | 3 | 8% |
| riscv64-linux | 40 | **0** | **0%** |
| armv7l-linux | 40 | 0 | 0% |

The riscv64 trap is worth naming: every package *evaluates* for riscv64-linux, so it
looks supported, but Hydra does not build it. "nixpkgs supports riscv64" and "riscv64
binaries exist" are different claims and only the first is true.

Consequence: **x86_64 and aarch64 are the only viable targets**, and qemu-wasm is the
only open project that reaches either with a real kernel and shell.

## Current status of the local experiment

Running qemu-wasm's prebuilt x86_64 artifact under headless Chromium with our own
`module.js` and page:

- Boots Alpine 3.21 to a login prompt, ~30s guest time, hostname from our own `-append`
- `crossOriginIsolated === true` with COOP/COEP served locally
- `Module.FS.readFile("/share/hello.txt")` returns what our `preRun` wrote — the JS
  half of the `-virtfs` seam works
- **Not verified:** the guest reading that share. Input was wired to the wrong end of
  the pty (`slave.write` paints the screen; input must go via `xterm.input`). Fix
  written, not yet run.
- **Not tried:** MTTCG (`thread=multi`, `-smp 4`), our own kernel/initramfs, rebuilding
  qemu-wasm from source.

Artifact size, measured: `qemu-system-x86_64.wasm` ships at 40,799,480 bytes of which
**69.6% is debug info** (`-O3 -g` in `create-images.sh`). Stripping `.debug_*`, `name`
and `llvm.*` custom sections gives 12,390,476 bytes / 3,181,907 gzipped, and both
versions compile to identical module shapes (112 exports, 119 imports).

## How much effort is a 64-bit CPU we control?

### The original 2011 JSLinux, for calibration

Bellard's first JSLinux (2011) was written entirely in JavaScript. The files survive in
the [Wayback Machine](https://web.archive.org/web/2011/http://bellard.org/jslinux/) —
`cpux86.js`, `cpux86-ta.js` (typed arrays), later `cpux86-std.js`.

**We cannot use it.** The header is explicit, and stronger than the unclear terms on the
wasm binaries:

```js
/*
   PC Emulator
   Copyright (c) 2011 Fabrice Bellard

   Redistribution or commercial use is prohibited without the author's permission.
*/
```

But as a size calibration it is valuable. Beautified, `cpux86-ta.js` is **7,045 lines /
192 functions**, and it implements TLB, CPL, segment registers, CR0–CR4, GDT and IDT —
a real protected-mode 486 with paging, enough to boot Linux 2.6.20.

```
Bellard 2011 JSLinux      7,045 lines JS     32-bit 486, paging, interpreter
v86                      34,710 lines Rust   32-bit, more complete, + JIT
Bochs x86-64-v1 subset   ~92,000 lines C++   x86_64, SSE, MSRs, APIC, complete
```

So the *floor* for "an x86 CPU that boots Linux" is single-digit thousands of lines.
Bochs is large because it is complete and modern, not because the task is inherently
that big.

(`linuxstart-*.tar.gz`, the guest-side bootstrap — `linuxstart.c`, `libc.c`,
`linuxstart_head.S`, a 2.6.20 config and patch — was published separately and is a
different artifact from the emulator.)

### The maintainer's own estimate

v86's [issue #648, "Tips for implementing x86_64 support"](https://github.com/copy/v86/issues/648),
answered by `copy` (the maintainer) on 2022-05-06:

> **Heads-up: This is probably a 3-6 month project (full-time).**
>
> I'd suggest starting with disabling the jit, because that's a complex project by itself.
>
> - Add support for switching to long mode
> - Extend the instruction decoder generator (`gen/generate_interpreter.js`), note that
>   some regular instructions (e.g. the brilliant single-byte `inc esp` instruction)
>   become prefixes
> - Add support for 64-bit paging (see PR #599 for an example of adding a new paging
>   mode) — probably requires adding support for NX pages, i.e. memory access from eip
>   need an extra check
> - Make register 64 bits wide (existing instructions might need special care; for
>   example, `mov eax, ebx` clears the upper bits of `rax`)
> - Make 64-bit variants of all instructions

[Issue #133, "x86_64 support"](https://github.com/copy/v86/issues/133), has been open
since 2017: *"It would be nice to have, but it's a huge project and I won't find enough
time any time soon. I'll leave this open, if someone wants to work on this I can help
you get started."*

This is the **optimistic** figure: a maintainer estimating work on his own codebase,
which already has a decoder generator, a paging abstraction with a precedent PR, and a
complete 32-bit protected-mode implementation to extend. Starting from TinyEMU's empty
stub would be worse.

### Summary of routes

| route to a 64-bit CPU we control | effort | basis |
|---|---|---|
| extend **v86** to x86_64 | **3–6 months full-time** | maintainer, v86 #648 |
| graft **Bochs** behind `x86_cpu.h` | 2–6 weeks | estimate; impedance is the risk |
| write `x86_cpu_interp` from scratch | ≥ 6 months | inference; 2011 JSLinux was 7,045 lines for a *486* |
| use **qemu-wasm** as-is | working today | measured |

On the Bochs graft: it is the only route measured in weeks, and it buys the *slowest*
emulator (FOSDEM slide 16: Bochs ~40 vs QEMU MTTCG ~7.5). It also buys little, because
**Bochs-in-wasm running x86_64 Linux already exists** — it is container2wasm's default
x86_64 backend, which is why ktock forked and ported it. The graft would swap Bochs'
own machine for TinyEMU's machine plus `fs_net.c`, and nothing else.

QEMU is the wrong donor for an interpreter: its x86 execution *is* TCG
(`translate.c` emits IR, it does not execute), so reusing it means bringing TCG, the
translation-block cache and the softmmu. Its helpers (`seg_helper.c` 2,534,
`fpu_helper.c` 3,316, `ops_sse.h` 2,672) are liftable in principle but are written
against `CPUX86State` and TCG conventions, and the decoder and dispatch loop — the bulk
— would still have to be written. QEMU's one standalone interpreter,
`target/i386/emulate/x86_{decode,emu}.c` (2,192 + 1,420 lines, used by the macOS HVF
accelerator), is deliberately partial: only the instructions a hypervisor traps on.

## Open questions

1. Can qemu-wasm be rebuilt from source? `create-images.sh` pins **emsdk 3.1.50** with
   a `# TODO: support recent version` note and builds through Docker. Without this,
   customization is limited to argv, guest images and the JS filesystem — real, but
   not unlimited.
2. Does the guest actually see the `-virtfs` share, with modes preserved?
3. How much does MTTCG help in practice? Slide 16 predicts ~1.7×.
4. Would Bellard grant redistribution of `x86_64emu-wasm.wasm`? Cheap to ask; a yes
   changes the size and complexity picture substantially.
5. Is there a path to v86's customization model at 64 bits — i.e. a JS-device /
   wasm-CPU split with a 64-bit core? No project does this today.

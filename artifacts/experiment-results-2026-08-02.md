# Experiment results, 2026-08-02

Everything below was run, not reasoned. Commands and raw output are quoted. Where a
number is an inference rather than a measurement it says so.

Two questions were open at the start:

1. Does the **nested design** work — TinyEMU riscv64 hosting `qemu-x86_64` to run x86_64
   binaries — and what does the second translation layer cost?
2. Can **TinyEMU be built to wasm** from the released source, and how fast does it boot?

Both are answered. Both came back better than expected.

---

## 1. The nested design works, and costs ~4×

### Setup

The Alpine riscv64 `qemu-x86_64` (static-PIE, 3.76 MB) and a static x86_64 busybox were
put in a host directory shared into the guest over virtio-9p:

```
fs0: { file: "…/share" }        # TinyEMU config
# guest:
mount -t 9p /dev/root /mnt
```

The 9p mount preserved exec bits:

```
/ # ls -l /mnt
-rwxr-xr-x    1 default  default    1038696 busybox        (x86_64, static-pie)
-rwxr-xr-x    1 default  default    3759784 qemu-x86_64    (riscv64, static-pie)
```

### It runs

```
/ # /mnt/qemu-x86_64 --version
qemu-x86_64 version 11.0.3
Copyright (c) 2003-2026 Fabrice Bellard and the QEMU Project developers
```

That is an x86_64 emulator, compiled for riscv64, running inside a riscv64 Linux which
is itself being emulated on an x86_64 host. Three levels.

### The cost

Same workload, `md5sum` of a 16 MB file in a tmpfs, inside the guest:

```
qemu-user startup only  (busybox true)        0.22 s
16 MB md5sum, native riscv64                  1.21 s
16 MB md5sum, x86_64 via qemu-user            5.32 s
```

Both produced the same digest (`2c7ab85a…`), so it is correct, not just fast.

**Steady-state translation cost: (5.32 − 0.22) / 1.21 = 4.2×**, plus a fixed ~0.22 s per
process launch.

Against the x86_64 host running the identical workload natively (0.025 s, three runs
within 0.5 ms):

| layer | time | vs native |
|---|---:|---:|
| host x86_64, native | 0.025 s | 1× |
| TinyEMU riscv64 interpretation | 1.21 s | **48×** |
| + qemu-user x86_64 on top | 5.32 s | **213×** |

So the emulator is the expensive layer; qemu-user adds comparatively little.

### Notes

- `binfmt_misc` is **not** in the 4.15 guest kernel (`/proc/sys/fs/binfmt_misc` absent).
  Expected — it is a config option we would enable in our own kernel build. Not needed
  for the measurement, since invoking `qemu-x86_64` explicitly tests the same path.
- `qemu-x86_64` came from `alpine/edge/community/riscv64`, 1.54 MB compressed, and ships
  its own binfmt registration file.

---

## 2. TinyEMU builds to wasm from the released source, and boots in 574 ms

### The build

`Makefile.js` from 2018 compiles cleanly under **emscripten 4.0.10**. Every object file
built without modification; only three link flags had been retired since:

```
--memory-init-file            removed
BINARYEN_TRAP_MODE=clamp      removed
EXTRA_EXPORTED_RUNTIME_METHODS  renamed to EXPORTED_RUNTIME_METHODS
```

With those dropped it links:

```
js/temu.wasm     159,193 bytes      (65,216 gzipped)
js/temu.js        29,696 bytes       (9,598 gzipped)
```

**155 KB** — smaller than Bellard's shipped `riscvemu64-wasm.wasm` (220 KB), because this
builds riscv64 only.

### Running it under node, no browser

`node-run.cjs` supplies what the 2018 code expects and modern emscripten no longer has:

- `term` (`write`/`getSize`), `document` stub, `btoa`
- **`Pointer_stringify`, `Runtime.dynCall`, `Browser.{wgetRequests,fbuf_table}`** — retired
  emscripten APIs still referenced by `js/lib.js` and by emscripten's own
  `emscripten_async_wget3_data`
- an `XMLHttpRequest` backed by `fs`, mapping every URL to a file under a base directory

Four things had to be right, each found by running it:

1. Build with `-sMODULARIZE=1`. Without it the glue's `var Module` hoists and shadows any
   global you set, so injected config is ignored.
2. `vm_start` in the 2019 source takes **7** arguments, not the 8 that today's
   `jslinux.js` passes — `drive_url` was added later.
3. The config path **must be a URL**. Under Emscripten `load_file()` is literally
   `abort()` (`machine.c:452`); only the `is_url()` branch works.
4. The disk must be in **split** format (`root-riscv64/blk.txt` + parts), which is what
   `splitimg` produces and why the demo ships it that way. A plain `.bin` fails.
5. Pass width/height `0` unless you supply a framebuffer, or `fb_refresh` dereferences
   a null `graphic_display`.

### Boot timings

```
===== timings, ms from process start =====
  runtime_ready         2.8      wasm instantiated
  vm_start_returned     3.9
  first_output        573.8
  shell_prompt        573.9      live shell, PID 1
  typed_at          12001.0      (deliberate delay)
  echo_marker       12004.3      3.3 ms to echo back
```

Across six runs, boot to prompt was **558–584 ms**. Round-trip on a typed command once
running: **3.3 ms**.

### A correction to an earlier claim

Natively I measured a 97 ms gap before `console [hvc0] enabled` and attributed 55% of
boot to console logging. In wasm, quiet (`loglevel=3`) and verbose kernels boot in the
same time — 558–584 ms either way. So that 97 ms was **host terminal I/O through the pty**
under `script`, not emulation work. In wasm the console sink is a string append and costs
nothing measurable.

### What a cold browser load actually fetches

```
temu.wasm             159,193 raw    65,216 gz
temu.js                29,696 raw     9,598 gz
bbl64.bin              53,786 raw    10,085 gz
kernel-riscv64.bin  3,979,556 raw 1,912,216 gz
                                  ──────────
                                    ~2.0 MB gzipped
```

The root filesystem is **not** in that total — it is 16 block parts fetched on demand.

For comparison, the qemu-wasm demo fetches ~119 MB before the guest starts and took ~30 s
to reach a login prompt.

**~60× smaller, ~50× faster to a prompt.**

---

## 3. Guest footprint, with the shell as PID 1

`init=/bin/sh`, no init system, no daemons:

```
$ free
             total         used         free       shared      buffers
Mem:         57236         5712        51524            0          148

$ ps | wc -l
1
```

Kernel accounting immediately before PID 1:

```
Memory: 57152K/129024K available (2206K kernel code, 188K rwdata, 627K rodata,
                                  84K init, 769K bss, 71872K reserved, 0K cma-reserved)
```

So ~3.8 MB of kernel image plus page tables and buffer cache, and 5.7 MB total once the
shell is up. Guest kernel is **Linux 4.15.0**, which predates RISC-V alternatives patching
and jump labels — so kernel text is genuinely unmodified after load on this build.

---

## What this settles

**The snapshot branch closes.** It existed because I measured 30 s to a prompt on
qemu-wasm's x86_64 with TCI warm-up. TinyEMU riscv64 in wasm reaches a live shell in
574 ms. There is nothing worth snapshotting, and the lazy-RAM question never arises.

**The nested design is viable on performance.** 4.2× for the qemu-user layer, on top of
48× for the emulator. Whether 213×-of-native is acceptable depends on the workload, but
it is a real number rather than a fear, and the interactive path — the shell itself — is
native riscv64 and responds in 3.3 ms.

**TinyEMU riscv64 is fully ours.** It builds from MIT source with a current toolchain,
which means `fs_net.c` is ours to modify, and the snapshot work, if ever wanted, is
available rather than blocked.

## Still untested

- The nested stack **inside wasm**. Both halves work separately; they have not been run
  together. The blocker is mechanical: the emscripten build's 9p uses `fs_net.c`'s vfsync
  format, not a local directory, so `qemu-x86_64` and the payload need to go into a
  generated vfsync tree (via `build_filelist`) or into the disk image.
- Boot time in an actual browser rather than node. Node uses the same V8, so I would
  expect it to be close, but that is an inference.
- `binfmt_misc`, which needs a kernel we build.

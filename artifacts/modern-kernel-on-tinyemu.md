# Running a modern Linux kernel on TinyEMU

Goal: a riscv64 kernel with `CONFIG_BINFMT_MISC=y`, so x86_64 binaries execute
transparently instead of needing an explicit `qemu-x86_64` prefix.

Result: **done.** An x86_64 binary now executes transparently inside the emulated
riscv64 guest, with no interpreter prefix and `uname -m` reporting `x86_64`:

```
/ # sh /mnt/setup-binfmt.sh
REGISTER_OK
/ # echo NATIVE_OK; /mnt/busybox echo TRANSPARENT_X86_64_OK
NATIVE_OK
TRANSPARENT_X86_64_OK
/ # /mnt/busybox uname -m
x86_64
```

Native riscv64 and emulated x86_64 coexist on one shell with the kernel choosing the
interpreter silently. Getting there needed **two fixes to TinyEMU**, both included here.

Companion notes: [`experiment-results-2026-08-02.md`](experiment-results-2026-08-02.md),
[`prior-art-browser-x86-emulators.md`](prior-art-browser-x86-emulators.md).

## Building the kernel

nixpkgs cross-compiles it, and the toolchain is cached — only the kernel itself builds:

```
these 4 derivations will be built:
these 23 paths will be fetched (259.6 MiB download)
```

The expression is [`nixbox-wasm/riscv64-kernel.nix`](nixbox-wasm/riscv64-kernel.nix).
Verified in the generated config:

```
CONFIG_BINFMT_MISC=y
CONFIG_RISCV_SBI_V01=y
CONFIG_NET_9P=y      CONFIG_NET_9P_VIRTIO=y
CONFIG_VIRTIO_MMIO=y CONFIG_VIRTIO_BLK=y  CONFIG_VIRTIO_CONSOLE=y
```

Two notes on the build:

- **`RISCV_SBI_V01` is required.** Bellard's `bbl64.bin` speaks legacy SBI v0.1. Without
  this the kernel never reaches a console. (I initially claimed Linux had *removed* this
  option — that was wrong; a `curl | grep` had failed silently and I did not check. It is
  present in 6.12 and 6.19 as a plain selectable bool.)
- **The resulting `Image` is 44.6 MB**, against Bellard's 3.98 MB. That is nixpkgs'
  NixOS-flavoured config with everything built in. For a browser artifact whose entire
  current payload is ~2 MB gzipped this is disqualifying, and a minimal config is separate
  work that has not been done.

## The bug in TinyEMU, and the fix

With the kernel built, the guest panicked immediately:

```
[    0.000000] kernel BUG at arch/riscv/kernel/smpboot.c:151!
[    0.000000] Kernel panic - not syncing: Fatal exception in interrupt
```

That line is `BUG_ON(!found_boot_cpu)` in `of_parse_and_init_cpus()`. The kernel walks
each CPU node calling `riscv_early_of_processor_hartid()`, which contains:

```c
if (IS_ENABLED(CONFIG_64BIT) && strncasecmp(isa, "rv64ima", 7))
    return -ENODEV;
```

So `riscv,isa` **must literally begin `rv64ima`**. TinyEMU builds that string by walking
MISA bits alphabetically:

```c
for(i = 0; i < 26; i++) {
    if (misa & (1 << i))
        *q++ = 'a' + i;
}
```

which produces `rv64acdfim…` rather than the canonical `rv64imafdc…`. Every CPU node is
rejected, `found_boot_cpu` stays false, and the kernel BUGs before reaching userspace.

The fix emits the canonical ISA order and is in
[`nixbox-wasm/tinyemu-canonical-isa-string.patch`](nixbox-wasm/tinyemu-canonical-isa-string.patch):

```c
static const char canonical[] = "imafdqlcbjtpvn";
for (cp = canonical; *cp; cp++)
    if (misa & (1 << (*cp - 'a'))) *q++ = *cp;
for (i = 0; i < 26; i++)
    if ((misa & (1 << i)) && !strchr(canonical, 'a' + i)) *q++ = 'a' + i;
```

This is presumably why nobody runs modern kernels on TinyEMU: it fails at the first
instruction of `start_kernel` with a message that points at SMP rather than at the DT.

## With the patch: it boots

```
[    0.000000] Linux version 6.12.77 (nixbld@localhost) … #1-NixOS SMP
[    0.000000] SBI specification v0.1 detected
[    0.000000] riscv: providing IPIs using SBI IPI extension
[    1.624190] 9p: Installing v9fs 9p2000 file system support
[    2.869158] virtio_blk virtio1: [vda] 8192 512-byte logical blocks
[    2.964140] 9pnet: Installing 9P2000 support
[    3.465649] VFS: Mounted root (ext4 filesystem) on device 254:0.
[    3.553997] Run /bin/sh as init process
```

## binfmt_misc works

```
/ # ls -d /proc/sys/fs/binfmt_misc && echo BINFMT_YES
/proc/sys/fs/binfmt_misc
BINFMT_YES
/ # mount -t binfmt_misc none /proc/sys/fs/binfmt_misc && echo BINFMT_MOUNTED
BINFMT_MOUNTED
/ # ls /proc/sys/fs/binfmt_misc
register  status
```

and registration of the x86_64 handler succeeds (`REGISTER_OK`), using Alpine's magic and
mask with the `F` flag.

**A practical trap:** busybox's `printf` does not support `\xNN` escapes, so writing the
registration line from inside the guest silently produces garbage magic that matches
*every* ELF — including the interpreter — and every exec then fails with `ELOOP`
("Too many levels of symbolic links"), including native riscv64 binaries. Generate the
registration bytes outside the guest and `cat` the file into `register`.

## Two remaining incompatibilities

### 9p zero-copy, worked around

With the default `msize`, `ls /mnt` splats:

```
virtqueue_add_split+0x366/0x67e
virtqueue_add_sgs+0xaa/0xac
p9_virtio_zc_request+0x210/0x774
p9_client_readdir+0x17a/0x216
v9fs_dir_readdir_dotl+0x128/0x17e
```

6.12's v9fs uses a zero-copy request path TinyEMU's 2019 virtio-9p cannot satisfy.
Mounting with `-o trans=virtio,version=9p2000.L,msize=8192` — the value Bellard's own
configs use — avoids it, and the filesystem then works.

### The second TinyEMU bug: the `time` CSR does not exist

With 9p mounted, `qemu-x86_64` was found and executed, then died:

```
status: 0000000200004003  badaddr: 00000000c01027f3  cause: 0000000000000002
Illegal instruction
```

`cause 2` is illegal instruction, and the opcode decodes as `csrrs` from CSR **`0xc01`,
the `time` counter**. TinyEMU never implemented it — deliberately, and Bellard left the
reason in a comment:

```c
/* the 'time' counter is usually emulated */
if (csr != 0xc01 && csr != 0xc81) {
```

He expected M-mode firmware to trap and emulate `rdtime`. Real bbl does, which is why the
4.15 guest worked. But 6.12's vDSO reads `rdtime` from **U-mode**, where the trap is
delivered as SIGILL instead.

There were two defects, not one:

```c
/* cycle and insn counters */
#define COUNTEREN_MASK ((1 << 0) | (1 << 2))     /* CY | IR — TM (bit 1) missing */
```

so even a kernel that sets `scounteren.TM` had the bit masked away; and there was no
`case 0xc01` in `csr_read` at all.

The fix in [`nixbox-wasm/tinyemu-time-csr.patch`](nixbox-wasm/tinyemu-time-csr.patch)
adds TM to `COUNTEREN_MASK`, implements CSR `0xc01`/`0xc81` with the standard
`scounteren`/`mcounteren` permission checks, and extends the CPU class vtable with a
`set_get_time` hook so `riscv_machine.c` can hand the CPU the same clock the CLINT's
`mtime` exposes. That keeps guest timekeeping consistent instead of inventing a second
time base.

Verified: `qemu-x86_64 --version` now runs under 6.12, and the 4.15 guest still boots and
runs x86_64 binaries, so no regression.

### A self-inflicted detour worth recording

Transparent execution then failed with `ELOOP` — "Too many levels of symbolic links" —
on *every* binary including native riscv64 ones. The rule was matching everything.

The cause was mine. `binfmt_misc` wants the magic and mask as **escaped text**
(`\x7fELF\x02…`), which is literally what Alpine's `/usr/lib/binfmt.d/qemu-x86_64.conf`
contains. I had "helpfully" converted them to raw bytes with `printf` on the host, and the
embedded NULs truncated the magic to `\x7fELF\x02\x01\x01`, which matches any ELF —
so each exec re-invoked the interpreter, which matched again, until the kernel gave up.

Use Alpine's file verbatim, rewriting only the interpreter path. Related trap: busybox's
`printf` has no `\xNN` escape, so generating the line inside the guest fails differently
and just as silently.

## Status summary

| step | state |
|---|---|
| cross-build kernel with `BINFMT_MISC` | **done** |
| Linux 6.12 boots on TinyEMU | **done**, needed the ISA-string patch |
| `binfmt_misc` present, mountable, registers | **done** |
| 9p usable | **done** with `msize=8192` |
| implement the `time` CSR | **done**, second patch |
| run an x86_64 binary transparently | **done** |
| kernel small enough to ship | **not started** — 44.6 MB vs 3.98 MB |

What binfmt adds over the 4.15 result is transparency, and it is not cosmetic: without it
an x86_64 process that execs another x86_64 binary fails, so nothing beyond a single
command works. With it, a shell can mix architectures on one `PATH` without wrappers.

## Both patches applied to the wasm build

Rebuilt `temu.wasm` with the ISA-string and time-CSR patches: **159,877 bytes** (was
159,193). Boot is unchanged at **574 ms**, and the nested stack still works in wasm:

```
/ # /mnt/qemu-x86_64 /mnt/busybox echo WASM_NESTED_STILL_OK
WASM_NESTED_STILL_OK
/ # /mnt/qemu-x86_64 /mnt/busybox uname -m
x86_64
```

## Shrinking the kernel: 44.6 MB → 15.6 MB, not finished

nixpkgs' `pkgsCross.riscv64.linux` starts from the NixOS config. Starting instead from
the kernel's own riscv `defconfig` and stripping what a TinyEMU guest cannot reach —
[`nixbox-wasm/riscv64-kernel-minimal.nix`](nixbox-wasm/riscv64-kernel-minimal.nix):

```
nixpkgs default        44,633,088
first strip            19,947,520
tightened strip        15,569,408 raw   4,210,138 gz    794 options enabled
Bellard's (4.15)        3,979,556 raw   1,912,216 gz
```

It also boots roughly 3× faster — `Run /bin/sh as init process` at **1.12 s** against
3.5 s for the fat kernel, and `console=hvc0` works without `earlycon`.

Three config traps found the hard way:

- The 9p filesystem symbol is **`9P_FS`**, not `V9FS_FS`. My first minimal build had
  `NET_9P` (the protocol) but no filesystem driver, so `mount -t 9p` could never work.
- **`HVC_RISCV_SBI` must stay off.** Enabling it makes the SBI console `hvc0`, and
  TinyEMU's SBI console is output-only — the guest printed fine but accepted no input.
  `SERIAL_EARLYCON_RISCV_SBI` alone gives early output without claiming `hvc0`, leaving
  it to virtio-console, which is what Bellard's configs rely on.
- nixpkgs' `linuxManualConfig` install phase runs `modules_install`, which fails with
  `CONFIG_MODULES=n`. Building the kernel directly and taking `arch/riscv/boot/Image` is
  simpler. Also `scripts/config` has a `/usr/bin/env` shebang the nix sandbox lacks, so
  invoke it as `bash scripts/config`.

**Unresolved:** on the minimal kernel the guest boots, mounts 9p and registers binfmt
(`REGISTER_OK`), but executing an x86_64 binary then hangs — no output after 200 s, where
the same script on the full-config kernel returns promptly. Something in the aggressive
strip list is needed by `qemu-user` and a config bisect will find it. The transparent
execution result above stands on the full-config kernel; it has not been reproduced on
the minimal one.

So the size work is most of the way there and the last step is ordinary bisection, not a
new problem.

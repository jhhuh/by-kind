# Running a modern Linux kernel on TinyEMU

Goal: a riscv64 kernel with `CONFIG_BINFMT_MISC=y`, so x86_64 binaries execute
transparently instead of needing an explicit `qemu-x86_64` prefix.

Result: **Linux 6.12.77 boots on TinyEMU and binfmt_misc works**, after fixing a bug in
TinyEMU. One blocker remains, precisely located.

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

### The blocker: `rdtime` traps

With 9p mounted, `qemu-x86_64` is found and executed, then dies:

```
status: 0000000200004003  badaddr: 00000000c01027f3  cause: 0000000000000002
Illegal instruction
```

`cause 2` is illegal instruction and the opcode decodes as `csrrs` from CSR **`0xc01`,
the `time` counter**. Modern riscv64 userspace reads `rdtime` directly.

The interesting part: **the same `qemu-x86_64` binary runs fine under the 4.15 kernel** on
the same emulator. So this is not simply a missing CSR — it is an interaction, most likely
around `scounteren`/`mcounteren` (the bits that permit U-mode access to the counters) and
how 6.12 programs them versus 4.15. That is the next thing to look at, and it is again in
code we own.

## Status summary

| step | state |
|---|---|
| cross-build kernel with `BINFMT_MISC` | **done** |
| Linux 6.12 boots on TinyEMU | **done**, needed the ISA-string patch |
| `binfmt_misc` present, mountable, registers | **done** |
| 9p usable | **done** with `msize=8192` |
| run an x86_64 binary transparently | **blocked** on `rdtime` trapping |
| kernel small enough to ship | **not started** — 44.6 MB vs 3.98 MB |

The x86_64-under-emulation result already stands independently on the 4.15 kernel, where
it works with an explicit interpreter prefix — see the companion note. What binfmt adds is
transparency, which matters because an x86_64 process that execs another x86_64 binary
fails without it.

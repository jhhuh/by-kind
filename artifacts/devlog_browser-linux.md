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

---

## 2026-08-03 (later) — the blocker is closed: two more TinyEMU bugs

With the idle theory gone, the workload was re-timed from outside the guest. The
mount took 30 ms and `cat /proc/cmdline` 20 ms, so the shell and 9p were fine; only
`exec` of the x86_64 binary wedged. During the wedge, virtio kicks, used-ring
completions, IRQ raises and guest acks were **frozen** — 942/1259/622/623, unchanged
for 40 s — so there was no outstanding I/O to lose. Not a stalled device, then.

Asking the guest directly, with `/proc` actually mounted this time:

```
FOUNDPID=29
SYSCALL=278 …                    # __NR_getrandom on riscv64
WCHAN=wait_for_random_bytes
```

### Bug 4: no entropy source, so getrandom() never returns

TinyEMU has no virtio-rng, and RISC-V has no unprivileged instruction the kernel can
seed from. Linux 5.x and later block `getrandom()` in `wait_for_random_bytes()` until
the CRNG is fully seeded, so the first caller waits forever. glibc startup and
qemu-user both call it. 4.15 predates those semantics, which is exactly why the same
binary works there.

Fix ([`tinyemu-dt-rng-seed.patch`](nixbox-wasm/tinyemu-dt-rng-seed.patch)): put an
`rng-seed` property in the device tree's `/chosen` node, which is the standard
bootloader contract — `early_init_dt_scan_chosen()` reads and credits it, and it is
what U-Boot and QEMU's own virt machine do. Result: `random: crng init done` at
timestamp 0.000000 and `entropy_avail=256`.

### Bug 5: the firmware is not reserved, so Linux overwrites it

That fix moved the failure rather than removing it: the guest stopped blocking and
started spinning at **240 Minsn/s with zero timer interrupts and zero I/O**. A PC
sampling profiler ([`tinyemu-pc-profiler.patch`](nixbox-wasm/tinyemu-pc-profiler.patch))
placed it exactly:

```
PROF total=17535 U=0.0% S=0.0% M=100.0% distinct=7 lost=0
PROF    14.3%  M  0x0000000080000004      (and 0x…06, 08, 0a, 0c, 0e, 10)
```

100% machine mode, in a seven-instruction loop at the BIOS entry. `mtvec` is
`0x80000004` — BBL's M-mode trap vector — and the samples are 2 bytes apart where BBL
has 4-byte instructions, so whatever is executing there is not BBL. Dumping the memory
each period settles it:

```
bbl64.bin:   2f00006f 34011173 1a010863 04a13823 …
early:       2f00006f 34011173 1a010863 04a13823 …   <- intact
later:       8fd90046 c29ce150 78826842 47f57322 …   <- overwritten
```

`fdt_output()` wrote an **empty** memory reservation map — literally
`re->address = 0; /* no reserved entry */`. The BIOS sits at `RAM_BASE_ADDR` and the
kernel at the next 2 MB boundary, and nothing told Linux the gap was occupied, so it
allocated over BBL's text. This is silent until the first trap the firmware still
owns: `medeleg=0xb109` delegates neither illegal-instruction nor access faults, so
`mtvec` jumps into what is now heap data, wanders, faults again, and the machine spins
in M-mode forever at full speed — never taking interrupts, never returning.

Fix ([`tinyemu-fdt-reserve-firmware.patch`](nixbox-wasm/tinyemu-fdt-reserve-firmware.patch)):
emit a real reserve entry covering `[RAM_BASE_ADDR, kernel_start)`.

### Result

```
                                       6.12 before   6.12 after   4.15
qemu-x86_64 busybox true               never (>380s)    0.29 s    0.22 s
qemu-x86_64 busybox uname -m           never            0.40 s    0.33 s
qemu-x86_64 busybox md5sum (1 MB)      never            0.95 s      -
```

Digest verified against the host (`d8236eb58cc8ef2ac907ce4b71f11910`) and `uname -m`
reports `x86_64`. 4.15 is unchanged, so neither fix regresses it.

### One hypothesis checked and killed on the way

`mcounteren`/`scounteren` were both `0x7` — the TM bit is set, so U-mode `rdtime` was
never trapping. That would have been a tidy story connecting back to the earlier
time-CSR patch, and it was wrong. Printing the register cost one build.

# Spike: `nix shell` in a box, without nix and without a browser

**Result: the whole loop works.** A `/nix/store` assembled from nothing but HTTP,
with a real `bash` running inside it under an x86-64 emulator, `PATH` resolving
into the store, forked children executing emulated binaries, and pipes working.

Run natively on 2026-07-31. The browser is deliberately absent — every piece here
is logic that ports to JavaScript, so proving it natively removes all the
nix-specific risk from the browser question.

## What was proven

```
$ python3 fetchstore.py root <store-path> ...
closure: 16 store paths, 58.3 MB uncompressed
downloaded 17.3 MB compressed into root
real 0m5.5s

$ blink -C root .../bash --noprofile --norc -c 'export PATH=...; source /guest.sh'
shell    : 5.2.37(1)-release  pid 4035840
which jq : /nix/store/r49zci44xq7i6wl61ayhq82wn6v7w9lm-jq-1.7.1-bin/bin/jq
jq       : jq-1.7.1
ls       : 16 store paths visible
pipeline : 55                       # seq 1 10 | jq -s add
```

No nix daemon, no nix installation, no root. `fetchstore.py` speaks the binary
cache protocol directly: resolve a store path, walk `References` transitively for
the closure, fetch each NAR over plain HTTP, parse it, materialise files with
correct permissions and symlinks.

## The number that matters for "instant"

Per-package cost once a shared base (bash + coreutils + jq, 16 paths) is cached:

| package | new store paths | download |
|---|---:|---:|
| tree | 1 | **0.04 MB** |
| hello | 1 | **0.1 MB** |
| fd | 3 | **2.0 MB** |
| ripgrep | 2 | **2.2 MB** |
| curl | 13 | 6.6 MB |

Tens of KB to a couple of MB for a typical CLI tool. Cache the base once in OPFS
and subsequent packages are effectively instant, which is exactly the requirement.

## Why the pieces fit

- **`cache.nixos.org` sends `access-control-allow-origin: *`** — browser JS can
  fetch NARs directly. No proxy, no server, no credentials.
- **NAR is a trivial format** — 64-bit LE integers, length-prefixed strings padded
  to 8 bytes, recursive nodes. The parser here is ~40 lines and ports directly.
- **Blink has a filesystem overlay layer** (`-C` / `$BLINK_OVERLAYS`). That is the
  seam a browser build would drive from OPFS instead of from disk.
- **Blink emulates processes internally**, so `fork`, `exec`, subshells and pipes
  work without host process support. This is why UML is unnecessary.

## Known gaps

- `/dev/null` and friends must be created in the root; `uname` fails without them.
- Store paths are not in `packages.json` (its `outputs` values are `null`). The
  channel publishes **`store-paths.xz`** — one small file listing every store path
  in the channel — which is the right source for a package → store-path map.
- **Blink has no browser build.** [Issue #8](https://github.com/jart/blink/issues/8)
  is an open request from Dec 2022 with no implementation. Emscripten has no
  `fork()`, and whether Blink's internal process emulation survives that port is
  the single unanswered question. Everything else above is settled.

## Follow-up: does Blink's browser build support a shell? **No.**

Answered by reading the source rather than guessing. Two corrections to earlier
assumptions, in opposite directions.

**Correction 1 — the Emscripten port is not hypothetical.** I previously said it
did not exist, based on issue #8 being an open request. Wrong: Blink has
first-class in-tree web support — `blink/web.h`, `__EMSCRIPTEN__` conditionals
through `blink.c`, `map.c`, `procfs.c`, `ioctl.c`, `readansi.c`, and a complete
`blink/blink-shell.html` Emscripten harness. Issue #8 simply never got closed.

**Correction 2 — but `fork` genuinely does not survive it.** Blink implements
guest fork by delegating to *host* fork:

```c
// blink/syscall.c:426
pid = fork();
```

gated behind a `configure`-probed `HAVE_FORK`, and when it is absent:

```c
// blink/syscall.c:597
#ifdef HAVE_FORK
    return Fork(m, flags, stack, ctid);
#else
    LOGF("forking support disabled");
    return enosys();
#endif
```

The syscall table registers `fork`, `vfork`, `wait4` and `kill` only under
`#ifdef HAVE_FORK`. Emscripten provides no `fork()`, so the probe fails and those
syscalls are absent entirely. `bash` cannot spawn a child.

Also note `blink/map.c:102` returns a **32-bit address space** under Emscripten,
a further constraint on what guests can run.

### What this means

| goal | browser feasibility |
|---|---|
| run **one command** (`jq .`, `rg pattern`, `hello`) | ✅ reachable today with in-tree Emscripten support |
| run a **real shell** with pipes and subshells | ❌ needs fork, which the WASM build lacks |

So there are two honest products:

1. **JS shell driving per-command Blink execs.** A prompt, `PATH` resolving into
   the OPFS store, each command a real emulated x86-64 binary. No subshells, no
   job control, no shell scripts. Everything else in this spike applies unchanged,
   and it is reachable now.
2. **A real shell**, which requires either full-system emulation
   (container2wasm, CheerpX, TEMU) or teaching Blink to emulate fork *internally*
   — copy-on-write memory, a process table, `wait4` — instead of borrowing the
   host's. That is genuine engineering, not configuration.

For a showcase where someone clicks `ripgrep` and types `rg --version`, option 1
may simply be the right product, and it is the cheaper one by a wide margin.

## Build attempt: Blink actually compiles to WASM

Went further than reading code — built it.

```
emconfigure ./configure && emmake make o//blink/blink
  o/blink/blink.wasm    409 KB      (331 KB at -O2)
  o/blink/blink          235 KB JS loader
$ node o/blink/blink
Usage: blink [-hvjemZs0L:C:] PROG [ARGS...]     ← runs
```

**The `fork` prediction is confirmed by the generated `config.h`**, not just by
reading source. Emscripten's feature probes produce:

```
#define DISABLE_THREADS
#define DISABLE_VFS
                      ← HAVE_FORK absent entirely
```

So the browser build loses **fork, threads, and the VFS/overlay layer**. `blink -C`
fails immediately with "bad blink overlays spec", which rules out the chroot
mechanism the native spike relied on.

### Executing a guest program did not work, and needs real debugging

Three successive failures, each a mismatch between Blink's syscall use and
Emscripten's shims rather than anything fundamental:

| attempt | failure |
|---|---|
| `-sNODERAWFS=1` | `TypeError … reading 'mode'` in `___syscall_getsockopt` — Blink probes an FD, Emscripten's SOCKFS assumes a real socket |
| + `DISABLE_SOCKETS` | `TypeError … reading 'poll'` in `___syscall_poll` — NODERAWFS bypasses the JS FS layer, so streams lack `poll` ops |
| Emscripten FS + `--preload-file` (255 MB store baked in) | `ErrnoError errno 20` (ENOTDIR) during path resolution |

Each is plausibly fixable, and the direction of travel suggests the *Emscripten
FS* path (not `NODERAWFS`) is the right one — which is also what a browser would
use, and what an OPFS backing would hook into. But this is now debugging someone
else's syscall shim compatibility, not verification, so I stopped.

### Honest status

- **Proven:** the entire nix half. Store assembly from HTTP, closure resolution,
  NAR parsing, per-package deltas, and a real shell — all working natively.
- **Proven:** Blink compiles to a 331 KB WASM binary and loads.
- **Proven:** the browser build cannot fork, so a real shell there is out.
- **Unproven:** running even a single guest program under WASM Blink. Not shown
  impossible — three fixable-looking errors in a row — but not achieved.

Anyone picking this up should start from the Emscripten-FS build (not NODERAWFS)
and work the ENOTDIR, since that path both matches the browser target and keeps
the JS filesystem layer that OPFS would plug into.

## i686 binary cache coverage: ~8%, which rules out every 32-bit emulator

Worth measuring because it decides whether **v86** is usable — v86 is fully open
(BSD-2), full-system (so a real kernel, real `fork`, real shell), and already has
a JS 9p filesystem layer that OPFS could back. If i686 had cache coverage it would
have solved every problem at once.

It does not. Random sample of 45 by-name packages, evaluated for `i686-linux`,
then checked against `cache.nixos.org`:

```
evaluate for i686-linux : 37/45   (8 unsupported or broken on that platform)
substitute in cache     :  3/37 = 8%

cached  : SDL_sound, aalib, abseil-cpp
missing : _3mux, _4th, _6tunnel, aaaaxy, aacgain, aactivator, a4, ...
```

Hydra treats i686-linux as best-effort rather than a release target. Note the
three hits are all *libraries*, almost certainly pulled in as dependencies of
something else that was built — leaf-application coverage is nearer zero.

*Caveat:* the sample was drawn from the first 400 names alphabetically, so it is
`_`- and `a`-heavy rather than uniform. 8% is far enough from viable that a
better sample would not change the conclusion.

### Consequence

Every 32-bit browser emulator is ruled out regardless of technical merit, because
there are no binaries to run. That reduces the landscape to:

| goal | remaining options |
|---|---|
| single command | Blink WASM (x86_64; builds, needs syscall-shim work) |
| real shell | 64-bit **full-system** only: container2wasm, CheerpX, TEMU |

container2wasm is now the only *fully open* route to a real shell, rather than
one pragmatic choice among several.

## Which architectures actually have NARs? Only x86_64 and aarch64.

Same 45-package sample, evaluated per system and checked against
`cache.nixos.org`:

| system | evaluates | cached | coverage |
|---|---:|---:|---:|
| x86_64-linux | — | — | ~100% (the reference) |
| **aarch64-linux** | 43 | 41 | **95%** |
| i686-linux | 37 | 3 | 8% |
| **riscv64-linux** | 40 | **0** | **0%** |
| armv7l-linux | 40 | 0 | 0% |

**The riscv64 trap:** every package *evaluates* for riscv64-linux, so it looks
supported. Hydra does not build it, so nothing is substitutable. "nixpkgs supports
riscv64" and "riscv64 binaries exist" are different claims and only the first is
true. Same for armv7l.

### Why this closes the "try another architecture" question

The two facts point in opposite directions:

- **riscv64** has the *best* browser emulation — TinyEMU/JSLinux's most mature
  target, and container2wasm's preferred path (it falls back to slower Bochs for
  x86_64) — and **zero binaries**.
- **aarch64** has the *binaries* at 95% and **no browser emulator**:
  container2wasm does x86_64 and riscv64; JSLinux does x86/x86_64/riscv64; v86 and
  CheerpX are x86-only; Blink is x86-64 by definition.

**x86_64 is the only architecture scoring on both axes.** Not best on either — it
is simply the only overlap. That retroactively justifies the path taken and rules
out arch-switching as an escape from the fork problem.

Worth revisiting if an **aarch64 WASM emulator** appears (a QEMU→WASM aarch64
target would qualify): it would be immediately viable at 95% coverage with no
nixpkgs-side work at all.

## container2wasm / qemu-wasm: the actual state of the art

`c2w` is packaged in nixpkgs (found, pleasingly, by querying by-kind itself).
Two of its flags change the design materially:

- **`--external-bundle`** — *"Do not embed container image to the Wasm image but
  mount it during runtime."* This is the pluggable filesystem seam, as a supported
  flag rather than a fork.
- **`--pack ... (valid only for aarch64 QEMU on emscripten)`** — an aarch64 target
  exists.

Its Dockerfile names the two emulator backends:

```
BOCHS_REPO=https://github.com/ktock/Bochs
QEMU_REPO=https://github.com/ktock/qemu-wasm
```

### Correction: aarch64 in the browser *does* exist

Earlier in this document I concluded aarch64 had 95% cache coverage but no browser
emulator, and that x86_64 was therefore the only architecture scoring on both
axes. **That was wrong.** `ktock/qemu-wasm` supports **x86_64, aarch64 and
riscv64**, full-system with a real kernel and shell, using a hybrid TCG→WASM JIT
(hot translation blocks compiled to Wasm, cold paths interpreted via TCI), with
`--enable-virtfs` for virtio-9p. Work is being upstreamed into QEMU — TCI for
32-bit guests landed in QEMU 10.1.

Revised architecture table:

| arch | nixpkgs cache | browser emulator | verdict |
|---|---:|---|---|
| x86_64 | ~100% | qemu-wasm (JIT) or Bochs | viable |
| **aarch64** | **95%** | **qemu-wasm (JIT)** | **viable — and previously dismissed in error** |
| riscv64 | 0% | qemu-wasm, TinyEMU | no binaries |
| i686 | 8% | v86 | no binaries |

Both x86_64 and aarch64 now work. aarch64 is worth considering on the merits:
QEMU's aarch64 TCG is heavily exercised, and it avoids x86_64's instruction
decode complexity.

### Blocked here: no container runtime

`c2w` requires docker/buildx and this sandbox has no docker, podman, buildah,
nerdctl or buildctl, so an image could not be built or tested. The findings above
are from source and documentation, not execution.

Two ways forward, neither attempted:

1. Run `c2w --to-js --external-bundle` on a machine with Docker. Cheapest path to
   a working artifact.
2. Build `qemu-wasm` directly with emscripten, skipping `c2w`. Emscripten works
   here (it built Blink), but a QEMU build is far larger than Blink's and this
   would be exploratory rather than bounded.

## The architecture exists and is documented: qemu-wasm + virtfs

`ktock/qemu-wasm` supports exactly the design proposed at the start of this
investigation — instantiate `/nix/store` *outside* the machine and hand it in —
with **no emulator modification**. From `examples/virtfs/`:

```js
// host side, plain JavaScript
Module['preRun'].push((mod) => {
    mod.FS.mkdir('/share');
    mod.FS.writeFile('/share/file', 'test');
});
'-virtfs', 'local,path=/share,mount_tag=share0,security_model=passthrough,id=share0',
```

```console
# guest side, a real Linux
$ mount -t 9p -o trans=virtio share0 /mnt/ -oversion=9p2000.L
$ cat /mnt/file
test
```

The chain is: **browser JS → Emscripten `FS` → QEMU virtio-9p → guest mount**.
The build exports `FS` to JavaScript (`EXPORTED_RUNTIME_METHODS=…,FS`) and enables
`--enable-virtfs`, both already in the documented configure line.

Substitute `/nixstore` for `/share`, populate it with closures fetched and
NAR-parsed in the browser — proven earlier in this document — and the guest gets
a real `/nix/store` with a real kernel, real `fork`, and a real shell.

### Everything needed now exists

| piece | status |
|---|---|
| fetch NARs from cache.nixos.org in-browser | ✅ CORS `*`, proven |
| parse NAR → files | ✅ proven, ~40 lines |
| closure walk via `References` | ✅ proven |
| per-package delta over cached base | ✅ 0.04–2 MB typical |
| real kernel / `fork` / shell | ✅ full-system QEMU |
| inject host files from JS | ✅ `Module.FS` + `-virtfs`, documented |
| x86_64 **and** aarch64 guests | ✅ both, both have nixpkgs cache coverage |
| live proof it runs in a browser | ✅ https://ktock.github.io/qemu-wasm-demo/ |

### Practical notes for whoever builds this

- **Cross-origin isolation is required.** The build uses pthreads
  (`-sPROXY_TO_PTHREAD=1`), so SharedArrayBuffer needs COOP/COEP headers. GitHub
  Pages does not set them; the demo works around it with `coi-serviceworker.js`.
  A by-kind integration would need the same shim.
- **`-sTOTAL_MEMORY=2300MB`** in the documented build. Desktop-fine, phone-hostile.
- **Building from source is a real project**, not a shortcut: zlib, libffi, a
  libresolv stub, glib 2.75 (meson) and pixman all compiled with emscripten, and
  the repo pins **emsdk 3.1.50** with a `# TODO: support recent version` note —
  our emscripten is 4.0.10, so newer is known-broken. Using Docker for the deps,
  as the README does, is by far the cheaper path.
- Reusing the demo's prebuilt artifacts is the cheapest way to prototype: the
  rootfs is a separately-fetched `.data` package, not baked into the wasm.

## FOSDEM 2025 talk: "Running QEMU Inside Browser" (Kohei Tokunaga, NTT)

[Slides](https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6290-running-qemu-inside-browser/slides/238760/slides_1dDtpcS.pdf).
Confirms the architecture from the author directly, and adds numbers.

**Performance (slide 16)** — pigz compressing 10 MB of random data on an emulated
x86_64 guest, Chrome 130, i7-10510U:

| backend | relative time (lower better) |
|---|---:|
| Bochs | ~40 |
| QEMU Wasm, single-threaded | ~13 |
| **QEMU Wasm, 4-thread MTTCG** | **~7.5** |

**qemu-wasm is roughly 5× faster than Bochs**, so `c2w --to-js` (which selects
qemu-wasm) is strongly preferable to the default Bochs path.

**Slide 17 states our design verbatim:**

```
JS:     FS.writeFile('/share/file', 'test');
QEMU:   -virtfs local,path=/share,mount_tag=share0…
Guest:  $ mount -t 9p share0 /mnt/  &&  cat /mnt/file  →  test
```

**Execution model (slides 12–14):** TCG IR is translated to Wasm modules via
`WebAssembly.Module`/`Instance`. Because browsers cannot create thousands of
modules at once, blocks run on the TCI *interpreter* by default and only blocks
executed many times (~1500) are compiled to Wasm. Expect warm-up: the first run
of anything is slow, repeated work speeds up. MTTCG uses emscripten pthreads.

**Other specifics:** demo guest runs `-m 512M` with `-accel tcg,tb-size=500`
(the 2300 MB figure elsewhere is the wasm heap, not guest RAM). Networking has
two modes — WebSocket to a host-side daemon, or Fetch API entirely in-browser
with the caveat "limited destination by CORS". For us the Fetch route is fine:
cache.nixos.org sends `access-control-allow-origin: *`.

**Listed as future work (slide 26):** *"Accessing package repos (e.g. apk, apt, …)
and container registries from browser (w/ CORS restriction)"* — precisely this
use case, and nix is the easiest instance of it because its cache is already
CORS-open.

Slide 27 also confirms independently that **v86 has no x86_64 guest support** and
Qemu.js is single-threaded with no 64-bit guests.

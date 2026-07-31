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

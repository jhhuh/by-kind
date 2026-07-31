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

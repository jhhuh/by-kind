# Reproducing the wasm build and boot measurement

`node-run.cjs` runs TinyEMU's wasm build under node — no browser, no HTTP server.
Results: [`../experiment-results-2026-08-02.md`](../experiment-results-2026-08-02.md).

## Build

```sh
curl -sSLO https://bellard.org/tinyemu/tinyemu-2019-12-21.tar.gz
tar xzf tinyemu-2019-12-21.tar.gz && cd tinyemu-2019-12-21

nix build nixpkgs#emscripten            # 4.0.10 works
export PATH=<emscripten>/bin:$PATH

make -f Makefile.js js/riscvemu64-wasm.js   # objects build; link fails on retired flags

# relink, dropping --memory-init-file and BINARYEN_TRAP_MODE, adding MODULARIZE:
emcc -O3 --closure 0 -sNO_EXIT_RUNTIME=1 -sNO_FILESYSTEM=1 \
  -sMODULARIZE=1 -sEXPORT_NAME=createTinyEMU -sALLOW_TABLE_GROWTH=1 \
  -s "EXPORTED_FUNCTIONS=['_console_queue_char','_vm_start','_fs_import_file','_display_key_event','_display_mouse_event','_display_wheel_event','_net_write_packet','_net_set_carrier','_malloc','_free']" \
  -s "EXPORTED_RUNTIME_METHODS=['ccall','cwrap','UTF8ToString','wasmTable','HEAPU8','addFunction']" \
  --js-library js/lib.js -sWASM=1 -sTOTAL_MEMORY=67108864 -sALLOW_MEMORY_GROWTH=1 \
  -o js/temu.js *.js.o
```

Produces `js/temu.wasm`, 159,193 bytes (65,216 gzipped).

## Native build (for the instrumented experiments)

```sh
nix-shell -p openssl pkg-config --run 'make temu -j8 CONFIG_SDL= CONFIG_FS_NET='
```

Both `CONFIG_` overrides are needed in this sandbox: `sdl.c` wants SDL **1.x**
(`SDL/SDL.h`), and `fs_wget.c` does not compile against curl 8.18
(`multi.h:429: expected identifier before '__extension__'`). Neither touches the riscv64
interpreter — local 9p (`fs0`) and local disk images still work; only the HTTP-backed
vfsync filesystem is missing from the resulting binary. `CFLAGS` cannot be overridden on
the command line without losing the required `-D`s; `TIME_CACHE_INSNS` is
`#ifndef`-guarded so it can be set with a per-build `-D`.

## Measuring anything

Two harnesses, both of which read the clock from **outside** the guest, because every
timing mistake in this project came from measuring on the wrong axis:

- `timed-run.py <cfg> <label> [--timeout N] -- <cmd> ...` drives the guest over a pty and
  timestamps each console byte on the host. Each command must end in `echo MARK<n>`; the
  next command is sent when that marker arrives on its own line.
- `idle-burn.sh <temu> <cfg> [secs]` boots to a prompt, sends nothing, and reports
  instructions per wall second from the wall-clock-keyed `TIMERSTAT` dumps.

`TLBSTAT` and `TIMERSTAT` both carry `t=` in `CLOCK_MONOTONIC` ms. Do not divide an
instruction count by a wall time you did not read from the same line.

## Run

Needs a directory holding `rv.cfg`, `bbl64.bin`, `kernel-riscv64.bin` and the **split**
disk image `root-riscv64/` — all from `jslinux-2019-12-21.tar.gz`.

```sh
node node-run.cjs <dir> "http://local/rv.cfg" 256
```

The config path must be a URL: under Emscripten `load_file()` is `abort()`
(`machine.c:452`), so only the `is_url()` branch works. The harness maps any URL to a
file under `<dir>`.

`CMDLINE=…` overrides the kernel command line, `VERBOSE=1` streams console output and
fetch activity, `RUN_MS=…` sets how long to run.

## Gotchas this encodes

- `-sMODULARIZE=1` is required — otherwise the glue's `var Module` hoists and shadows any
  global you set, silently ignoring injected config.
- `vm_start` takes **7** args in the 2019 source; today's `jslinux.js` passes 8
  (`drive_url` was added later).
- The disk must be in split format (`blk.txt` + parts), not a plain `.bin`.
- Pass width/height `0` unless supplying a framebuffer, or `fb_refresh` dereferences null.
- `js/lib.js` and emscripten's own `emscripten_async_wget3_data` still reference
  `Pointer_stringify`, `Runtime.dynCall` and `Browser.*`, all retired. The harness shims
  them.

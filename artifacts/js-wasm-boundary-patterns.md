# The JavaScript ↔ WebAssembly boundary in browser emulators

A self-contained explanation of how projects that run a whole operating system inside a
web browser connect their compiled code to the page around it — written for someone who
has not worked with WebAssembly, emulators, or emscripten before.

Companion note: [`prior-art-browser-x86-emulators.md`](prior-art-browser-x86-emulators.md)
surveys *which* projects exist. This note explains *how they are wired*.

---

## Part 1 — Background

### 1.1 The problem

We want a web page where clicking a link gives you a working Linux shell — a real
kernel, real processes, real `fork`, real pipes — with no server doing the work. The
computation happens in the visitor's browser tab.

Nothing about a browser is designed for this. A browser runs JavaScript and
WebAssembly in a sandbox with no access to the host CPU's privileged instructions, no
ability to install a kernel, and no filesystem. So the only way to get Linux is to
**emulate an entire computer in software**: a CPU that decodes and executes machine
instructions one at a time, plus emulated hardware — disk controller, timer, serial
port, interrupt controller — that the guest kernel talks to as if it were real.

That emulator is a large C or Rust program. To run it in a browser it must be compiled
to **WebAssembly**.

### 1.2 What WebAssembly is

[WebAssembly](https://webassembly.org/) (wasm) is a binary instruction format that
browsers execute at near-native speed. You compile C, C++, or Rust to a `.wasm` file
and the browser runs it.

A wasm **module** is a self-contained unit with a strictly defined interface:

- **Linear memory** — one big flat `ArrayBuffer` that acts as the module's RAM. All the
  module's data structures live in here.
- **Imports** — functions the module needs *from* the outside world. The module cannot
  run until you supply them.
- **Exports** — functions the module offers *to* the outside world.

Everything crossing that line is a number: `i32`, `i64`, `f32`, `f64`. There are no
strings, no objects, no arrays. If C code wants to give JavaScript a string, it passes
a *pointer* — an integer offset into linear memory — and JavaScript reads the bytes out
of the `ArrayBuffer` itself.

**This is the boundary.** Every project in this note is an answer to one question:
*what should the imports and exports be?*

### 1.3 Three limits that shape every design

**(a) Wasm cannot see the outside world at all.** It has no syscalls, no network, no
console, no clock. If the emulator wants to print a character, it must call an imported
JavaScript function. This is not a restriction that can be worked around; it is the
entire security model.

**(b) Wasm is W^X — "write XOR execute".** Linear memory is never executable, and the
code section is immutable once the module is instantiated. A traditional
*just-in-time compiler* (JIT) — which writes fresh machine code into a memory buffer and
jumps to it — is therefore impossible.

This matters enormously for emulator speed. Interpreting one guest instruction at a
time is roughly 10–100× slower than translating a block of guest instructions into host
machine code once and reusing it. The only escape is to compile new **wasm modules** at
runtime:

```js
const module   = new WebAssembly.Module(generatedBytes);   // compile
const instance = new WebAssembly.Instance(module, imports); // make callable
```

Kohei Tokunaga states the constraint directly in his [FOSDEM 2025 talk](https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6290-running-qemu-inside-browser/slides/238760/slides_1dDtpcS.pdf)
(slide 12): *"Wasm can't execute code generated on memory."* Both QEMU Wasm and v86
independently converged on the `WebAssembly.Module` workaround.

It is also slow enough that neither compiles everything. QEMU Wasm runs blocks on a
slow interpreter and only compiles a block after it has executed ~1500 times
(slide 14) — which is why the first boot of any of these is sluggish and it speeds up
as it runs.

**(c) Blocking is forbidden on the main thread.** A browser tab has one main thread that
also draws the page. If JavaScript blocks, the tab freezes. But emulators are written
as `while (true) { execute_instruction(); }` — they never return.

Three different escapes appear below: cooperative time-slicing, running on a worker
thread, and Asyncify.

### 1.4 What emscripten is

[Emscripten](https://emscripten.org/) is the toolchain that compiles C/C++ to
WebAssembly *and* generates the JavaScript needed to make it usable. It provides:

- A **libc** implemented on top of browser APIs, so `printf`, `malloc` and `fopen` work.
- A **virtual filesystem** called `FS`, living in wasm linear memory, so `open("/foo")`
  succeeds. See [File System Overview](https://emscripten.org/docs/porting/files/file_systems_overview.html).
- A **`Module` object** in JavaScript — the configuration and control surface. You set
  fields on it before startup and read from it afterwards.
- `ccall` / `cwrap` — helpers that call an exported C function with JS arguments,
  handling the string-to-pointer marshalling for you.
- `--js-library` — a way to supply your own JavaScript implementations for functions
  the C code declares but does not define.

Emscripten's own runtime is MIT/Apache-2.0 licensed.

### 1.5 Reading a minified glue file

Emscripten-generated JavaScript is usually shipped minified and often passed through
[Closure Compiler](https://developers.google.com/closure/compiler), which renames
everything to single letters. That makes the boundary invisible at a glance. It is
trivially recoverable:

```sh
curl -sSLO https://bellard.org/jslinux/x86_64emu-wasm.js
npx js-beautify --indent-size 2 x86_64emu-wasm.js -o x86_64emu-wasm.beautified.js
# 1 line  ->  1,172 lines
```

All the deobfuscated excerpts in Part 2 came from exactly that command. The full
beautified file is not committed here — it is generated third-party code whose
redistribution terms are unclear (see the companion note) — but the two lines above
reproduce it in seconds.

---

## Part 2 — The four patterns

Four projects, four fundamentally different answers to "where does the boundary go".

### 2.1 Pattern A — a bespoke C API (TinyEMU / JSLinux)

**Project:** [TinyEMU](https://bellard.org/tinyemu/) by Fabrice Bellard, driving
[JSLinux](https://bellard.org/jslinux/). Source MIT; the compiled x86_64 emulator is
not distributed.

The design: invent a small, purpose-built C API and expose exactly that. Nothing
generic, nothing standard.

**Exports — what JavaScript may call.** The whole minified export table, deobfuscated:

```js
function assignWasmExports(wasmExports) {
  _console_queue_char   = Module["_console_queue_char"]   = wasmExports["D"];
  _console_resize_event = Module["_console_resize_event"] = wasmExports["E"];
  _display_key_event    = Module["_display_key_event"]    = wasmExports["F"];
  _display_mouse_event  = Module["_display_mouse_event"]  = wasmExports["H"];
  _display_wheel_event  = Module["_display_wheel_event"]  = wasmExports["I"];
  _net_write_packet     = Module["_net_write_packet"]     = wasmExports["J"];
  _net_set_carrier      = Module["_net_set_carrier"]      = wasmExports["K"];
  _vm_start             = Module["_vm_start"]             = wasmExports["L"];
  _free                 = Module["_free"]                 = wasmExports["M"];
  _malloc               = Module["_malloc"]               = wasmExports["N"];
  _fs_import_file       = Module["_fs_import_file"]       = wasmExports["O"];
  __emscripten_stack_restore    = wasmExports["P"];
  __emscripten_stack_alloc      = wasmExports["Q"];
  _emscripten_stack_get_current = wasmExports["R"];
  memory                   = wasmMemory = wasmExports["B"];
  __indirect_function_table = wasmTable = wasmExports["G"];
}
```

Seventeen entries. The binary itself is stripped bare — 2,101 functions with no name
section at all — but this one generated function restores every name that matters.
**The consequence is that the emulator is fully drivable by name from JavaScript
despite the minification**; only reaching *internal* C functions would require reverse
engineering.

**Imports — what the emulator asks of JavaScript:**

```js
var wasmImports = {
  a: ___assert_fail,        y: __abort_js,
  u: __gmtime_js,           v: __localtime_js,        w: __tzset_js,
  x: _clock_time_get,       m: _emscripten_date_now,  e: _emscripten_random,
  A: _console_get_size,     p: _console_write,
  l: _emscripten_async_call,
  j: _emscripten_async_wget3_data,
  r: _emscripten_resize_heap, b: _exit,
  q: _fb_refresh,
  t: _fd_close,  s: _fd_seek,  k: _fd_write,
  h: _file_buffer_init,   d: _file_buffer_read,   i: _file_buffer_reset,
  g: _file_buffer_resize, z: _file_buffer_set,    c: _file_buffer_write,
  n: _fs_export_file,     f: _fs_wget_update_downloading,
  o: _net_recv_packet
};
```

Twenty-seven, all in a single namespace `"a"`. Two of them tell you the whole
architecture:

- **`_emscripten_async_call`** — the scheduler. See below.
- **`_emscripten_async_wget3_data`** — an HTTP fetch. *The emulator downloads its own
  files.* The filesystem is not handed in from JavaScript; the C code goes and gets it.

The non-emscripten imports (`console_write`, `fb_refresh`, `net_recv_packet`,
`fs_export_file`, and the seven `file_buffer_*`) are implemented in `js/lib.js`, a
280-line file in the MIT source tarball, supplied to the compiler with `--js-library`.

**Startup.** One entry point, called with emscripten's `ccall`:

```js
Module.preRun = start;                       // emscripten calls start() before main()

function start() {
    console_write1    = Module.cwrap('console_queue_char', null, ['number']);
    fs_import_file    = Module.cwrap('fs_import_file', null, ['string','number','number']);
    display_key_event = Module.cwrap('display_key_event', null, ['number','number']);
    net_write_packet  = Module.cwrap('net_write_packet', null, ['number','number']);

    Module.ccall("vm_start", null,
        ["string","number","string","string","number","number","number","string"],
        [cfgUrl, mem_size, cmdline, pwd, width, height, net_enabled, drive_url]);
}
```

`vm_start` does not start anything synchronously. On the C side (`jsemu.c`, 349 lines,
MIT) it begins an asynchronous chain, because the config file and disk images arrive
over HTTP:

```
vm_start ──fetch .cfg──► init_vm_fs ──fetch fs──► init_vm_drive ──fetch drive──► init_vm
```

**The main loop is not a loop.** This is the heart of Pattern A:

```c
void virt_machine_run(void *opaque) {
    ...  /* execute a slice of emulation */
    if (work_pending) emscripten_async_call(virt_machine_run, m, 0);
    else              emscripten_async_call(virt_machine_run, m, MAX_SLEEP_TIME);
}
```

There is no `while(1)`. The emulator runs a time-slice, then schedules itself to
continue via the browser's event loop, yielding control so the page stays responsive.
Cooperative multitasking, hand-written.

**Why that matters:** it needs no threads. No threads means no
[`SharedArrayBuffer`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/SharedArrayBuffer),
which means no [cross-origin isolation](https://web.dev/articles/coop-coep) headers,
which means it deploys on any static web host with no configuration at all. That is a
real and underrated advantage.

**Filesystem: pull.** Because the C code fetches its own files, a custom filesystem
requires a *wire protocol*. TinyEMU's is called vfsync:

```c
fs_utils.h:  #define HEAD_FILENAME "head"    /* base_url/head  — manifest header  */
fs_utils.h:  #define ROOT_FILENAME "files"   /* base_url/files — directory tree   */
fs_utils.c:  sprintf(buf, "%016" PRIx64, file_id);  /* base_url/00000000000004a1 */
```

A static host serves a manifest plus one opaque blob per file, named by a 16-hex ID.
JSLinux's x86_64 config uses it as the *root* filesystem — there is no disk image:

```js
kernel: "kernel-x86_64-new.bin",
cmdline: "... root=root rootfstype=9p rootflags=trans=virtio,msize=8192 ro",
fs0: { file: "https://vfsync.org/u/os/alpine-x86_64" },
```

Elegant, but changing the protocol means changing C and recompiling.

---

### 2.2 Pattern B — emscripten's runtime as the boundary (QEMU Wasm)

**Project:** [qemu-wasm](https://github.com/ktock/qemu-wasm) by Kohei Tokunaga, a fork
of [QEMU](https://www.qemu.org/). GPLv2. Demo:
[qemu-wasm-demo](https://github.com/ktock/qemu-wasm-demo).

The design: don't invent an API. Ship QEMU as an ordinary program and use emscripten's
standard surfaces — `Module.arguments` is `argv`, `Module.FS` is the filesystem,
`Module.TTY` is the console.

**Configuration is command-line arguments.** From FOSDEM slide 7:

```js
Module['arguments'] = [
  '-nographic', '-m', '512M', '-accel', 'tcg,tb-size=500',
  '-L', '/pack/',
  '-drive',  'if=virtio,format=raw,file=/pack/rootfs.bin',
  '-kernel', '/pack/bzImage',
  '-append', 'console=ttyS0 root=/dev/vda',
];
```

That is QEMU's real argv. Every QEMU flag is available, which is an enormous
customization surface obtained for free — machines, CPU models, devices, boot
parameters, all without touching the emulator.

**The filesystem is a push.** Emscripten's `FS` is a JavaScript-visible filesystem in
linear memory, and QEMU's `-virtfs` exports a directory of it to the guest over
[virtio-9p](https://www.linux-kvm.org/page/9p_virtio). FOSDEM slide 17 shows all three
layers:

```
Guest        $ mount -t 9p share0 /mnt/   &&  cat /mnt/file   →  test
QEMU Wasm    -virtfs local,path=/share,mount_tag=share0,security_model=passthrough
JS           FS.writeFile('/share/file', 'test');
```

Contrast with Pattern A: there is **no wire protocol**. JavaScript writes a file, the
guest reads it. Adding a custom filesystem means writing JavaScript, not modifying C.

In practice:

```js
Module['preRun'].push((mod) => {
    mod.FS.mkdir('/share');
    mod.FS.writeFile('/share/hello.txt', 'written-from-javascript\n');
    mod.FS.writeFile('/share/bin/probe.sh', '#!/bin/sh\necho probe-ran\n', { mode: 0o755 });
});
```

**Threads and blocking.** QEMU keeps its real main loop, run on a worker thread via
emscripten's [pthreads](https://emscripten.org/docs/porting/pthreads.html) support
(`-sPROXY_TO_PTHREAD=1`), with
[Asyncify](https://emscripten.org/docs/porting/asyncify.html) (`-sASYNCIFY=1`) letting
blocking C calls suspend and resume. Multiple threads also enable **MTTCG** —
multi-threaded translation — which slide 16 measures at ~1.7× single-threaded.

The cost: pthreads require `SharedArrayBuffer`, which requires cross-origin isolation:

```
Cross-Origin-Opener-Policy:   same-origin
Cross-Origin-Embedder-Policy: require-corp
```

GitHub Pages cannot set headers, so deployments there use the
[`coi-serviceworker`](https://github.com/gzuidhof/coi-serviceworker) shim, which
installs a service worker that adds them to every response.

**The build.** From `create-images.sh` in the demo repo:

```sh
emconfigure /qemu/configure --static --target-list=x86_64-softmmu --cpu=wasm32 \
    --without-default-features --enable-system --with-coroutine=fiber --enable-virtfs \
    --extra-ldflags="-sEXPORTED_RUNTIME_METHODS=getTempRet0,setTempRet0,addFunction,removeFunction,TTY,FS"
```

Two details matter. `--enable-virtfs` and `FS` in `EXPORTED_RUNTIME_METHODS` appear
**only in the x86_64 build** — the AArch64 build in the same script has neither, so its
filesystem seam does not exist. And `-O3 -g` leaves debug information in: the shipped
`qemu-system-x86_64.wasm` is 40,799,480 bytes of which 69.6% is `.debug_*` sections.
Stripping them yields 12,390,476 bytes (3,181,907 gzipped) that compiles to an
identical module — 112 exports, 119 imports, verified.

**Import surface:** 119 functions across two namespaces — `env` (108) and
`wasi_snapshot_preview1` (11). Far larger than Pattern A's 27, because it is emscripten's
whole libc rather than a hand-picked API.

---

### 2.3 Pattern C — a standard ABI (container2wasm / WASI)

**Project:** [container2wasm](https://github.com/ktock/container2wasm), Apache-2.0. Its
default output is a [WASI](https://wasi.dev/) module rather than an emscripten one.

WASI — the WebAssembly System Interface — is a *standardised* set of imports covering
files, clocks, randomness and sockets: `fd_read`, `fd_write`, `path_open`, `poll_oneoff`
and so on. A WASI module runs unchanged in a browser, in
[wasmtime](https://wasmtime.dev/), or in Node.

The entire boundary is one import object:

```js
var wasi = new WASI(args, env, fds);
wasiHack(wasi, ttyClient, connfd);
wasiHackSocket(wasi, listenfd, connfd);
WebAssembly.instantiate(wasm, {
    "wasi_snapshot_preview1": wasi.wasiImport,
}).then((inst) => {
    wasi.start(inst.instance);
});
```

`WASI` here is [browser_wasi_shim](https://github.com/bjorn3/browser_wasi_shim), a
JavaScript implementation of the WASI syscalls.

**The filesystem is preopened file descriptors.** WASI has no absolute-path filesystem;
a program can only reach directories the host explicitly handed it:

```js
fds = [
    undefined,  // 0: stdin
    undefined,  // 1: stdout
    undefined,  // 2: stderr
    certDir,    // 3: a JS PreopenDirectory object
    undefined,  // 4: socket listenfd
    undefined,  // 5: accepted socket
];
```

`certDir` is a plain JavaScript object. **A custom filesystem in this model is a class
implementing `path_open`, `fd_read` and `fd_readdir`** — no manifest, no file IDs, no C
changes. This is the cleanest of the four seams.

**Blocking on asynchronous work.** WASI's `fd_read` is synchronous — it must return
data, and it cannot `await`. But reading a key from the terminal is inherently
asynchronous. The resolution is the most instructive trick in this whole note.

The emulator runs in a [Web Worker](https://developer.mozilla.org/en-US/docs/Web/API/Web_Workers_API),
not the main thread, so it *is* allowed to block. From
[xterm-pty](https://github.com/mame/xterm-pty)'s worker helper:

```js
req(t) {
  this.streamCtrl[0] = 0;
  self.postMessage(t);                  // ask the main thread to do the async work
  Atomics.wait(this.streamCtrl, 0, 0);  // block this worker until it answers
}
```

`streamCtrl` is an `Int32Array` over a `SharedArrayBuffer`.
[`Atomics.wait`](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Atomics/wait)
suspends the worker until the main thread writes the slot and calls `Atomics.notify`.
The overridden `fd_read` simply calls it:

```js
wasi.wasiImport.fd_read = (fd, iovs_ptr, iovs_len, nread_ptr) => {
    ...
    var data = ttyClient.onRead(iovec.buf_len);   // blocks here
    ...
};
```

**Any** asynchronous source — a `fetch()` for a lazily-loaded file, an IndexedDB read —
can be made to look synchronous to the emulator this way. It is the general solution to
"my emulator wants a blocking read but the browser only offers promises."

The price is again `SharedArrayBuffer`, hence cross-origin isolation.

---

### 2.4 Pattern D — the machine in JavaScript, only the CPU compiled (v86)

**Project:** [v86](https://github.com/copy/v86) by Fabian Hemmer, BSD-2. **32-bit x86
only** — the Readme is explicit: *"Linux works pretty well. 64-bit kernels are not
supported."*

Architecturally it is the inverse of A, B and C, and by far the most interesting for
customization.

```
src/rust/     34,710 lines   the CPU: JIT, paging, modrm, registers, softfloat
src/*.js                     everything else: acpi.js, ide.js, pci.js, virtio.js,
                             vga.js, ne2k.js, pit.js, rtc.js, ps2.js, uart.js
lib/9p.js      1,170 lines   the 9p protocol
lib/filesystem.js 1,969 lines the filesystem itself
```

**Only the CPU is compiled. Every device is ordinary JavaScript you can read, edit and
debug in devtools, with no build step.**

The boundary makes this explicit. The Rust CPU's imports are the hardware access
functions, handed in from JS at instantiation:

```js
const wasm_shared_funcs = {
    "cpu_exception_hook":  n => this.cpu_exception_hook(n),
    "run_hardware_timers": function(a, t) { return cpu.run_hardware_timers(a, t); },
    "cpu_event_halt":      () => { this.emulator_bus.send("cpu-event-halt"); },
    "microtick":           v86.microtick,
    "get_rand_int":        function() { return get_rand_int(); },

    "io_port_read8":   function(addr) { return cpu.io.port_read8(addr); },
    "io_port_read16":  function(addr) { return cpu.io.port_read16(addr); },
    "io_port_read32":  function(addr) { return cpu.io.port_read32(addr); },
    "io_port_write8":  function(addr, value) { cpu.io.port_write8(addr, value); },
    "io_port_write16": function(addr, value) { cpu.io.port_write16(addr, value); },
    "io_port_write32": function(addr, value) { cpu.io.port_write32(addr, value); },

    "mmap_read8":   function(addr) { return cpu.mmap_read8(addr); },
    "mmap_read32":  function(addr) { return cpu.mmap_read32(addr); },
    "mmap_write8":  function(addr, value) { cpu.mmap_write8(addr, value); },
    "mmap_write16": function(addr, value) { cpu.mmap_write16(addr, value); },
    "mmap_write32": function(addr, value) { cpu.mmap_write32(addr, value); },
    ...
};

wasm_fn({ "env": wasm_shared_funcs }).then((exports) => {
    wasm_memory = exports.memory;
    exports["rust_init"]();
    ...
});
```

Every I/O port access and every memory-mapped device access made by the guest exits
wasm and lands in a JavaScript function. That is precisely the shape of TinyEMU's
`x86_cpu.h` interface — `port_read`/`port_write` callbacks plus a memory map — but
expressed as a wasm import object rather than C function pointers.

**Its JIT does the same W^X dance:**

```js
const module = new WebAssembly.Module(code);
const result = new WebAssembly.Instance(module, { "e": this.jit_imports });
this.wm.wasm_table.set(wasm_table_index + WASM_TABLE_OFFSET, f);
```

Generated code is installed into the
[wasm table](https://developer.mozilla.org/en-US/docs/WebAssembly/JavaScript_interface/Table)
so the Rust CPU can call it indirectly.

**Customization is a documented, typed API.** `v86.d.ts` is 892 lines. The filesystem
section is the striking part:

```ts
filesystem?: {
    /** A URL to a JSON file created using fs2json. */
    baseurl?: string;

    /** A directory of 9p files, as created by copy-to-sha256.py. */
    basefs?: string;

    /**
     * A function that will be called for each 9p request.
     * If specified, this will back Virtio9p instead of a filesystem.
     * Use this to build or connect to a custom 9p server.
     */
    handle9p?: (reqbuf: Uint8Array, reply: (replybuf: Uint8Array) => void) => void;

    /**
     * A URL to a websocket proxy for 9p.
     * Use this to connect to a custom 9p server over websocket.
     */
    proxy_url?: string;
};
```

`handle9p` is a **first-class custom-filesystem hook**: implement the 9p protocol in
JavaScript and v86 hands you every request. No other project here offers anything
equivalent as a supported API.

And because the devices are JavaScript, the filesystem methods can simply be
asynchronous —

```js
FS.prototype.Read  = async function(inodeid, offset, count) { ... }
FS.prototype.Write = async function(id, offset, count, buffer) { ... }
```

— so lazily fetching file contents over the network needs **no `SharedArrayBuffer`, no
`Atomics.wait`, no Asyncify**. The awkwardness that Patterns B and C work hard to
overcome simply does not arise.

Other niceties visible in the same type definitions: chunked disk images with
`use_parts` and `fixed_chunk_size`, documented as *"useful with `use_parts: true` for
GitHub Pages users"*; and a built-in zstd decompressor for `.zst` images.

**And none of it can be used for a nixpkgs shell, because it is 32-bit.** Measured
`cache.nixos.org` coverage for `i686-linux` is ~8%. The project with the best
customization story in the landscape is on the wrong side of the one hard constraint.

---

## Part 3 — Cross-cutting comparison

### 3.1 Summary

| | A: TinyEMU | B: QEMU Wasm | C: c2w / WASI | D: v86 |
|---|---|---|---|---|
| guest bitness | x86_64 (binary unreleased) | **x86_64** | x86_64 | 32-bit only |
| what is compiled | CPU + devices + FS | CPU + devices + FS | CPU + devices + FS | **CPU only** |
| imports | 27, namespace `a` | 119 (`env`, `wasi`) | ~1 namespace (WASI) | device callbacks |
| exports | 17, minified `B`–`R` | 112, real names | `_start`, memory | CPU entry points |
| config surface | `vm_start(cfgUrl, …)` | **QEMU argv** | WASI `args`/`env`/`fds` | typed JS options object |
| console | `console_write` callback | xterm-pty patches `Module.TTY` | `wasiHack` on `fd_read`/`fd_write` | JS `uart.js` |
| filesystem | **pull** — C fetches by file ID | **push** — `FS.writeFile` → `-virtfs` | **preopened fds** — JS objects | **JS 9p**, plus `handle9p` hook |
| custom FS needs | wire protocol + recompile | JavaScript only | JavaScript only | JavaScript only |
| concurrency | none, cooperative slices | pthreads + Asyncify | one worker | none |
| `SharedArrayBuffer` | **not needed** | required | required | not needed |
| COOP/COEP headers | **not needed** | required | required | not needed |
| async → sync | never blocks | Asyncify | `Atomics.wait` | FS methods are `async` |
| licence | MIT src; **binary ungranted** | GPLv2 | Apache-2.0 + GPLv2 | BSD-2 |

### 3.2 The four ways to not block the browser

This is the single most confusing part of the field, so, plainly:

1. **Cooperative slicing (TinyEMU).** Run for 10 ms, call
   `emscripten_async_call(self)`, return. Simple, portable, single-threaded, and
   requires no special headers. The emulator author must thread this through the
   design by hand.
2. **Asyncify (QEMU Wasm).** Emscripten rewrites the wasm so functions can save their
   state, return to the event loop, and later resume exactly where they left off. Works
   with unmodified blocking C code; costs code size and speed.
3. **Worker + `Atomics.wait` (c2w).** Move the emulator off the main thread, where
   blocking is legal, and use a `SharedArrayBuffer` to signal. The blocking call is
   real, not simulated. Needs cross-origin isolation.
4. **Be asynchronous natively (v86).** If the device is JavaScript, its methods can be
   `async` and the problem disappears. Only available if you put the devices outside
   wasm in the first place.

### 3.3 The filesystem question, which is the whole game for us

Ranked by how much work a custom, network-backed filesystem costs:

1. **v86** — implement `handle9p`, a documented hook, in async JavaScript. Trivially the
   best design, and unusable at 32-bit.
2. **WASI (c2w)** — implement a `PreopenDirectory`-shaped object. Standard interface,
   but synchronous, so lazy fetching needs the `Atomics.wait` trick.
3. **QEMU Wasm** — write files into `Module.FS` and let `-virtfs` export them. No wire
   protocol. Naturally eager: everything must be materialised in linear memory before
   the guest reads it. A lazy variant would require a custom emscripten FS backend.
4. **TinyEMU** — generate a vfsync manifest, host one blob per file, or interpose a
   service worker to translate. The most work by a wide margin, and altering the
   protocol means recompiling an emulator whose CPU source we do not have.

### 3.4 What this implies

The awkward conclusion: **the customization qualities we want and the 64-bit support we
need are, today, in different projects.** v86 has the best boundary design by a
distance; qemu-wasm is the only open project with a 64-bit guest.

The interesting question that follows — and it is genuinely open — is whether v86's
split (compiled CPU, JavaScript machine, `handle9p`) can be reached at 64 bits. Nobody
has built that. The pieces exist in principle: QEMU's i386 TCG core measures ~1.2 MB of
the qemu-wasm artifact (~12% of its 10.3 MB code section), and TinyEMU demonstrates a
14-function CPU/machine interface. But extracting TCG is hard — it is bound to QEMU's
`CPUState`, `MemoryRegion` and translation-block cache — and the only existing
extraction, [Unicorn Engine](https://github.com/unicorn-engine/unicorn) (GPLv2, QEMU
5.0.1), has no wasm or TCI target in its build at all.

---

## Glossary

- **9p** — a network filesystem protocol, originally from Plan 9, used by
  [virtio-9p](https://www.linux-kvm.org/page/9p_virtio) to share host directories into a
  virtual machine.
- **Asyncify** — an emscripten transform letting compiled code suspend and resume across
  the JavaScript event loop.
- **COOP/COEP** — `Cross-Origin-Opener-Policy` and `Cross-Origin-Embedder-Policy`, the
  HTTP headers that enable `SharedArrayBuffer`. See [web.dev](https://web.dev/articles/coop-coep).
- **Emscripten** — the C/C++ → WebAssembly toolchain.
- **JIT** — just-in-time compilation; translating guest code to host code at runtime.
- **Linear memory** — a wasm module's single flat memory, visible to JS as an `ArrayBuffer`.
- **MTTCG** — Multi-Threaded TCG; QEMU translating and executing on several threads.
- **NAR** — Nix ARchive, the serialisation format used by the nix binary cache.
- **TCG** — Tiny Code Generator, QEMU's translation layer: guest → IR → host.
- **TCI** — TCG Interpreter; interprets the IR instead of compiling it. Slow but portable.
- **Translation Block (TB)** — a run of guest instructions QEMU translates as one unit.
- **W^X** — write XOR execute; memory is writable or executable, never both.
- **WASI** — the WebAssembly System Interface, a standard syscall-like import set.

## References

**Projects**
- QEMU Wasm — https://github.com/ktock/qemu-wasm
- QEMU Wasm demo — https://github.com/ktock/qemu-wasm-demo · live: https://ktock.github.io/qemu-wasm-demo/
- container2wasm — https://github.com/ktock/container2wasm · live: https://ktock.github.io/container2wasm-demo/
- TinyEMU — https://bellard.org/tinyemu/ · JSLinux: https://bellard.org/jslinux/
- v86 — https://github.com/copy/v86 · live: https://copy.sh/v86/
- Unicorn Engine — https://github.com/unicorn-engine/unicorn
- Blink — https://github.com/jart/blink
- WebVM / CheerpX — https://github.com/leaningtech/webvm
- Qemu.js (dead, 2019) — https://github.com/atrosinenko/qemujs

**Talks and documentation**
- "Running QEMU Inside Browser", Kohei Tokunaga, FOSDEM 2025 —
  https://archive.fosdem.org/2025/events/attachments/fosdem-2025-6290-running-qemu-inside-browser/slides/238760/slides_1dDtpcS.pdf
- Emscripten file systems — https://emscripten.org/docs/porting/files/file_systems_overview.html
- Emscripten pthreads — https://emscripten.org/docs/porting/pthreads.html
- Emscripten Asyncify — https://emscripten.org/docs/porting/asyncify.html
- WebAssembly — https://webassembly.org/
- WASI — https://wasi.dev/
- browser_wasi_shim — https://github.com/bjorn3/browser_wasi_shim
- xterm-pty — https://github.com/mame/xterm-pty
- coi-serviceworker — https://github.com/gzuidhof/coi-serviceworker
- `Atomics.wait` — https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Atomics/wait
- Linux x86 boot protocol — https://www.kernel.org/doc/html/latest/arch/x86/boot.html

**Reproducing the deobfuscated glue**
```sh
curl -sSLO https://bellard.org/jslinux/x86_64emu-wasm.js
npx js-beautify --indent-size 2 x86_64emu-wasm.js -o x86_64emu-wasm.beautified.js
```

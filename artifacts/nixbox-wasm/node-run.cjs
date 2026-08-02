// Run TinyEMU's wasm build under node — no browser.
//
// Supplies what the 2018-era js/lib.js and emscripten's async_wget3_data expect
// but modern emscripten no longer provides:
//   Pointer_stringify, Runtime.dynCall, Browser.{wgetRequests,fbuf_table}
// plus term / document / XMLHttpRequest.
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const BASE = process.argv[2] || HERE;
const CFG = process.argv[3] || 'rv.cfg';
const MEM = parseInt(process.argv[4] || '256', 10);
const RUN_MS = parseInt(process.env.RUN_MS || '30000', 10);

let out = '';
const t0 = process.hrtime.bigint();
const ms = () => Number(process.hrtime.bigint() - t0) / 1e6;
const marks = {};
const PATTERNS = {
  first_output: /[^\s]/,
  kernel_banner: /Linux version|riscv/i,
  freeing_init: /Freeing unused kernel memory/,
  shell_prompt: /can't access tty|\/ #/,
  echo_marker: /XREADYX/,
};

globalThis.term = {
  write(s) {
    out += s;
    if (process.env.VERBOSE) process.stdout.write(s);
    for (const [k, re] of Object.entries(PATTERNS))
      if (marks[k] === undefined && re.test(out)) marks[k] = ms();
  },
  getSize: () => [80, 25],
};

globalThis.document = { createElement: () => ({ setAttribute() {}, click() {}, style: {} }),
                        body: { appendChild() {}, removeChild() {} } };
globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
globalThis.update_downloading = () => {};
globalThis.graphic_display = null;
globalThis.net_state = null;

// --- shims for retired emscripten APIs (resolved lazily against the glue's globals)
globalThis.Pointer_stringify = (ptr) => M.UTF8ToString(ptr);
globalThis.Runtime = {
  dynCall: (sig, ptr, args) => (M.getWasmTableEntry ? M.getWasmTableEntry(ptr) : M.wasmTable.get(ptr))(...args),
};
globalThis.Browser = {
  wgetRequests: {},
  nextWget: 1,
  getNextWgetRequestHandle() { return Browser.nextWget++; },
  fbuf_table: {},
  fbuf_next_handle: 1,
};

// --- fs-backed XHR: every URL maps to a file under BASE
globalThis.XMLHttpRequest = class {
  open(m, u) { this._url = u; }
  setRequestHeader() {}
  send() {
    const rel = String(this._url).replace(/^[a-z]+:\/\/[^/]*/i, '').replace(/^\/+/, '');
    const file = path.resolve(BASE, rel);
    setTimeout(() => {
      try {
        const b = fs.readFileSync(file);
        if (process.env.VERBOSE) console.error('[xhr]', this._url, '->', file, b.length, 'bytes');
        this.status = 200;
        this.response = b.buffer.slice(b.byteOffset, b.byteOffset + b.byteLength);
        this.onload && this.onload({});
      } catch (e) {
        console.error('[xhr] MISS', this._url, '->', file);
        this.status = 404;
        (this.onerror || this.onload || (() => {}))({});
      }
    }, 0);
  }
};

let M = null;
const createTinyEMU = require(path.join(HERE, 'js/temu.js'));
createTinyEMU({
  print() {}, printErr(t) { if (process.env.VERBOSE) console.error('[stderr]', t); },
}).then((mod) => {
  M = mod;
  globalThis.Module = mod;                 // js/lib.js callbacks reach HEAPU8 via the glue's own scope
  marks.runtime_ready = ms();
  mod.cwrap('vm_start', null,
    ['string','number','string','string','number','number','number'])(
    CFG, MEM, (process.env.CMDLINE || 'loglevel=3 console=hvc0 root=/dev/vda rw init=/bin/sh'), '', 0, 0, 0);
  marks.vm_start_returned = ms();
}).catch(e => { console.error('init failed:', e && e.message); process.exit(1); });

setTimeout(() => {
  if (!M) return;
  const q = M.cwrap('console_queue_char', null, ['number']);
  for (const c of 'echo XREADYX\n') q(c.charCodeAt(0));
  marks.typed_at = ms();
}, Math.min(RUN_MS - 6000, 12000));

setTimeout(() => {
  console.log('\n===== timings, ms from process start =====');
  for (const k of ['runtime_ready', 'vm_start_returned', 'first_output',
                   'kernel_banner', 'freeing_init', 'shell_prompt', 'typed_at', 'echo_marker'])
    console.log(`  ${k.padEnd(18)} ${marks[k] === undefined ? '—' : marks[k].toFixed(1)}`);
  console.log('\n===== console output (last 14 lines) =====');
  console.log(out.split('\n').slice(-14).join('\n'));
  process.exit(0);
}, RUN_MS);

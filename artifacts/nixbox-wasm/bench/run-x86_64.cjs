// Drive Bellard's published x86_64 jslinux emulator under node, for measurement.
//
// The binary is NOT redistributable -- it is downloaded from bellard.org and
// used here only to benchmark against our own stack. Nothing in this harness
// vendors it.
//
// Same shim set as node-run.cjs (term / document / retired emscripten APIs /
// fs-backed XHR), with two differences: this glue is not MODULARIZE'd, so
// globalThis.Module must exist before it loads, and vm_start takes 8 args.
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const BASE = process.argv[2] || HERE;
const CFG = process.argv[3] || 'http://local/x86-bench.cfg';
const MEM = parseInt(process.argv[4] || '256', 10);
const RUN_MS = parseInt(process.env.RUN_MS || '120000', 10);

let out = '';
const t0 = process.hrtime.bigint();
const ms = () => Number(process.hrtime.bigint() - t0) / 1e6;
const marks = {};
let vmStartedAt = null;

globalThis.term = {
  write(s) {
    out += s;
    if (process.env.VERBOSE) process.stdout.write(s);
    if (marks.first_output === undefined && /\S/.test(s)) marks.first_output = ms();
    const m = /(?:CK|SC)=([0-9a-f]{16})/.exec(out);
    if (m && marks.checksum_at === undefined) {
      marks.checksum_at = ms();
      marks.checksum = m[1];
      finish();
    }
  },
  getSize: () => [80, 25],
};

globalThis.document = { createElement: () => ({ setAttribute() {}, click() {}, style: {} }),
                        body: { appendChild() {}, removeChild() {} } };
globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
globalThis.update_downloading = () => {};
globalThis.graphic_display = null;
globalThis.net_state = null;
globalThis.Pointer_stringify = (ptr) => Module.UTF8ToString(ptr);
globalThis.Runtime = {
  dynCall: (sig, ptr, args) =>
    (Module.getWasmTableEntry ? Module.getWasmTableEntry(ptr) : Module.wasmTable.get(ptr))(...args),
};
globalThis.Browser = {
  wgetRequests: {}, nextWget: 1,
  getNextWgetRequestHandle() { return globalThis.Browser.nextWget++; },
  fbuf_table: {}, fbuf_next_handle: 1,
};

let xhrBytes = 0, xhrCount = 0;
globalThis.XMLHttpRequest = class {
  open(m, u) { this._url = u; }
  setRequestHeader() {}
  send() {
    const rel = String(this._url).split('?')[0].replace(/^[a-z]+:\/\/[^/]*/i, '').replace(/^\/+/, '');
    const file = path.resolve(BASE, rel);
    setTimeout(() => {
      try {
        const b = fs.readFileSync(file);
        xhrCount++; xhrBytes += b.length;
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

const EMU_DIR = process.env.EMU_DIR || path.resolve(HERE, '..');

// This glue is not MODULARIZE'd: `var Module = typeof Module != "undefined" ? Module : {}`
// hoists Module as a module-local, so typeof sees the *local* (undefined) and the
// injected config is silently discarded -- the same trap as TinyEMU's build. The
// object require() returns IS the glue's Module, and createWasm() is async, so
// hooks attached immediately after require still land before the runtime is up.
// The wasm path is resolved by scriptDirectory, hence the sibling symlink.

let finished = false;
function finish() {
  if (finished) return;
  finished = true;
  const boot = marks.first_output, ck = marks.checksum_at;
  console.log(JSON.stringify({
    label: process.env.LABEL || 'x86_64emu',
    runtime_ready_ms: marks.runtime_ready,
    first_output_ms: boot,
    checksum_ms: ck,
    since_vm_start_ms: ck !== undefined && vmStartedAt !== null ? ck - vmStartedAt : null,
    checksum: marks.checksum || null,
    xhr_requests: xhrCount, xhr_bytes: xhrBytes,
  }, null, 1));
  process.exit(0);
}

const Module = require(path.resolve(EMU_DIR, 'x86_64emu-wasm.js'));
globalThis.Module = Module;
Module.onRuntimeInitialized = () => {
  marks.runtime_ready = ms();
  vmStartedAt = ms();
  Module.cwrap('vm_start', null,
    ['string', 'number', 'string', 'string', 'number', 'number', 'number', 'string'])(
    CFG, MEM, process.env.CMDLINE || '', '', 0, 0, 0, '');
  marks.vm_start_returned = ms();
};

setTimeout(() => {
  console.log(JSON.stringify({
    label: process.env.LABEL || 'x86_64emu', timeout: true,
    checksum: marks.checksum || null, xhr_requests: xhrCount, xhr_bytes: xhrBytes,
    tail: out.split('\n').slice(-12),
  }, null, 1));
  process.exit(1);
}, RUN_MS);

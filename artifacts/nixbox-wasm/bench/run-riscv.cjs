// Same measurement as run-x86_64.cjs, but for our stack:
//   wasm -> TinyEMU riscv64 -> riscv64 Linux -> qemu-x86_64 -> the x86_64 binary
//
// Both cpubench variants run in one guest session, so the N=0 run subtracts
// qemu-user startup and 9p load and leaves the pure interpreted compute.
// Times are taken here on the host as console bytes arrive, never in the guest.
const fs = require('fs');
const path = require('path');

const HERE = __dirname;
const BASE = process.argv[2] || HERE;
const CFG = process.argv[3] || 'http://local/rv-bench.cfg';
const MEM = parseInt(process.argv[4] || '256', 10);
const RUN_MS = parseInt(process.env.RUN_MS || '600000', 10);
const TEMU_DIR = process.env.TEMU_DIR || path.resolve(HERE, '../tinyemu-2019-12-21');

let out = '';
const t0 = process.hrtime.bigint();
const ms = () => Number(process.hrtime.bigint() - t0) / 1e6;
const cks = [];        // [{ck, at}] in arrival order
let promptAt = null, typedAt = null;

globalThis.term = {
  write(s) {
    out += s;
    if (process.env.VERBOSE) process.stdout.write(s);
    if (promptAt === null && /can't access tty|\/ #/.test(out)) promptAt = ms();
    let m;
    const re = /(?:CK|SC)=([0-9a-f]{16})/g;
    while ((m = re.exec(out)) !== null) {
      if (!cks.some(c => c.idx === m.index))
        cks.push({ idx: m.index, ck: m[1], at: ms() });
    }
    if (cks.length >= 3) finish();
  },
  getSize: () => [80, 25],
};

globalThis.document = { createElement: () => ({ setAttribute() {}, click() {}, style: {} }),
                        body: { appendChild() {}, removeChild() {} } };
globalThis.btoa = (s) => Buffer.from(s, 'binary').toString('base64');
globalThis.update_downloading = () => {};
globalThis.graphic_display = null;
globalThis.net_state = null;
globalThis.Pointer_stringify = (ptr) => M.UTF8ToString(ptr);
globalThis.Runtime = {
  dynCall: (sig, ptr, args) =>
    (M.getWasmTableEntry ? M.getWasmTableEntry(ptr) : M.wasmTable.get(ptr))(...args),
};
globalThis.Browser = {
  wgetRequests: {}, nextWget: 1,
  getNextWgetRequestHandle() { return globalThis.Browser.nextWget++; },
  fbuf_table: {}, fbuf_next_handle: 1,
};

globalThis.XMLHttpRequest = class {
  open(m, u) { this._url = u; }
  setRequestHeader() {}
  send() {
    const rel = String(this._url).split('?')[0].replace(/^[a-z]+:\/\/[^/]*/i, '').replace(/^\/+/, '');
    const file = path.resolve(BASE, rel);
    setTimeout(() => {
      try {
        const b = fs.readFileSync(file);
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

let finished = false;
function finish() {
  if (finished) return;
  finished = true;
  // a: mount + qemu-user startup;  b: qemu-user startup alone;
  // c: qemu-user startup + compute.  compute = (c - b).
  const [a, b, c] = cks;
  console.log(JSON.stringify({
    label: process.env.LABEL || 'nested-riscv64',
    prompt_ms: promptAt,
    mount_plus_startup_ms: a ? a.at - typedAt : null,
    startup_ms: b ? b.at - a.at : null,
    startup_plus_compute_ms: c ? c.at - b.at : null,
    compute_ms: (b && c) ? (c.at - b.at) - (b.at - a.at) : null,
    checksums: cks.map(x => x.ck),
  }, null, 1));
  process.exit(0);
}

let M = null;
const createTinyEMU = require(path.join(TEMU_DIR, 'js/temu.js'));
createTinyEMU({ print() {}, printErr(t) { if (process.env.VERBOSE) console.error('[stderr]', t); } })
  .then((mod) => {
    M = mod;
    globalThis.Module = mod;
    mod.cwrap('vm_start', null,
      ['string','number','string','string','number','number','number'])(
      CFG, MEM, 'console=hvc0 root=/dev/vda rw init=/bin/sh', '', 0, 0, 0);
  })
  .catch(e => { console.error('init failed:', e && e.message); process.exit(1); });

// wait for the prompt, then run both binaries back to back
const poll = setInterval(() => {
  if (promptAt === null || !M) return;
  clearInterval(poll);
  const q = M.cwrap('console_queue_char', null, ['number']);
  const script =
    'mount -t 9p -o trans=virtio,version=9p2000.L,msize=131072 /dev/root /mnt\n' +
    `/mnt/qemu-x86_64 /mnt/${process.env.BIN0 || 'cpubench-0'}\n` +
    `/mnt/qemu-x86_64 /mnt/${process.env.BIN0 || 'cpubench-0'}\n` +
    `/mnt/qemu-x86_64 /mnt/${process.env.BINN || 'cpubench-20000000'}\n`;
  typedAt = ms();
  for (const c of script) q(c.charCodeAt(0));
}, 50);

setTimeout(() => {
  console.log(JSON.stringify({
    label: process.env.LABEL || 'nested-riscv64', timeout: true,
    checksums: cks.map(c => c.ck), tail: out.split('\n').slice(-12),
  }, null, 1));
  process.exit(1);
}, RUN_MS);

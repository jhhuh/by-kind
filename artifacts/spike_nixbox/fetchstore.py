"""Assemble a /nix/store from cache.nixos.org alone -- no nix, no daemon.

This is the exact logic a browser would run: resolve a package to its store
path, walk References to get the closure, fetch each NAR over plain HTTP,
parse it, and materialise the files. Everything here ports to JS; nothing
depends on a local nix installation.
"""
import json, os, struct, subprocess, sys, urllib.request
from pathlib import Path

BASE = "https://cache.nixos.org"

def get(url):
    with urllib.request.urlopen(url, timeout=300) as r:
        return r.read()

def narinfo(h, _cache={}):
    if h not in _cache:
        txt = get(f"{BASE}/{h}.narinfo").decode()
        d = {}
        for line in txt.splitlines():
            k, _, v = line.partition(": ")
            d[k] = v
        _cache[h] = d
    return _cache[h]

def closure(roots):
    """Transitive References. Same walk a browser-side resolver would do."""
    seen, queue, order = set(), list(roots), []
    while queue:
        h = queue.pop()
        if h in seen:
            continue
        seen.add(h)
        try:
            info = narinfo(h)
        except Exception as e:
            print(f"  !! {h}: {e}", file=sys.stderr); continue
        order.append(info)
        for ref in info.get("References", "").split():
            rh = ref.split("-")[0]
            if rh not in seen:
                queue.append(rh)
    return order

class Nar:
    def __init__(self, b): self.b, self.i = b, 0
    def u64(self):
        v = struct.unpack_from("<Q", self.b, self.i)[0]; self.i += 8; return v
    def s(self):
        n = self.u64(); v = self.b[self.i:self.i+n]; self.i += (n + 7) & ~7; return v
    def expect(self, w):
        g = self.s()
        if g != w: raise ValueError(f"want {w!r} got {g!r}")

def extract(n, dest: Path):
    n.expect(b"("); n.expect(b"type")
    kind = n.s()
    if kind == b"regular":
        tag = n.s(); ex = False
        if tag == b"executable":
            n.s(); ex = True; tag = n.s()
        data = n.s()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(data)
        if ex: dest.chmod(0o555)
        else:  dest.chmod(0o444)
    elif kind == b"symlink":
        n.expect(b"target"); target = n.s().decode()
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.is_symlink() or dest.exists(): dest.unlink()
        dest.symlink_to(target)
    elif kind == b"directory":
        dest.mkdir(parents=True, exist_ok=True)
        while True:
            tag = n.s()
            if tag == b")": return
            n.expect(b"("); n.expect(b"name"); name = n.s().decode()
            n.expect(b"node"); extract(n, dest / name); n.expect(b")")
    n.expect(b")")

def materialise(info, root: Path):
    store_path = info["StorePath"]                     # /nix/store/<hash>-<name>
    dest = root / store_path.lstrip("/")
    if dest.exists():
        return 0
    raw = get(f"{BASE}/{info['URL']}")
    comp = info.get("Compression", "none")
    if comp == "zstd":
        plain = subprocess.run(["zstd", "-d", "-c"], input=raw, capture_output=True).stdout
    elif comp == "xz":
        plain = subprocess.run(["xz", "-d", "-c"], input=raw, capture_output=True).stdout
    else:
        plain = raw
    n = Nar(plain); n.expect(b"nix-archive-1")
    extract(n, dest)
    return len(raw)

if __name__ == "__main__":
    root = Path(sys.argv[1]); store_paths = sys.argv[2:]
    roots, paths = [], {}
    for out in store_paths:
        name = Path(out).name.split("-", 1)[1]
        paths[name] = out
        roots.append(Path(out).name.split("-")[0])
        print(f"{name:<26} {out}")
    print("\nresolving closure ...")
    infos = closure(roots)
    total = sum(int(i.get("NarSize", 0)) for i in infos)
    print(f"closure: {len(infos)} store paths, {total/1e6:.1f} MB uncompressed")
    got = 0
    for k, info in enumerate(infos, 1):
        got += materialise(info, root)
        print(f"  [{k}/{len(infos)}] {info['StorePath'][11:60]}", end="\r", flush=True)
    print(f"\ndownloaded {got/1e6:.1f} MB compressed into {root}")
    json.dump(paths, open(root / "paths.json", "w"), indent=1)

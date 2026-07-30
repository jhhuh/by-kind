"""Stage ① — acquire: build data/packages.jsonl, one row per by-name package.

Three sources, joined on the by-name package name:

  git ls-tree HEAD -- pkgs/by-name    authoritative name set (21,511 at HEAD)
  package.nix blobs                   structural signals (builder fn, desktop item)
  channel packages.json.br            descriptions and meta

No nixpkgs evaluation is required: `meta.position` in the channel dump already
identifies which attributes originate in by-name.

Two facts drive the join logic and are easy to get wrong:

  * The unit is the *directory*, not the attribute. Scoped sets re-export the same
    file, which inflates 21,327 packages into 29,564 attributes. Dedupe on the
    `meta.position` path and prefer the dot-free top-level attribute.
  * The channel snapshot lags git master, so ~1.2% of packages have no channel
    entry. Those are kept, marked, and counted — never silently dropped.

Usage:
    nix develop -c python3 src/acquire.py                     # fetch everything
    nix develop -c python3 src/acquire.py --packages-json X   # reuse a local dump
"""
from __future__ import annotations

import argparse
import collections
import json
import re
import subprocess
import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "nixpkgs"
OUT = ROOT / "data" / "packages.jsonl"
NIXPKGS_URL = "https://github.com/NixOS/nixpkgs.git"
CHANNEL_URL = "https://channels.nixos.org/nixpkgs-unstable/packages.json.br"

# Structural signals grepped from package.nix. Each becomes a feature token in
# stage ③; the *weights* are learned there, not asserted here.
BUILDERS = [
    "buildGoModule", "buildNpmPackage", "buildPythonApplication",
    "buildPythonPackage", "rustPlatform.buildRustPackage", "buildRustPackage",
    "buildDotnetModule", "buildDunePackage", "mkYarnPackage", "buildNimPackage",
    "buildHomeAssistantComponent", "buildKodiAddon", "vimUtils.buildVimPlugin",
    "buildLinux", "buildFHSEnv", "appimageTools.wrapType2",
    "appimageTools.wrapType1", "writeShellApplication", "symlinkJoin",
    "buildEnv", "runCommand", "dockerTools", "stdenvNoCC.mkDerivation",
    "stdenv.mkDerivation", "mkDerivation", "fetchzip", "fetchurl",
]
# Longest-first so `stdenvNoCC.mkDerivation` wins over `mkDerivation`.
BUILDER_RE = re.compile(
    "|".join(re.escape(b) for b in sorted(BUILDERS, key=len, reverse=True))
)
DESKTOP_RE = re.compile(r"\b(makeDesktopItem|copyDesktopItems|desktopItems)\b")
TOOLKIT_RE = re.compile(
    r"\b(wrapQtAppsHook|wrapGAppsHook\w*|libsForQt5|qt6\.|qt5\.|gtk4|gtk3|SDL2|electron)\b"
)
SERVICE_RE = re.compile(r"\b(nixosTests|passthru\.tests|systemd)\b")


def run(args: list[str], cwd: Path | None = None) -> str:
    result = subprocess.run(
        args, cwd=cwd, check=True, text=True, capture_output=True
    )
    return result.stdout


# --------------------------------------------------------------------------
# sources
# --------------------------------------------------------------------------
def ensure_nixpkgs(rev: str | None) -> str:
    """Blobless sparse clone of nixpkgs; returns the HEAD sha.

    --filter=blob:none + --sparse keeps this at ~42 MB / ~6 s even though it
    materialises every package.nix file.
    """
    if not (VENDOR / ".git").exists():
        VENDOR.parent.mkdir(parents=True, exist_ok=True)
        print(f"cloning nixpkgs into {VENDOR} ...", file=sys.stderr)
        run(["git", "clone", "--depth=1", "--filter=blob:none", "--sparse",
             NIXPKGS_URL, str(VENDOR)])
        run(["git", "sparse-checkout", "set", "pkgs/by-name"], cwd=VENDOR)
    else:
        run(["git", "fetch", "--depth=1", "origin",
             rev or "HEAD"], cwd=VENDOR)
        run(["git", "reset", "--hard", "FETCH_HEAD"], cwd=VENDOR)
    return run(["git", "rev-parse", "HEAD"], cwd=VENDOR).strip()


def list_by_name() -> dict[str, str]:
    """name -> repo-relative package.nix path, from git (authoritative)."""
    out = run(["git", "ls-tree", "-r", "--name-only", "HEAD", "--",
               "pkgs/by-name"], cwd=VENDOR)
    packages = {}
    for line in out.splitlines():
        if not line.endswith("/package.nix"):
            continue
        parts = line.split("/")
        if len(parts) != 5:  # pkgs/by-name/<shard>/<name>/package.nix
            continue
        packages[parts[3]] = line
    return packages


def structural_signals(paths: dict[str, str]) -> dict[str, dict]:
    """Grep each package.nix for builder function and GUI/service markers."""
    signals = {}
    for name, rel in paths.items():
        try:
            text = (VENDOR / rel).read_text(errors="replace")
        except OSError:
            signals[name] = {}
            continue
        signals[name] = {
            "builders": sorted(set(BUILDER_RE.findall(text))),
            "desktop_item": bool(DESKTOP_RE.search(text)),
            "gui_toolkit": sorted(set(m[0] if isinstance(m, tuple) else m
                                      for m in TOOLKIT_RE.findall(text))),
            "service_markers": bool(SERVICE_RE.search(text)),
            "nix_bytes": len(text),
        }
    return signals


JQ_FILTER = r"""
.packages | to_entries[]
| select(.value.meta.position // "" | startswith("pkgs/by-name/"))
| {
    attr: .key,
    position: .value.meta.position,
    description: (.value.meta.description // null),
    homepage: ([.value.meta.homepage] | flatten
               | map(select(type == "string")) | first // null),
    main_program: (.value.meta.mainProgram // null),
    broken: (.value.meta.broken // false),
    unfree: (.value.meta.unfree // false),
    available: (.value.meta.available // true),
    license: ([.value.meta.license] | flatten
              | map(if type=="object" then (.spdxId // .shortName // empty)
                    elif type=="string" then . else empty end) | unique),
    platforms: ([.value.meta.platforms // []] | flatten
                | map(select(type=="string") | split("-") | last) | unique)
  }
"""


def load_channel(packages_json: Path) -> tuple[dict[str, list[dict]], dict[str, list[dict]]]:
    """Index the channel dump two ways: by position path, and by attribute name.

    Both are needed. `meta.position` records where a derivation was *defined*,
    which for alias and override packages is a DIFFERENT file than their own
    by-name directory:

        abseil-cpp  -> pkgs/by-name/ab/abseil-cpp_202601/package.nix  (alias)
        _7zz-rar    -> pkgs/by-name/_7/_7zz/package.nix               (override)

    Joining on path alone silently loses those (~200 packages, enough to push
    coverage below the 98% floor). The name index recovers them.
    """
    proc = subprocess.run(
        ["jq", "-c", JQ_FILTER, str(packages_json)],
        check=True, text=True, capture_output=True,
    )
    by_path: dict[str, list[dict]] = collections.defaultdict(list)
    by_name: dict[str, list[dict]] = collections.defaultdict(list)
    for line in proc.stdout.splitlines():
        record = json.loads(line)
        by_path[record["position"].split(":")[0]].append(record)
        if "." not in record["attr"]:  # top-level attrs only
            by_name[record["attr"]].append(record)
    return by_path, by_name


def download_channel(dest: Path) -> Path:
    """Fetch and brotli-decompress the channel dump."""
    dest.parent.mkdir(parents=True, exist_ok=True)
    compressed = dest.with_suffix(".json.br")
    print(f"downloading {CHANNEL_URL} ...", file=sys.stderr)
    with urllib.request.urlopen(CHANNEL_URL, timeout=300) as response:
        compressed.write_bytes(response.read())
    subprocess.run(["brotli", "-d", "-f", "-o", str(dest), str(compressed)],
                   check=True)
    compressed.unlink()
    return dest


# --------------------------------------------------------------------------
# join
# --------------------------------------------------------------------------
def pick_canonical(records: list[dict]) -> dict:
    """Prefer the dot-free top-level attribute; scoped re-exports are aliases.

    Deterministic: ties break on attribute name, never on dict order.
    """
    return sorted(records, key=lambda r: (r["attr"].count("."), r["attr"]))[0]


def join(paths: dict[str, str], signals: dict[str, dict],
         channel: dict[str, list[dict]], head: str,
         by_name: dict[str, list[dict]] | None = None
         ) -> tuple[list[dict], list[str]]:
    """Join git's name set against the channel: by position path, then by name."""
    by_name = by_name or {}
    rows, missing = [], []
    for name in sorted(paths):
        rel = paths[name]
        records, how = channel.get(rel), "position"
        if not records:
            # Alias/override packages point their position at another file.
            records, how = by_name.get(name), "name"
        row = {
            "name": name,
            "path": rel,
            "nixpkgs_rev": head,
            "structural": signals.get(name, {}),
        }
        if records:
            canonical = pick_canonical(records)
            row.update({
                "attr": canonical["attr"],
                "attr_count": len(records),
                "matched_by": how,
                "description": canonical["description"],
                "homepage": canonical["homepage"],
                "main_program": canonical["main_program"],
                "license": canonical["license"],
                "platforms": canonical["platforms"],
                "broken": canonical["broken"],
                "unfree": canonical["unfree"],
                "source": "channel",
            })
        else:
            # Absent from the channel: broken, platform-excluded, or newer than
            # the snapshot. Still scored in stage ④ from name + package.nix.
            missing.append(name)
            row.update({
                "attr": None, "attr_count": 0, "description": None,
                "homepage": None, "main_program": None, "license": [],
                "platforms": [], "broken": None, "unfree": None,
                "source": "missing",
            })
        rows.append(row)
    return rows, missing


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages-json", type=Path,
                    help="reuse a local decompressed channel dump")
    ap.add_argument("--rev", help="nixpkgs revision (default: current master)")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    head = ensure_nixpkgs(args.rev)
    paths = list_by_name()
    print(f"nixpkgs {head[:12]}  by-name packages: {len(paths)}", file=sys.stderr)

    signals = structural_signals(paths)

    packages_json = args.packages_json or download_channel(ROOT / "data" / "packages.json")
    by_path, by_name = load_channel(packages_json)

    rows, missing = join(paths, signals, by_path, head, by_name)

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("w") as handle:
        for row in rows:  # rows are name-sorted -> byte-stable output
            handle.write(json.dumps(row, sort_keys=True) + "\n")

    matched = len(rows) - len(missing)
    coverage = matched / len(rows) if rows else 0.0
    described = sum(1 for r in rows if r["description"])
    with_builder = sum(1 for r in rows if r["structural"].get("builders"))
    desktop = sum(1 for r in rows if r["structural"].get("desktop_item"))

    print(f"\nwrote {args.out}  ({len(rows)} rows)")
    print(f"  channel coverage : {matched}/{len(rows)} = {coverage:.1%}")
    print(f"  with description : {described}/{len(rows)} = {described/len(rows):.1%}")
    print(f"  with builder fn  : {with_builder}/{len(rows)} = {with_builder/len(rows):.1%}")
    print(f"  desktop item     : {desktop}/{len(rows)} = {desktop/len(rows):.1%}")
    by_pos = sum(1 for r in rows if r.get("matched_by") == "position")
    by_nm = sum(1 for r in rows if r.get("matched_by") == "name")
    print(f"  matched by position/name : {by_pos} / {by_nm}")
    print(f"  MISSING from channel: {len(missing)}"
          + (f"  e.g. {', '.join(missing[:5])}" if missing else ""))

    if coverage < 0.98:
        print(f"\nERROR: channel coverage {coverage:.1%} < 98% floor", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()

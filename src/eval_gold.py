"""Evaluate against the hand-labelled by-name gold set.

Every other number in this repo is measured on the LEGACY tree. This is the only
in-distribution measurement -- and it is the one that decides whether the output
is usable, because the legacy corpus and pkgs/by-name differ sharply.

Usage:  nix develop -c python3 src/eval_gold.py
"""
import argparse, sqlite3, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "tests" / "fixtures" / "gold_by_name.tsv"
DB = ROOT / "data" / "categories.sqlite"

_ap = argparse.ArgumentParser()
_ap.add_argument("--min-kind", type=float, default=None,
                 help="exit non-zero if kind top-1 falls below this (CI gate)")
_args = _ap.parse_args()

gold = {}
for line in GOLD.read_text().splitlines():
    if line.startswith("#") or not line.strip():
        continue
    parts = line.split("#")[0].strip().split("\t")
    if len(parts) >= 3:
        gold[parts[0]] = (parts[1].strip(), parts[2].strip())

conn = sqlite3.connect(DB)
rows = {n: (d, k, dc, alt, kc, kalt) for n, d, k, dc, alt, kc, kalt in conn.execute(
    "SELECT name, domain, kind, domain_confidence, domain_alternates,"
    " kind_confidence, kind_alternates FROM packages")}

hit_d = hit_k = hit_both = hit_d3 = n = 0
conf_n = conf_hit = 0
misses = []
for name, (gd, gk) in sorted(gold.items()):
    if name not in rows:
        continue
    pd, pk, dc, alt, kc, kalt = rows[name]
    n += 1
    ok_d, ok_k = pd == gd, pk == gk
    hit_d += ok_d; hit_k += ok_k; hit_both += ok_d and ok_k
    hit_d3 += gd in __import__("json").loads(alt or "[]")
    if dc == "confident":
        conf_n += 1; conf_hit += ok_d
    if not ok_d:
        misses.append((name, gd, pd, dc))

print(f"gold set: {n} hand-labelled by-name packages\n")
print(f"  domain top-1 : {hit_d}/{n} = {hit_d/n:.1%}")
print(f"  domain top-3 : {hit_d3}/{n} = {hit_d3/n:.1%}")
print(f"  kind   top-1 : {hit_k}/{n} = {hit_k/n:.1%}")
print(f"  both         : {hit_both}/{n} = {hit_both/n:.1%}")
if conf_n:
    print(f"\n  of those marked `confident`: {conf_hit}/{conf_n} = {conf_hit/conf_n:.1%} correct"
          "   <-- the tier's promise is 95%")
# Tier calibration per facet. The domain tier scored 0/12 on gold; kind's must
# be checked before shipping, or the same mistake ships with a quality badge.
print("\nTIER CALIBRATION ON GOLD (the promise is 95% for `confident`):")
for facet, idx, cidx in (("domain", 0, 2), ("kind", 1, 4)):
    buckets = {}
    for name, (gd, gk) in gold.items():
        if name not in rows: continue
        truth = gd if facet == "domain" else gk
        pred = rows[name][idx]; tier = rows[name][cidx]
        b = buckets.setdefault(tier, [0, 0]); b[1] += 1; b[0] += pred == truth
    for tier in ("confident", "probable", "uncertain", "none"):
        if tier in buckets:
            hit, tot = buckets[tier]
            print(f"  {facet:<7} {tier:<11} {hit}/{tot} = {hit/tot:6.1%}")

print(f"\nmost common wrong domain predictions:")
for pred, c in Counter(p for _, _, p, _ in misses).most_common(5):
    print(f"  predicted {pred:<16} {c} times")
if _args.min_kind is not None:
    if hit_k / n < _args.min_kind:
        print(f"\nFAIL: kind top-1 {hit_k/n:.1%} below floor {_args.min_kind:.1%}",
              file=sys.stderr)
        raise SystemExit(1)
    print(f"\nOK: kind top-1 {hit_k/n:.1%} >= floor {_args.min_kind:.1%}")

print(f"\nfirst 12 domain misses:")
for name, gd, pd, dc in misses[:12]:
    print(f"  {name:<20} gold {gd:<14} got {pd:<14} ({dc})")

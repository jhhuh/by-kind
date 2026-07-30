"""Evaluate against the hand-labelled by-name gold set.

Every other number in this repo is measured on the LEGACY tree. This is the only
in-distribution measurement -- and it is the one that decides whether the output
is usable, because the legacy corpus and pkgs/by-name differ sharply.

Usage:  nix develop -c python3 src/eval_gold.py
"""
import sqlite3, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
GOLD = ROOT / "tests" / "fixtures" / "gold_by_name.tsv"
DB = ROOT / "data" / "categories.sqlite"

gold = {}
for line in GOLD.read_text().splitlines():
    if line.startswith("#") or not line.strip():
        continue
    parts = line.split("#")[0].strip().split("\t")
    if len(parts) >= 3:
        gold[parts[0]] = (parts[1].strip(), parts[2].strip())

conn = sqlite3.connect(DB)
rows = {n: (d, k, dc, alt) for n, d, k, dc, alt in conn.execute(
    "SELECT name, domain, kind, domain_confidence, domain_alternates FROM packages")}

hit_d = hit_k = hit_both = hit_d3 = n = 0
conf_n = conf_hit = 0
misses = []
for name, (gd, gk) in sorted(gold.items()):
    if name not in rows:
        continue
    pd, pk, dc, alt = rows[name]
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
print(f"\nmost common wrong domain predictions:")
for pred, c in Counter(p for _, _, p, _ in misses).most_common(5):
    print(f"  predicted {pred:<16} {c} times")
print(f"\nfirst 12 domain misses:")
for name, gd, pd, dc in misses[:12]:
    print(f"  {name:<20} gold {gd:<14} got {pd:<14} ({dc})")

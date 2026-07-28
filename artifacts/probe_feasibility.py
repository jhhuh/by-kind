"""Feasibility probe for the cat-nixpkgs design (2026-07-28).

Measures whether description-only log-odds weights, trained on nixpkgs' own
legacy directory tree, separate categories well enough to be worth building on.
Descriptions only: no structural features, no bigrams, no tuning.

Result on 2026-07-28 (nixpkgs-unstable snapshot):
    top-1 accuracy   : 76.3%   (2,004 held-out)
    top-3 accuracy   : 84.3%
    high-margin (>=4): covers 54.7% of the set at 97.4% accuracy

Usage:
    curl -sL https://channels.nixos.org/nixpkgs-unstable/packages.json.br \\
      | brotli -d > packages.json
    python3 artifacts/probe_feasibility.py packages.json
"""
import json, re, math, random, sys
from collections import Counter, defaultdict

P = sys.argv[1] if len(sys.argv) > 1 else "packages.json"
d=json.load(open(P))["packages"]

# legacy leaf packages only: exclude module sets (they'd swamp everything into "library")
BAD=re.compile(r'(-modules|-packages|python-modules|haskell-modules|r-modules|lisp-modules)')
rows=[]
for k,v in d.items():
    pos=(v.get("meta") or {}).get("position") or ""
    desc=(v.get("meta") or {}).get("description") or ""
    if not pos.startswith("pkgs/") or pos.startswith("pkgs/by-name/"): continue
    if BAD.search(pos) or not desc: continue
    parts=pos.split("/")
    if len(parts)<3: continue
    lab="/".join(parts[1:3])
    rows.append((lab,desc))

# dedupe identical (label, desc) — versioned duplicates would inflate counts
# sorted(), not list(): set iteration order varies with PYTHONHASHSEED, which
# would reshuffle the train/test split on every run despite the fixed seed.
rows=sorted({(l,dd) for l,dd in rows})
cnt=Counter(l for l,_ in rows)
keep={l for l,c in cnt.items() if c>=40}
rows=[(l,dd) for l,dd in rows if l in keep]
print(f"labeled examples: {len(rows)}   categories kept (>=40 ex): {len(keep)}\n")

TOK=re.compile(r"[a-z][a-z0-9+#-]{2,}")
STOP=set("the and for with that this from you your are was има can its it's not use used using based which their there other more than into over out via".split())
def tok(s): return {w for w in TOK.findall(s.lower()) if w not in STOP}

random.seed(0); random.shuffle(rows)
cut=int(len(rows)*0.8); train,test=rows[:cut],rows[cut:]

df=Counter(); dfc=defaultdict(Counter); N=len(train); Nc=Counter()
for lab,desc in train:
    ws=tok(desc); Nc[lab]+=1
    for w in ws: df[w]+=1; dfc[lab][w]+=1

VOCAB={w for w,c in df.items() if c>=5}
# log-odds weight: how much does seeing w shift the posterior toward c
W=defaultdict(dict)
for c in Nc:
    for w in VOCAB:
        a=dfc[c][w]+0.5; b=Nc[c]-dfc[c][w]+0.5
        x=df[w]-dfc[c][w]+0.5; y=(N-Nc[c])-(df[w]-dfc[c][w])+0.5
        W[c][w]=math.log((a/b)/(x/y))

print("=== top discriminative words per category (learned, not authored) ===")
for c in sorted(list(Nc), key=lambda z:-Nc[z])[:9]:
    top=sorted(((w,s) for w,s in W[c].items() if dfc[c][w]>=4), key=lambda t:-t[1])[:9]
    print(f"{c:34s} {', '.join(w for w,_ in top)}")

prior={c: math.log(Nc[c]/N) for c in Nc}
def pred(desc):
    ws=tok(desc)&VOCAB
    sc={c: prior[c]+sum(W[c].get(w,0) for w in ws) for c in Nc}
    r=sorted(sc.items(), key=lambda t:-t[1])
    return r[0][0], r[0][1]-r[1][1], [x[0] for x in r[:3]]

ok=top3=0; hi_ok=hi_n=0
for lab,desc in test:
    p,margin,t3=pred(desc)
    ok+=(p==lab); top3+=(lab in t3)
    if margin>=4: hi_n+=1; hi_ok+=(p==lab)
print(f"\ntop-1 accuracy : {ok/len(test):.1%}   ({len(test)} held-out)")
print(f"top-3 accuracy : {top3/len(test):.1%}")
print(f"high-margin(>=4): covers {hi_n/len(test):.1%} of set at {hi_ok/max(hi_n,1):.1%} accuracy")

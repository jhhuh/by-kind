"""Does the domain collapse come from prior shift rather than vocabulary?

Stage 4 measured 13.8% domain accuracy on the by-name gold set against 75.1% on
legacy held-out. The misses concentrate on `development` and `desktop` -- the two
largest training priors -- which is the signature of PRIOR SHIFT, not of the
model failing to recognise words.

The legacy prior is known to be unrepresentative of by-name (69% of the corpus is
four package families). If that is the cause, dropping the prior at inference
should recover accuracy with no new labels at all.

Sweeps shrinkage x prior treatment and scores the held-out gold set.
"""
import json, math, sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import train, features as F

gold = {}
for line in (ROOT / "tests/fixtures/gold_by_name.tsv").read_text().splitlines():
    if line.startswith("#") or not line.strip():
        continue
    p = line.split("#")[0].strip().split("\t")
    if len(p) >= 3:
        gold[p[0]] = (p[1].strip(), p[2].strip())

rows = {}
for line in (ROOT / "data/packages.jsonl").read_text().splitlines():
    r = json.loads(line)
    if r["name"] in gold:
        rows[r["name"]] = r
print(f"gold packages resolved: {len(rows)}/{len(gold)}\n")

_t, mapping = train.load_mapping()
corpus = train.cap_families(train.build_corpus(
    Path(sys.argv[1]), mapping), 25)


def build(facet, shrink):
    data = [(r[facet], F.featurize(r, True)) for r in corpus if r.get(facet)]
    counts = Counter(c for c, _ in data)
    keep = {c for c, n in counts.items() if n >= train.MIN_EXAMPLES}
    data = [(c, t) for c, t in data if c in keep]
    df, df_c, n_c = Counter(), {}, Counter()
    for c, toks in data:
        n_c[c] += 1
        df_c.setdefault(c, Counter())
        for t in toks:
            df[t] += 1; df_c[c][t] += 1
    vocab = {t for t, n in df.items() if n >= train.MIN_DF}
    total = len(data)
    W = {}
    for c in n_c:
        W[c] = {}
        for t in vocab:
            s = df_c[c][t]
            raw = math.log(((s + .5) / (n_c[c] - s + .5)) /
                           ((df[t] - s + .5) / ((total - n_c[c]) - (df[t] - s) + .5)))
            W[c][t] = raw * (s / (s + shrink)) if shrink else raw
    prior = {c: math.log(n_c[c] / total) for c in n_c}
    return W, prior, vocab


print(f"{'facet':<8}{'shrink':>7}{'prior':>10}{'top-1':>8}{'top-3':>8}   most-over-predicted")
print("-" * 74)
for facet in ("domain", "kind"):
    for shrink in (0.0, 2.0, 5.0, 20.0):
        W, prior, vocab = build(facet, shrink)
        for mode in ("trained", "uniform"):
            pri = prior if mode == "trained" else {c: 0.0 for c in prior}
            hit = hit3 = n = 0
            wrong = Counter()
            for name, (gd, gk) in gold.items():
                if name not in rows:
                    continue
                truth = gd if facet == "domain" else gk
                toks = F.featurize(rows[name], True) & vocab
                if not toks:
                    continue
                n += 1
                ranked = sorted(((c, pri[c] + sum(W[c].get(t, 0.) for t in toks))
                                 for c in pri), key=lambda kv: (-kv[1], kv[0]))
                if ranked[0][0] == truth: hit += 1
                else: wrong[ranked[0][0]] += 1
                if truth in [c for c, _ in ranked[:3]]: hit3 += 1
            top = wrong.most_common(1)
            print(f"{facet:<8}{shrink:>7.0f}{mode:>10}{hit/n:>8.1%}{hit3/n:>8.1%}   "
                  f"{top[0][0] if top else '-'} ({top[0][1] if top else 0})")
    print()

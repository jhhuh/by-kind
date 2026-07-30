"""Stage ③ — train: learn log-odds weights per (feature, category), per facet.

Labels come from nixpkgs' own pre-by-name directory tree via
data/legacy_mapping.yaml. Features come from src/features.py, the same module
stage ④ uses, so training and serving cannot drift.

Runs an ABLATION by default: descriptions-only vs descriptions+structural. That
tests the prediction recorded at milestone zero — structural features should move
`kind` substantially more than `domain`, because descriptions say what a package
does rather than what form it takes. If they do not, the feature hypothesis is
wrong and needs rethinking rather than tuning.

Reports raw and family-capped numbers. The capped ones are the baseline, because
the legacy corpus is dominated by package families (tools/typesetting alone is
44% of it).

Usage:
    nix develop -c python3 src/train.py packages.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
VENDOR = ROOT / "vendor" / "nixpkgs"
MODEL_DIR = ROOT / "model"

MODULE_SET = re.compile(r"(-modules|-packages)")
SEED = 0
MIN_DF = 5
MIN_EXAMPLES = 20
SHRINK = 5.0   # evidence-based damping; see train_weights
TARGET_CONFIDENT = 0.95   # the `confident` tier's promise
TARGET_PROBABLE = 0.80    # the `probable` tier's promise
FAMILY_CAP = 25

# Every legacy attribute with a position, plus the meta fields features.py wants.
JQ_FILTER = r"""
.packages | to_entries[]
| select(.value.meta.position // "" | startswith("pkgs/"))
| select(.value.meta.position // "" | startswith("pkgs/by-name/") | not)
| {
    attr: .key,
    position: .value.meta.position,
    description: (.value.meta.description // null),
    homepage: ([.value.meta.homepage] | flatten
               | map(select(type == "string")) | first // null),
    main_program: (.value.meta.mainProgram // null),
    license: ([.value.meta.license] | flatten
              | map(if type=="object" then (.spdxId // .shortName // empty)
                    elif type=="string" then . else empty end) | unique)
  }
"""


# --------------------------------------------------------------------------
# corpus
# --------------------------------------------------------------------------
def load_mapping():
    taxonomy = yaml.safe_load((ROOT / "data" / "taxonomy.yaml").read_text())
    mapping = yaml.safe_load((ROOT / "data" / "legacy_mapping.yaml").read_text())
    return taxonomy, mapping


def structural_for(files: set[str]) -> dict[str, dict]:
    """Grep each legacy definition file for the same signals acquire.py extracts."""
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import acquire  # noqa: E402  (reuses the identical regexes)

    out = {}
    for rel in files:
        try:
            text = (VENDOR / rel).read_text(errors="replace")
        except OSError:
            out[rel] = {}
            continue
        out[rel] = {
            "builders": sorted(set(acquire.BUILDER_RE.findall(text))),
            "desktop_item": bool(acquire.DESKTOP_RE.search(text)),
            "gui_toolkit": sorted(set(acquire.TOOLKIT_RE.findall(text))),
            "service_markers": bool(acquire.SERVICE_RE.search(text)),
        }
    return out


def build_corpus(packages_json: Path, mapping: dict) -> list[dict]:
    proc = subprocess.run(["jq", "-c", JQ_FILTER, str(packages_json)],
                          check=True, text=True, capture_output=True)
    excluded = set(mapping.get("exclude") or [])
    depth2, depth3 = mapping["depth2"], mapping["depth3"]

    staged, files = [], set()
    for line in proc.stdout.splitlines():
        rec = json.loads(line)
        pos = rec["position"]
        parts = pos.split("/")
        if len(parts) < 3 or parts[1] in excluded or MODULE_SET.search(pos):
            continue
        if not rec["description"]:
            continue
        p2 = "/".join(parts[1:3])
        p3 = "/".join(parts[1:4]) if len(parts) >= 5 else None
        rule = (depth3.get(p3) if p3 else None) or depth2.get(p2)
        if rule is None:
            continue
        rel = pos.split(":")[0]
        files.add(rel)
        staged.append({
            "name": rec["attr"].split(".")[-1],
            "description": rec["description"],
            "homepage": rec["homepage"],
            "main_program": rec["main_program"],
            "license": rec["license"],
            "file": rel,
            "family": p3 or p2,
            "domain": rule.get("domain"),
            "kind": rule["kind"],
        })

    signals = structural_for(files)
    for row in staged:
        row["structural"] = signals.get(row["file"], {})

    # Dedupe on the facts that drive a prediction. Versioned duplicates
    # (abseil-cpp / abseil-cpp_202505) share a description and would otherwise
    # inflate their category. Sorted -> reproducible split and capping.
    seen, rows = set(), []
    for row in sorted(staged, key=lambda r: (r["domain"] or "", r["kind"],
                                             r["family"], r["description"], r["name"])):
        key = (row["domain"], row["kind"], row["description"])
        if key in seen:
            continue
        seen.add(key)
        rows.append(row)
    return rows


def cap_families(rows: list[dict], cap: int) -> list[dict]:
    kept, seen = [], Counter()
    for row in rows:
        if seen[row["family"]] < cap:
            seen[row["family"]] += 1
            kept.append(row)
    return kept


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def train_weights(train_rows: list[tuple[str, set[str]]]):
    df, df_c, n_c = Counter(), defaultdict(Counter), Counter()
    for category, tokens in train_rows:
        n_c[category] += 1
        for token in tokens:
            df[token] += 1
            df_c[category][token] += 1
    vocab = {t for t, n in df.items() if n >= MIN_DF}

    total = len(train_rows)
    weights = defaultdict(dict)
    for category in n_c:
        for token in vocab:
            support = df_c[category][token]
            a = support + 0.5
            b = n_c[category] - support + 0.5
            x = df[token] - support + 0.5
            y = (total - n_c[category]) - (df[token] - support) + 0.5
            raw = math.log((a / b) / (x / y))
            # Shrink toward zero by how much evidence actually backs this
            # (category, feature) pair. Without it a category with ~35 training
            # rows gets large, noisy weights and -- because scoring only sums
            # PRESENT features, never penalising absent ones -- accumulates
            # enough score to swallow thousands of packages at inference.
            # Micro-averaged held-out accuracy cannot see this: the tiny class
            # is tiny in the test split too. Caught only by running stage 4 and
            # looking at the output distribution.
            weights[category][token] = raw * (support / (support + SHRINK))
    prior = {c: math.log(n_c[c] / total) for c in n_c}
    return weights, prior, vocab


def evaluate(rows: list[dict], facet: str, use_structural: bool):
    data = [(r[facet], F.featurize(r, use_structural)) for r in rows
            if r.get(facet) is not None]
    counts = Counter(c for c, _ in data)
    keep = {c for c, n in counts.items() if n >= MIN_EXAMPLES}
    data = [(c, t) for c, t in data if c in keep]
    if len(keep) < 2 or len(data) < 50:
        return None

    # 70/15/15. The calibration split exists because the confident-tier
    # threshold must be FITTED, not hardcoded: richer feature sets inflate
    # margins, so a fixed cut-off silently admits weaker cases as the model
    # improves. Fitting it on the test set would leak, hence three splits.
    rng = random.Random(SEED)
    order = list(range(len(data)))
    rng.shuffle(order)
    data = [data[i] for i in order]
    n_train = int(len(data) * 0.70)
    n_calib = int(len(data) * 0.15)
    train = data[:n_train]
    calib = data[n_train:n_train + n_calib]
    test = data[n_train + n_calib:]
    if not calib or not test:
        return None

    weights, prior, vocab = train_weights(train)

    def score(tokens):
        seen = tokens & vocab
        ranked = sorted(
            ((c, prior[c] + sum(weights[c].get(t, 0.0) for t in seen)) for c in prior),
            key=lambda kv: (-kv[1], kv[0]),
        )
        return ranked[0][0], ranked[0][1] - ranked[1][1], [c for c, _ in ranked[:3]]

    # Fit the smallest margin whose held-back precision still meets each target.
    graded = sorted(((score(t)[1], score(t)[0] == c) for c, t in calib),
                    key=lambda kv: -kv[0])

    def fit(target: float) -> float:
        cut, hits_so_far = float("inf"), 0
        for i, (margin, correct) in enumerate(graded, start=1):
            hits_so_far += correct
            if hits_so_far / i >= target:
                cut = margin
        if cut == float("inf"):
            cut = graded[0][0] if graded else 0.0
        return cut

    threshold = fit(TARGET_CONFIDENT)
    threshold_probable = min(fit(TARGET_PROBABLE), threshold)

    hits = top3 = conf_n = conf_hits = 0
    per_class_hits, per_class_n, predicted = Counter(), Counter(), Counter()
    for category, tokens in test:
        pred, margin, best3 = score(tokens)
        hits += pred == category
        top3 += category in best3
        per_class_n[category] += 1
        per_class_hits[category] += pred == category
        predicted[pred] += 1
        if margin >= threshold:
            conf_n += 1
            conf_hits += pred == category

    # Macro recall weights every category equally, so a tiny class collapsing is
    # visible instead of being averaged away by the head of the distribution.
    macro = (sum(per_class_hits[c] / per_class_n[c] for c in per_class_n)
             / len(per_class_n)) if per_class_n else 0.0
    # Over-prediction ratio: how much more often a category is predicted than it
    # truly occurs. The direct symptom of the tiny-class failure mode.
    worst_ratio, worst_cat = 0.0, None
    for c in per_class_n:
        ratio = predicted[c] / per_class_n[c]
        if ratio > worst_ratio:
            worst_ratio, worst_cat = ratio, c

    return {
        "rows": len(data), "categories": len(keep), "held_out": len(test),
        "top1": hits / len(test), "top3": top3 / len(test),
        "conf_cov": conf_n / len(test),
        "conf_acc": conf_hits / conf_n if conf_n else 0.0,
        "macro_recall": macro,
        "worst_overprediction": worst_ratio, "worst_overpredicted": worst_cat,
        "threshold": threshold, "threshold_probable": threshold_probable,
        "vocab_set": vocab,
        "vocab": len(vocab), "weights": weights, "prior": prior,
    }


def write_model(result: dict, facet: str) -> Path:
    """Persist EXACTLY the model that was measured.

    No pruning. An earlier version dropped |w| < 0.5 when writing, which meant
    the saved artifact differed from the evaluated one -- train/serve skew that
    would show up as unexplained accuracy loss in stage 4 and nowhere else.
    Fidelity beats file size.

    TSV because reviewing a model diff is the point; JSON sidecar for the scalars
    a classifier needs to load (thresholds, vocabulary).
    """
    MODEL_DIR.mkdir(exist_ok=True)
    path = MODEL_DIR / f"weights.{facet}.tsv"
    with path.open("w") as fh:
        fh.write("# category\tfeature\tweight   (see model.json for thresholds)\n")
        for category in sorted(result["prior"]):
            fh.write(f"{category}\t__PRIOR__\t{result['prior'][category]:.6f}\n")
        for category in sorted(result["weights"]):
            row = result["weights"][category]
            for token in sorted(row):
                fh.write(f"{category}\t{token}\t{row[token]:.6f}\n")
    return path


def report(label: str, result: dict | None) -> None:
    if result is None:
        print(f"  {label:<34} insufficient data")
        return
    print(f"  {label:<34} top-1 {result['top1']:6.1%}  macro-recall {result['macro_recall']:6.1%}  "
          f"top-3 {result['top3']:6.1%}  confident {result['conf_cov']:5.1%} @ {result['conf_acc']:6.1%}  "
          f"[worst over-prediction: {result['worst_overpredicted']} "
          f"{result['worst_overprediction']:.1f}x]")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("packages_json")
    ap.add_argument("--cap", type=int, default=FAMILY_CAP)
    args = ap.parse_args()

    _taxonomy, mapping = load_mapping()
    rows = build_corpus(Path(args.packages_json), mapping)
    capped = cap_families(rows, args.cap)
    print(f"corpus: {len(rows)} rows ({len(capped)} capped at {args.cap}/family, "
          f"{len({r['family'] for r in rows})} families)\n")

    deltas, saved = {}, {}
    for run_label, subset in (("RAW", rows), ("CAPPED (baseline)", capped)):
        print(f"{run_label}:")
        for facet in ("domain", "kind"):
            without = evaluate(subset, facet, use_structural=False)
            with_ = evaluate(subset, facet, use_structural=True)
            report(f"{facet}  description only", without)
            report(f"{facet}  + structural", with_)
            if without and with_:
                delta = with_["top1"] - without["top1"]
                print(f"  {'':<34} Δ top-1 {delta:+.1%}")
                if run_label.startswith("CAPPED"):
                    deltas[facet] = delta
                    saved[facet] = with_
        print()

    # Ship the family-capped model: the raw corpus is 69% package families, and a
    # model fitted to that over-predicts typesetting and desktop on by-name.
    import datetime as _dt
    meta = {"taxonomy_version": _taxonomy.get("version"),
            "mapping_version": mapping.get("version"),
            "corpus": "family-capped", "family_cap": args.cap,
            "target_confident": TARGET_CONFIDENT, "target_probable": TARGET_PROBABLE,
            "facets": {}}
    for facet, res in saved.items():
        write_model(res, facet)
        meta["facets"][facet] = {
            "threshold_confident": res["threshold"],
            "threshold_probable": res["threshold_probable"],
            "vocab": sorted(res["vocab_set"]),
            "held_out_top1": res["top1"], "held_out_top3": res["top3"],
            "held_out_confident_coverage": res["conf_cov"],
            "held_out_confident_precision": res["conf_acc"],
            "categories": sorted(res["prior"]),
            "training_share": {c: math.exp(v) for c, v in res["prior"].items()},
            "train_rows": res["rows"],
        }
    (MODEL_DIR / "model.json").write_text(json.dumps(meta, indent=2, sort_keys=True) + "\n")
    print(f"wrote model/ ({', '.join(sorted(saved))})\n")

    if len(deltas) == 2:
        print("PREDICTION CHECK (milestone zero): structural features should move "
              "`kind` more than `domain`.")
        gap = deltas["kind"] - deltas["domain"]
        verdict = ("CONFIRMED" if gap > 0.02 else
                   "NOT CONFIRMED" if gap < -0.02 else
                   "INCONCLUSIVE (gap within noise)")
        print(f"  domain {deltas['domain']:+.1%}   kind {deltas['kind']:+.1%}   -> {verdict}")


if __name__ == "__main__":
    main()

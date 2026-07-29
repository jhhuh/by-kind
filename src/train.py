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
TARGET_PRECISION = 0.95   # the `confident` tier's promise
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
    homepage: (.value.meta.homepage // null),
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
            a = df_c[category][token] + 0.5
            b = n_c[category] - df_c[category][token] + 0.5
            x = df[token] - df_c[category][token] + 0.5
            y = (total - n_c[category]) - (df[token] - df_c[category][token]) + 0.5
            weights[category][token] = math.log((a / b) / (x / y))
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

    # Fit the smallest margin whose held-back precision still meets the target.
    graded = sorted(((score(t)[1], score(t)[0] == c) for c, t in calib),
                    key=lambda kv: -kv[0])
    threshold, hits_so_far = float("inf"), 0
    for i, (margin, correct) in enumerate(graded, start=1):
        hits_so_far += correct
        if hits_so_far / i >= TARGET_PRECISION:
            threshold = margin
    if threshold == float("inf"):
        threshold = graded[0][0] if graded else 0.0

    hits = top3 = conf_n = conf_hits = 0
    for category, tokens in test:
        pred, margin, best3 = score(tokens)
        hits += pred == category
        top3 += category in best3
        if margin >= threshold:
            conf_n += 1
            conf_hits += pred == category

    return {
        "rows": len(data), "categories": len(keep), "held_out": len(test),
        "top1": hits / len(test), "top3": top3 / len(test),
        "conf_cov": conf_n / len(test),
        "conf_acc": conf_hits / conf_n if conf_n else 0.0,
        "threshold": threshold,
        "vocab": len(vocab), "weights": weights, "prior": prior,
    }


def write_weights(result: dict, facet: str) -> Path:
    """Human-readable, diffable, git-tracked. Reviewing a model diff is the point."""
    MODEL_DIR.mkdir(exist_ok=True)
    path = MODEL_DIR / f"weights.{facet}.tsv"
    with path.open("w") as fh:
        fh.write(f"# confident-tier margin threshold: {result['threshold']:.4f}\n")
        fh.write(f"# fitted for >={TARGET_PRECISION:.0%} precision on a held-back split\n")
        fh.write("# category\tfeature\tweight\n")
        for category in sorted(result["prior"]):
            fh.write(f"{category}\t__PRIOR__\t{result['prior'][category]:.6f}\n")
        for category in sorted(result["weights"]):
            row = result["weights"][category]
            for token in sorted(row, key=lambda t: (-row[t], t)):
                if abs(row[t := token]) < 0.5:   # prune near-zero noise
                    continue
                fh.write(f"{category}\t{token}\t{row[token]:.4f}\n")
    return path


def report(label: str, result: dict | None) -> None:
    if result is None:
        print(f"  {label:<34} insufficient data")
        return
    print(f"  {label:<34} top-1 {result['top1']:6.1%}  top-3 {result['top3']:6.1%}  "
          f"confident {result['conf_cov']:5.1%} @ {result['conf_acc']:6.1%} "
          f"(margin≥{result['threshold']:.1f})  "
          f"[{result['rows']} rows, {result['categories']} cat, {result['vocab']} feat]")


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

    deltas = {}
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
                    write_weights(with_, facet)
        print()

    if len(deltas) == 2:
        print("PREDICTION CHECK (milestone zero): structural features should move "
              "`kind` more than `domain`.")
        verdict = "CONFIRMED" if deltas["kind"] > deltas["domain"] else "NOT CONFIRMED"
        print(f"  domain {deltas['domain']:+.1%}   kind {deltas['kind']:+.1%}   -> {verdict}")


if __name__ == "__main__":
    main()

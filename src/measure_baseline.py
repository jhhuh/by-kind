"""Milestone-zero baseline: two-facet log-odds classifier on legacy labels.

Establishes the number that every downstream regression gate compares against.
Descriptions only — structural features are stage ③ work, deliberately excluded
here so the baseline measures the same thing the single-facet probe did.

Reports TWO numbers per facet:

  raw     — every legacy row. Comparable to the 76.3% single-facet probe, and
            dominated by package families (tools/typesetting alone is 44% of the
            corpus), so it substantially overstates real-world performance.
  capped  — at most --cap rows per package family. Approximates the actual task
            of classifying independent by-name packages. THIS is the baseline.

Usage:
    curl -sL https://channels.nixos.org/nixpkgs-unstable/packages.json.br \\
      | brotli -d > packages.json
    nix develop -c python3 src/measure_baseline.py packages.json
"""
from __future__ import annotations

import argparse
import json
import math
import random
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
MODULE_SET = re.compile(r"(-modules|-packages)")
TOKEN = re.compile(r"[a-z][a-z0-9+#-]{2,}")
STOPWORDS = {
    "the", "and", "for", "with", "that", "this", "from", "you", "your", "are",
    "was", "can", "its", "not", "use", "used", "using", "based", "which",
    "their", "there", "other", "more", "than", "into", "over", "out", "via",
}
SEED = 0
MIN_DF = 5           # feature must appear in >= this many training docs
MIN_EXAMPLES = 20    # category must have >= this many training examples
HIGH_MARGIN = 4.0    # log-odds margin defining the `confident` tier


def tokenize(text: str) -> frozenset[str]:
    return frozenset(w for w in TOKEN.findall(text.lower()) if w not in STOPWORDS)


# --------------------------------------------------------------------------
# label construction
# --------------------------------------------------------------------------
def load_config() -> tuple[dict, dict]:
    taxonomy = yaml.safe_load((ROOT / "data" / "taxonomy.yaml").read_text())
    mapping = yaml.safe_load((ROOT / "data" / "legacy_mapping.yaml").read_text())
    return taxonomy, mapping


def build_rows(packages: dict, mapping: dict) -> tuple[list, list[str]]:
    """Return (rows, unmapped_paths). Each row: (domain, kind, desc, family)."""
    excluded = set(mapping.get("exclude") or [])
    depth2, depth3 = mapping["depth2"], mapping["depth3"]

    seen, unmapped = set(), Counter()
    for _attr, pkg in packages.items():
        meta = pkg.get("meta") or {}
        pos = meta.get("position") or ""
        desc = meta.get("description") or ""
        if not pos.startswith("pkgs/") or pos.startswith("pkgs/by-name/"):
            continue
        if MODULE_SET.search(pos) or not desc:
            continue
        parts = pos.split("/")
        if len(parts) < 3 or parts[1] in excluded:
            continue

        p2 = "/".join(parts[1:3])
        # depth-3 only when the 3rd component is a directory, not the .nix file
        p3 = "/".join(parts[1:4]) if len(parts) >= 5 else None

        rule = depth3.get(p3) if p3 else None
        if rule is None:
            rule = depth2.get(p2)
        if rule is None:
            unmapped[p2] += 1
            continue

        # dedupe identical (labels, description): versioned duplicates such as
        # abseil-cpp / abseil-cpp_202505 share a description and would otherwise
        # inflate their category's counts.
        seen.add((rule.get("domain"), rule["kind"], desc, p3 or p2))

    # sort with an explicit key: `domain` may be None, which is not orderable
    # against str. Sorting is what makes family capping and the split reproducible.
    ordered = sorted(seen, key=lambda r: (r[0] or "", r[1], r[3], r[2]))
    return ordered, [p for p, _ in unmapped.most_common()]


def validate(taxonomy: dict, mapping: dict, unmapped: list[str]) -> None:
    """Mapping must be total over observed paths and reference only real values."""
    problems = []
    if unmapped:
        problems.append(f"mapping is not total — unmapped depth-2 paths: {unmapped}")

    domains, kinds = set(taxonomy["domain"]), set(taxonomy["kind"])
    for table in ("depth2", "depth3"):
        for path, rule in mapping[table].items():
            d, k = rule.get("domain"), rule["kind"]
            if d is not None and d not in domains:
                problems.append(f"{table}:{path} -> unknown domain '{d}'")
            if k not in kinds:
                problems.append(f"{table}:{path} -> unknown kind '{k}'")

    for facet in ("domain", "kind"):
        for value, spec in taxonomy[facet].items():
            if not (spec.get("gloss") or "").strip():
                problems.append(f"taxonomy {facet}:{value} has no gloss")
            if value not in ("other", "unclassified") and len(spec.get("exemplars") or []) < 3:
                problems.append(f"taxonomy {facet}:{value} has <3 exemplars")

    if problems:
        print("VALIDATION FAILED:", file=sys.stderr)
        for p in problems:
            print(f"  - {p}", file=sys.stderr)
        sys.exit(1)


def cap_families(rows: list, cap: int) -> list:
    """Keep at most `cap` rows per package family, deterministically."""
    kept, per_family = [], Counter()
    for row in rows:  # rows are already sorted -> deterministic selection
        family = row[3]
        if per_family[family] < cap:
            per_family[family] += 1
            kept.append(row)
    return kept


# --------------------------------------------------------------------------
# model
# --------------------------------------------------------------------------
def evaluate(rows: list, facet_index: int, label: str) -> dict | None:
    """Train log-odds weights on 80% of rows, evaluate on 20%."""
    data = [(r[facet_index], r[2]) for r in rows if r[facet_index] is not None]
    counts = Counter(c for c, _ in data)
    keep = {c for c, n in counts.items() if n >= MIN_EXAMPLES}
    data = sorted((c, d) for c, d in data if c in keep)
    if len(keep) < 2 or len(data) < 50:
        print(f"  {label:<22} insufficient data ({len(data)} rows, {len(keep)} categories)")
        return None

    rng = random.Random(SEED)
    rng.shuffle(data)
    split = int(len(data) * 0.8)
    train, test = data[:split], data[split:]

    df, df_c, n_c = Counter(), defaultdict(Counter), Counter()
    for category, desc in train:
        n_c[category] += 1
        for w in tokenize(desc):
            df[w] += 1
            df_c[category][w] += 1
    vocab = {w for w, n in df.items() if n >= MIN_DF}

    total = len(train)
    weights = defaultdict(dict)
    for category in n_c:
        for w in vocab:
            a = df_c[category][w] + 0.5
            b = n_c[category] - df_c[category][w] + 0.5
            x = df[w] - df_c[category][w] + 0.5
            y = (total - n_c[category]) - (df[w] - df_c[category][w]) + 0.5
            weights[category][w] = math.log((a / b) / (x / y))
    prior = {c: math.log(n_c[c] / total) for c in n_c}

    def predict(desc: str):
        words = tokenize(desc) & vocab
        scored = sorted(
            ((c, prior[c] + sum(weights[c].get(w, 0.0) for w in words)) for c in n_c),
            key=lambda t: (-t[1], t[0]),
        )
        margin = scored[0][1] - scored[1][1]
        return scored[0][0], margin, [c for c, _ in scored[:3]]

    hits = top3 = conf_n = conf_hits = 0
    for category, desc in test:
        pred, margin, best3 = predict(desc)
        hits += pred == category
        top3 += category in best3
        if margin >= HIGH_MARGIN:
            conf_n += 1
            conf_hits += pred == category

    result = {
        "rows": len(data), "categories": len(keep), "held_out": len(test),
        "top1": hits / len(test), "top3": top3 / len(test),
        "conf_cov": conf_n / len(test),
        "conf_acc": conf_hits / conf_n if conf_n else 0.0,
    }
    print(
        f"  {label:<22} top-1 {result['top1']:6.1%}   top-3 {result['top3']:6.1%}   "
        f"confident tier: {result['conf_cov']:5.1%} of set @ {result['conf_acc']:6.1%}   "
        f"[{result['rows']} rows, {result['categories']} categories]"
    )
    return result


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("packages_json")
    ap.add_argument("--cap", type=int, default=25,
                    help="max rows per package family in the capped run")
    args = ap.parse_args()

    taxonomy, mapping = load_config()
    packages = json.loads(Path(args.packages_json).read_text())["packages"]

    rows, unmapped = build_rows(packages, mapping)
    validate(taxonomy, mapping, unmapped)
    print(f"mapping is total over observed paths.  labeled rows: {len(rows)}\n")

    capped = cap_families(rows, args.cap)
    families = len({r[3] for r in rows})
    print(f"raw    : {len(rows):>6} rows across {families} families")
    print(f"capped : {len(capped):>6} rows (max {args.cap}/family)\n")

    print("RAW (family-dominated — overstates real performance):")
    evaluate(rows, 0, "domain")
    evaluate(rows, 1, "kind")

    print("\nCAPPED (the baseline — approximates classifying by-name packages):")
    evaluate(capped, 0, "domain")
    evaluate(capped, 1, "kind")


if __name__ == "__main__":
    main()

"""Stage ④ — classify: score every by-name package into data/categories.sqlite.

A pure function of (packages.jsonl, model/). No network, no API key, no
randomness: same inputs and same model_version give byte-identical output.

Confidence comes from the score MARGIN between the top two categories, against
thresholds fitted in stage ③. Absolute precision is explicitly not the goal --
an `uncertain` package is listed under all three of its candidates, because for
a browsing tool a package discoverable under three plausible categories beats one
confidently filed in the wrong place.

Usage:
    nix develop -c python3 src/classify.py
"""
from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import features as F  # noqa: E402

ROOT = Path(__file__).resolve().parent.parent
MODEL_DIR = ROOT / "model"
PACKAGES = ROOT / "data" / "packages.jsonl"
OUT = ROOT / "data" / "categories.sqlite"

SCHEMA = """
CREATE TABLE packages (
  channel           TEXT NOT NULL,
  channel_release   TEXT,
  name              TEXT NOT NULL,
  path              TEXT NOT NULL,
  attr              TEXT,
  description       TEXT,
  homepage          TEXT,
  license           TEXT,          -- JSON array
  main_program      TEXT,
  broken            INTEGER,
  unfree            INTEGER,
  source            TEXT NOT NULL, -- channel | missing
  domain            TEXT NOT NULL,
  domain_score      REAL,
  domain_margin     REAL,
  domain_confidence TEXT NOT NULL, -- confident | probable | uncertain | none
  domain_alternates TEXT,          -- JSON top-3
  kind              TEXT NOT NULL,
  kind_score        REAL,
  kind_margin       REAL,
  kind_confidence   TEXT NOT NULL,
  kind_alternates   TEXT,
  top_features      TEXT,          -- JSON {facet: [[feature, weight], ...]}
  provenance        TEXT NOT NULL, -- model | llm | manual
  model_version     TEXT NOT NULL,
  nixpkgs_rev       TEXT,
  is_new            INTEGER NOT NULL DEFAULT 0,  -- absent from the predecessor
  compared_to       TEXT,                        -- which release that was
  PRIMARY KEY (channel, name)
);
CREATE INDEX idx_kind ON packages(channel, kind);
CREATE INDEX idx_name ON packages(name);
CREATE INDEX idx_confidence ON packages(channel, kind_confidence);

CREATE TABLE run_meta (key TEXT PRIMARY KEY, value TEXT);
"""


def load_model() -> tuple[dict, dict, str]:
    """Returns (per-facet weight tables, metadata, model_version).

    model_version is a hash of the model files, so a row can always be traced to
    the exact artifact that produced it -- and so the determinism test has a
    stable key that does not involve a wall-clock timestamp.
    """
    meta = json.loads((MODEL_DIR / "model.json").read_text())
    digest = hashlib.sha256()
    tables: dict[str, dict] = {}

    for facet in sorted(meta["facets"]):
        path = MODEL_DIR / f"weights.{facet}.tsv"
        raw = path.read_bytes()
        digest.update(raw)
        weights: dict[str, dict[str, float]] = {}
        prior: dict[str, float] = {}
        for line in raw.decode().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            category, feature, value = line.split("\t")
            if feature == "__PRIOR__":
                prior[category] = float(value)
            else:
                weights.setdefault(category, {})[feature] = float(value)
        tables[facet] = {
            "weights": weights,
            "prior": prior,
            "vocab": set(meta["facets"][facet]["vocab"]),
            "confident": meta["facets"][facet]["threshold_confident"],
            "probable": meta["facets"][facet]["threshold_probable"],
        }
    digest.update((MODEL_DIR / "model.json").read_bytes())
    return tables, meta, digest.hexdigest()[:12]


def score(row: dict, table: dict) -> dict:
    """Rank every category for one facet. Deterministic tie-break on name."""
    tokens = F.featurize(row, use_structural=True) & table["vocab"]
    if not tokens:
        # No usable signal at all: no description AND no structural feature.
        return {"label": None, "score": None, "margin": None,
                "confidence": "none", "alternates": [], "features": []}

    prior, weights = table["prior"], table["weights"]
    ranked = sorted(
        ((c, prior[c] + sum(weights.get(c, {}).get(t, 0.0) for t in tokens))
         for c in prior),
        key=lambda kv: (-kv[1], kv[0]),
    )
    top, runner_up = ranked[0], ranked[1]
    margin = top[1] - runner_up[1]

    if margin >= table["confident"]:
        confidence = "confident"
    elif margin >= table["probable"]:
        confidence = "probable"
    else:
        confidence = "uncertain"

    contributions = sorted(
        ((t, weights.get(top[0], {}).get(t, 0.0)) for t in tokens),
        key=lambda kv: (-kv[1], kv[0]),
    )[:5]

    return {
        "label": top[0], "score": top[1], "margin": margin,
        "confidence": confidence,
        "alternates": [c for c, _ in ranked[:3]],
        "features": contributions,
    }


def _text(value):
    """Coerce to something sqlite can bind.

    meta.homepage is normally a string but is a LIST for one package in 21,511.
    acquire.py now normalises it, but a single malformed row must never be able
    to take down the whole stage -- so the sink is defensive too.
    """
    if value is None or isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        return next((v for v in value if isinstance(v, str)), None)
    return str(value)


def classify(rows: list[dict], tables: dict, version: str) -> list[tuple]:
    out = []
    for row in rows:
        domain = score(row, tables["domain"])
        kind = score(row, tables["kind"])
        out.append((
            row.get("channel", "nixpkgs-unstable"), row.get("channel_release"),
            row["name"], row["path"], _text(row.get("attr")),
            _text(row.get("description")),
            _text(row.get("homepage")), json.dumps(row.get("license") or []),
            _text(row.get("main_program")),
            None if row.get("broken") is None else int(bool(row["broken"])),
            None if row.get("unfree") is None else int(bool(row["unfree"])),
            row.get("source", "channel"),
            domain["label"] or "unclassified", domain["score"], domain["margin"],
            domain["confidence"], json.dumps(domain["alternates"]),
            kind["label"] or "other", kind["score"], kind["margin"],
            kind["confidence"], json.dumps(kind["alternates"]),
            json.dumps({"domain": domain["features"], "kind": kind["features"]}),
            "model", version, row.get("nixpkgs_rev"), 0, None,
        ))
    return out


STABLE = re.compile(r"^nixos-\d\d\.\d\d$")


def predecessor(channel: str, channels: list[str]) -> str | None:
    """The release a channel should be diffed against.

    Stable releases chain: 25.05 -> 25.11 -> 26.05. Unstable channels are
    compared against the newest stable, which is the question people actually
    ask -- "what is in unstable that is not in the release I am running?"
    """
    stables = sorted(c for c in channels if STABLE.match(c))
    if not stables:
        return None
    if STABLE.match(channel):
        i = stables.index(channel)
        return stables[i - 1] if i > 0 else None
    return stables[-1]


def channel_attrs(channel: str) -> set[str] | None:
    """Every top-level attribute of a channel, if acquire recorded them."""
    path = ROOT / "data" / f"attrs.{channel}.txt"
    if not path.exists():
        return None
    return {line for line in path.read_text().split("\n") if line}


def mark_new(conn, channels: list[str]) -> None:
    names = {c: {r[0] for r in conn.execute(
        "SELECT name FROM packages WHERE channel=?", (c,))} for c in channels}
    # Prefer the full attribute set for the PREDECESSOR side of the diff.
    full = {c: (channel_attrs(c) or names[c]) for c in channels}
    for channel in channels:
        prev = predecessor(channel, channels)
        if prev is None:
            continue
        fresh = names[channel] - full[prev]
        conn.executemany(
            "UPDATE packages SET is_new=1, compared_to=? WHERE channel=? AND name=?",
            [(prev, channel, n) for n in sorted(fresh)])
        conn.execute("UPDATE packages SET compared_to=? WHERE channel=?",
                     (prev, channel))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--packages", type=Path, default=None,
                    help="single jsonl; default is every data/packages.*.jsonl")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    tables, meta, version = load_model()
    sources = ([args.packages] if args.packages
               else sorted((ROOT / "data").glob("packages.*.jsonl")))
    if not sources:
        raise SystemExit("no data/packages.*.jsonl — run src/acquire.py first")
    rows = []
    for src in sources:
        rows += [json.loads(line) for line in src.read_text().splitlines()]
    # (channel, name) is the primary key, so sort by both for byte-stable output
    rows.sort(key=lambda r: (r.get("channel", ""), r["name"]))

    records = classify(rows, tables, version)

    args.out.unlink(missing_ok=True)
    conn = sqlite3.connect(args.out)
    conn.executescript(SCHEMA)
    conn.executemany(
        f"INSERT INTO packages VALUES ({','.join('?' * len(records[0]))})", records
    )
    # Wall-clock lives here, NOT on the rows, so the packages table stays
    # byte-identical across runs and the determinism test has something to assert.
    conn.executemany("INSERT INTO run_meta VALUES (?,?)", [
        ("model_version", version),
        ("taxonomy_version", str(meta.get("taxonomy_version"))),
        ("corpus", meta.get("corpus", "")),
        ("classified_at", dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")),
        ("packages", str(len(records))),
        ("channels", ",".join(sorted({r.get("channel", "") for r in rows}))),
    ])
    channels = sorted({r.get("channel", "") for r in rows})
    mark_new(conn, channels)
    conn.commit()

    def tally(sql):
        return conn.execute(sql).fetchall()

    print(f"wrote {args.out}  ({len(records)} packages, model {version})\n")
    for channel, n, new, prev in tally(
            "SELECT channel, COUNT(*), SUM(is_new), MAX(compared_to)"
            " FROM packages GROUP BY 1 ORDER BY 1"):
        tail = f"  +{new:,} new vs {prev}" if prev else ""
        print(f"  {channel:<24}{n:>7,}{tail}")
    print()
    for facet in ("domain", "kind"):
        dist = dict(tally(
            f"SELECT {facet}_confidence, COUNT(*) FROM packages GROUP BY 1"))
        total = sum(dist.values())
        parts = "  ".join(f"{k} {v} ({v/total:.0%})"
                          for k, v in sorted(dist.items()))
        print(f"{facet:<8} {parts}")

    print("\ntop domains:")
    for name, n in tally("SELECT domain, COUNT(*) c FROM packages "
                         "GROUP BY 1 ORDER BY c DESC LIMIT 8"):
        print(f"  {name:<18}{n}")
    print("\nkind distribution:")
    for name, n in tally("SELECT kind, COUNT(*) c FROM packages "
                         "GROUP BY 1 ORDER BY c DESC"):
        print(f"  {name:<18}{n}")

    # Compare predicted share against the training prior. A category predicted
    # far more often than it was trained on is the signature of an unrepresentative
    # training corpus -- which micro-averaged held-out accuracy cannot show.
    print("\nDISTRIBUTION SKEW (predicted share / training share):")
    for facet in ("domain", "kind"):
        prior = meta["facets"][facet].get("training_share") or {}
        if not prior:
            print(f"  {facet}: training_share absent from model.json; rerun train.py")
            continue
        counts = dict(conn.execute(
            f"SELECT {facet}, COUNT(*) FROM packages GROUP BY 1").fetchall())
        total = sum(counts.values())
        skews = []
        for cat, share in prior.items():
            got = counts.get(cat, 0) / total
            if share > 0:
                skews.append((got / share, cat, got, share))
        for ratio, cat, got, share in sorted(skews, reverse=True)[:3]:
            flag = "  <-- over-predicted" if ratio >= 2 else ""
            print(f"  {facet:<7} {cat:<16} {got:6.1%} vs {share:6.1%} "
                  f"= {ratio:4.1f}x{flag}")

    other = conn.execute(
        "SELECT COUNT(*) FROM packages WHERE domain IN ('other','unclassified')"
    ).fetchone()[0]
    print(f"\ndomain 'other'/'unclassified': {other} ({other/len(records):.1%})"
          "   [>10% means the taxonomy needs a category, not tuning]")
    conn.close()


if __name__ == "__main__":
    main()

"""Stage ⑤ — bootstrap in-distribution labels for pkgs/by-name.

Why this exists, in one line: stage ④ measured 13.8% domain accuracy on by-name
against 75.1% on legacy held-out, and `src/experiment_prior.py` showed that NO
modelling change recovers it (10.8–16.1% across a shrinkage × prior sweep). The
label source is the problem, so this stage makes labels for the actual target
population instead of borrowing them from a different one.

`kind` does not need this — it transfers at 71.3% because builder functions are
properties of how a package is built, not of which tree it lives in. This stage
targets `domain`.

Design notes:
  * Enum-constrained structured output, so an off-taxonomy value is not
    representable rather than merely discouraged.
  * The gold set is EXCLUDED from sampling. Labelling the evaluation set would
    make every downstream number meaningless.
  * Resumable: labels are appended to a JSONL cache keyed by package name, so an
    interrupted run costs nothing and a rerun labels only what is missing.
  * The taxonomy system block is cached. On Haiku 4.5 the minimum cacheable
    prefix is 4096 tokens — a shorter block silently does not cache at all
    (`cache_creation_input_tokens: 0`, no error), so the block size is asserted.

Usage:
    nix develop -c python3 src/label.py --sample 2000            # needs a key
    nix develop -c python3 src/label.py --sample 50 --dry-run    # offline check
"""
from __future__ import annotations

import argparse
import json
import os
import random
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
PACKAGES = ROOT / "data" / "packages.jsonl"
GOLD = ROOT / "tests" / "fixtures" / "gold_by_name.tsv"
OUT = ROOT / "data" / "llm_labels.jsonl"

MODEL = "claude-haiku-4-5"      # cheapest model with structured-output support
BATCH = 50
SEED = 0
CACHE_MIN_TOKENS = 4096         # Haiku 4.5's minimum cacheable prefix


def load_taxonomy() -> dict:
    return yaml.safe_load((ROOT / "data" / "taxonomy.yaml").read_text())


def build_system_prompt(taxonomy: dict) -> str:
    """Taxonomy + glosses + exemplars. Deliberately verbose: it must exceed the
    4096-token cache minimum, and glosses measurably improve label quality."""
    lines = [
        "You are categorising Nix packages from the nixpkgs repository.",
        "For each package, choose exactly one `domain` (what it is ABOUT) and one",
        "`kind` (what it IS). These are independent axes: Ardour is",
        "(audio, application), sox is (audio, cli-tool), libsndfile is",
        "(audio, library).",
        "",
        "Choose `other` only when nothing fits. Choose the most specific value",
        "that applies. Judge from the description and the package name.",
        "",
        "## domain values",
    ]
    for value, spec in taxonomy["domain"].items():
        ex = ", ".join(spec.get("exemplars") or [])
        lines.append(f"- {value}: {spec['gloss'].strip()}"
                     + (f" Examples: {ex}." if ex else ""))
    lines += ["", "## kind values"]
    for value, spec in taxonomy["kind"].items():
        ex = ", ".join(spec.get("exemplars") or [])
        lines.append(f"- {value}: {spec['gloss'].strip()}"
                     + (f" Examples: {ex}." if ex else ""))

    # Few-shot examples serve two purposes: they improve label quality, and they
    # push the system block past Haiku 4.5's 4096-token cache minimum. Without
    # them the block is ~1650 tokens and prompt caching SILENTLY does not apply
    # (no error, just cache_creation_input_tokens: 0 and ~5x the input cost).
    fewshot = ROOT / "data" / "fewshot.tsv"
    if fewshot.exists():
        lines += ["", "## worked examples", ""]
        for line in fewshot.read_text().splitlines():
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) >= 4:
                lines.append(f"{parts[0]}: {parts[1]}  ->  "
                             f"domain={parts[2]}, kind={parts[3]}")
    return "\n".join(lines)


def response_schema(taxonomy: dict) -> dict:
    """Enum-constrained: an off-taxonomy value cannot be emitted."""
    return {
        "type": "object",
        "properties": {
            "labels": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "index": {"type": "integer"},
                        "domain": {"type": "string",
                                   "enum": sorted(taxonomy["domain"])},
                        "kind": {"type": "string",
                                 "enum": sorted(taxonomy["kind"])},
                    },
                    "required": ["index", "domain", "kind"],
                    "additionalProperties": False,
                },
            }
        },
        "required": ["labels"],
        "additionalProperties": False,
    }


def render_batch(batch: list[dict]) -> str:
    lines = []
    for i, row in enumerate(batch):
        desc = (row.get("description") or "(no description)").replace("\n", " ")
        extra = []
        if row.get("main_program"):
            extra.append(f"binary={row['main_program']}")
        builders = (row.get("structural") or {}).get("builders") or []
        if builders:
            extra.append(f"built-with={builders[0]}")
        suffix = f"  [{', '.join(extra)}]" if extra else ""
        lines.append(f"{i}. {row['name']}: {desc}{suffix}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# backends
# --------------------------------------------------------------------------
class AnthropicLabeller:
    def __init__(self, taxonomy: dict):
        import anthropic  # imported lazily so --dry-run needs no credentials
        self.client = anthropic.Anthropic()
        self.system = build_system_prompt(taxonomy)
        self.schema = response_schema(taxonomy)
        self.usage = {"cache_read": 0, "cache_write": 0, "input": 0, "output": 0}

    def check_cacheable(self) -> int:
        """A system block under 4096 tokens silently fails to cache on Haiku."""
        n = self.client.messages.count_tokens(
            model=MODEL,
            system=[{"type": "text", "text": self.system}],
            messages=[{"role": "user", "content": "x"}],
        ).input_tokens
        return n

    def label(self, batch: list[dict]) -> list[dict]:
        response = self.client.messages.create(
            model=MODEL,
            max_tokens=4096,
            system=[{"type": "text", "text": self.system,
                     "cache_control": {"type": "ephemeral"}}],
            output_config={"format": {"type": "json_schema",
                                      "schema": self.schema}},
            messages=[{"role": "user", "content":
                       "Categorise each package:\n\n" + render_batch(batch)}],
        )
        u = response.usage
        self.usage["cache_read"] += getattr(u, "cache_read_input_tokens", 0) or 0
        self.usage["cache_write"] += getattr(u, "cache_creation_input_tokens", 0) or 0
        self.usage["input"] += u.input_tokens
        self.usage["output"] += u.output_tokens
        text = next(b.text for b in response.content if b.type == "text")
        return json.loads(text)["labels"]


class DryRunLabeller:
    """Offline pipeline check ONLY. Emits a fixed label so the batching, schema,
    caching and resume paths can be exercised without credentials. Its output is
    marked provenance=dry-run and must never be used for training."""

    def __init__(self, taxonomy: dict):
        self.system = build_system_prompt(taxonomy)
        self.schema = response_schema(taxonomy)
        self.usage = {"cache_read": 0, "cache_write": 0, "input": 0, "output": 0}

    def check_cacheable(self) -> int:
        return len(self.system) // 4        # rough tokens; no API call

    def label(self, batch: list[dict]) -> list[dict]:
        return [{"index": i, "domain": "other", "kind": "other"}
                for i in range(len(batch))]


# --------------------------------------------------------------------------
def load_gold_names() -> set[str]:
    names = set()
    for line in GOLD.read_text().splitlines():
        if line.startswith("#") or not line.strip():
            continue
        names.add(line.split("#")[0].strip().split("\t")[0])
    return names


def sample(rows: list[dict], n: int, exclude: set[str]) -> list[dict]:
    """Stratify by two-letter shard so the sample spans the alphabet rather than
    clustering, and drop packages with no description (nothing to label from)."""
    pool = [r for r in rows
            if r["name"] not in exclude and r.get("description")]
    by_shard: dict[str, list[dict]] = {}
    for row in pool:
        by_shard.setdefault(row["path"].split("/")[2], []).append(row)

    rng = random.Random(SEED)
    picked, shards = [], sorted(by_shard)
    for shard in shards:
        by_shard[shard].sort(key=lambda r: r["name"])
        rng.shuffle(by_shard[shard])
    i = 0
    while len(picked) < n and any(by_shard.values()):
        shard = shards[i % len(shards)]
        if by_shard[shard]:
            picked.append(by_shard[shard].pop())
        i += 1
    return sorted(picked, key=lambda r: r["name"])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=2000)
    ap.add_argument("--dry-run", action="store_true",
                    help="exercise the pipeline offline; output is not trainable")
    ap.add_argument("--out", type=Path, default=OUT)
    args = ap.parse_args()

    taxonomy = load_taxonomy()
    rows = [json.loads(line) for line in PACKAGES.read_text().splitlines()]
    gold = load_gold_names()

    done = {}
    if args.out.exists():
        for line in args.out.read_text().splitlines():
            rec = json.loads(line)
            done[rec["name"]] = rec
    print(f"already labelled: {len(done)}")

    batch_rows = [r for r in sample(rows, args.sample, gold)
                  if r["name"] not in done]
    print(f"to label: {len(batch_rows)}  (gold set of {len(gold)} excluded)")

    labeller = DryRunLabeller(taxonomy) if args.dry_run else AnthropicLabeller(taxonomy)
    tokens = labeller.check_cacheable()
    status = "OK" if tokens >= CACHE_MIN_TOKENS else "TOO SHORT — will not cache"
    print(f"system prompt: ~{tokens} tokens (need >= {CACHE_MIN_TOKENS}) [{status}]")
    if tokens < CACHE_MIN_TOKENS and not args.dry_run:
        print("  warning: prompt caching will silently not apply; cost ~5x higher",
              file=sys.stderr)

    provenance = "dry-run" if args.dry_run else "llm"
    written = 0
    with args.out.open("a") as fh:
        for start in range(0, len(batch_rows), BATCH):
            chunk = batch_rows[start:start + BATCH]
            try:
                labels = labeller.label(chunk)
            except Exception as exc:                      # noqa: BLE001
                print(f"batch at {start} failed: {exc}", file=sys.stderr)
                continue
            for item in labels:
                idx = item.get("index")
                if not isinstance(idx, int) or not 0 <= idx < len(chunk):
                    continue
                fh.write(json.dumps({
                    "name": chunk[idx]["name"],
                    "domain": item["domain"], "kind": item["kind"],
                    "provenance": provenance, "model": MODEL,
                }, sort_keys=True) + "\n")
                written += 1
            fh.flush()
            print(f"  {start + len(chunk)}/{len(batch_rows)}", end="\r", flush=True)

    print(f"\nwrote {written} labels to {args.out}  (provenance={provenance})")
    u = labeller.usage
    if u["input"]:
        print(f"tokens: input {u['input']}  output {u['output']}  "
              f"cache read {u['cache_read']}  write {u['cache_write']}")
        if u["cache_read"] == 0 and written > BATCH:
            print("  WARNING: zero cache reads across multiple batches — the "
                  "system prompt is not caching. Check its length.", file=sys.stderr)


if __name__ == "__main__":
    main()

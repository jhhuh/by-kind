# by-kind

**→ Browse it live: https://jhhuh.github.io/by-kind/**

nixpkgs gives you `pkgs/by-name`. This gives you *by kind*.

Reconstruct a browsable **category axis** over `pkgs/by-name/` in nixpkgs.

The `by-name` convention shards 21,444 packages into two-letter directories for
filesystem scaling. That sharding carries no semantics, and no metadata field
replaces the category structure the old `pkgs/applications/audio`,
`pkgs/tools/networking` tree provided — but every package still has a
natural-language `meta.description`.

## What ships, and what doesn't

The project classifies along two facets. **Only one of them works, and only that
one is shipped.**

| facet | what it answers | in-distribution accuracy | status |
|---|---|---:|---|
| **`kind`** | what a package *is* — application, cli-tool, library, server, data, plugin, driver | **71.3%** | **shipped** |
| `domain` | what a package is *about* — audio, networking, fonts, … | 13.8% | **withheld** |

Accuracy is measured on [`tests/fixtures/gold_by_name.tsv`](tests/fixtures/gold_by_name.tsv),
94 packages hand-labelled from `by-name` itself. Held-out accuracy on the *legacy*
tree the model trains from is much higher — 75.1% for `domain` — but that number
does not describe this corpus, and publishing it would be misleading.

**Why `domain` is withheld.** Labels come from nixpkgs' own pre-`by-name`
directory tree, which still categorises ~10k unmigrated packages. `kind` transfers
from that corpus essentially perfectly (71.6% → 71.3%) because its signal is
structural: builder functions and desktop-item calls describe how a package is
*built*, which does not depend on which tree it lives in. `domain` is learned from
description vocabulary, and the legacy corpus's vocabulary is GNOME extensions,
TeX packages and Chicken eggs — it has almost nothing to say about `nmap`. A
shrinkage × prior sweep (`src/experiment_prior.py`) confirmed no modelling change
recovers it (10.8–16.1%). The fix is in-distribution labels, not tuning:
`src/label.py` is built and waiting on API credentials.

Per-tier accuracy for `kind`, also measured on the gold set — `uncertain` rows are
marked `?` in the CLI and show their alternatives:

| tier | share of corpus | accuracy |
|---|---:|---:|
| probable | 63% | 79.0% |
| uncertain | 36% | 58.1% |

## Channels

Six channels are tracked, each pinned to **its own** nixpkgs revision, so the
descriptions and the `package.nix` permalinks always agree:

`nixpkgs-unstable` · `nixos-unstable` · `nixos-unstable-small` ·
`nixos-26.05` · `nixos-25.11` · `nixos-25.05`

Packages absent from the previous release are marked **new**. The diff runs
against the predecessor's *entire attribute set*, not just its `by-name`
directory — otherwise a package that merely migrated into `by-name` would look
new, which removed about a third of the candidate badges.

Stable releases chain (25.05 → 25.11 → 26.05); unstable channels are compared
against the newest stable, which is the question people actually ask.

## Use it

```sh
nix develop -c python3 src/cli.py tree            # counts per kind
nix develop -c python3 src/cli.py ls server       # packages of one kind
nix develop -c python3 src/cli.py ls cli-tool --new   # only what is new since the last release
nix develop -c python3 src/cli.py --channel nixos-26.05 tree
nix develop -c python3 src/cli.py search borg
nix develop -c python3 src/cli.py show ripgrep    # with the evidence for the label
nix develop -c python3 src/cli.py status          # what is shipped and how good it is
```

Browse in a browser — a single self-contained page, no server needed:

```sh
nix develop -c overmind start        # http://localhost:8080
```

Artifacts land in `dist/`: `index.html` (1.9 MB, no external resources) and
`categories.json` (5.5 MB).

## Rebuild from scratch

```sh
nix develop -c python3 src/acquire.py     # ① git + channel dump -> packages.jsonl
nix develop -c python3 src/train.py X     # ③ learn weights (X = decompressed packages.json)
nix develop -c python3 src/classify.py    # ④ score -> categories.sqlite
nix develop -c python3 src/eval_gold.py   #   measure against the gold set
nix develop -c python3 src/emit.py        # ⑥ dist/
nix develop -c python3 -m pytest tests/   #   57 tests
```

Classification is a deterministic table lookup — no network, no API key, no
randomness. The same inputs and model version produce byte-identical output.
Learned weights are checked in as diffable TSV under `model/`.

## Published automatically

`.github/workflows/pages.yml` rebuilds daily and publishes to GitHub Pages.
A build is ~1 minute because **it does not retrain** — `model/` is committed, so
CI only re-scores the current nixpkgs against the existing weights. That keeps
results diffable across runs. Retraining is an explicit, occasional act
(`src/train.py`) done when the taxonomy or feature set changes.

`scripts/build.sh` is the same code path CI runs, so a green CI means a working
local build. It includes a quality gate: if `kind` accuracy on the hand-labelled
gold set drops below 65%, the build fails rather than publishing a regression.

### What is committed, and what is not

Text that *determines* the output is tracked so changes are reviewable as diffs:
`model/weights.*.tsv`, `data/taxonomy.yaml`, `data/legacy_mapping.yaml`,
`data/fewshot.tsv`.

Everything *derived* is rebuilt and ignored: `data/packages.jsonl`,
`data/categories.sqlite` (18 MB of binary — committing it daily would add several
GB per year and produce no readable diff), and `dist/`.

## Documentation

- **Design spec:** [`docs/superpowers/specs/2026-07-28-cat-nixpkgs-design.md`](docs/superpowers/specs/2026-07-28-cat-nixpkgs-design.md)
- **Development journal:** [`artifacts/devlog.md`](artifacts/devlog.md) — including
  the measurement mistakes, what they cost, and the negative results

# cat-nixpkgs — Design

**Status:** stages ①③④⑥ implemented and shipped for `kind`; `domain` withheld
(13.8% in-distribution). Stage ⑤ built, blocked on credentials. See README for
current measured accuracy — several numbers in this document were superseded by
measurement and are marked where that happened.
**Date:** 2026-07-28

## Problem

The `pkgs/by-name/` convention flattened nixpkgs into a 21,511-entry directory
sharded by two-letter prefix. The sharding is a filesystem-scaling device and
carries no semantics, so the category axis that the old `pkgs/applications/audio`,
`pkgs/tools/networking` tree provided is gone. There is no metadata field that
replaces it — but every package does carry a natural-language `meta.description`.

Reconstruct a browsable category axis over `by-name` from that description plus
other available metadata.

## Approach

A **learned statistical classifier** whose weights are derived from nixpkgs' own
pre-`by-name` directory tree, which still labels ~10k packages that have not yet
migrated. Inference is a deterministic table lookup — no network, no API key, no
nondeterminism. An LLM is used **only** in the offline improvement loop, to find
and explain misclassifications.

Rejected alternatives and why:

- **LLM classifies all 21.5k at runtime.** Costs ~$2–4 and works, but is
  irreproducible: reruns on a nixpkgs bump can silently drift, and a wrong answer
  can only be fixed by re-rolling. Demoted to an optional pass over the uncertain
  tail.
- **Hand-authored regex rules.** Deterministic and auditable, but every weight is
  an assertion by the author rather than evidence from the corpus, and the rule
  set grows without bound against a long tail.

The chosen approach keeps determinism and auditability while learning the weights
from data, and spends LLM budget where it has comparative advantage — judgment on
hard cases — rather than on brute force.

## Verified facts

Established empirically on 2026-07-28, not assumed:

| Fact | Value |
|---|---|
| by-name packages at nixpkgs HEAD | **21,511** (5-part paths). 21,576 files match `*/package.nix`, but 65 are nested sub-packages (`kicad/addons/`, `navidrome/plugins/`) and are not by-name entries. |
| Channel `packages.json.br` | 10 MB → 394 MB JSON, 0.6 s download |
| by-name packages present in the channel dump | 21,321 (**99.1%** coverage) — needs a name-fallback join, see stage ① |
| ...with a non-empty `meta.description` | 99.4% of those |
| All `package.nix` files fetchable locally | 42 MB, 6 s (sparse-checkout on a blobless clone) |
| Legacy leaf packages usable as labels | 10,696 across 119 categories (10,016 in the 26 categories with ≥40 examples) |

**Feasibility probe** (descriptions only, no structural features, no tuning —
`artifacts/probe_feasibility.py`, reproducible across runs):

```
top-1 accuracy   : 76.3%   (2,004 held-out)
top-3 accuracy   : 84.3%
high-margin (≥4) : covers 54.7% of the set at 97.4% accuracy
```

**Read these numbers with their restriction.** The probe keeps only categories
with ≥40 examples. Measured effect: 26 of 119 legacy categories survive, which is
**93.6% of the labeled corpus (10,016 of 10,696) but only 22% of the categories**
— the tail is many tiny classes, not much mass. So the figures describe the head
of the distribution that holds nearly all packages; per-category behaviour on the
93 excluded categories is **unmeasured**, and a real taxonomy presents more
classes and therefore more ways to be wrong.

The probe is evidence that the approach is worth building, **not** a performance
forecast for the system. See "Baseline" below.

The third line is the design-defining result: the score margin is a
well-calibrated confidence signal.

### Baseline

Every figure above comes from classifying **single-facet legacy paths**
(`tools/typesetting`, `data/fonts`). The system classifies **`(domain, kind)`
pairs** through a mapping table that does not exist yet. Whether the two-facet
split helps is an open empirical question, not a given: each `domain` value pools
labels from several legacy paths (`applications/audio` *and* `tools/audio` →
`audio`), which changes the class-conditional word distributions in ways that
could cut either way.

Therefore **milestone zero of implementation** is: author `data/taxonomy.yaml`
plus the legacy→facet mapping, rerun the probe against two-facet labels over the
full category set, and record *that* as the project baseline. All downstream
regression gates compare against it. The 76.3% figure justifies building; it does
not gate anything, because gating against it would compare two different
measurements.

**Milestone zero result (2026-07-28, `src/measure_baseline.py`, deterministic).**
The legacy corpus is dominated by package *families*: `tools/typesetting` alone is
44% of it and the top four paths are 69%, because by-name migration already
absorbed the ordinary single packages and left behind what cannot easily migrate.
Measuring on it unmodified therefore mostly measures "can we recognise TeX". Two
numbers are reported: `raw` (all 10,704 rows) and `capped` (≤25 rows per family,
2,842 rows), which approximates the real task.

| facet | run | top-1 | top-3 | confident tier |
|---|---|---:|---:|---|
| domain | raw | 75.4% | 83.3% | 53.3% of set @ 97.8% |
| kind | raw | 76.9% | 91.5% | 53.3% of set @ 94.1% |
| **domain** | **capped** | **71.4%** | **83.1%** | **46.8% of set @ 95.7%** |
| **kind** | **capped** | **66.1%** | **86.5%** | **35.3% of set @ 92.0%** |

**The capped row is the project baseline.** Three findings:

1. **The two-facet split neither helped nor hurt.** Raw `domain` is 75.4% against
   the 76.3% single-facet probe — within noise. The open question from the design
   is now closed: splitting is safe, and it is kept for its browsing value.
2. **Family bias costs less than feared** — ~4pp on `domain`, ~11pp on `kind`.
   The corpus is skewed but the families were not carrying all the signal.
3. **`kind` is the weaker facet, and predictably so.** Descriptions say what a
   package *does* (domain), not what form it *takes* (kind). Kind's real signal is
   structural — builder function, desktop-item calls — which stage ③ adds and this
   baseline deliberately excludes. **Prediction to test: structural features
   should move `kind` substantially more than `domain`.** If they do not, that
   assumption is wrong and the feature set needs rethinking.
   → **Resolved in stage ③: confirmed, but narrowly** (`kind` +12.0pp vs `domain`
   +11.4pp). The unpredicted result is that structural features help `domain`
   nearly as much, because builders encode domain too (`buildKodiAddon` → video,
   `buildHomeAssistantComponent` → home-automation). See the stage-③ section.

Critically, the **confident tier survives capping** at 92–96% accuracy. The design
premise — that the margin is a calibrated confidence signal — holds on the
harder, more representative sample.

## Taxonomy

Two independent facets, distilled from the legacy `pkgs/<top>/<sub>` vocabulary.
That tree conflates two orthogonal axes — which is why it contains both
`applications/backup` and `tools/backup`, and both `applications/networking` and
`tools/networking`. Separating them removes the ambiguity and doubles the
browsing axes.

- **`domain`** — what it is *about*: `audio`, `video`, `graphics`, `networking`,
  `security`, `science`, `text`, `fonts`, `games`, `system`, … (**44 values**
  as authored, plus `other` and `unclassified` — more granular than the ~30
  originally estimated; the `other` rate will show whether that was right)
- **`kind`** — what it *is*: `application`, `cli-tool`, `library`, `server`,
  `data`, `plugin`, `driver`, `build-support` (**9 values** as authored,
  including `other`)

Frozen in a git-tracked `data/taxonomy.yaml`, each value carrying a one-line gloss
and ≥3 exemplars. Frozen means results are diffable across nixpkgs revisions;
changing the taxonomy is a reviewable commit that invalidates the model version.

Legacy paths map onto both facets (`applications/audio` → `(audio, application)`,
`servers/sql` → `(database, server)`, `data/fonts` → `(fonts, data)`), supplying
labels for each. `development/libraries` (1,024 packages) is domain-less and
trains `kind` only.

Each facet has an `other` value. Its **rate is a taxonomy-quality metric**: if
>10% of packages land in `other`, the taxonomy is missing a category — that is a
signal to change `taxonomy.yaml`, not to tune the model.

## Architecture

Six stages, each writing a file the next reads, each independently rerunnable.

```
① acquire   git ls-tree HEAD -- pkgs/by-name        → authoritative name set
            + sparse-checkout of package.nix blobs   → structural signals
            + channel packages.json.br (jq-filtered) → descriptions & meta
            → data/packages.jsonl

② taxonomy  data/taxonomy.yaml (hand-authored, frozen, git-tracked)
            + legacy-path → (domain, kind) mapping table

③ train     legacy-labeled corpus → log-odds weights per (feature, category)
            → model/weights.{domain,kind}.tsv   + held-out metrics

④ classify  deterministic scoring of all 21,511 by-name packages
            → data/categories.sqlite

⑤ improve   LLM error analysis over sampled cases → corrections → back to ③
            (offline, optional, iterative)

⑥ emit      → categories.json · CLI browser · static HTML browser
```

### ① acquire

Name set from `git ls-tree` on a `--depth=1 --filter=blob:none` clone of master
(authoritative). Descriptions from the channel snapshot (98.8% hit).
`meta.position` identifies which attrs originate in `by-name`; deduping on that
path yields the package, since scoped re-exports inflate the attr count from
21,327 to 29,564.

The ~250 packages absent from the channel get a row with `description: null` and
are **counted and reported** — never silently dropped. They are still *scored*:
the package name and `package.nix` text remain available, which is often enough
for `kind` (see assumption 7). `domain` falls back to `unclassified` when no
signal exists.

### ③ train — the model

**Result (2026-07-29, `src/train.py`, family-capped, 70/15/15 split):**

| facet | descriptions only | + structural | Δ | confident tier |
|---|---:|---:|---:|---|
| domain | 68.5% | **79.9%** | +11.4pp | 67.3% of set @ 96.4% |
| kind | 64.3% | **76.3%** | +12.0pp | 22.8% of set @ 99.0% |

Structural features are worth ~11–12 points on both facets. `kind`'s confident
tier is narrow (22.8%) because its threshold is fitted on only ~425 calibration
rows and the fit is conservative; widening it is a stage-⑤ labelling problem, not
a modelling one.

**One model per facet.** Weights are log-odds: how much observing a feature
shifts the posterior toward a category, with add-½ smoothing:

```
w(f, c) = log[ (df_c(f)+½)/(N_c−df_c(f)+½) ] − log[ (df(f)−df_c(f)+½)/((N−N_c)−(df(f)−df_c(f))+½) ]
```

Features with document frequency below a floor are dropped, which removes noise
words without a hand-maintained stopword list.

**Everything is a feature token**, so structural signals get *learned* weights
rather than author-asserted ones:

| Feature family | Examples |
|---|---|
| description tokens | `tex`, `kernel`, `monospace`, `compiler` |
| builder function | `builder:buildGoModule`, `builder:stdenvNoCC` |
| package.nix greps | `desktop:true` (`makeDesktopItem`/`copyDesktopItems`) |
| metadata presence | `has:mainProgram` |
| name morphology | `name:*-cli`, `name:lib*`, `name:*-font` |
| homepage host | `homepage:crates.io`, `homepage:pypi.org` |

`meta.mainProgram` is a **positive-only** signal: ~50% of by-name attrs carry it,
and absence reflects unpopulated metadata rather than "not executable". An earlier
hypothesis that it was a strong two-way discriminator was tested and disproved.

Module-set paths (`*-modules`, `*-packages`) are excluded from training — 76k
R/Python/Haskell entries would otherwise collapse the model onto `library`.
Identical `(label, description)` pairs are deduped so versioned duplicates
(`abseil-cpp`, `abseil-cpp_202505`, …) do not inflate counts.

`model/weights.*.tsv` is checked into git: human-readable, diffable, reviewable.

### ④ classify — scoring and confidence

Score every category, take argmax, and record the **margin** to the runner-up.
The margin drives a confidence tier:

| tier | behavior |
|---|---|
| `confident` | high margin — ~97% accurate, ~54% of corpus |
| `probable` | mid margin — shown normally, flagged |
| `uncertain` | low margin — **listed under its top-3 candidates, not forced into one** |

Absolute precision is explicitly not a goal. For a browsing tool, a package
discoverable under three plausible categories is more useful than one confidently
filed in the wrong place, and the tier makes that distinction visible.

Rows record the top contributing features, so any classification can be explained.

### ⑤ improve — the LLM loop

The LLM never classifies the corpus. It performs three **sampled** jobs, each
touching hundreds of packages rather than 21,511:

1. **Adjudicate low-margin cases.** Labeling where the model is least certain
   yields the most information per label (active learning). Results become new
   training data.
2. **Audit confident disagreements.** Where the model is high-margin but
   contradicts its legacy label, one of the two is wrong — often the legacy label.
   This cleans the training set.
3. **Review top-weighted features.** Surfaces artifacts and taxonomy gaps. The
   probe already produced a live example: `tools/package-management` learned
   `r6rs, r7rs, rnrs, chez`, because Scheme implementations sit under that legacy
   path. That is label noise in nixpkgs, and exactly what this step is for.

Each round retrains and re-measures on held-out data; the metric says when to
stop. Backend is a `Classifier` protocol with Anthropic (`claude-haiku-4-5`) and
Ollama implementations. Cost per round is cents.

Output rows carry `provenance` (`model` | `llm` | `manual`) so LLM-derived and
learned classifications are always distinguishable.

## Deliverables

**`data/categories.sqlite`** — source of truth:

```sql
CREATE TABLE packages (
  name TEXT PRIMARY KEY,        -- by-name directory name = the unit
  path TEXT NOT NULL,           -- pkgs/by-name/<shard>/<name>/package.nix
  attr TEXT,                    -- canonical dot-free attr; NULL if absent from channel
  description TEXT,
  homepage TEXT, license TEXT, main_program TEXT,
  broken INT, unfree INT,
  domain TEXT NOT NULL, domain_score REAL, domain_margin REAL,
  kind   TEXT NOT NULL, kind_score   REAL, kind_margin   REAL,
  confidence TEXT NOT NULL,     -- confident | probable | uncertain
  alternates TEXT,              -- JSON top-3 per facet when uncertain
  top_features TEXT,            -- JSON: features that drove the decision
  provenance TEXT NOT NULL,     -- model | llm | manual
  model_version TEXT, taxonomy_version TEXT, classified_at TEXT
);
```

Plus `data/categories.json` as a flat export.

**CLI** — `cat-nixpkgs`:

```
cat-nixpkgs tree                          # domain × kind matrix with counts
cat-nixpkgs ls networking --kind server   # either facet, or both
cat-nixpkgs search borg --domain backup
cat-nixpkgs show ripgrep                  # row, confidence, top features
```

**Static web browser** — one self-contained HTML file generated from the SQLite.
Composable facet filters, client-side search over all 21.5k rows, confidence
shown, uncertain packages appearing under each candidate. ~3 MB inlined JSON
(~1 MB gzipped). No server, no build step, no external resources.

## Repository layout

```
flake.nix              devShell: python3+pyyaml, jq, brotli, git, sqlite, overmind, tmux
Procfile               web: local preview server for the static page
data/taxonomy.yaml     frozen taxonomy — the only hand-authored classification artifact
model/weights.*.tsv    learned weights, checked in, diffable
src/acquire.py         ①
src/train.py           ③
src/classify.py        ④
src/improve.py         ⑤ LLM loop (Classifier protocol: anthropic | ollama)
src/emit.py            ⑥ sqlite → json / html
src/cli.py             CLI browser
tests/
artifacts/             devlog.md, plan_*.md, logs/, skills/
```

## Verification

Each stage has a checkable goal rather than a subjective one.

| Stage | Verification |
|---|---|
| ① acquire | Unit test on the `meta.position` → by-name-name join against a fixture. Assert coverage ≥98% and that the missing set is reported. |
| ② taxonomy | Every value has a gloss and ≥3 exemplars; values unique per facet; the legacy-path → facet mapping is total over the observed vocabulary. |
| ③ train | Held-out top-1, top-3, and per-tier accuracy reported every run, with a descriptions-only ablation arm. **Regression gate: family-capped top-1 must not fall below the stage-③ result — `domain` 79.9%, `kind` 76.3%** (milestone zero's 71.4% / 66.1% were descriptions-only and are superseded) (not the 76.3% single-facet probe, and not the `raw` figures — different measurements, not comparable). Training must be reproducible: fixed seed *and* no reliance on `set`/`dict` iteration order (see devlog 2026-07-28). Per-category precision/recall and a confusion matrix are emitted for the ⑤ loop, with rare categories reported separately so the head does not mask them. |
| ④ classify | Determinism test: same inputs + same `model_version` → byte-identical output. The `confident` threshold is **fitted per model** on a held-back calibration split targeting 95% precision — never hardcoded, because richer feature sets inflate margins and a fixed cut-off silently admits weaker cases as the model improves. Measured test-set precision lands 94–99%; the gap to target is the calibration-split generalisation error and is reported, not hidden. |
| ⑤ improve | Each round must improve held-out top-1 or be reverted. Corrections are stored as data, so the round is reproducible. |
| ⑥ emit | Round-trip sqlite → json → parse preserves row and facet counts. HTML asserted to contain no external `http(s)://` resource reference. |

## Assumptions and limits

Stated explicitly rather than discovered later.

1. **"HEAD" is served by two sources.** Names come from git master
   (authoritative); descriptions from the channel snapshot, which lags master by
   hours-to-days and only advances when Hydra passes. Measured overlap is 98.8%.
2. **The unit is the `package.nix` directory**, not the attribute.
3. **One-line descriptions only.** The channel dump carries `description` but not
   `longDescription`. That is the signal budget, and it is why structural features
   from `package.nix` matter.
4. **Training labels are noisy and biased.** They come from packages that have
   *not* migrated to `by-name`, which may differ systematically from those that
   have; and the legacy tree itself contains misfilings (`r6rs` →
   `package-management`). Held-out accuracy measured against these labels is
   therefore a *relative* signal, not ground truth. Stage ⑤ exists to attack both
   problems.
5. **Only 26 of 119 legacy categories had ≥40 training examples.** Those 26 hold
   93.6% of the labeled corpus, so the mass is covered — but the 93 excluded
   categories are unmeasured and will likely underperform. They are the primary
   candidates for LLM-assisted label bootstrapping in stage ⑤.
6. **76.3% is a design-justification figure, not a forecast and not a gate.** It
   was measured with descriptions only, single-facet labels, unigrams, no tuning,
   and only on the ≥40-example head. Structural features and bigrams should help;
   the two-facet split may help or hurt. Both directions are to be measured at
   milestone zero, not assumed.
7. **~250 packages have no channel entry**, so they have no `description`,
   `homepage`, `license`, or `mainProgram`. Their only available features are the
   package **name** and the **`package.nix` text** (builder function, desktop-item
   calls). That is often enough for a `kind` guess even with no description, so
   they should be scored rather than skipped — with `domain` left `unclassified`
   where no signal exists.

## Non-goals

- Evaluating nixpkgs. All signals come from the channel dump, git, and
  `package.nix` text.
- Modifying nixpkgs or proposing metadata changes upstream.
- Classifying packages outside `pkgs/by-name/`.
- Absolute precision. Confidence tiers are the deliberate alternative.

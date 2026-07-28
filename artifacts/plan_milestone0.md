# Plan — Milestone Zero: taxonomy + two-facet baseline

Goal (from the spec's "Baseline" section): author the taxonomy and the
legacy→facet mapping, re-measure against two-facet labels, and record that as the
project baseline. Everything downstream gates against it.

## Steps

1. **`data/taxonomy.yaml`** — enumerate `domain` and `kind` values, each with a
   one-line gloss and ≥3 exemplars.
   → verify: schema test — every value has gloss + ≥3 exemplars, values unique
   per facet.

2. **`data/legacy_mapping.yaml`** — depth-2 legacy path → `(domain, kind)`, with a
   depth-3 override table where the third path component is semantic
   (`applications/networking/browsers` → `web`) rather than a family name
   (`applications/editors/vscode`).
   → verify: mapping is **total** over all 119 observed depth-2 paths; every
   target value exists in `taxonomy.yaml`.

3. **`src/measure_baseline.py`** — train two-facet log-odds weights, report
   held-out top-1/top-3 and tier calibration per facet.
   → verify: deterministic across 3 runs (no `set`/`dict` order dependence).

4. **Record the baseline** in the devlog and the spec.

## Known risk, discovered during step 0

The legacy corpus is dominated by package *families*: `tools/typesetting` alone
is 44% of it, and the top 4 paths are 69%. Those are TeX packages, GNOME
extensions, Chicken eggs, and VSCode extensions — not representative of the
by-name population being classified.

Consequence: an unweighted baseline mostly measures "can we recognise TeX", which
is not the task. The measurement must therefore report **two numbers**:

- **raw** — all legacy rows, comparable to the 76.3% single-facet probe
- **family-capped** — at most N rows per depth-3 family directory, which
  approximates the real task far better

The capped number is the honest baseline. If it is much lower than raw, that is
the finding, and it means the label source needs supplementing (see step 5).

5. **If capped accuracy is poor**, the label strategy — not the taxonomy — is what
   needs revisiting. Options to put to the user, cheapest first:
   a. LLM-label a stratified sample of *by-name* packages directly (~2k rows,
      well under $1) → in-distribution labels for the actual target population.
   b. Mine nixpkgs git history for by-name migration renames
      (`pkgs/applications/audio/foo` → `pkgs/by-name/fo/foo`) → true historical
      labels for the target population, but needs a full-history clone.
   c. Accept lower accuracy and lean on the confidence tiers.

Do not silently pick one. Measure first, report, then decide.

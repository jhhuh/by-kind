# cat-nixpkgs — development journal

Append-only. Newest entries at the bottom. Timestamps are UTC.

---

## 2026-07-28 — Design session

**Problem framing.** `pkgs/by-name/` sharding is a filesystem-scaling device with
no semantics, so the category axis the old `pkgs/applications/audio` tree provided
is gone. Goal: reconstruct a browsable one from `meta.description` plus other
available metadata.

**Facts established empirically before designing anything** (this mattered — two
assumptions were disproved):

- 21,575 `package.nix` files at HEAD `109963f4`.
- The channel `packages.json.br` (10 MB → 394 MB) covers 21,327 of them = 98.8%.
  So **no nixpkgs evaluation is needed** — `meta.position` identifies which attrs
  originate in `by-name`.
- Attr count (29,564) ≫ package count (21,327) because scoped sets re-export the
  same file. Dedupe on `meta.position` path; the unit is the directory.
- All 21,575 `package.nix` files fetch in **6 s / 42 MB** via sparse-checkout on a
  blobless clone. Structural signals (builder function, `makeDesktopItem`) are
  therefore free.
- The legacy tree still labels **10,016** leaf packages across 26 categories with
  ≥40 examples — free training data.

**Disproved assumption #1.** Hypothesised `meta.mainProgram` presence/absence
would be a strong two-way `kind` discriminator. Tested: ~50% of by-name attrs
carry it, and the legacy-set numbers were skewed by module sets. Absence reflects
unpopulated metadata, not "not executable". Demoted to a positive-only signal.

**Disproved assumption #2.** Assumed the channel snapshot might have a large
coverage gap versus master. Measured: 98.8%. Not a design blocker; the ~250
missing get an `unclassified` row and are reported.

**Approach evolution** (steered by the user across three revisions):

1. *LLM classifies all 21.5k at runtime.* Costed at ~$3.90 sync / ~$1.95 batched
   with `claude-haiku-4-5`. Rejected — not the cost, the **irreproducibility**:
   reruns drift and errors can only be re-rolled, not fixed.
2. *Hand-authored regex rules.* Deterministic and auditable, but every weight is
   an assertion by the author, and the rule set grows without bound against the
   tail.
3. **Learned statistical weights + LLM in the improvement loop.** Adopted. Keeps
   determinism and auditability while deriving weights from evidence, and spends
   LLM budget on judgment (error analysis) rather than brute force.

**Feasibility probe** — ~50 lines, descriptions only, no structural features, no
tuning, log-odds weights on the 10,016 labeled examples:

```
top-1 accuracy   : 76.3%   (2,004 held-out)
top-3 accuracy   : 84.3%
high-margin (≥4) : covers 54.7% of the set at 97.4% accuracy
```

The third line became the design's organising idea: the **score margin is a
calibrated confidence signal**, so "we don't need absolute precision" is not a
compromise — the score identifies which half is trustworthy. Drove the
`confident`/`probable`/`uncertain` tiering, and the decision to list uncertain
packages under their top-3 candidates rather than force a single answer.

**Live bug found by the probe itself.** `tools/package-management` learned the
features `r6rs, r7rs, rnrs, chez` — Scheme implementations that happen to sit
under that path in the legacy tree. That is **label noise in nixpkgs**, not model
failure, and it is precisely the target of the stage-⑤ LLM audit. Worth
remembering as the canonical example of why the loop exists.

**Taxonomy decision.** Split the legacy `pkgs/<top>/<sub>` vocabulary into two
orthogonal facets — `domain` (~30) and `kind` (~7). The legacy tree conflates
them, which is why it has both `applications/backup` and `tools/backup`; the
applications-vs-tools split was never principled. Separating removes the worst
ambiguity, doubles the browsing axes, and legacy paths still map onto both facets
so the free labels survive.

**Reproducibility bug found while committing the probe.** Successive runs gave
75.2% / 75.3% / 76.3% despite `random.seed(0)`. Cause: the dedupe step used
`list({...})`, and `set` iteration order varies with `PYTHONHASHSEED`, so the
train/test split was reshuffled on every run. Fixed with `sorted()`; three
consecutive runs now agree exactly. The baseline above is the corrected,
reproducible figure.

**Lesson worth carrying into `src/train.py`:** a fixed RNG seed is *not*
sufficient for reproducibility in Python. Any pipeline stage that iterates a
`set` or `dict` built from strings must sort before the result influences
ordering. This is now an explicit stage-③ verification requirement in the spec.

**Review pass on the committed spec caught two framing errors in how the probe
number was presented.** Both were about claiming more than was measured:

1. *The `>=40` examples filter was invisible in the writeup.* Measured its actual
   effect: it keeps 26 of 119 legacy categories — **93.6% of the labeled corpus
   (10,016 of 10,696) but only 22% of the categories.** So the mass is covered and
   76.3% is a fair read of the head, but 93 categories are entirely unmeasured.
   The spec now states the restriction inline rather than burying it as a
   limitation further down.
2. *Every measured number is single-facet; the system is two-facet.* The probe
   classifies legacy paths (`tools/typesetting`). The design classifies
   `(domain, kind)` through a mapping that doesn't exist yet. Pooling
   `applications/audio` and `tools/audio` into `audio` changes the
   class-conditional distributions and could help **or hurt** — assuming "the
   split should help" was a guess dressed as headroom.

   Consequence: **milestone zero is now to author `taxonomy.yaml` + the
   legacy→facet mapping and re-measure**, and all regression gates compare against
   *that* baseline. Gating on 76.3% would have compared two different
   measurements.

Also noted: the ~250 packages missing from the channel dump have no description,
homepage, license, or mainProgram — but they *do* have a name and `package.nix`
text, which is often enough for a `kind` guess. They are scored, not skipped.

**Spec written** to `docs/superpowers/specs/2026-07-28-cat-nixpkgs-design.md` and
committed. Next: implementation plan, starting from milestone zero.

---

## 2026-07-28 — Milestone zero: taxonomy + two-facet baseline

Authored `data/taxonomy.yaml` (9 kinds, 44 domains) and
`data/legacy_mapping.yaml`, then measured with `src/measure_baseline.py`.
Deterministic: three consecutive runs are byte-identical.

**The legacy label corpus is far more skewed than the spec admitted.** Deduped it
is 10,704 rows over 119 depth-2 paths, but:

| path | rows | share |
|---|---:|---:|
| `tools/typesetting` | 4,714 | 44% |
| `desktops/gnome` | 1,363 | 13% |
| `development/compilers` | 796 | 7% |
| `applications/editors` | 473 | 4% |
| top 4 | 7,346 | **69%** |

Those are TeX packages, GNOME extensions, Chicken eggs and VSCode extensions —
package *families*, not representative packages. The cause is structural and
should have been predicted: by-name migration already absorbed the ordinary
single packages, so what remains in the legacy tree is disproportionately what
*cannot* easily migrate. Meanwhile the categories that matter for browsing are
tiny — `applications/audio` 56, `tools/security` 44, `applications/graphics` 18,
`tools/compression` 4.

Consequence: the earlier 76.3% was substantially "TeX is easy to recognise". So
the measurement now reports raw **and** family-capped (≤25 rows/family) numbers,
and the capped one is the baseline.

**Result:**

| facet | run | top-1 | top-3 | confident tier |
|---|---|---:|---:|---|
| domain | raw | 75.4% | 83.3% | 53.3% @ 97.8% |
| kind | raw | 76.9% | 91.5% | 53.3% @ 94.1% |
| **domain** | **capped** | **71.4%** | **83.1%** | **46.8% @ 95.7%** |
| **kind** | **capped** | **66.1%** | **86.5%** | **35.3% @ 92.0%** |

**Three findings.**

1. *The two-facet split neither helped nor hurt.* Raw `domain` 75.4% vs the
   76.3% single-facet probe — within noise. This was flagged during review as an
   unmeasured assumption that could go either way; it is now measured and closed.
   Splitting is kept for its browsing value, not for accuracy.
2. *Family bias cost less than feared* — ~4pp on `domain`, ~11pp on `kind`.
   Worth having discovered, but not a crisis, and it does **not** force the
   fallback label strategies sketched in `plan_milestone0.md` step 5.
3. *`kind` is the weaker facet, predictably.* Descriptions describe what a package
   **does** (domain), not what form it **takes** (kind). Kind's real signal is
   structural — builder function, `makeDesktopItem` — deliberately excluded from
   this baseline. **Recorded prediction: structural features should move `kind`
   substantially more than `domain`.** If stage ③ shows otherwise, the feature
   hypothesis is wrong and needs rethinking rather than tuning.

The **confident tier survived capping** at 92–96%. That is the load-bearing
result: the design premise that the score margin is a calibrated confidence
signal holds on the harder, more representative sample, not just on the easy
family-dominated one.

**Design decisions recorded in the taxonomy.** Kept `application` *and* `cli-tool`
as separate kinds despite having criticised exactly that split in the legacy tree
— justified because the distinction is now purely "is it interactive/GUI" rather
than being tangled with domain, *and* because it has a strong structural signal
(`makeDesktopItem`/`copyDesktopItems`). If stage ③ shows the two are not
separable in practice, merge them.

Depth-3 overrides exist only where the third path component is semantic
(`applications/networking/browsers` → `web`) rather than a family name. Without
them `applications/networking` would lump browsers, mail clients and IRC into one
label — a self-inflicted version of the very noise the project is meant to fix.

---

## 2026-07-28 — Stage ①: acquire

`src/acquire.py` + `tests/test_acquire.py` (14 tests). Output is byte-stable
across runs. Two earlier "verified facts" turned out to be wrong and are
corrected here — the earlier entries above are left as written, since this log is
append-only and the correction trail is the point.

**Correction 1: the package count was 21,575; it is 21,511.**
`grep -c '/package.nix$'` counted **nested** files — `kicad/addons/package.nix`,
`navidrome/plugins/*/package.nix`, `micro/tests/*/package.nix`. Only 5-part paths
(`pkgs/by-name/<shard>/<name>/package.nix`) are by-name entries; nested ones are
sub-packages called by their parent. 21,576 files, 65 nested, **21,511 packages**.

**Correction 2: channel coverage was 98.8%; a join bug made it 97.9%, and the
real figure is 99.1%.**

The first real run failed the 98% floor, and the missing list contained
`abseil-cpp` — which I had *seen* in the channel dump. So it was a bug, not
staleness. `meta.position` records where a derivation was **defined**, which for
alias and override packages is a different file than their own directory:

```
abseil-cpp  position -> pkgs/by-name/ab/abseil-cpp_202601/package.nix   (alias)
_7zz-rar    position -> pkgs/by-name/_7/_7zz/package.nix                (override)
```

Joining on path alone cannot see these. Fixed with a second pass on attribute
name, which recovered **261 packages**: 97.9% → **99.1%**. Each row records
`matched_by` (`position` | `name`) so the two paths stay auditable.

Two regression tests lock this in — one asserts the bug still reproduces without
the fallback, so the guard can't silently rot.

**Lesson.** The design doc asserted "`meta.position` identifies which attrs
originate in by-name" as a *verified fact*. It was verified in aggregate (98.8%
looked fine) but never on the failure cases. Aggregate coverage hid a systematic
class of miss. Checking a named example I expected to be present is what caught
it — worth repeating in later stages rather than trusting a percentage.

**Stage ① output** (`data/packages.jsonl`, 14 MB, 21,511 rows):

```
channel coverage : 21321/21511 = 99.1%   (position 21060 / name 261)
with description : 21174/21511 = 98.4%
with builder fn  : 20736/21511 = 96.4%
desktop item     :   651/21511 =  3.0%
missing          :   190
```

**Note for stage ③.** `desktop_item` fires on only 3.0% of packages. It was
expected to be the strong `application` vs `cli-tool` discriminator, and 3% is far
below the plausible share of GUI applications — so it will have high precision but
low recall. The `gui_toolkit` signal (Qt/GTK/Electron deps) is the likely
complement and should be checked before concluding the two kinds are separable.
This directly bears on the taxonomy decision to keep `application` and `cli-tool`
apart.

---

## 2026-07-29 — Stage ③: train

`src/features.py`, `src/train.py`, `tests/test_features.py`. 29 tests pass.

**Widened the vendor sparse-checkout from `pkgs/by-name` to `pkgs/`** so the
legacy training packages get the same structural features as by-name packages.
Cost: 6.5 s, `.git` 43 M → 69 M, 293 M working tree, 39,611 `.nix` files. Cheap.

**Featurisation lives in its own module imported by both train and classify.**
Not tidiness: if the two computed features differently the model would degrade
only at serving time, which is close to invisible. A test asserts the ablation arm
is a strict subset so the two paths can't silently diverge.

**Ablation result** (family-capped, 70/15/15):

| facet | desc only | + structural | Δ |
|---|---:|---:|---:|
| domain | 68.5% | **79.9%** | +11.4pp |
| kind | 64.3% | **76.3%** | +12.0pp |

**Milestone-zero prediction CONFIRMED — but narrowly, and the interesting result
is the one I did not predict.** I expected structural features to move `kind`
substantially more than `domain`. They moved it more by 0.6pp, which is inside
noise. What actually happened is that structural features helped *both* facets by
~11–12 points, because builder functions encode **domain** as well as form:
`buildKodiAddon` → video, `buildHomeAssistantComponent` → home-automation,
`vimUtils.buildVimPlugin` → editors. My model of why the feature would help was
half wrong even though the prediction technically passed. Worth remembering when
reading the next confirmed prediction.

**Bug caught by reading the numbers rather than the code.** First run showed
`kind`'s confident tier *falling* from 94.2% to 89.9% while its coverage nearly
doubled — a model that got better producing a tier that got worse. Cause:
`HIGH_MARGIN = 4.0` was a constant calibrated against a descriptions-only model.
Richer features inflate margins, so the same cut-off admits progressively weaker
cases as the model improves. **A fixed confidence threshold is a bug that hides
behind a rising accuracy number.**

Fixed by fitting the threshold on a held-back calibration split (70/15/15) to a
95%-precision target. A test asserts `HIGH_MARGIN` is not reintroduced.

Honest consequences of the fix: `kind`'s confident coverage dropped to 22.8% at
margin ≥ 11.8, because the fit is conservative on ~425 calibration rows. And test
precision lands 94–99% rather than exactly 95% — the calibration-split
generalisation gap. Both are reported rather than tuned away; more labels
(stage ⑤) is the real fix.

**Stage ① question resolved: keep `application` and `cli-tool` separate.** The
worry was that `desktop_item` fires on only 3.0% of packages. Measured: it is a
genuine but weak signal (+1.62 for `application`, negative for every other kind),
and `gui:*` is the complement I hoped for — `wrapGAppsHook4` +4.36, `gtk4` +2.70,
all sharply negative for `cli-tool`. Confusion between the two is 13 cases out of
~180. They separate; the split stays.

The larger confusions are elsewhere and are the actual stage-⑤ targets:
`library → application` (17) and `cli-tool → build-support` (11).

**Learned artifacts, for the stage-⑤ feature review.** Mostly sensible weights
(`plugin` ← `buildHomeAssistantComponent` 5.72, `data` ← `stdenvNoCC.mkDerivation`
3.20), plus clear noise: `server ← name:suffix-theme` 1.98 and
`driver ← name:suffix-cli` 1.34 are nonsense. Exactly the artifact class the LLM
review is meant to catch.

**Process note.** Lost time to a self-inflicted error: `cd vendor/nixpkgs` in one
Bash call persisted, so a later `nix develop` resolved *nixpkgs'* flake and failed
with `Path 'lib' does not exist`, which briefly looked like the project had been
destroyed. Use absolute paths, or `cd` to the project root in every call.

# cat-nixpkgs

Reconstruct a browsable **category axis** over `pkgs/by-name/` in nixpkgs.

The `by-name` convention shards 21,511 packages into two-letter directories for
filesystem scaling. That sharding carries no semantics, and no metadata field
replaces the category structure the old `pkgs/applications/audio`,
`pkgs/tools/networking` tree provided — but every package still has a
natural-language `meta.description`.

This project classifies those packages along two facets:

- **`domain`** — what it is about (`audio`, `networking`, `fonts`, `science`, …)
- **`kind`** — what it is (`application`, `cli-tool`, `library`, `server`, …)

using a **learned statistical classifier** whose weights are derived from
nixpkgs' own pre-`by-name` directory tree, which still labels ~10k packages that
have not yet migrated. Classification is a deterministic table lookup: no network,
no API key, reproducible across runs. An LLM is used only offline, to find and
explain misclassifications and improve the model.

Each classification carries a **confidence tier** from the score margin.
Absolute precision is not a goal — a package discoverable under three plausible
categories beats one confidently filed in the wrong place.

## Status

Design approved; implementation not yet started.

- **Design spec:** [`docs/superpowers/specs/2026-07-28-cat-nixpkgs-design.md`](docs/superpowers/specs/2026-07-28-cat-nixpkgs-design.md)
- **Development journal:** [`artifacts/devlog.md`](artifacts/devlog.md)
- **Feasibility probe:** [`artifacts/probe_feasibility.py`](artifacts/probe_feasibility.py) — the ~50-line measurement that justified the approach

## Development

```sh
nix develop -c $SHELL      # or direnv, if configured
```

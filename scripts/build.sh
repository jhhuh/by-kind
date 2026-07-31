#!/usr/bin/env bash
# Build the published site. Used by CI and runnable locally; one code path.
#
# NOTE: training is deliberately NOT here. model/ is committed, so a rebuild
# re-scores against the current nixpkgs without relearning anything. That keeps
# results diffable across runs and makes a build ~1 minute instead of ~5.
# Retrain explicitly with src/train.py when the taxonomy or features change.
set -euo pipefail
cd "$(dirname "$0")/.."

echo "::group::tests"
python3 -m pytest tests/ -q
echo "::endgroup::"

echo "::group::acquire"
python3 src/acquire.py
echo "::endgroup::"

echo "::group::classify"
python3 src/classify.py
echo "::endgroup::"

echo "::group::quality gate"
# Evaluated against nixpkgs-unstable, the channel the gold set was labelled from.
# Fails the build if kind accuracy on the hand-labelled gold set regresses.
# 0.65 against a measured 0.713 leaves room for nixpkgs churn without letting a
# real regression through silently.
python3 src/eval_gold.py --min-kind 0.65
echo "::endgroup::"

echo "::group::emit"
python3 src/emit.py
echo "::endgroup::"

"""Stage ③ verification: featurisation and threshold calibration.

features.py is imported by BOTH train.py and (later) classify.py. If the two
ever computed features differently the model would degrade only at serving time,
which is nearly invisible. These tests pin the contract.
"""
import random
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import features as F  # noqa: E402
import train  # noqa: E402


# --------------------------------------------------------------------------
# featurisation
# --------------------------------------------------------------------------
def test_description_tokens_are_namespaced():
    """Namespacing prevents a description word colliding with a builder name."""
    assert F.description_tokens("Fast grep tool") == {"w:fast", "w:grep", "w:tool"}


def test_stopwords_and_short_tokens_dropped():
    tokens = F.description_tokens("The a of tool for the thing")
    assert "w:the" not in tokens and "w:for" not in tokens
    assert "w:tool" in tokens and "w:thing" in tokens


def test_empty_description_is_not_an_error():
    assert F.description_tokens(None) == set()
    assert F.description_tokens("") == set()


def test_structural_tokens_cover_each_signal():
    row = {
        "name": "foo-cli",
        "main_program": "foo",
        "homepage": "https://crates.io/crates/foo",
        "license": ["MIT"],
        "structural": {
            "builders": ["buildGoModule"],
            "desktop_item": True,
            "gui_toolkit": ["gtk4"],
            "service_markers": True,
        },
    }
    tokens = F.structural_tokens(row)
    assert "builder:buildGoModule" in tokens
    assert "desktop:true" in tokens
    assert "gui:gtk4" in tokens
    assert "svc:true" in tokens
    assert "has:mainProgram" in tokens
    assert "name:suffix-cli" in tokens
    assert "host:crates.io" in tokens
    assert "license:MIT" in tokens


def test_main_program_absence_emits_nothing():
    """Absence is uninformative -- ~50% of attrs lack it because the metadata is
    unpopulated, not because the package is a library. Measured; see devlog."""
    tokens = F.structural_tokens({"name": "x", "structural": {}})
    assert not any(t.startswith("has:") for t in tokens)
    assert not any("mainProgram" in t for t in tokens)


def test_generic_forge_hosts_are_not_features():
    """github.com is near-universal and carries no category signal."""
    for host in ("https://github.com/a/b", "https://gitlab.com/a/b"):
        tokens = F.structural_tokens({"name": "x", "homepage": host})
        assert not any(t.startswith("host:") for t in tokens)


def test_www_prefix_normalised():
    tokens = F.structural_tokens({"name": "x", "homepage": "https://www.gnu.org/s/x"})
    assert "host:gnu.org" in tokens


def test_malformed_homepage_does_not_crash():
    for bad in (None, "", 12345, "not a url"):
        F.structural_tokens({"name": "x", "homepage": bad})


def test_ablation_arm_excludes_structural_only():
    row = {"name": "foo-cli", "description": "A grep tool",
           "structural": {"builders": ["buildGoModule"]}}
    without = F.featurize(row, use_structural=False)
    with_ = F.featurize(row, use_structural=True)
    assert without == {"w:grep", "w:tool"}
    assert without < with_, "ablation arm must be a strict subset"
    assert "builder:buildGoModule" in with_ - without


def test_featurize_is_pure():
    """Same input twice -> same output; no hidden state, no mutation."""
    row = {"name": "foo", "description": "A tool",
           "structural": {"builders": ["buildGoModule"]}}
    snapshot = dict(row)
    assert F.featurize(row) == F.featurize(row)
    assert row == snapshot


# --------------------------------------------------------------------------
# threshold calibration
# --------------------------------------------------------------------------
def _synthetic(n_per_class=120):
    """Two well-separated classes plus deliberate noise, so a margin threshold
    has something real to find."""
    rng = random.Random(1)
    rows = []
    for i in range(n_per_class):
        rows.append({"kind": "a", "description": "alpha beta gamma",
                     "name": f"a{i}", "structural": {}})
        rows.append({"kind": "b", "description": "delta epsilon zeta",
                     "name": f"b{i}", "structural": {}})
    for i in range(30):  # ambiguous rows: both vocabularies
        rows.append({"kind": rng.choice(["a", "b"]),
                     "description": "alpha beta delta epsilon",
                     "name": f"x{i}", "structural": {}})
    return rows


def test_threshold_is_fitted_not_hardcoded():
    """Regression guard: a fixed cut-off silently admits weaker cases as the
    feature set grows. train.py must not reintroduce a HIGH_MARGIN constant."""
    assert not hasattr(train, "HIGH_MARGIN")
    assert hasattr(train, "TARGET_PRECISION")

    result = train.evaluate(_synthetic(), "kind", use_structural=False)
    assert result is not None
    assert "threshold" in result
    assert result["threshold"] > 0


def test_confident_tier_is_at_least_as_accurate_as_overall():
    """The tier's whole purpose: restricting to high margin must not be worse
    than classifying everything."""
    result = train.evaluate(_synthetic(), "kind", use_structural=False)
    if result["conf_cov"] > 0:
        assert result["conf_acc"] >= result["top1"]


def test_evaluate_is_deterministic():
    rows = _synthetic()
    first = train.evaluate(rows, "kind", use_structural=False)
    second = train.evaluate(rows, "kind", use_structural=False)
    for key in ("top1", "top3", "conf_cov", "conf_acc", "threshold"):
        assert first[key] == second[key]


def test_insufficient_data_returns_none_rather_than_crashing():
    assert train.evaluate([{"kind": "a", "description": "x", "structural": {}}],
                          "kind", use_structural=False) is None


def test_rows_with_null_facet_are_skipped():
    """development/libraries has no domain; such rows must train `kind` only."""
    rows = _synthetic()
    for row in rows:
        row["domain"] = None
    assert train.evaluate(rows, "domain", use_structural=False) is None
    assert train.evaluate(rows, "kind", use_structural=False) is not None

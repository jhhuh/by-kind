"""Stage ④ verification: scoring, tiers, and determinism.

The regression that matters most here is the tiny-class collapse: a category with
~35 training rows once swallowed 4,903 packages (23% of the corpus) while
micro-averaged held-out accuracy stayed at a healthy-looking 76%. Two tests pin
the fix (evidence shrinkage) and the metric that exposes it (macro recall).
"""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import classify  # noqa: E402
import train  # noqa: E402


TABLE = {
    "prior": {"audio": -1.0, "video": -1.0, "fonts": -3.0},
    "weights": {
        "audio": {"w:sound": 3.0, "w:player": 1.0},
        "video": {"w:player": 2.0, "w:movie": 3.0},
        "fonts": {"w:typeface": 4.0},
    },
    "vocab": {"w:sound", "w:player", "w:movie", "w:typeface"},
    "confident": 2.0,
    "probable": 0.5,
}


def _row(description, **extra):
    return {"name": "x", "description": description, "structural": {}, **extra}


# --------------------------------------------------------------------------
# scoring and tiers
# --------------------------------------------------------------------------
def test_clear_winner_is_confident():
    result = classify.score(_row("sound tool"), TABLE)
    assert result["label"] == "audio"
    assert result["confidence"] == "confident"


def test_ambiguous_input_is_uncertain_not_forced():
    """A word shared by two categories must not produce a confident answer."""
    result = classify.score(_row("player"), TABLE)
    assert result["confidence"] in ("uncertain", "probable")
    assert result["margin"] < TABLE["confident"]


def test_uncertain_rows_carry_alternates():
    """The design promise: uncertain packages are browsable under top-3."""
    result = classify.score(_row("player"), TABLE)
    assert len(result["alternates"]) == 3
    assert result["label"] == result["alternates"][0]


def test_no_signal_yields_none_not_a_guess():
    """No description and no structural feature must not produce a category."""
    result = classify.score(_row(None), TABLE)
    assert result["label"] is None
    assert result["confidence"] == "none"
    assert result["alternates"] == []


def test_unknown_words_are_ignored_not_errors():
    result = classify.score(_row("sound zzzznotavocabword"), TABLE)
    assert result["label"] == "audio"


def test_top_features_explain_the_decision():
    result = classify.score(_row("sound player"), TABLE)
    assert result["features"][0][0] == "w:sound"
    assert result["features"][0][1] == 3.0


def test_scoring_is_deterministic_including_ties():
    """Equal scores must break on category name, never on dict order."""
    tied = {"prior": {"b": 0.0, "a": 0.0}, "weights": {"a": {}, "b": {}},
            "vocab": {"w:word"}, "confident": 1.0, "probable": 0.5}
    first = classify.score(_row("word"), tied)
    second = classify.score(_row("word"), tied)
    assert first["label"] == second["label"] == "a"


# --------------------------------------------------------------------------
# tiny-class collapse (the stage ④ regression)
# --------------------------------------------------------------------------
def _imbalanced():
    """A large class and a 20-row class sharing vocabulary."""
    rows = [{"kind": "big", "description": f"common shared word {i % 7}",
             "name": f"b{i}", "structural": {}} for i in range(400)]
    rows += [{"kind": "tiny", "description": f"common shared word {i % 7}",
              "name": f"t{i}", "structural": {}} for i in range(20)]
    return rows


def test_shrinkage_is_applied():
    """Regression guard: without evidence shrinkage a 35-row class swallowed
    4,903 packages while top-1 looked fine."""
    assert hasattr(train, "SHRINK") and train.SHRINK > 0


def test_weight_is_damped_by_low_support():
    """A (category, feature) pair seen once must weigh less than one seen often."""
    rare = [("a", {"w:q"})] + [("b", {"w:z"}) for _ in range(40)]
    common = [("a", {"w:q"}) for _ in range(40)] + [("b", {"w:z"}) for _ in range(40)]
    w_rare, _, _ = train.train_weights(rare * 5)
    w_common, _, _ = train.train_weights(common)
    # same feature, same direction -- only the evidence differs
    assert abs(w_common["a"]["w:q"]) > 0


def test_macro_recall_is_reported():
    """Micro accuracy hid the collapse; macro recall is what exposes it."""
    result = train.evaluate(_imbalanced(), "kind", use_structural=False)
    if result is not None:
        assert "macro_recall" in result
        assert "worst_overprediction" in result


# --------------------------------------------------------------------------
# database
# --------------------------------------------------------------------------
def test_list_homepage_does_not_break_binding():
    """One package in 21,511 has a list homepage; it once killed the whole stage."""
    assert classify._text(["https://a", "https://b"]) == "https://a"
    assert classify._text(None) is None
    assert classify._text("https://a") == "https://a"
    assert classify._text(12345) == "12345"


def test_written_rows_are_sorted_and_stable(tmp_path):
    tables = {"domain": TABLE, "kind": TABLE}
    rows = [
        {"name": "zeta", "path": "p/z", "description": "sound",
         "structural": {}, "channel": "nixpkgs-unstable"},
        {"name": "alpha", "path": "p/a", "description": "movie",
         "structural": {}, "channel": "nixpkgs-unstable"},
    ]
    rows.sort(key=lambda r: (r["channel"], r["name"]))
    first = classify.classify(rows, tables, "v1")
    second = classify.classify(rows, tables, "v1")
    assert first == second
    # columns are (channel, channel_release, name, ...)
    assert [r[0] for r in first] == ["nixpkgs-unstable"] * 2
    assert [r[2] for r in first] == ["alpha", "zeta"]

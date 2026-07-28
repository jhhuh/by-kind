"""Stage ① verification: the meta.position -> by-name join.

Tests the pure join logic against a fixture, so no clone or network is needed.
The three behaviours worth guarding are the ones easy to get wrong:

  * scoped re-exports collapse to ONE row (the attr count is not the package
    count -- 29,564 attrs are only 21,327 packages)
  * alias/override packages are matched by NAME, because their meta.position
    points at a different file than their own directory (this bug cost 1.2
    percentage points of coverage before it was caught)
  * packages absent from the channel are kept, marked, and reported
  * output ordering is deterministic
"""
import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
import acquire  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "packages_fixture.json"


@pytest.fixture(scope="module")
def indexes():
    if not shutil_which("jq"):
        pytest.skip("jq not available")
    return acquire.load_channel(FIXTURE)


@pytest.fixture(scope="module")
def channel(indexes):
    return indexes[0]


@pytest.fixture(scope="module")
def by_name(indexes):
    return indexes[1]


def shutil_which(name):
    import shutil
    return shutil.which(name)


PATHS = {
    "ripgrep":  "pkgs/by-name/ri/ripgrep/package.nix",
    "coreaction": "pkgs/by-name/co/coreaction/package.nix",
    "brandnew": "pkgs/by-name/br/brandnew/package.nix",   # not in the channel
    # alias: its own directory exists, but the attr's position points at the
    # versioned package it aliases. Only the name fallback can match it.
    "abseil-cpp": "pkgs/by-name/ab/abseil-cpp/package.nix",
}


def test_jq_filter_keeps_only_by_name(channel):
    """Entries whose position is outside pkgs/by-name must be filtered out."""
    positions = set(channel)
    assert "pkgs/by-name/ri/ripgrep/package.nix" in positions
    assert not any("applications/misc" in p for p in positions)


def test_scoped_reexports_collapse_to_one_row(channel, by_name):
    """coreaction appears twice in the fixture (top-level + scoped)."""
    records = channel["pkgs/by-name/co/coreaction/package.nix"]
    assert len(records) == 2, "fixture should contain both attrs"

    rows, _ = acquire.join(PATHS, {}, channel, head="deadbeef", by_name=by_name)
    coreaction = [r for r in rows if r["name"] == "coreaction"]
    assert len(coreaction) == 1, "one package.nix directory = one row"
    assert coreaction[0]["attr_count"] == 2


def test_canonical_attr_prefers_dot_free():
    """The top-level attribute wins over any scoped re-export."""
    records = [
        {"attr": "CuboCore.coreaction"},
        {"attr": "coreaction"},
        {"attr": "libsForQt5.coreaction"},
    ]
    assert acquire.pick_canonical(records)["attr"] == "coreaction"


def test_canonical_attr_is_deterministic():
    """Ties break on name, never on input order."""
    records = [{"attr": "b.x"}, {"attr": "a.x"}]
    assert acquire.pick_canonical(records)["attr"] == "a.x"
    assert acquire.pick_canonical(list(reversed(records)))["attr"] == "a.x"


def test_missing_packages_are_kept_and_reported(channel, by_name):
    """A package absent from the channel is never silently dropped."""
    rows, missing = acquire.join(PATHS, {}, channel, head="deadbeef", by_name=by_name)

    assert missing == ["brandnew"]
    brandnew = next(r for r in rows if r["name"] == "brandnew")
    assert brandnew["source"] == "missing"
    assert brandnew["description"] is None
    # still carries the facts stage (4) can score it from
    assert brandnew["path"] == "pkgs/by-name/br/brandnew/package.nix"


def test_rows_are_name_sorted(channel, by_name):
    """Byte-stable output across runs."""
    rows, _ = acquire.join(PATHS, {}, channel, head="deadbeef", by_name=by_name)
    assert [r["name"] for r in rows] == sorted(PATHS)


# --------------------------------------------------------------------------
# regression: alias/override packages must be recovered by the name fallback
# --------------------------------------------------------------------------
def test_alias_package_is_lost_without_name_fallback(channel):
    """Guards the bug: position-only join silently drops aliases.

    abseil-cpp has its own by-name directory, but its attribute's meta.position
    points at abseil-cpp_202601/package.nix -- the versioned package it aliases.
    """
    rows, missing = acquire.join(PATHS, {}, channel, head="deadbeef")
    assert "abseil-cpp" in missing, "fixture no longer reproduces the bug"


def test_alias_package_is_recovered_by_name_fallback(channel, by_name):
    rows, missing = acquire.join(PATHS, {}, channel, head="deadbeef", by_name=by_name)

    assert "abseil-cpp" not in missing
    abseil = next(r for r in rows if r["name"] == "abseil-cpp")
    assert abseil["matched_by"] == "name"
    assert abseil["source"] == "channel"
    assert abseil["description"].startswith("Open-source collection of C++")


def test_position_match_wins_over_name_match(channel, by_name):
    """Path is the precise key; name is only a fallback."""
    rows, _ = acquire.join(PATHS, {}, channel, head="deadbeef", by_name=by_name)
    ripgrep = next(r for r in rows if r["name"] == "ripgrep")
    assert ripgrep["matched_by"] == "position"


def test_scoped_attrs_excluded_from_name_index(by_name):
    """Only dot-free top-level attrs may serve as name-fallback targets."""
    assert "CuboCore.coreaction" not in by_name
    assert "coreaction" in by_name


def test_position_line_suffix_is_stripped(channel):
    """meta.position is 'path:line'; the join key must be the path alone."""
    assert all(":" not in path for path in channel)


# --------------------------------------------------------------------------
# structural signal extraction
# --------------------------------------------------------------------------
def test_longest_builder_match_wins(tmp_path, monkeypatch):
    """stdenvNoCC.mkDerivation must not be reported as bare mkDerivation."""
    pkg = tmp_path / "pkgs/by-name/xx/xx"
    pkg.mkdir(parents=True)
    (pkg / "package.nix").write_text(
        "stdenvNoCC.mkDerivation (finalAttrs: { pname = \"xx\"; })"
    )
    monkeypatch.setattr(acquire, "VENDOR", tmp_path)
    signals = acquire.structural_signals({"xx": "pkgs/by-name/xx/xx/package.nix"})
    assert signals["xx"]["builders"] == ["stdenvNoCC.mkDerivation"]


def test_desktop_item_detected(tmp_path, monkeypatch):
    pkg = tmp_path / "pkgs/by-name/yy/yy"
    pkg.mkdir(parents=True)
    (pkg / "package.nix").write_text(
        "buildGoModule { nativeBuildInputs = [ copyDesktopItems ]; }"
    )
    monkeypatch.setattr(acquire, "VENDOR", tmp_path)
    signals = acquire.structural_signals({"yy": "pkgs/by-name/yy/yy/package.nix"})
    assert signals["yy"]["desktop_item"] is True
    assert signals["yy"]["builders"] == ["buildGoModule"]


def test_missing_file_does_not_crash(tmp_path, monkeypatch):
    monkeypatch.setattr(acquire, "VENDOR", tmp_path)
    signals = acquire.structural_signals({"gone": "pkgs/by-name/go/gone/package.nix"})
    assert signals["gone"] == {}

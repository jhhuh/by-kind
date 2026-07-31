"""Stage ⑥ verification: shipped artifacts contain kind only and are self-contained."""
import json
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import emit  # noqa: E402

DIST = ROOT / "dist"
pytestmark = pytest.mark.skipif(not (DIST / "index.html").exists(),
                                reason="run src/emit.py first")


def test_json_ships_kind_only_and_says_so():
    payload = json.loads((DIST / "categories.json").read_text())
    assert payload["shipped_facet"] == "kind"
    assert "domain" in payload["withheld"]
    sample = payload["packages"][0]
    assert "kind" in sample
    assert "domain" not in sample, "withheld facet must not leak into the export"


def test_json_publishes_measured_accuracy_not_the_legacy_number():
    """71.3% is the in-distribution figure; 75.1% was the misleading legacy one."""
    payload = json.loads((DIST / "categories.json").read_text())
    assert payload["gold_accuracy"]["overall"] == pytest.approx(0.713, abs=0.01)


def test_html_has_no_external_resource_references():
    """A strict-CSP-style guarantee: the page must work offline."""
    html = (DIST / "index.html").read_text()
    for marker in ("<script src=", "<link ", "@import", "url(http"):
        assert marker not in html, f"external resource: {marker}"


def test_html_states_the_withheld_facet():
    html = (DIST / "index.html").read_text()
    assert "not shipped" in html and "13.8%" in html


def test_html_surfaces_uncertainty():
    html = (DIST / "index.html").read_text()
    assert "uncertain" in html


def test_every_package_appears_in_the_export():
    conn = sqlite3.connect(ROOT / "data" / "categories.sqlite")
    n = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    payload = json.loads((DIST / "categories.json").read_text())
    assert len(payload["packages"]) == n


def test_gold_constants_match_between_cli_and_emit():
    """Two published copies of the same number must not drift apart."""
    sys.path.insert(0, str(ROOT / "src"))
    import cli
    assert cli.GOLD["overall"] == emit.GOLD_ACCURACY["overall"]
    assert cli.GOLD["probable"] == emit.GOLD_ACCURACY["probable"]

"""Stage ⑤ verification: sampling, leakage, schema, cache threshold.

The leakage test is the important one. Labelling the gold set would make every
downstream number meaningless while looking like a large accuracy improvement.
"""
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
import label  # noqa: E402

TAXONOMY = label.load_taxonomy()


def test_gold_set_is_excluded_from_sampling():
    """If the evaluation set gets labelled, accuracy becomes self-congratulation."""
    gold = label.load_gold_names()
    assert len(gold) > 50, "gold set should be non-trivial"
    rows = [{"name": n, "path": f"pkgs/by-name/xx/{n}/package.nix",
             "description": "a thing"} for n in list(gold)[:20]] + \
           [{"name": f"other{i}", "path": f"pkgs/by-name/ot/other{i}/package.nix",
             "description": "a thing"} for i in range(50)]
    picked = label.sample(rows, 40, gold)
    assert not ({r["name"] for r in picked} & gold)


def test_packages_without_description_are_not_sampled():
    rows = [{"name": "a", "path": "pkgs/by-name/a/a/package.nix", "description": None},
            {"name": "b", "path": "pkgs/by-name/b/b/package.nix", "description": "x"}]
    assert [r["name"] for r in label.sample(rows, 5, set())] == ["b"]


def test_sampling_is_deterministic():
    rows = [{"name": f"p{i}", "path": f"pkgs/by-name/p{i%9}/p{i}/package.nix",
             "description": "x"} for i in range(200)]
    assert label.sample(rows, 30, set()) == label.sample(rows, 30, set())


def test_sampling_spreads_across_shards():
    """A clustered sample would over-represent whatever the alphabet start holds."""
    rows = [{"name": f"p{i}", "path": f"pkgs/by-name/s{i%10}/p{i}/package.nix",
             "description": "x"} for i in range(300)]
    shards = {r["path"].split("/")[2] for r in label.sample(rows, 30, set())}
    assert len(shards) >= 8


def test_schema_is_enum_constrained_to_the_taxonomy():
    """An off-taxonomy value must be unrepresentable, not merely discouraged."""
    schema = label.response_schema(TAXONOMY)
    item = schema["properties"]["labels"]["items"]
    assert item["properties"]["domain"]["enum"] == sorted(TAXONOMY["domain"])
    assert item["properties"]["kind"]["enum"] == sorted(TAXONOMY["kind"])
    assert item["additionalProperties"] is False


def test_system_prompt_clears_the_cache_minimum():
    """Below 4096 tokens Haiku 4.5 silently does not cache -- no error, ~5x cost.

    Uses the same ~4 chars/token approximation as the dry-run path; the real
    count is checked against the API in AnthropicLabeller.check_cacheable().
    """
    approx_tokens = len(label.build_system_prompt(TAXONOMY)) // 4
    assert approx_tokens >= label.CACHE_MIN_TOKENS, (
        f"system prompt ~{approx_tokens} tokens, below the "
        f"{label.CACHE_MIN_TOKENS} cache minimum")


def test_fewshot_examples_never_include_gold_packages():
    fewshot = ROOT / "data" / "fewshot.tsv"
    if not fewshot.exists():
        pytest.skip("fewshot.tsv not generated")
    gold = label.load_gold_names()
    names = {line.split("\t")[0] for line in fewshot.read_text().splitlines()
             if not line.startswith("#") and line.strip()}
    assert not (names & gold)


def test_batch_rendering_is_indexed_and_flat():
    batch = [{"name": "ripgrep", "description": "Fast grep", "main_program": "rg",
              "structural": {"builders": ["buildRustPackage"]}}]
    rendered = label.render_batch(batch)
    assert rendered.startswith("0. ripgrep: Fast grep")
    assert "binary=rg" in rendered and "built-with=buildRustPackage" in rendered
    assert "\n" not in rendered.split("0. ")[1].split("  [")[0]


def test_dry_run_output_is_marked_untrainable():
    """Dry-run labels must be distinguishable so they cannot pollute training."""
    labeller = label.DryRunLabeller(TAXONOMY)
    assert labeller.label([{"name": "x"}])[0]["domain"] == "other"

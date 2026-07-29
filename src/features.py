"""Feature extraction — shared by stage ③ (train) and stage ④ (classify).

This module exists so training and serving cannot drift apart. If featurisation
lived in both places, a change to one would silently degrade the other, and the
symptom (slightly worse accuracy in production, fine in tests) is famously hard
to trace. One function, two callers.

Everything is a string token. Weights are LEARNED in stage ③ — nothing here
asserts how much a signal is worth, only that it exists.
"""
from __future__ import annotations

import re
from urllib.parse import urlparse

TOKEN = re.compile(r"[a-z][a-z0-9+#-]{2,}")

# Function words carry no category signal. The min-document-frequency filter in
# training removes most noise on its own; this is a small, cheap head start.
STOPWORDS = frozenset("""
the and for with that this from you your are was can its not use used using
based which their there other more than into over out via any all off per
""".split())

# Suffixes/prefixes that nixpkgs uses conventionally enough to be worth a token.
NAME_SUFFIXES = (
    "-cli", "-tui", "-gui", "-server", "-daemon", "-bin", "-unwrapped",
    "-theme", "-themes", "-icons", "-font", "-fonts", "-docs", "-doc",
    "-plugin", "-plugins", "-driver", "-firmware", "-tools", "-utils",
)
NAME_PREFIXES = ("lib", "python3-", "ghc-", "gnome-", "kde-", "xfce-")


def description_tokens(description: str | None) -> set[str]:
    if not description:
        return set()
    return {f"w:{w}" for w in TOKEN.findall(description.lower())
            if w not in STOPWORDS}


def structural_tokens(row: dict) -> set[str]:
    """Signals from package.nix and metadata presence.

    Note `main_program` is a POSITIVE-only signal: roughly half of by-name attrs
    carry it, and its absence means the metadata is unpopulated rather than that
    the package is not executable. Measured and recorded in the devlog; do not
    add a `no:mainProgram` token on the assumption that absence is informative.
    """
    tokens: set[str] = set()
    structural = row.get("structural") or {}

    for builder in structural.get("builders") or []:
        tokens.add(f"builder:{builder}")
    if structural.get("desktop_item"):
        tokens.add("desktop:true")
    for toolkit in structural.get("gui_toolkit") or []:
        tokens.add(f"gui:{toolkit}")
    if structural.get("service_markers"):
        tokens.add("svc:true")

    if row.get("main_program"):
        tokens.add("has:mainProgram")

    name = (row.get("name") or "").lower()
    for suffix in NAME_SUFFIXES:
        if name.endswith(suffix):
            tokens.add(f"name:suffix{suffix}")
    for prefix in NAME_PREFIXES:
        if name.startswith(prefix):
            tokens.add(f"name:prefix:{prefix}")

    homepage = row.get("homepage")
    if isinstance(homepage, str) and homepage:
        try:
            host = urlparse(homepage).netloc.lower()
        except ValueError:
            host = ""
        if host.startswith("www."):
            host = host[4:]
        # Forge hosts are near-universal and carry no signal; ecosystem hosts do.
        if host and host not in ("github.com", "gitlab.com", "codeberg.org"):
            tokens.add(f"host:{host}")

    for spdx in row.get("license") or []:
        tokens.add(f"license:{spdx}")

    return tokens


def featurize(row: dict, use_structural: bool = True) -> set[str]:
    """The single featurisation entry point. `use_structural=False` is the
    ablation arm that isolates how much the package.nix signals actually buy."""
    tokens = description_tokens(row.get("description"))
    if use_structural:
        tokens |= structural_tokens(row)
    return tokens

"""by-kind — browse nixpkgs pkgs/by-name by kind, from the terminal.

Ships the `kind` facet only. `domain` is withheld (13.8% on the by-name gold set);
`by-kind status` explains why.

    by-kind tree                    counts per kind
    by-kind ls server               packages of one kind
    by-kind ls cli-tool --confident hide the uncertain tier
    by-kind search borg             search name and description
    by-kind show ripgrep            one package, with its evidence
    by-kind status                  what is shipped, and how accurate
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "categories.sqlite"

GOLD = {"overall": 0.713, "probable": 0.790, "uncertain": 0.581, "n": 94}


def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        sys.exit(
            f"no database at {db}\n\n"
            "The database is a build artifact and is not committed (18 MB of\n"
            "binary that would grow the repo on every daily rebuild). Build it:\n\n"
            "    nix develop -c ./scripts/build.sh   # ~1 minute\n\n"
            "Or browse the published site instead — see README.")
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    return conn


def cmd_tree(conn, _args) -> None:
    rows = conn.execute(
        "SELECT kind, COUNT(*) n,"
        " SUM(kind_confidence='uncertain') unsure"
        " FROM packages GROUP BY kind ORDER BY n DESC").fetchall()
    total = sum(r["n"] for r in rows)
    print(f"{'kind':<16}{'packages':>9}{'uncertain':>11}")
    print("-" * 36)
    for r in rows:
        print(f"{r['kind']:<16}{r['n']:>9,}{r['unsure']:>10,} ")
    print("-" * 36)
    print(f"{'total':<16}{total:>9,}")


def cmd_ls(conn, args) -> None:
    sql = "SELECT name, kind_confidence c, description d FROM packages WHERE kind=?"
    params = [args.kind]
    if args.confident:
        sql += " AND kind_confidence != 'uncertain'"
    sql += " ORDER BY name LIMIT ?"
    params.append(args.limit)
    rows = conn.execute(sql, params).fetchall()
    if not rows:
        kinds = [r[0] for r in conn.execute(
            "SELECT DISTINCT kind FROM packages ORDER BY 1")]
        sys.exit(f"no packages of kind '{args.kind}'. known: {', '.join(kinds)}")
    for r in rows:
        mark = "?" if r["c"] == "uncertain" else " "
        print(f"{mark} {r['name']:<28}{(r['d'] or '')[:60]}")


def cmd_search(conn, args) -> None:
    like = f"%{args.term}%"
    sql = ("SELECT name, kind, kind_confidence c, description d FROM packages"
           " WHERE (name LIKE ? OR description LIKE ?)")
    params = [like, like]
    if args.kind:
        sql += " AND kind=?"
        params.append(args.kind)
    sql += " ORDER BY name LIMIT ?"
    params.append(args.limit)
    for r in conn.execute(sql, params):
        mark = "?" if r["c"] == "uncertain" else " "
        print(f"{mark} {r['name']:<26}{r['kind']:<14}{(r['d'] or '')[:48]}")


def cmd_show(conn, args) -> None:
    r = conn.execute("SELECT * FROM packages WHERE name=?", (args.name,)).fetchone()
    if r is None:
        sys.exit(f"no package '{args.name}' in pkgs/by-name")
    print(f"{r['name']}")
    print(f"  description  {r['description'] or '-'}")
    print(f"  attribute    {r['attr'] or '-'}")
    print(f"  path         {r['path']}")
    if r["nixpkgs_rev"]:
        # commit-pinned so the link does not rot when the package moves
        print(f"  source       https://github.com/NixOS/nixpkgs/blob/"
              f"{r['nixpkgs_rev']}/{r['path']}")
    print(f"  homepage     {r['homepage'] or '-'}")
    print(f"\n  kind         {r['kind']}  ({r['kind_confidence']})")
    alts = json.loads(r["kind_alternates"] or "[]")
    if r["kind_confidence"] == "uncertain" and len(alts) > 1:
        print(f"  could also be  {', '.join(alts[1:])}")
    feats = json.loads(r["top_features"] or "{}").get("kind") or []
    if feats:
        print("  because      " + ", ".join(f"{f}({w:+.1f})" for f, w in feats[:4]))
    print(f"\n  domain       withheld — see `by-kind status`")


def cmd_status(conn, _args) -> None:
    meta = dict(conn.execute("SELECT key, value FROM run_meta").fetchall())
    total = conn.execute("SELECT COUNT(*) FROM packages").fetchone()[0]
    print(f"packages       {total:,} from pkgs/by-name")
    print(f"model          {meta.get('model_version', '?')}")
    print(f"classified     {meta.get('classified_at', '?')}")
    print("\nSHIPPED: kind — what a package is")
    print(f"  measured on {GOLD['n']} hand-labelled packages drawn from by-name:")
    print(f"    overall            {GOLD['overall']:.0%}")
    print(f"    probable tier      {GOLD['probable']:.0%}")
    print(f"    uncertain tier     {GOLD['uncertain']:.0%}   (shown with '?')")
    print("\nWITHHELD: domain — what a package is about")
    print("  Scored 13.8% on the same sample, against 75.1% on the legacy tree it")
    print("  was trained from. The vocabulary does not transfer, and no modelling")
    print("  change recovered it. Shipping it would mislead, so it is left out")
    print("  until in-distribution labels exist (src/label.py).")


def main() -> None:
    ap = argparse.ArgumentParser(prog="by-kind", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", type=Path, default=DB)
    sub = ap.add_subparsers(dest="cmd", required=True)

    sub.add_parser("tree").set_defaults(fn=cmd_tree)
    sub.add_parser("status").set_defaults(fn=cmd_status)

    p = sub.add_parser("ls"); p.add_argument("kind")
    p.add_argument("--confident", action="store_true")
    p.add_argument("--limit", type=int, default=50); p.set_defaults(fn=cmd_ls)

    p = sub.add_parser("search"); p.add_argument("term")
    p.add_argument("--kind"); p.add_argument("--limit", type=int, default=50)
    p.set_defaults(fn=cmd_search)

    p = sub.add_parser("show"); p.add_argument("name"); p.set_defaults(fn=cmd_show)

    args = ap.parse_args()
    args.fn(connect(args.db), args)


if __name__ == "__main__":
    main()

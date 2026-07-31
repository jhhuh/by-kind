"""Stage ⑥ — emit: shippable artifacts from data/categories.sqlite.

SHIPS `kind` ONLY. The `domain` facet measured 13.8% on the by-name gold set
(against 75.1% on legacy held-out) and no modelling change recovered it, so it is
deliberately withheld until stage ⑤ produces in-distribution labels. The column
still exists in the database for that future work; nothing here reads it.

Every published number is the one measured on the 94-package hand-labelled gold
set, not the legacy held-out figure, because the legacy number does not describe
this corpus.

Usage:
    nix develop -c python3 src/emit.py
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB = ROOT / "data" / "categories.sqlite"
DIST = ROOT / "dist"

# Measured on tests/fixtures/gold_by_name.tsv. Published verbatim so a reader can
# calibrate their trust rather than taking a bare label at face value.
GOLD_ACCURACY = {"overall": 0.713, "probable": 0.790, "uncertain": 0.581, "n": 94}

# Commit-pinned, not branch-pinned: a /blob/master/ link rots as soon as a
# package moves or is renamed. Pinning to the revision this build saw makes the
# link permanent and makes the page honest about which nixpkgs it describes.
GITHUB = "https://github.com/NixOS/nixpkgs/blob/{rev}/{path}"

KIND_ORDER = ["application", "cli-tool", "library", "server", "data",
              "plugin", "driver", "build-support", "other"]


def load(db: Path) -> list[dict]:
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(
        "SELECT name, path, attr, description, homepage, main_program, broken,"
        " unfree, kind, kind_confidence, kind_alternates, top_features,"
        " nixpkgs_rev FROM packages ORDER BY name")]
    meta = dict(conn.execute("SELECT key, value FROM run_meta").fetchall())
    conn.close()
    return rows, meta


def write_json(rows: list[dict], meta: dict, out: Path) -> None:
    payload = {
        "shipped_facet": "kind",
        "withheld": {"domain": "13.8% on the by-name gold set; see README"},
        "gold_accuracy": GOLD_ACCURACY,
        "nixpkgs_rev": meta.get("model_version"),
        "packages": [
            {"name": r["name"], "kind": r["kind"],
             "confidence": r["kind_confidence"],
             "alternates": json.loads(r["kind_alternates"] or "[]"),
             "description": r["description"], "attr": r["attr"],
             "path": r["path"],
             "source_url": GITHUB.format(rev=r["nixpkgs_rev"], path=r["path"])}
            for r in rows
        ],
    }
    out.write_text(json.dumps(payload, indent=None, sort_keys=True) + "\n")


HTML_HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>by-kind — browse nixpkgs pkgs/by-name by kind</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--dim:#666;--line:#e3e3e3;--accent:#2d5b8e;--warn:#8a5a00}
@media(prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8e8e8;--dim:#9aa0a6;--line:#2c2f36;--accent:#7fb0e8;--warn:#d8a13a}}
:root[data-theme=dark]{--bg:#16181c;--fg:#e8e8e8;--dim:#9aa0a6;--line:#2c2f36;--accent:#7fb0e8;--warn:#d8a13a}
:root[data-theme=light]{--bg:#fff;--fg:#1a1a1a;--dim:#666;--line:#e3e3e3;--accent:#2d5b8e;--warn:#8a5a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{padding:1.2rem 1rem;border-bottom:1px solid var(--line)}
h1{margin:0 0 .3rem;font-size:1.2rem}
.note{color:var(--dim);font-size:.85rem;max-width:60ch}
.note strong{color:var(--warn)}
.wrap{display:flex;gap:1.5rem;padding:1rem;align-items:flex-start;flex-wrap:wrap}
nav{flex:0 0 200px;position:sticky;top:1rem}
nav button{display:flex;justify-content:space-between;width:100%;background:none;
 border:0;border-radius:5px;color:var(--fg);font:inherit;padding:.35rem .5rem;cursor:pointer;text-align:left}
nav button:hover{background:var(--line)}
nav button[aria-current=true]{background:var(--accent);color:#fff}
nav button span{color:var(--dim);font-variant-numeric:tabular-nums}
nav button[aria-current=true] span{color:#fff}
main{flex:1 1 420px;min-width:0}
input[type=search]{width:100%;padding:.5rem .7rem;border:1px solid var(--line);
 border-radius:6px;background:var(--bg);color:var(--fg);font:inherit;margin-bottom:.8rem}
table{width:100%;border-collapse:collapse}
th,td{text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line);vertical-align:top}
th{font-size:.8rem;color:var(--dim);font-weight:600}
td.n{font-family:ui-monospace,monospace;white-space:nowrap}
td.n a{color:var(--accent);text-decoration:none}
td.n a:hover{text-decoration:underline}
td.d{color:var(--dim)}
.tag{font-size:.72rem;padding:.1rem .4rem;border-radius:99px;border:1px solid var(--line);white-space:nowrap}
.tag.uncertain{color:var(--warn);border-color:var(--warn)}
.scroll{overflow-x:auto}
#count{color:var(--dim);font-size:.85rem;margin-bottom:.5rem}
</style>"""


def write_html(rows: list[dict], meta: dict, out: Path) -> None:
    counts: dict[str, int] = {}
    for r in rows:
        counts[r["kind"]] = counts.get(r["kind"], 0) + 1
    ordered = [k for k in KIND_ORDER if k in counts] + \
              sorted(k for k in counts if k not in KIND_ORDER)

    rev = rows[0]["nixpkgs_rev"] if rows else "master"
    # The by-name path is fully derivable: pkgs/by-name/<first two chars,
    # lowercased>/<name>/package.nix. Verified for all 21,443 packages, so
    # deriving it in JS instead of shipping it per row saves ~30% of page size.
    compact = [[r["name"], r["kind"], r["kind_confidence"],
                r["description"] or ""] for r in rows]

    body = f"""<header>
<h1>by-kind — {len(rows):,} packages in <code>pkgs/by-name</code></h1>
<p class="note">Classified by <strong>kind</strong> (what a package <em>is</em>).
Measured accuracy on a 94-package hand-labelled sample: <strong>{GOLD_ACCURACY['overall']:.0%}</strong>
overall — {GOLD_ACCURACY['probable']:.0%} for <span class="tag">probable</span>,
{GOLD_ACCURACY['uncertain']:.0%} for <span class="tag uncertain">uncertain</span>.
The <em>domain</em> facet (audio, networking, …) is <strong>not shipped</strong>: it scored 13.8%
and would mislead. Uncertain rows show their alternatives.
Package names link to their <code>package.nix</code> at the exact nixpkgs revision
this page was built from.</p>
</header>
<div class="wrap">
<nav id="nav"></nav>
<main>
<input type="search" id="q" placeholder="Search name or description…" autocomplete="off">
<div id="count"></div>
<div class="scroll"><table><thead><tr><th>package</th><th>kind</th><th>description</th></tr></thead>
<tbody id="rows"></tbody></table></div>
</main></div>
<script>
const BLOB="https://github.com/NixOS/nixpkgs/blob/{rev}/";
const DATA={json.dumps(compact, separators=(',', ':'))};
const COUNTS={json.dumps(counts)};
const ORDER={json.dumps(ordered)};
let active=null,q="";
const nav=document.getElementById('nav'),tbody=document.getElementById('rows'),
      count=document.getElementById('count');
function draw(){{
  nav.innerHTML='';
  const mk=(label,n,val)=>{{const b=document.createElement('button');
    b.innerHTML=`${{label}}<span>${{n.toLocaleString()}}</span>`;
    if(active===val)b.setAttribute('aria-current','true');
    b.onclick=()=>{{active=(active===val?null:val);draw();}};nav.appendChild(b);}};
  mk('all kinds',DATA.length,null);
  ORDER.forEach(k=>mk(k,COUNTS[k],k));
  const needle=q.toLowerCase();
  const hits=DATA.filter(r=>(!active||r[1]===active)&&
      (!needle||r[0].toLowerCase().includes(needle)||r[3].toLowerCase().includes(needle)));
  count.textContent=`${{hits.length.toLocaleString()}} package${{hits.length===1?'':'s'}}`;
  tbody.innerHTML='';
  const frag=document.createDocumentFragment();
  hits.slice(0,600).forEach(r=>{{const tr=document.createElement('tr');
    const path=`pkgs/by-name/${{r[0].slice(0,2).toLowerCase()}}/${{r[0]}}/package.nix`;
    tr.innerHTML=`<td class="n"><a href="${{BLOB}}${{path}}" rel="noreferrer">${{r[0]}}</a></td>`+
      `<td><span class="tag ${{r[2]==='uncertain'?'uncertain':''}}">${{r[1]}}</span></td>`+
      `<td class="d">${{r[3].replace(/[<&]/g,c=>c==='<'?'&lt;':'&amp;')}}</td>`;
    frag.appendChild(tr);}});
  tbody.appendChild(frag);
  if(hits.length>600){{const tr=document.createElement('tr');
    tr.innerHTML=`<td colspan="3" class="d">… ${{(hits.length-600).toLocaleString()}} more; refine the search</td>`;
    tbody.appendChild(tr);}}
}}
document.getElementById('q').addEventListener('input',e=>{{q=e.target.value;draw();}});
draw();
</script>"""
    out.write_text(f"<!doctype html><html lang=en>{HTML_HEAD}<body>{body}</body></html>\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--dist", type=Path, default=DIST)
    args = ap.parse_args()

    rows, meta = load(args.db)
    args.dist.mkdir(exist_ok=True)
    write_json(rows, meta, args.dist / "categories.json")
    write_html(rows, meta, args.dist / "index.html")

    for path in sorted(args.dist.iterdir()):
        print(f"  {path.relative_to(ROOT)}  {path.stat().st_size/1024:.0f} KB")
    print(f"\n{len(rows)} packages, kind facet only "
          f"({GOLD_ACCURACY['overall']:.0%} measured on {GOLD_ACCURACY['n']} gold packages)")
    print("domain facet withheld: 13.8% on gold, see README")


if __name__ == "__main__":
    main()

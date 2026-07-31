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
REPO = "https://github.com/jhhuh/by-kind"

KIND_ORDER = ["application", "cli-tool", "library", "server", "data",
              "plugin", "driver", "build-support", "other"]


def load(db: Path):
    conn = sqlite3.connect(db)
    conn.row_factory = sqlite3.Row
    channels = [dict(r) for r in conn.execute(
        "SELECT channel, channel_release, nixpkgs_rev, COUNT(*) n,"
        " SUM(is_new) new, MAX(compared_to) prev FROM packages"
        " GROUP BY channel ORDER BY channel")]
    rows = {}
    for c in channels:
        rows[c["channel"]] = [dict(r) for r in conn.execute(
            "SELECT name, kind, kind_confidence, description, is_new,"
            " compared_to FROM packages WHERE channel=? ORDER BY name",
            (c["channel"],))]
    meta = dict(conn.execute("SELECT key, value FROM run_meta").fetchall())
    conn.close()
    return channels, rows, meta


def order_channels(channels: list[dict]) -> list[dict]:
    """Unstable first (what most people want), then stable newest-first."""
    def key(c):
        name = c["channel"]
        return (0 if "unstable" in name else 1,
                0 if name == "nixpkgs-unstable" else 1,
                [-int(p) for p in name.split("-")[-1].split(".")
                 if p.isdigit()] or [0], name)
    return sorted(channels, key=key)


def write_channel_json(channel: dict, rows: list[dict], out: Path) -> None:
    """One file per channel, fetched lazily. Doubles as the data API."""
    out.write_text(json.dumps({
        "channel": channel["channel"],
        "release": channel["channel_release"],
        "nixpkgs_rev": channel["nixpkgs_rev"],
        "gold_accuracy": GOLD_ACCURACY,
        "shipped_facet": "kind",
        "withheld": {"domain": "13.8% on the by-name gold set; see README"},
        # compact: the by-name path is derivable from the name, so it is not stored
        "compared_to": rows[0]["compared_to"] if rows else None,
        "packages": [[r["name"], r["kind"], r["kind_confidence"],
                      r["description"] or "", r["is_new"]] for r in rows],
        "columns": ["name", "kind", "confidence", "description", "is_new"],
    }, separators=(",", ":"), sort_keys=True) + "\n")


HTML_HEAD = """<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>by-kind — nixpkgs pkgs/by-name, organised by kind</title>
<style>
:root{--bg:#fff;--fg:#1a1a1a;--dim:#666;--line:#e3e3e3;--accent:#2d5b8e;--warn:#8a5a00}
@media(prefers-color-scheme:dark){:root{--bg:#16181c;--fg:#e8e8e8;--dim:#9aa0a6;--line:#2c2f36;--accent:#7fb0e8;--warn:#d8a13a}}
:root[data-theme=dark]{--bg:#16181c;--fg:#e8e8e8;--dim:#9aa0a6;--line:#2c2f36;--accent:#7fb0e8;--warn:#d8a13a}
:root[data-theme=light]{--bg:#fff;--fg:#1a1a1a;--dim:#666;--line:#e3e3e3;--accent:#2d5b8e;--warn:#8a5a00}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,sans-serif}
header{padding:1.2rem 1rem;border-bottom:1px solid var(--line)}
h1{margin:0 0 .3rem;font-size:1.15rem}
.note{color:var(--dim);font-size:.85rem;max-width:68ch}
.note strong{color:var(--warn)}
.tabs{display:flex;gap:.3rem;margin-top:.9rem;flex-wrap:wrap}
.tab{background:none;border:1px solid var(--line);border-radius:99px;color:var(--dim);
 font:inherit;font-size:.82rem;padding:.25rem .7rem;cursor:pointer}
.tab:hover{color:var(--fg)}
.tab[aria-current=true]{background:var(--accent);border-color:var(--accent);color:#fff}
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
.tag.new{color:#0a7d3f;border-color:#0a7d3f;font-weight:600}
@media(prefers-color-scheme:dark){.tag.new{color:#5fd08a;border-color:#5fd08a}}
.bar{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin-bottom:.5rem}
.only{color:var(--dim);font-size:.85rem;display:flex;gap:.35rem;align-items:center;cursor:pointer}
.scroll{overflow-x:auto}
#count{color:var(--dim);font-size:.85rem}
.repo{margin:.35rem 0 0;font-size:.85rem}
.repo a,footer a{color:var(--accent);text-decoration:none}
.repo a:hover,footer a:hover{text-decoration:underline}
footer{border-top:1px solid var(--line);margin-top:2rem;padding:1rem;
 color:var(--dim);font-size:.82rem;line-height:1.8}
</style>"""


def write_html(channels: list[dict], counts: dict, out: Path) -> None:
    tabs = [{"channel": c["channel"], "release": c["channel_release"],
             "rev": c["nixpkgs_rev"], "n": c["n"], "new": c["new"],
             "prev": c["prev"], "kinds": counts[c["channel"]]} for c in channels]
    body = f"""<header>
<h1>by-kind — nixpkgs <code>pkgs/by-name</code>, organised by what things are</h1>
<p class="repo"><a href="{REPO}" rel="noreferrer">source &amp; method on GitHub ↗</a></p>
<p class="note">nixpkgs gives you <code>pkgs/by-name</code>; this gives you <em>by kind</em>.
Measured accuracy on a 94-package hand-labelled sample: <strong>{GOLD_ACCURACY['overall']:.0%}</strong>
overall — {GOLD_ACCURACY['probable']:.0%} for <span class="tag">probable</span>,
{GOLD_ACCURACY['uncertain']:.0%} for <span class="tag uncertain">uncertain</span>, which are marked and
show alternatives. The <em>domain</em> facet (audio, networking, …) is
<strong>not shipped</strong>: it scored 13.8% and would mislead.
Package names link to <code>package.nix</code> at each channel's exact revision.</p>
<div id="tabs" class="tabs"></div>
</header>
<div class="wrap">
<nav id="nav"></nav>
<main>
<input type="search" id="q" placeholder="Search name or description…" autocomplete="off">
<div class="bar"><div id="count"></div>
<label class="only"><input type="checkbox" id="newonly"> only new since <span id="prev"></span></label></div>
<div class="scroll"><table><thead><tr><th>package</th><th>kind</th><th>description</th></tr></thead>
<tbody id="rows"></tbody></table></div>
</main></div>
<footer>
<a href="{REPO}" rel="noreferrer">jhhuh/by-kind</a> ·
rebuilt daily from <a href="https://channels.nixos.org/" rel="noreferrer">channels.nixos.org</a> ·
classification is a deterministic table lookup, no LLM at query time ·
<a href="{REPO}/blob/main/artifacts/devlog.md" rel="noreferrer">how it was built, and what it gets wrong</a>
</footer>
<script>
const TABS={json.dumps(tabs, separators=(',', ':'))};
const cache={{}};
let chan=TABS[0].channel, active=null, q="", DATA=[], newonly=false;
const $=id=>document.getElementById(id);

function drawTabs(){{
  $('tabs').innerHTML='';
  TABS.forEach(t=>{{const b=document.createElement('button');
    b.className='tab'; b.textContent=t.channel;
    if(t.channel===chan) b.setAttribute('aria-current','true');
    b.title=`${{t.release}} — ${{t.n.toLocaleString()}} packages`;
    b.onclick=()=>{{if(t.channel!==chan){{chan=t.channel;active=null;load();}}}};
    $('tabs').appendChild(b);}});
}}
async function load(){{
  drawTabs();
  if(!cache[chan]){{
    $('count').textContent='loading…';
    const r=await fetch(`data/${{chan}}.json`);
    cache[chan]=(await r.json()).packages;
  }}
  DATA=cache[chan]; draw();
}}
function draw(){{
  const tab=TABS.find(t=>t.channel===chan);
  const BLOB=`https://github.com/NixOS/nixpkgs/blob/${{tab.rev}}/`;
  $('nav').innerHTML='';
  const mk=(label,n,val)=>{{const b=document.createElement('button');
    b.innerHTML=`${{label}}<span>${{n.toLocaleString()}}</span>`;
    if(active===val)b.setAttribute('aria-current','true');
    b.onclick=()=>{{active=(active===val?null:val);draw();}};$('nav').appendChild(b);}};
  mk('all kinds',DATA.length,null);
  Object.entries(tab.kinds).forEach(([k,n])=>mk(k,n,k));
  $('prev').textContent=tab.prev||'previous release';
  const needle=q.toLowerCase();
  const hits=DATA.filter(r=>(!active||r[1]===active)&&(!newonly||r[4])&&
      (!needle||r[0].toLowerCase().includes(needle)||r[3].toLowerCase().includes(needle)));
  $('count').textContent=`${{hits.length.toLocaleString()}} package${{hits.length===1?'':'s'}} in ${{chan}}`
    +(tab.prev?` · ${{tab.new.toLocaleString()}} new since ${{tab.prev}}`:'');
  const frag=document.createDocumentFragment();
  hits.slice(0,600).forEach(r=>{{const tr=document.createElement('tr');
    const path=`pkgs/by-name/${{r[0].slice(0,2).toLowerCase()}}/${{r[0]}}/package.nix`;
    tr.innerHTML=`<td class="n"><a href="${{BLOB}}${{path}}" rel="noreferrer">${{r[0]}}</a></td>`+
      `<td><span class="tag ${{r[2]==='uncertain'?'uncertain':''}}">${{r[1]}}</span>`+
       (r[4]?` <span class="tag new">new</span>`:'')+`</td>`+
      `<td class="d">${{r[3].replace(/[<&]/g,c=>c==='<'?'&lt;':'&amp;')}}</td>`;
    frag.appendChild(tr);}});
  $('rows').innerHTML=''; $('rows').appendChild(frag);
  if(hits.length>600){{const tr=document.createElement('tr');
    tr.innerHTML=`<td colspan="3" class="d">… ${{(hits.length-600).toLocaleString()}} more; refine the search</td>`;
    $('rows').appendChild(tr);}}
}}
$('q').addEventListener('input',e=>{{q=e.target.value;draw();}});
$('newonly').addEventListener('change',e=>{{newonly=e.target.checked;draw();}});
load();
</script>"""
    out.write_text(f"<!doctype html><html lang=en>{HTML_HEAD}<body>{body}</body></html>\n")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--dist", type=Path, default=DIST)
    args = ap.parse_args()

    channels, rows, _meta = load(args.db)
    channels = order_channels(channels)
    (args.dist / "data").mkdir(parents=True, exist_ok=True)

    counts = {}
    for c in channels:
        per = {}
        for r in rows[c["channel"]]:
            per[r["kind"]] = per.get(r["kind"], 0) + 1
        counts[c["channel"]] = {k: per[k] for k in KIND_ORDER if k in per}
        write_channel_json(c, rows[c["channel"]],
                           args.dist / "data" / f"{c['channel']}.json")

    write_html(channels, counts, args.dist / "index.html")

    total = sum(c["n"] for c in channels)
    for path in sorted(args.dist.rglob("*")):
        if path.is_file():
            print(f"  {path.relative_to(ROOT)}  {path.stat().st_size/1024:.0f} KB")
    print(f"\n{total:,} packages across {len(channels)} channels, kind facet only "
          f"({GOLD_ACCURACY['overall']:.0%} on {GOLD_ACCURACY['n']} gold packages)")


if __name__ == "__main__":
    main()

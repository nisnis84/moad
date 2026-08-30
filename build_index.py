#!/usr/bin/env python3
"""Build a single hub page indexing every HTML dashboard/deck/report on this machine.

Usage:
    python3 build_index.py              # rescan + regenerate index.html
    python3 build_index.py --open       # ...and open it
    python3 build_index.py --add PATH   # add an extra root directory to scan

Scan results are merged with manual overrides in dashboards.json (title, category,
tags, pinned, hidden). Overrides survive every rescan.
"""

import argparse
import html
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "dashboards.json"
OUTPUT = HERE / "index.html"
THUMBS = HERE / "thumbs"

DEFAULT_ROOTS = [str(Path.home() / "Downloads")]
HEAD_BYTES = 65536

# --------------------------------------------------------------------------- scan

TITLE_RE = re.compile(rb"<title[^>]*>(.*?)</title>", re.I | re.S)
H1_RE = re.compile(rb"<h1[^>]*>(.*?)</h1>", re.I | re.S)
TAG_RE = re.compile(r"<[^>]+>")
GENERIC_TITLES = {"", "document", "untitled", "index", "report", "dashboard", "page"}

# Categories are yours, not the tool's: put your own (regex, label) pairs in
# dashboards.json under "category_rules". First match wins; anything unmatched is
# "Other". These generic defaults apply when you haven't defined any.
DEFAULT_CATEGORY_RULES = [
    [r"^ai[_-]|llm|agent", "AI"],
    [r"exec|board|quarter|qbr|q[1-4][_-]", "Exec / Quarterly"],
    [r"finops|cost|pricing|licen|budget", "Cost"],
    [r"^rnd|^r&d|squad|headcount|hiring|org", "Org"],
]

KIND_RULES = [
    (r"deck|slide|presentation|keynote", "deck"),
    (r"flow|arch|diagram|architecture", "diagram"),
    (r"report|audit|findings", "report"),
    (r"dashboard|db|planner|tracker|runs", "dashboard"),
]


def decode(raw: bytes) -> str:
    return raw.decode("utf-8", errors="replace")


def clean_text(s: str) -> str:
    s = TAG_RE.sub(" ", s)
    s = html.unescape(s)
    return re.sub(r"\s+", " ", s).strip()


def pretty_from_filename(stem: str) -> str:
    s = re.sub(r"[_\-]+", " ", stem)
    s = re.sub(r"\s+\(\d+\)$", "", s)
    return re.sub(r"\s+", " ", s).strip()


def extract_meta(path: Path) -> dict:
    """Read only the first HEAD_BYTES -- some of these files are 19 MB."""
    title, h1 = "", ""
    try:
        with open(path, "rb") as fh:
            head = fh.read(HEAD_BYTES)
        m = TITLE_RE.search(head)
        if m:
            title = clean_text(decode(m.group(1)))
        m = H1_RE.search(head)
        if m:
            h1 = clean_text(decode(m.group(1)))
    except OSError:
        pass
    return {"title": title, "h1": h1}


def categorize(stem: str, title: str, rules) -> str:
    """First matching rule wins; no match means "Other".

    Rules are tested against the filename stem followed by the document title,
    because the title usually says what a thing is and the filename usually
    doesn't. A rule anchored with ^ still anchors to the filename.
    """
    hay = f"{stem} {title}".lower()
    for pattern, label in rules:
        try:
            if re.search(pattern, hay):
                return label
        except re.error:
            continue
    return "Other"


def kind_of(stem: str) -> str:
    low = stem.lower()
    for pattern, label in KIND_RULES:
        if re.search(pattern, low):
            return label
    return "page"


def customer_of(stem: str, pattern: str) -> str:
    """Pull a per-file label (a customer, a team, a tenant) out of the filename.

    Opt-in: set "customer_pattern" in dashboards.json to a regex with one capture
    group. Empty pattern means the feature is off.
    """
    if not pattern:
        return ""
    try:
        m = re.match(pattern, stem, re.I)
    except re.error:
        return ""
    if not m or not m.groups():
        return ""
    name = m.group(1)
    if re.fullmatch(r"[0-9a-f-]{30,}", name):
        return "(unnamed)"
    return pretty_from_filename(name)


def iter_html(rp: Path, max_depth: int):
    """Yield .html files up to max_depth levels below rp (1 = top level only)."""
    pattern = "*.html"
    for depth in range(1, max(1, max_depth) + 1):
        yield from sorted(rp.glob("/".join(["*"] * (depth - 1) + [pattern])))


def scan(roots, exclude_globs, max_depth, cat_rules, customer_pattern):
    items = []
    seen = set()
    for root in roots:
        rp = Path(root).expanduser()
        if not rp.is_dir():
            print(f"  ! skipping missing root: {rp}", file=sys.stderr)
            continue
        for path in iter_html(rp, max_depth):
            if not path.is_file():
                continue
            rs = str(path.resolve())
            if rs in seen or THUMBS in path.parents or path.resolve() == OUTPUT:
                continue
            seen.add(rs)
            if any(path.match(g) for g in exclude_globs):
                continue
            st = path.stat()
            stem = path.stem
            meta = extract_meta(path)
            title = meta["title"]
            if title.lower() in GENERIC_TITLES:
                title = meta["h1"] or ""
            if not title or title.lower() in GENERIC_TITLES:
                title = pretty_from_filename(stem)
            items.append({
                "path": rs,
                "file": path.name,
                "dir": str(path.parent),
                "title": title,
                "subtitle": meta["h1"] if meta["h1"] and meta["h1"] != title else "",
                "category": categorize(stem, title, cat_rules),
                "kind": kind_of(stem),
                "customer": customer_of(stem, customer_pattern),
                "mtime": int(st.st_mtime),
                "size": st.st_size,
            })
    return items



# --------------------------------------------------------------------------- suggest

# Tokens that describe the artifact's format, its version, or when it was made --
# never what it is about. Proposing "2026" or "final" as a category is useless.
SUGGEST_STOPWORDS = {
    "html", "htm", "report", "reports", "dashboard", "dashboards", "deck", "slide",
    "slides", "page", "doc", "docs", "draft", "final", "copy", "new", "old", "temp",
    "tmp", "test", "untitled", "index", "output", "export", "version", "all", "full",
    "review", "tracker", "weekly", "monthly", "daily", "quarterly", "detail",
    "the", "and", "for", "with", "from", "of", "to", "in", "on", "by", "a", "an",
    "vs", "v", "x", "data", "view", "summary", "overview", "analysis",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec",
}

MIN_TOKEN_LEN = 3
MAX_SUGGESTIONS = 9


def tokenize(text: str):
    """Meaningful lowercase tokens: no dates, no version numbers, no format words."""
    out = []
    for t in re.split(r"[^A-Za-z0-9]+", text.lower()):
        if len(t) < MIN_TOKEN_LEN or t in SUGGEST_STOPWORDS:
            continue
        if t.isdigit():                      # 2026, 07, 23
            continue
        if re.fullmatch(r"v\d+|\d+[a-z]|[a-z]\d+", t):   # v2, 4b, q3 handled below
            continue
        out.append(t)
    return out


def suggest_categories(items):
    """Derive category rules from the corpus instead of asking the user to invent them.

    Greedy set cover over tokens drawn from filenames and titles. Title tokens are
    preferred as labels because a title says what a thing is; a filename rarely does.
    """
    token_files, token_label = {}, {}
    for idx, it in enumerate(items):
        stem = Path(it["file"]).stem
        file_tokens = set(tokenize(pretty_from_filename(stem)))
        title_tokens = set(tokenize(it["title"]))
        for t in file_tokens | title_tokens:
            token_files.setdefault(t, set()).add(idx)
            # remember how the token is actually written in a title, for the label
            if t in title_tokens and t not in token_label:
                for word in re.split(r"[^A-Za-z0-9]+", it["title"]):
                    if word.lower() == t:
                        token_label[t] = word
                        break

    total = len(items)
    min_files = max(3, round(total * 0.015))
    uncovered = set(range(total))
    picked = []

    while len(picked) < MAX_SUGGESTIONS:
        best, best_gain = None, 0
        for t, files in token_files.items():
            gain = len(files & uncovered)
            if gain > best_gain or (gain == best_gain and best and t in token_label
                                    and best not in token_label):
                best, best_gain = t, gain
        if not best or best_gain < min_files:
            break
        picked.append((best, best_gain, len(token_files[best])))
        uncovered -= token_files[best]
        del token_files[best]

    # \b treats "_" as a word character, so \bsales\b never matches
    # sales_pipeline_2026. Use explicit lookarounds over the alphanumeric class.
    rules = [[rf"(?<![a-z0-9]){re.escape(t)}(?![a-z0-9])",
              (token_label.get(t) or t).strip().title()]
             for t, _, _ in picked]

    # Report what will actually happen by running the real classifier over the
    # proposed rules, rather than what the set cover believed it covered. Those
    # two numbers diverged once, and the printed count is the point of a dry run.
    counts = {}
    for it in items:
        label = categorize(Path(it["file"]).stem, it["title"], rules)
        counts[label] = counts.get(label, 0) + 1
    picked = [(t, counts.get(rule[1], 0), tot)
              for (t, _, tot), rule in zip(picked, rules)]
    return rules, picked, counts.get("Other", 0), total

# --------------------------------------------------------------------------- registry

def load_registry():
    if REGISTRY.exists():
        with open(REGISTRY) as fh:
            reg = json.load(fh)
    else:
        # First run: seed from the shipped example so there is a real file to edit,
        # rather than an invisible built-in default.
        example = HERE / "dashboards.example.json"
        reg = json.loads(example.read_text()) if example.exists() else {}
        reg["overrides"] = {}
        print(f"first run: created {REGISTRY.name} "
              f"(scanning {', '.join(reg.get('roots', DEFAULT_ROOTS))})")
        print("set your own directory with:  python3 build_index.py --root ~/path/to/dashboards")
    reg.setdefault("roots", list(DEFAULT_ROOTS))
    reg.setdefault("max_depth", 1)
    reg.setdefault("exclude_globs", [])
    reg.setdefault("category_rules", "auto")
    reg.setdefault("customer_pattern", "")
    reg.setdefault("overrides", {})
    return reg


def save_registry(reg):
    with open(REGISTRY, "w") as fh:
        json.dump(reg, fh, indent=2, ensure_ascii=False)
        fh.write("\n")


def apply_overrides(items, overrides):
    out = []
    for it in items:
        ov = overrides.get(it["path"], {})
        if ov.get("hidden"):
            continue
        it = {**it, **{k: v for k, v in ov.items() if k != "hidden"}}
        it.setdefault("tags", [])
        it["pinned"] = bool(ov.get("pinned"))
        out.append(it)
    return out


def attach_thumbs(items):
    for it in items:
        t = THUMBS / (thumb_name(it["path"], it["mtime"]))
        it["thumb"] = f"thumbs/{t.name}" if t.exists() else ""
    return items


def thumb_name(path, mtime):
    import hashlib
    h = hashlib.sha1(f"{path}:{mtime}".encode()).hexdigest()[:16]
    return f"{h}.jpg"


def file_url(path: str) -> str:
    return "file://" + "/".join(quote(seg, safe="") for seg in path.split("/"))


# --------------------------------------------------------------------------- render

def render(items, roots):
    for it in items:
        it["url"] = file_url(it["path"])
    payload = json.dumps(
        {
            "items": items,
            "roots": roots,
            "generated": int(time.time()),
            "generatedLabel": datetime.now().strftime("%d %b %Y, %H:%M"),
        },
        ensure_ascii=False,
    ).replace("</", "<\\/")
    return TEMPLATE.replace("__DATA__", payload)


TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>MOAD — Mother Of All Dashboards</title>
<link rel="icon" href="data:image/svg+xml,%3Csvg%20xmlns%3D%27http%3A%2F%2Fwww.w3.org%2F2000%2Fsvg%27%20viewBox%3D%270%200%2032%2032%27%3E%3Crect%20width%3D%2732%27%20height%3D%2732%27%20rx%3D%277%27%20fill%3D%27%234f46e5%27%2F%3E%3Crect%20x%3D%275%27%20y%3D%275%27%20width%3D%2710%27%20height%3D%279%27%20rx%3D%272%27%20fill%3D%27%23fff%27%2F%3E%3Crect%20x%3D%2718%27%20y%3D%275%27%20width%3D%279%27%20height%3D%279%27%20rx%3D%272%27%20fill%3D%27%23fff%27%20opacity%3D%27.6%27%2F%3E%3Crect%20x%3D%275%27%20y%3D%2717%27%20width%3D%2710%27%20height%3D%2710%27%20rx%3D%272%27%20fill%3D%27%23fff%27%20opacity%3D%27.6%27%2F%3E%3Crect%20x%3D%2718%27%20y%3D%2717%27%20width%3D%279%27%20height%3D%2710%27%20rx%3D%272%27%20fill%3D%27%23fff%27%2F%3E%3C%2Fsvg%3E">
<style>
  :root{
    --bg:#f6f7fb; --panel:#ffffff; --ink:#12141a; --muted:#6b7280; --line:#e6e8ef;
    --accent:#4f46e5; --accent-soft:#eef0ff; --ok:#0f9d76; --warm:#e0703a;
    --radius:14px; --shadow:0 1px 2px rgba(16,20,40,.05),0 8px 24px rgba(16,20,40,.06);
  }
  *{box-sizing:border-box}
  html,body{margin:0;padding:0}
  body{
    background:var(--bg); color:var(--ink); font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Inter,Roboto,Helvetica,Arial,sans-serif;
    -webkit-font-smoothing:antialiased;
  }
  a{color:inherit;text-decoration:none}
  .wrap{max-width:1500px;margin:0 auto;padding:28px 32px 80px}

  header.top{display:flex;align-items:flex-end;justify-content:space-between;gap:24px;flex-wrap:wrap;margin-bottom:20px}
  h1{font-size:34px;line-height:1.1;letter-spacing:-.03em;margin:0 0 7px;display:flex;align-items:baseline;gap:12px;flex-wrap:wrap}
  h1 .tag{font-size:14px;font-weight:600;letter-spacing:.02em;color:var(--accent);
    background:var(--accent-soft);padding:5px 11px;border-radius:999px;white-space:nowrap}
  .sub{color:var(--muted);font-size:14px}
  .sub b{color:var(--ink);font-weight:600}

  .controls{position:sticky;top:0;z-index:20;background:var(--bg);padding:12px 0 14px;border-bottom:1px solid var(--line);margin-bottom:20px}
  .searchrow{display:flex;gap:10px;align-items:center;flex-wrap:wrap}
  #q{
    flex:1 1 340px;min-width:260px;padding:13px 16px;font-size:16px;border:1px solid var(--line);
    border-radius:11px;background:var(--panel);box-shadow:var(--shadow);outline:none;
  }
  #q:focus{border-color:var(--accent);box-shadow:0 0 0 4px var(--accent-soft)}
  select,button.tog{
    padding:12px 14px;font-size:14px;border:1px solid var(--line);border-radius:11px;background:var(--panel);
    color:var(--ink);cursor:pointer;box-shadow:var(--shadow);
  }
  .chips{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
  .chip{
    padding:7px 13px;font-size:13px;font-weight:550;border:1px solid var(--line);border-radius:999px;
    background:var(--panel);color:var(--muted);cursor:pointer;white-space:nowrap;
  }
  .chip:hover{border-color:#c9cddb;color:var(--ink)}
  .chip.on{background:var(--accent);border-color:var(--accent);color:#fff}
  .chip .n{opacity:.65;margin-left:6px;font-variant-numeric:tabular-nums}

  .groupname{
    display:flex;align-items:baseline;gap:10px;margin:30px 0 12px;font-size:13px;font-weight:700;
    letter-spacing:.09em;text-transform:uppercase;color:var(--muted);
  }
  .groupname .rule{flex:1;height:1px;background:var(--line)}

  .grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(330px,1fr));gap:16px}
  .card{
    display:flex;flex-direction:column;background:var(--panel);border:1px solid var(--line);border-radius:var(--radius);
    box-shadow:var(--shadow);overflow:hidden;transition:transform .12s ease,box-shadow .12s ease,border-color .12s ease;
  }
  .card:hover{transform:translateY(-2px);border-color:#c9cddb;box-shadow:0 4px 8px rgba(16,20,40,.06),0 16px 40px rgba(16,20,40,.10)}
  .thumb{height:152px;background:linear-gradient(135deg,#eef0ff,#f7f8fc);border-bottom:1px solid var(--line);
    display:flex;align-items:center;justify-content:center;overflow:hidden}
  .thumb img{width:100%;height:100%;object-fit:cover;object-position:top center}
  .thumb .glyph{font-size:13px;font-weight:700;letter-spacing:.12em;color:#a6abbd;text-transform:uppercase}
  .body{padding:15px 16px 14px;display:flex;flex-direction:column;gap:8px;flex:1}
  .ttl{font-size:16px;font-weight:640;line-height:1.3;letter-spacing:-.01em}
  .meta{display:flex;gap:7px;align-items:center;flex-wrap:wrap;font-size:12.5px;color:var(--muted);margin-top:auto}
  .stamp{display:flex;gap:7px;align-items:center;font-size:12.5px;color:var(--muted);font-variant-numeric:tabular-nums}
  .badge{padding:3px 9px;border-radius:999px;background:var(--accent-soft);color:var(--accent);font-weight:640;font-size:11.5px}
  .badge.k{background:#f1f3f7;color:#6b7280}
  .fname{font-size:12px;color:#9aa0b0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;
    white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:100%}
  .pin{color:var(--warm);font-size:13px}

  .list .card{flex-direction:row;align-items:center;padding:0}
  .list .thumb{width:120px;height:72px;flex:0 0 120px;border-bottom:none;border-right:1px solid var(--line)}
  .list .body{padding:12px 16px;flex-direction:row;align-items:center;gap:16px}
  .list .ttl{flex:0 1 auto;font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:52%}
  .list .fname{flex:1 1 auto;min-width:0}
  .list .meta{margin-top:0;flex:0 0 auto}
  .list .stamp{flex:0 0 auto}
  .list .body{gap:14px}
  .list .grid{grid-template-columns:1fr}

  .empty{padding:70px 0;text-align:center;color:var(--muted)}
  kbd{font:12px ui-monospace,Menlo,monospace;background:#fff;border:1px solid var(--line);border-bottom-width:2px;border-radius:6px;padding:2px 6px}
  footer{margin-top:48px;padding-top:18px;border-top:1px solid var(--line);color:var(--muted);font-size:13px;line-height:1.7}
  code{font:12.5px ui-monospace,Menlo,monospace;background:#fff;border:1px solid var(--line);border-radius:6px;padding:2px 6px}
</style>
</head>
<body>
<div class="wrap">
  <header class="top">
    <div>
      <h1>MOAD<span class="tag">Mother Of All Dashboards</span></h1>
      <div class="sub"><b id="total">0</b> artifacts &middot; refreshed <b id="gen"></b></div>
    </div>
    <div class="sub">Rescan: <code>python3 build_index.py</code></div>
  </header>

  <div class="controls">
    <div class="searchrow">
      <input id="q" type="search" placeholder="Search titles, filenames, customers…  (press / to focus)" autofocus>
      <select id="sort">
        <option value="date">Newest first</option>
        <option value="dateasc">Oldest first</option>
        <option value="name">Name A→Z</option>
        <option value="size">Largest first</option>
      </select>
      <select id="group">
        <option value="category">Group: category</option>
        <option value="month">Group: month</option>
        <option value="none">Group: none</option>
      </select>
      <button class="tog" id="view">List view</button>
    </div>
    <div class="chips" id="chips"></div>
  </div>

  <div id="out"></div>

  <footer>
    Scanning: <span id="roots"></span><br>
    Pin, rename, re-categorise or hide any item by editing <code>dashboards.json</code> → <code>overrides</code>, then rescan. Overrides survive rescans.
  </footer>
</div>

<script>
const DATA = __DATA__;
const $ = s => document.querySelector(s);
const state = {q:"", cat:"", sort:"date", group:"category", list:false};

const fmtDate = t => new Date(t*1000).toLocaleDateString(undefined,{day:"2-digit",month:"short",year:"numeric"});
// mtime is a unix timestamp, so Date renders it in the viewer's own timezone and
// their locale's clock convention -- 24h or am/pm, whichever they use.
const fmtTime = t => new Date(t*1000).toLocaleTimeString(undefined,{hour:"2-digit",minute:"2-digit"});
const fmtExact = t => new Date(t*1000).toLocaleString(undefined,{dateStyle:"full",timeStyle:"long"});
const fmtMonth = t => new Date(t*1000).toLocaleDateString(undefined,{month:"long",year:"numeric"});
const fmtSize = b => b>=1048576 ? (b/1048576).toFixed(b>=10485760?0:1)+" MB" : Math.max(1,Math.round(b/1024))+" KB";
const esc = s => String(s).replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;"}[c]));

DATA.items.forEach(it => {
  it._hay = [it.title, it.file, it.category, it.kind, it.customer, it.subtitle, (it.tags||[]).join(" ")].join(" ").toLowerCase();
});

$("#total").textContent = DATA.items.length;
$("#gen").textContent = DATA.generatedLabel;
$("#roots").textContent = DATA.roots.join(" · ");

const counts = {};
DATA.items.forEach(i => counts[i.category] = (counts[i.category]||0)+1);
const cats = Object.keys(counts).sort((a,b) => counts[b]-counts[a] || a.localeCompare(b));
$("#chips").innerHTML = [`<button class="chip on" data-c="">All<span class="n">${DATA.items.length}</span></button>`]
  .concat(cats.map(c => `<button class="chip" data-c="${esc(c)}">${esc(c)}<span class="n">${counts[c]}</span></button>`)).join("");

function filtered(){
  const terms = state.q.toLowerCase().split(/\s+/).filter(Boolean);
  let r = DATA.items.filter(i =>
    (!state.cat || i.category === state.cat) && terms.every(t => i._hay.includes(t)));
  const cmp = {
    date:    (a,b) => b.mtime-a.mtime,
    dateasc: (a,b) => a.mtime-b.mtime,
    name:    (a,b) => a.title.localeCompare(b.title),
    size:    (a,b) => b.size-a.size,
  }[state.sort];
  return r.sort((a,b) => (b.pinned?1:0)-(a.pinned?1:0) || cmp(a,b));
}

function cardHTML(i){
  const glyph = i.thumb
    ? `<img loading="lazy" src="${esc(i.thumb)}" alt="">`
    : `<span class="glyph">${esc(i.kind)}</span>`;
  return `<a class="card" href="${i.url}" target="_blank" title="${esc(i.path)}">
    <div class="thumb">${glyph}</div>
    <div class="body">
      <div class="ttl">${i.pinned?'<span class="pin">★</span> ':''}${esc(i.title)}</div>
      <div class="fname">${esc(i.file)}</div>
      <div class="meta">
        <span class="badge">${esc(i.category)}</span>
        <span class="badge k">${esc(i.kind)}</span>
      </div>
      <div class="stamp" title="Last modified ${esc(fmtExact(i.mtime))}">${fmtDate(i.mtime)}, ${fmtTime(i.mtime)} <span>·</span> ${fmtSize(i.size)}</div>
    </div>
  </a>`;
}

function render(){
  const rows = filtered();
  const out = $("#out");
  if(!rows.length){ out.innerHTML = `<div class="empty">Nothing matches <b>${esc(state.q)}</b>.</div>`; return; }
  let html = "";
  if(state.group === "none"){
    html = `<div class="grid">${rows.map(cardHTML).join("")}</div>`;
  } else {
    const key = state.group === "month" ? (i => fmtMonth(i.mtime)) : (i => i.category);
    const groups = new Map();
    rows.forEach(i => { const k = key(i); if(!groups.has(k)) groups.set(k, []); groups.get(k).push(i); });
    for(const [k, v] of groups){
      html += `<div class="groupname">${esc(k)} <span>${v.length}</span><span class="rule"></span></div>
               <div class="grid">${v.map(cardHTML).join("")}</div>`;
    }
  }
  out.className = state.list ? "list" : "";
  out.innerHTML = html;
}

$("#q").addEventListener("input", e => { state.q = e.target.value; render(); });
$("#sort").addEventListener("change", e => { state.sort = e.target.value; render(); });
$("#group").addEventListener("change", e => { state.group = e.target.value; render(); });
$("#view").addEventListener("click", e => {
  state.list = !state.list;
  e.target.textContent = state.list ? "Grid view" : "List view";
  render();
});
$("#chips").addEventListener("click", e => {
  const b = e.target.closest(".chip"); if(!b) return;
  state.cat = b.dataset.c;
  document.querySelectorAll(".chip").forEach(c => c.classList.toggle("on", c === b));
  render();
});
document.addEventListener("keydown", e => {
  if(e.key === "/" && document.activeElement !== $("#q")){ e.preventDefault(); $("#q").focus(); }
  if(e.key === "Escape"){ $("#q").value = ""; state.q = ""; render(); $("#q").blur(); }
});
render();
</script>
</body>
</html>
"""


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", metavar="DIR",
                    help="set the scan directory, replacing any existing roots")
    ap.add_argument("--add", metavar="DIR", help="add another directory to the scan roots")
    ap.add_argument("--remove", metavar="DIR", help="stop scanning a directory")
    ap.add_argument("--roots", action="store_true", help="print the scan roots and exit")
    ap.add_argument("--suggest-categories", action="store_true",
                    help="derive category rules from your actual files and print them")
    ap.add_argument("--apply", action="store_true",
                    help="with --suggest-categories, write the rules into dashboards.json")
    ap.add_argument("--list-titles", action="store_true",
                    help="print filename + title for every artifact as JSON (nothing else)")
    ap.add_argument("--check-categories", metavar="FILE",
                    help="dry-run a proposed category_rules JSON file; writes nothing")
    ap.add_argument("--set-categories", metavar="FILE",
                    help="write a category_rules JSON file into dashboards.json")
    ap.add_argument("--open", action="store_true", help="open index.html when done")
    ap.add_argument("--depth", type=int, help="scan depth per root (1 = top level only)")
    args = ap.parse_args()

    reg = load_registry()

    if args.roots:
        for r in reg["roots"]:
            exists = "" if Path(r).expanduser().is_dir() else "   (missing)"
            print(f"{r}{exists}")
        return

    if args.depth:
        reg["max_depth"] = args.depth

    def resolve(d):
        p = Path(d).expanduser()
        if not p.is_dir():
            sys.exit(f"not a directory: {p}")
        return str(p.resolve())

    if args.root:
        reg["roots"] = [resolve(args.root)]
        print(f"scan root set to: {reg['roots'][0]}")
    if args.add:
        d = resolve(args.add)
        if d not in reg["roots"]:
            reg["roots"].append(d)
            print(f"added root: {d}")
    if args.remove:
        d = str(Path(args.remove).expanduser().resolve())
        before = len(reg["roots"])
        reg["roots"] = [r for r in reg["roots"] if str(Path(r).expanduser().resolve()) != d]
        print(f"removed root: {d}" if len(reg["roots"]) < before else f"not a root: {d}")
        if not reg["roots"]:
            sys.exit("refusing to leave zero scan roots -- add one first")

    if args.list_titles:
        # The complete payload for an external categoriser: names and titles only.
        # No file contents, no paths, no sizes -- what you see here is what leaves.
        raw = scan(reg["roots"], reg["exclude_globs"], reg["max_depth"], [], "")
        print(json.dumps([{"file": i["file"], "title": i["title"]} for i in raw],
                         ensure_ascii=False, indent=1))
        return

    if args.check_categories or args.set_categories:
        path = Path(args.check_categories or args.set_categories)
        try:
            proposed = json.loads(path.read_text())
        except (OSError, ValueError) as exc:
            sys.exit(f"cannot read rules from {path}: {exc}")
        if isinstance(proposed, dict):
            proposed = proposed.get("category_rules", [])
        if (not isinstance(proposed, list) or
                not all(isinstance(r, (list, tuple)) and len(r) == 2 for r in proposed)):
            sys.exit("expected a JSON list of [regex, label] pairs")
        for pattern, _ in proposed:
            try:
                re.compile(pattern)
            except re.error as exc:
                sys.exit(f"invalid regex {pattern!r}: {exc}")

        raw = scan(reg["roots"], reg["exclude_globs"], reg["max_depth"], [], "")
        current = reg["category_rules"]
        auto = current == "auto" or not current
        if auto:
            current, *_ = suggest_categories(raw)

        now, new = {}, {}
        for it in raw:
            stem = Path(it["file"]).stem
            a = categorize(stem, it["title"], current)
            bb = categorize(stem, it["title"], proposed)
            now[a] = now.get(a, 0) + 1
            new[bb] = new.get(bb, 0) + 1

        print(f"\n{len(raw)} artifacts\n")
        print(f"  {'proposed':<28}{'files':>6}     {'current':<28}{'files':>6}")
        print("  " + "-" * 76)
        pro = sorted(new.items(), key=lambda x: (x[0] == "Other", -x[1]))
        cur = sorted(now.items(), key=lambda x: (x[0] == "Other", -x[1]))
        for i in range(max(len(pro), len(cur))):
            l = f"{pro[i][0]:<28}{pro[i][1]:>6}" if i < len(pro) else " " * 34
            r = f"{cur[i][0]:<28}{cur[i][1]:>6}" if i < len(cur) else ""
            print(f"  {l}     {r}")
        print(f"\n  Other: {new.get('Other', 0)} proposed vs "
              f"{now.get('Other', 0)} current")

        if args.set_categories:
            reg["category_rules"] = [list(r) for r in proposed]
            save_registry(reg)
            print(f"\nwritten to {REGISTRY.name} -- rerun build_index.py to apply")
        else:
            print("\ndry run, nothing written")
        return

    if args.suggest_categories:
        raw = scan(reg["roots"], reg["exclude_globs"], reg["max_depth"],
                   [], reg["customer_pattern"])
        rules, picked, unmatched, total = suggest_categories(raw)
        if not rules:
            sys.exit("not enough shared vocabulary to suggest categories -- "
                     "add rules by hand in dashboards.json")
        print(f"\n{total} artifacts, {len(rules)} categories proposed, "
              f"{unmatched} would fall into Other\n")
        for (tok, gain, tot), rule in zip(picked, rules):
            print(f'  {gain:>4} files   {rule[1]:<22} {rule[0]}')
        print("\n  \"category_rules\": " + json.dumps(rules, indent=2).replace("\n", "\n  "))
        if args.apply:
            reg["category_rules"] = rules
            save_registry(reg)
            print(f"\nwritten to {REGISTRY.name} -- rerun build_index.py to apply")
        else:
            print("\nreview them, then rerun with --apply to write them "
                  "into dashboards.json")
        return

    print(f"scanning: {', '.join(reg['roots'])}")

    rules = reg["category_rules"]
    auto = rules == "auto" or not rules
    items = scan(reg["roots"], reg["exclude_globs"], reg["max_depth"],
                 [] if auto else rules, reg["customer_pattern"])

    if auto:
        # No rules configured: derive them from the files themselves, every run,
        # so categories keep up as the corpus grows. Freeze them by writing a list
        # into dashboards.json (--suggest-categories --apply does exactly that).
        rules, _, other, _ = suggest_categories(items)
        for it in items:
            it["category"] = categorize(Path(it["file"]).stem, it["title"], rules)
        if rules:
            print(f"categories (auto): {', '.join(r[1] for r in rules)}"
                  + (f" · {other} in Other" if other else ""))
    items = apply_overrides(items, reg["overrides"])
    items = attach_thumbs(items)

    save_registry(reg)
    OUTPUT.write_text(render(items, reg["roots"]), encoding="utf-8")

    thumbed = sum(1 for i in items if i["thumb"])
    print(f"indexed {len(items)} artifacts ({thumbed} with thumbnails) -> {OUTPUT}")

    if args.root or args.add or args.remove:
        print("roots changed -- rerun ./install-watcher.sh so the watcher follows them")

    if args.open:
        opener = {"darwin": "open", "win32": "start"}.get(sys.platform, "xdg-open")
        os.system(f'{opener} "{OUTPUT}"')


if __name__ == "__main__":
    main()

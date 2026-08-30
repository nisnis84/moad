# MOAD — Mother Of All Dashboards

One page that indexes every HTML dashboard, deck, report and diagram you generate.
`index.html` is generated — open it, don't edit it.

```
./refresh.sh          # rescan + thumbnail new files + rebuild   ← the one command
open index.html
```

## Files

| File | What it is |
|---|---|
| `index.html` | **The hub.** Generated. Search, category filters, sort, grid/list, thumbnails. |
| `build_index.py` | Scanner + generator. Reads titles from the first 64 KB of each file (some are 19 MB). |
| `make_thumbs.py` | Playwright screenshot pass. Cached on `path+mtime`, so reruns only render what's new. |
| `dashboards.json` | Your local registry: roots, depth, category rules, overrides. Gitignored. |
| `dashboards.example.json` | Copy it to `dashboards.json` to get started. |
| `thumbs/` | JPEG cache, ~37 KB each. Referenced relatively, not inlined. |
| `install-watcher.sh` | Installs the launchd auto-refresh agent. |
| `com.moad.watcher.plist` | launchd template (paths substituted at install). |
| `.venv/` | Playwright, for `make_thumbs.py` only. `build_index.py` needs nothing but python3. |

## Choosing which directory to index

MOAD indexes whatever directories you point it at. `~/Downloads` is only the
default because that is where browsers put things.

```
python3 build_index.py --root ~/work/reports     # use this directory instead
python3 build_index.py --add  ~/Documents/decks  # index a second one too
python3 build_index.py --remove ~/Downloads      # stop indexing one
python3 build_index.py --roots                   # what am I indexing?
./install-watcher.sh ~/work/reports              # set the root AND watch it
```

The watcher's `WatchPaths` are generated from `roots`, so the indexer and the
watcher can never point at different places. After changing roots, rerun
`./install-watcher.sh` — `build_index.py` reminds you.

Anything saved to a root shows up automatically if the watcher is installed, or
on the next `./refresh.sh` if it isn't.

## Auto-refresh (launchd watcher)

A launchd agent watches `~/Downloads` and rebuilds the hub whenever a dashboard
lands, so `index.html` is never stale — you just reload the tab.

```
./install-watcher.sh [watch-dir]                # installs the agent (default ~/Downloads)
~/Library/LaunchAgents/com.moad.watcher.plist   # WatchPaths on the watch dir
watch-refresh.sh                                         # debounce + guard + refresh.sh
watch.log                                                # one line per real rebuild
watch-launchd.log                                        # launchd's own stdout/stderr
```

`watch-refresh.sh` does three things before it spends any time:

1. **Locks** — a second trigger during a rebuild is a no-op.
2. **Settles** — waits 3s of quiet, so Chrome's `.crdownload` → rename doesn't
   rebuild twice.
3. **Fingerprints** — name + size + mtime of every indexed `*.html`. If a PDF or a
   zip landed instead of a dashboard, it exits in ~0.5s without launching anything.

Measured: ~0.5s for an idle wake, ~2.8s to index and thumbnail one new dashboard.

```
launchctl print gui/$UID/com.moad.watcher    # status
tail -f watch.log                                     # watch it work

launchctl bootout gui/$UID/com.moad.watcher  # stop
launchctl bootstrap gui/$UID ~/Library/LaunchAgents/com.moad.watcher.plist  # start
rm ~/Library/LaunchAgents/com.moad.watcher.plist   # uninstall (after bootout)
```

**macOS TCC gotcha, learned the hard way.** Under launchd, `/bin/bash` is denied a
directory listing of `~/Downloads` — a glob there silently returns the literal
pattern, not a file list — while `python3` in the *same job* reads it fine. So the
fingerprint is computed in python. A bash `stat`/glob implementation hashes the empty
string, every trigger compares empty-to-empty, and the watcher silently never fires.
If the fingerprint ever comes back empty the script logs `ABORT` and refuses to
rebuild, rather than recording a valid-looking hash.

Two limits worth knowing: `WatchPaths` is not recursive (top level only, matching
`max_depth: 1`), and launchd can drop an event that lands mid-rebuild. Nothing
breaks — the next trigger, or the next login, resyncs.

## How a dashboard gets its category

The filename minus `.html`, lowercased, is tested with `re.search` against each
`[regex, label]` pair in `category_rules`. **First match wins**; no match means
`Other`. An explicit `category` in `overrides` beats every rule.

```json
"category_rules": [
  ["^report[_-]",              "Customer Reports"],
  ["^ai[_-]|llm|agent",        "AI"],
  ["exec|board|quarter|qbr",   "Exec / Quarterly"]
]
```

Two things to know. It is `search`, not `match`, so `ai` matches anywhere in the
name — anchor with `^` when you mean "starts with". And order is load-bearing:
put the specific pattern before the general one, or the general one swallows it.

## Overrides

Rescans refresh titles/dates/sizes but never touch `overrides` in `dashboards.json`:

```json
"overrides": {
  "~/Downloads/quarterly_review.html": {
    "title": "Q3 Review",
    "category": "Exec / Quarterly",
    "pinned": true,
    "tags": ["weekly"]
  },
  "~/Downloads/scratch.html": { "hidden": true }
}
```

`pinned` floats an item to the top of every view. `hidden` drops it from the index.

## Settings in `dashboards.json`

- `roots` — directories to scan.
- `category_rules` — your own `[regex, label]` pairs, first match wins, anything
  unmatched is `Other`. Categories are yours; the tool ships only generic defaults.
- `customer_pattern` — optional regex with one capture group, to pull a customer or
  team name out of a filename. Empty (default) turns the feature off.
- `max_depth` — `1` = top level only. Raise it to pull in report bundles from
  subfolders (`~/Downloads` may hold many more HTML files one level down).
- `exclude_globs` — e.g. `["*_draft.html", "*copy*.html"]`.

## Keyboard

`/` focus search · `Esc` clear · click a card to open it in a new tab.

## Note

The hub links to absolute `file://` paths on this machine. Unlike the dashboards it
indexes, it is deliberately *not* portable — emailing it gets you a page of dead links.

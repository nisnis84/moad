#!/usr/bin/env python3
"""Render a JPEG thumbnail for every indexed HTML artifact.

Cached on (path, mtime): reruns only render new or changed files.
Requires playwright:  ./.venv/bin/python -m pip install playwright && ... playwright install chromium

Usage:
    .venv/bin/python make_thumbs.py            # render missing thumbs
    .venv/bin/python make_thumbs.py --limit 20 # try a handful first
    .venv/bin/python make_thumbs.py --force    # re-render everything
"""

import argparse
import concurrent.futures as cf
import json
import sys
import threading
from pathlib import Path

from build_index import (THUMBS, apply_overrides, file_url, load_registry, scan,
                         thumb_name)

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    sys.exit("playwright not installed. Run:\n"
             "  python3 -m venv .venv && .venv/bin/pip install playwright && "
             ".venv/bin/python -m playwright install chromium")

WIDTH, HEIGHT = 1440, 900          # render viewport
THUMB_W = 560                      # stored thumbnail width
MAX_BYTES = 40 * 1024 * 1024       # skip pathological files
PAGE_TIMEOUT = 25_000
SETTLE_MS = 1200
WORKERS = 4

lock = threading.Lock()
done = {"n": 0, "ok": 0, "fail": 0}
written = []


def shoot(browser, item, total):
    out = THUMBS / thumb_name(item["path"], item["mtime"])
    ctx = browser.new_context(viewport={"width": WIDTH, "height": HEIGHT},
                              device_scale_factor=1)
    page = ctx.new_page()
    ok = False
    try:
        page.goto(file_url(item["path"]), wait_until="load", timeout=PAGE_TIMEOUT)
        page.wait_for_timeout(SETTLE_MS)
        page.screenshot(path=str(out), type="jpeg", quality=62,
                        clip={"x": 0, "y": 0, "width": WIDTH, "height": HEIGHT},
                        scale="css")
        ok = True
        with lock:
            written.append(str(out))
    except Exception as exc:
        msg = str(exc).splitlines()[0][:80]
    finally:
        ctx.close()
    with lock:
        done["n"] += 1
        done["ok" if ok else "fail"] += 1
        tag = "ok " if ok else "FAIL"
        print(f"[{done['n']:>4}/{total}] {tag} {item['file'][:70]}"
              + ("" if ok else f"  -- {msg}"), flush=True)
    return ok


def downscale(files):
    """Shrink this run's shots to THUMB_W using sips (ships with macOS).

    Only the files just written -- re-running sips over already-shrunk JPEGs
    re-encodes them and loses quality on every refresh.
    """
    import subprocess
    if not files:
        return
    subprocess.run(["sips", "--resampleWidth", str(THUMB_W), *files,
                    "--out", str(THUMBS)],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


MANIFEST = THUMBS / "manifest.json"


def load_manifest():
    try:
        return json.loads(MANIFEST.read_text())
    except (OSError, ValueError):
        return {}


def save_manifest(m):
    MANIFEST.write_text(json.dumps(m, indent=0, sort_keys=True))


def sweep(items):
    """Delete a cached thumb only when its own source file is gone or has changed.

    Deliberately NOT "delete anything the current scan didn't produce": the scan
    roots are configurable, so a thumb can be absent from this run simply because
    you pointed MOAD at a different directory. Sweeping on that basis destroys the
    cache every time you switch roots.
    """
    manifest = load_manifest()
    # record what we know from this run
    for i in items:
        manifest[thumb_name(i["path"], i["mtime"])] = {"src": i["path"], "mtime": i["mtime"]}

    n = 0
    for f in THUMBS.glob("*.jpg"):
        entry = manifest.get(f.name)
        if entry is None:
            continue                      # unknown provenance -- leave it alone
        src = Path(entry["src"])
        try:
            stale = (not src.exists()) or int(src.stat().st_mtime) != entry["mtime"]
        except OSError:
            stale = True
        if stale:
            f.unlink()
            manifest.pop(f.name, None)
            n += 1

    # drop manifest rows whose thumb no longer exists
    for name in [k for k in manifest if not (THUMBS / k).exists()]:
        manifest.pop(name)
    save_manifest(manifest)
    return n


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    THUMBS.mkdir(exist_ok=True)
    reg = load_registry()
    items = apply_overrides(
        scan(reg["roots"], reg["exclude_globs"], reg["max_depth"],
             reg["category_rules"], reg["customer_pattern"]),
        reg["overrides"])

    todo = [i for i in items
            if i["size"] <= MAX_BYTES
            and (args.force or not (THUMBS / thumb_name(i["path"], i["mtime"])).exists())]
    todo.sort(key=lambda i: -i["mtime"])          # newest first, so an interrupted run still helps
    if args.limit:
        todo = todo[:args.limit]

    skipped = len(items) - len([i for i in items if i["size"] <= MAX_BYTES])
    print(f"{len(items)} artifacts · {len(todo)} to render · {skipped} over size cap")
    if not todo:
        print(f"nothing to render; {sweep(items)} stale thumbs swept")
        return

    # playwright's sync API is per-thread: each worker starts its own driver.
    shards = [todo[i::args.workers] for i in range(args.workers)]

    def run_shard(shard):
        if not shard:
            return
        with sync_playwright() as p:
            browser = p.chromium.launch(args=["--allow-file-access-from-files"])
            for item in shard:
                shoot(browser, item, len(todo))
            browser.close()

    with cf.ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(run_shard, shards))

    print(f"downscaling {len(written)}…")
    downscale(written)
    swept = sweep(items)
    print(f"done: {done['ok']} rendered, {done['fail']} failed, {swept} stale thumbs swept. "
          f"Now rerun: python3 build_index.py")


if __name__ == "__main__":
    main()

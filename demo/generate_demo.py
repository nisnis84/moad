#!/usr/bin/env python3
"""Generate a corpus of synthetic dashboards, so you can try MOAD (or screenshot it)
without pointing it at anything real.

    python3 demo/generate_demo.py ~/moad-demo
    python3 build_index.py --root ~/moad-demo && ./refresh.sh

Filenames deliberately mix conventions -- snake_case, kebab-case, spaces, dates in
three formats -- because a corpus that follows one scheme proves nothing.
"""

import random
import sys
from pathlib import Path

SEED = 7

# Written into every generated file. The generator will only ever delete files
# that contain this string -- it never blind-deletes anything in your directory.
MARKER = "generated for the MOAD demo, no real data"

# Six domains, several artifacts each -- the shape a real corpus actually has.
# The domain word recurs in filenames (sales_*, infra_*) exactly the way it does in
# real folders; that recurrence is the signal --suggest-categories is built to find.
SUBJECTS = [
    ("sales",     "Sales",     "Pipeline",   ["pipeline", "win rate", "bookings", "quota"]),
    ("sales",     "Sales",     "Bookings",   ["bookings", "churn", "expansion", "ARR"]),
    ("sales",     "Sales",     "Territory",  ["coverage", "reps", "quota attainment"]),
    ("infra",     "Infra",     "Latency",    ["p99", "p50", "error budget", "saturation"]),
    ("infra",     "Infra",     "Capacity",   ["node count", "utilisation", "headroom"]),
    ("infra",     "Infra",     "Incidents",  ["MTTR", "pages", "postmortems", "SLO burn"]),
    ("people",    "People",    "Hiring",     ["open roles", "time to hire", "offer accept"]),
    ("people",    "People",    "Headcount",  ["attrition", "span of control", "growth"]),
    ("people",    "People",    "Onboarding", ["ramp time", "30-day retention"]),
    ("model",     "AI",        "Evals",      ["eval score", "regressions", "pass rate"]),
    ("model",     "AI",        "Inference",  ["throughput", "cache hit", "GPU hours"]),
    ("model",     "AI",        "Cost",       ["cost per call", "tokens", "spend"]),
    ("finance",   "Finance",   "Budget",     ["burn", "runway", "variance to plan"]),
    ("finance",   "Finance",   "Spend",      ["cloud", "vendors", "per-seat"]),
    ("finance",   "Finance",   "Forecast",   ["pipeline coverage", "confidence"]),
    ("customer",  "Customers", "Support",    ["CSAT", "backlog", "first response"]),
    ("customer",  "Customers", "Health",     ["accounts", "risk", "renewals"]),
    ("customer",  "Customers", "Activation", ["time to value", "drop-off"]),
]

PERIODS = ["Q1 2026", "Q2 2026", "Q3 2026", "August 2026", "H1 2026", "Week 34"]

# five naming conventions on purpose
# five naming conventions on purpose -- snake, kebab, spaces, caps, prefixed
NAMERS = [
    lambda d, t, i: f"{d}_{t.lower()}_2026_0{i % 9 + 1}",
    lambda d, t, i: f"{d}-{t.lower()}-{['q1','q2','q3','h1'][i % 4]}-2026",
    lambda d, t, i: f"{d.title()} {t} Review",
    lambda d, t, i: f"{d.upper()}_{t.upper()}_{i:02d}",
    lambda d, t, i: f"{['team','exec','ops'][i % 3]}-{d}-{t.lower()}",
]

PALETTES = [("#4f46e5", "#eef0ff"), ("#0f766e", "#e6f5f2"), ("#b45309", "#fdf3e3"),
            ("#9333ea", "#f5ecfd"), ("#0369a1", "#e6f2fb")]

TPL = """<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>{title}</title>
<style>
 body{{margin:0;font:15px -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
   background:#f7f8fb;color:#12141a;padding:40px 48px}}
 h1{{font-size:26px;margin:0 0 4px;letter-spacing:-.02em}}
 .sub{{color:#6b7280;font-size:14px;margin-bottom:26px}}
 .tiles{{display:grid;grid-template-columns:repeat(4,1fr);gap:14px;margin-bottom:26px}}
 .tile{{background:#fff;border:1px solid #e6e8ef;border-radius:12px;padding:16px 18px}}
 .k{{font-size:12px;color:#6b7280;text-transform:uppercase;letter-spacing:.07em}}
 .v{{font-size:26px;font-weight:650;margin-top:6px;letter-spacing:-.02em}}
 .d{{font-size:12.5px;margin-top:4px;color:{accent}}}
 .card{{background:#fff;border:1px solid #e6e8ef;border-radius:12px;padding:20px 22px}}
 .card h2{{font-size:15px;margin:0 0 16px;font-weight:640}}
</style></head><body>
<h1>{title}</h1><div class="sub">{period} &middot; {marker}</div>
<div class="tiles">{tiles}</div>
<div class="card"><h2>{chart_title}</h2>
<svg viewBox="0 0 900 220" width="100%" height="220">
  <polyline fill="none" stroke="{accent}" stroke-width="3" points="{points}"/>
  <polyline fill="{soft}" stroke="none" points="{area}"/>
  {bars}
</svg></div>
</body></html>
"""


def build(rng, subject, category, metrics, period, accent, soft):
    # The category name is deliberately NOT in the title. A demo corpus that leaks
    # its own answer proves nothing about --suggest-categories.
    title = f"{subject} \u2014 {metrics[0].title()}"
    tiles = "".join(
        f'<div class="tile"><div class="k">{m}</div>'
        f'<div class="v">{rng.choice(["$", "", ""])}{rng.randrange(12, 980):,}'
        f'{rng.choice(["", "", "%", "k"])}</div>'
        f'<div class="d">{rng.choice("+-")}{rng.randrange(1, 40)}% vs last period</div></div>'
        for m in metrics[:4])
    ys = [rng.randrange(30, 180) for _ in range(12)]
    pts = " ".join(f"{20 + i * 78},{200 - y}" for i, y in enumerate(ys))
    area = f"20,200 {pts} {20 + 11 * 78},200"
    bars = "".join(
        f'<rect x="{18 + i * 78}" y="{200 - y // 3}" width="10" height="{y // 3}" '
        f'fill="{accent}" opacity=".22"/>' for i, y in enumerate(ys))
    return TPL.format(title=title, period=period, marker=MARKER, tiles=tiles, accent=accent, soft=soft,
                      chart_title=f"{metrics[0].title()} over time", points=pts,
                      area=area, bars=bars)


def main():
    out = Path(sys.argv[1] if len(sys.argv) > 1 else Path.home() / "moad-demo").expanduser()
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 32
    out.mkdir(parents=True, exist_ok=True)
    rng = random.Random(SEED)

    # Clear previous demo output so a rerun doesn't leave a mix of old and new
    # naming conventions behind. Only files carrying MARKER are touched.
    removed = 0
    for old in out.glob("*.html"):
        try:
            if MARKER in old.read_text(errors="ignore"):
                old.unlink()
                removed += 1
        except OSError:
            pass
    if removed:
        print(f"removed {removed} files from a previous demo run")

    stray = [f.name for f in out.glob("*.html")]
    if stray:
        print(f"note: leaving {len(stray)} non-demo file(s) in place: "
              f"{', '.join(stray[:3])}{' …' if len(stray) > 3 else ''}")

    for i in range(count):
        domain, category, topic, metrics = SUBJECTS[i % len(SUBJECTS)]
        accent, soft = PALETTES[i % len(PALETTES)]
        name = NAMERS[i % len(NAMERS)](domain, topic, i)
        (out / f"{name}.html").write_text(
            build(rng, topic, category, metrics, rng.choice(PERIODS), accent, soft))

    print(f"wrote {count} synthetic dashboards to {out}")
    print(f"try it:  python3 build_index.py --root {out} && ./refresh.sh")


if __name__ == "__main__":
    main()

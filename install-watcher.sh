#!/usr/bin/env bash
# Install the launchd agent that rebuilds MOAD whenever a dashboard lands.
#
#   ./install-watcher.sh              watch whatever dashboards.json scans
#   ./install-watcher.sh ~/Reports    set that as the scan root, then watch it
#
# The watched directories always come from dashboards.json, so the indexer and the
# watcher can never drift apart.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LABEL="com.moad.watcher"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ $# -gt 0 ]; then
  python3 "$HERE/build_index.py" --root "$1" >/dev/null
fi

# One <string> per configured root, XML-escaped.
WATCH_PATHS="$(python3 - "$HERE" <<'PY'
import json, sys
from pathlib import Path
from xml.sax.saxutils import escape
here = Path(sys.argv[1])
cfg = here / "dashboards.json"
if not cfg.exists():
    sys.exit("no dashboards.json yet -- run: python3 build_index.py")
roots = json.loads(cfg.read_text()).get("roots", [])
roots = [str(Path(r).expanduser()) for r in roots]
missing = [r for r in roots if not Path(r).is_dir()]
if missing:
    sys.exit("scan root does not exist: " + ", ".join(missing))
if not roots:
    sys.exit("no scan roots configured")
print("\n".join(f"        <string>{escape(r)}</string>" for r in roots))
PY
)"

python3 - "$HERE" "$DEST" "$WATCH_PATHS" <<'PY'
import sys
from pathlib import Path
here, dest, paths = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3]
tpl = (here / "com.moad.watcher.plist").read_text()
dest.write_text(tpl.replace("__MOAD_DIR__", str(here)).replace("__WATCH_PATHS__", paths))
PY
plutil -lint "$DEST" >/dev/null

# bootout is asynchronous: bootstrapping before the old service has actually
# gone gives "Bootstrap failed: 5: Input/output error". Wait for the label to
# disappear rather than racing it.
if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
  for _ in $(seq 1 50); do
    launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1 || break
    sleep 0.2
  done
  if launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
    echo "error: $LABEL is still loaded after bootout; unload it and retry" >&2
    exit 1
  fi
fi

launchctl bootstrap "gui/$UID" "$DEST"

# bootstrap can report success and still not register the job -- confirm it.
if ! launchctl print "gui/$UID/$LABEL" >/dev/null 2>&1; then
  echo "error: bootstrap reported success but $LABEL is not loaded" >&2
  exit 1
fi

echo "installed $LABEL — watching:"
python3 "$HERE/build_index.py" --roots | sed 's/^/  /'
echo "  status:    launchctl print gui/\$UID/$LABEL"
echo "  uninstall: launchctl bootout gui/\$UID/$LABEL && rm $DEST"

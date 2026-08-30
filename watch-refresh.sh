#!/usr/bin/env bash
# Triggered by launchd whenever ~/Downloads changes. Debounces, checks whether the
# set of HTML files actually changed, and only then rebuilds the hub.
#
# launchd gives a job almost no environment, so PATH is set explicitly here rather
# than inherited.
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
set -u

HERE="$(cd "$(dirname "$0")" && pwd)"
LOCK="$HERE/.watch.lock"
SIGFILE="$HERE/.watch-sig"
LOG="$HERE/watch.log"
SETTLE=3          # seconds of quiet required before rebuilding
MAX_SETTLE=20     # give up waiting for quiet after this many rounds (~60s)

log() { printf '%s  %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*" >> "$LOG"; }

# Single instance: a second trigger while a rebuild is running is a no-op.
if ! mkdir "$LOCK" 2>/dev/null; then
  exit 0
fi
trap 'rmdir "$LOCK" 2>/dev/null' EXIT

# Roots come from the registry, so adding one with --add also extends the watch scope.
roots=()
while IFS= read -r r; do [ -n "$r" ] && roots+=("$r"); done < <(
  python3 -c "import json;print('\n'.join(json.load(open('$HERE/dashboards.json'))['roots']))" 2>/dev/null
)
[ ${#roots[@]} -gt 0 ] || roots=("$HOME/Downloads")

# Fingerprint of every indexed HTML file: name + size + exact mtime.
#
# Computed in python, not with a bash glob + stat. Under launchd, /bin/bash is
# denied a directory listing of ~/Downloads by TCC (the glob silently returns the
# literal pattern) while python3 -- which holds its own grant -- reads it fine.
# A bash implementation here hashes the empty string and the watcher never fires.
sig() {
  python3 -c '
import sys, glob, os, hashlib
h = hashlib.sha1()
n = 0
for root in sys.argv[1:]:
    for f in sorted(glob.glob(os.path.join(root, "*.html"))):
        try:
            st = os.stat(f)
        except OSError:
            continue
        h.update(("%s|%d|%d\n" % (f, st.st_size, int(st.st_mtime))).encode())
        n += 1
# An empty fingerprint means we could not read the roots at all. Emit a sentinel
# rather than a valid-looking hash, so a permissions failure cannot masquerade as
# "nothing changed".
print("EMPTY-UNREADABLE" if n == 0 else h.hexdigest())
' "${roots[@]}" 2>/dev/null
}

# Debounce: wait for the directory to stop changing. Chrome writes foo.crdownload
# and then renames it, so a single save produces several launchd wakeups.
prev=""
cur="$(sig)"
rounds=0
while [ "$cur" != "$prev" ] && [ $rounds -lt $MAX_SETTLE ]; do
  prev="$cur"
  sleep "$SETTLE"
  cur="$(sig)"
  rounds=$((rounds + 1))
done

# Nothing relevant changed -- a PDF or a zip landed, not a dashboard.
if [ "$cur" = "EMPTY-UNREADABLE" ] || [ -z "$cur" ]; then
  log "ABORT: could not read any scan root (permissions?) -- not rebuilding"
  exit 1
fi

last="$(cat "$SIGFILE" 2>/dev/null || true)"
if [ "$cur" = "$last" ]; then
  exit 0
fi

start=$(date +%s)
out="$(cd "$HERE" && ./refresh.sh 2>&1)"
rc=$?
elapsed=$(( $(date +%s) - start ))

count="$(printf '%s' "$out" | sed -n 's/^indexed \([0-9]*\) artifacts.*/\1/p' | tail -1)"
if [ $rc -eq 0 ]; then
  printf '%s' "$cur" > "$SIGFILE"
  log "rebuilt: ${count:-?} artifacts in ${elapsed}s"
else
  log "FAILED (rc=$rc) after ${elapsed}s: $(printf '%s' "$out" | tail -3 | tr '\n' ' ')"
fi

# Keep the log from growing without bound.
if [ "$(wc -l < "$LOG" 2>/dev/null || echo 0)" -gt 500 ]; then
  tail -300 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
fi

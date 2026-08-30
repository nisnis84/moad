#!/usr/bin/env bash
# Install the launchd agent that rebuilds MOAD whenever a dashboard lands.
#   ./install-watcher.sh [watch-dir]     (default: ~/Downloads)
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
WATCH="${1:-$HOME/Downloads}"
LABEL="com.moad.watcher"
DEST="$HOME/Library/LaunchAgents/$LABEL.plist"

sed "s|__MOAD_DIR__|$HERE|g; s|__WATCH_DIR__|$WATCH|g" "$HERE/$LABEL.plist" > "$DEST"
plutil -lint "$DEST" >/dev/null

launchctl bootout "gui/$UID/$LABEL" 2>/dev/null || true
launchctl bootstrap "gui/$UID" "$DEST"

echo "installed $LABEL — watching $WATCH"
echo "  status:    launchctl print gui/\$UID/$LABEL"
echo "  uninstall: launchctl bootout gui/\$UID/$LABEL && rm $DEST"

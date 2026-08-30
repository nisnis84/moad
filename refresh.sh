#!/usr/bin/env bash
# Rescan every dashboard root, render thumbnails for anything new, rebuild the hub.
set -euo pipefail
cd "$(dirname "$0")"

python3 build_index.py                      # scan + regenerate index.html
if [ -x .venv/bin/python ]; then
  .venv/bin/python make_thumbs.py "$@"      # thumbnails for new/changed files only
  python3 build_index.py                    # re-link the fresh thumbnails
fi
echo "hub: $(pwd)/index.html"

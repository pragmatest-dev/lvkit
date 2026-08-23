#!/usr/bin/env bash
# Build the web extension's bundled Python wheels into media/wheels/ — the input
# the browser (Pyodide) build installs at runtime instead of a native binary.
#
#   - lvkit wheel: built FROM THE CURRENT SOURCE TREE. A stale wheel silently
#     ships old renderer code (bit the spike twice), so it is ALWAYS rebuilt.
#   - pylabview wheel: the pinned release, fetched from PyPI AT BUILD TIME (the
#     browser must NEVER touch PyPI at runtime). The URL is read from uv.lock so
#     it tracks the dependency pin with no second place to bump.
#
# (Pyodide core is still loaded from the CDN by web/extension.js; self-hosting it
# as an asset for strict-CSP / offline hosts is the next step.)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
WHEELS="$HERE/../media/wheels"

rm -rf "$WHEELS"
mkdir -p "$WHEELS"

echo "building lvkit wheel from $REPO …"
( cd "$REPO" && uv build --wheel -o "$WHEELS" )

URL="$(grep -oE 'https://[^"]*pylabview-[0-9.]+-py3-none-any\.whl' "$REPO/uv.lock" | head -1)"
[ -n "$URL" ] || { echo "ERROR: pylabview wheel URL not found in uv.lock" >&2; exit 1; }
echo "fetching pinned pylabview wheel: $URL"
curl -sSfL "$URL" -o "$WHEELS/$(basename "$URL")"

echo "media/wheels/:"
ls -1 "$WHEELS"

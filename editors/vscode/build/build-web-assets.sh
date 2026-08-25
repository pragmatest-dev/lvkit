#!/usr/bin/env bash
# Build the web extension's self-hosted runtime into media/ — everything the
# browser (Pyodide) build needs, so it NEVER touches a CDN or PyPI at runtime
# (strict-CSP / offline / air-gapped hosts must work).
#
#   media/wheels/  — the pure-Python wheels micropip installs with deps=False:
#     * lvkit     — built FROM THE CURRENT SOURCE TREE. A stale wheel silently
#                   ships old renderer code (bit the spike twice), so ALWAYS
#                   rebuilt.
#     * pylabview — the pinned release (URL read from uv.lock).
#     * networkx  — the pinned release (URL read from uv.lock). Self-hosted as a
#                   wheel — NOT via Pyodide's `networkx` package — because that
#                   package declares matplotlib+numpy as deps (~25 MB) that lvkit
#                   never imports; installing the pure-Python wheel deps=False
#                   drops the whole chain.
#
#   media/pyodide/ — the Pyodide CORE + only the packages the browser actually
#     loadPackage()s (micropip, pydantic, Pillow + their closure), pruned from
#     the pinned `pyodide` npm dist by build/prune_pyodide.py.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
VSC="$HERE/.."
WHEELS="$VSC/media/wheels"
PYODIR="$VSC/media/pyodide"

# --- wheels (micropip, deps=False) ------------------------------------------
rm -rf "$WHEELS"
mkdir -p "$WHEELS"

echo "building lvkit wheel from $REPO …"
( cd "$REPO" && uv build --wheel -o "$WHEELS" )
# uv drops a `.gitignore` (content `*`) into its output dir. It must NOT ship in
# the VSIX — Open VSX's resource CDN 404s the media/ tree when it's present (the
# web extension then can't read its wheels on GitLab Web IDE / Cursor / Gitpod).
# .vscodeignore.web also excludes **/.gitignore; removing it here keeps the local
# media/ tree clean too.
rm -f "$WHEELS/.gitignore"

fetch_pinned_wheel() {  # $1 = package name (matches <name>-<ver>-py3-none-any.whl)
  local url
  url="$(grep -oE "https://[^\"]*$1-[0-9][0-9.]*-py3-none-any\.whl" "$REPO/uv.lock" | head -1)"
  [ -n "$url" ] || { echo "ERROR: $1 wheel URL not found in uv.lock" >&2; exit 1; }
  echo "fetching pinned $1 wheel: $url"
  curl -sSfL "$url" -o "$WHEELS/$(basename "$url")"
}
fetch_pinned_wheel pylabview
fetch_pinned_wheel networkx

# Manifest of wheel filenames — the web extension reads THIS known file, because a
# browser extension host cannot list a directory (readDirectory of the extension's
# own resources throws EntryNotADirectory in the web worker).
node -e 'const fs=require("fs"),d=process.argv[1];const w=fs.readdirSync(d).filter(f=>f.endsWith(".whl"));fs.writeFileSync(d+"/manifest.json",JSON.stringify(w));console.log("wrote manifest.json:",w.join(", "))' "$WHEELS"

# --- pyodide core + pruned package set --------------------------------------
PYSRC="$VSC/node_modules/pyodide"
[ -d "$PYSRC" ] || { echo "ERROR: $PYSRC missing — run 'npm install' first" >&2; exit 1; }
PYVER="$(node -p "require('$PYSRC/package.json').version")"
CDN="https://cdn.jsdelivr.net/pyodide/v$PYVER/full/"
rm -rf "$PYODIR"
echo "assembling Pyodide $PYVER (core from npm, packages from $CDN) → media/pyodide …"
uv run --project "$REPO" python "$HERE/prune_pyodide.py" "$PYSRC" "$PYODIR" "$CDN" micropip pydantic Pillow

# --- report ------------------------------------------------------------------
echo "media/wheels/:"; ls -1 "$WHEELS"
echo "media/pyodide/ ($(du -sh "$PYODIR" | cut -f1)):"; ls -1 "$PYODIR"

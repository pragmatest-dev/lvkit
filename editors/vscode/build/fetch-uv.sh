#!/usr/bin/env bash
# Fetch the `uv` binary bundled into the VS Code extension (editors/vscode/bin/uv/).
#
# The extension runs lvkit as a MODULE — `uv run --with lvkit==<pin> python -m
# lvkit` — so no unsigned `lvkit.exe` is ever created or executed. That is what
# lets it work under Windows Device Guard / Smart App Control, where an unsigned
# PyInstaller binary is blocked: `uv` is a signed, high-reputation executable
# that those policies allow, and it provisions a managed Python + the pinned
# lvkit on first use.
#
# PER-PLATFORM (each VSIX carries its own bin/): pass the vsce target.
#   editors/vscode/build/fetch-uv.sh win32-x64        # default
#   editors/vscode/build/fetch-uv.sh linux-x64 | darwin-arm64 | ...
set -euo pipefail
UV_VERSION="${UV_VERSION:-0.11.32}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VSCODE="$(cd "$HERE/.." && pwd)"
TARGET="${1:-win32-x64}"
case "$TARGET" in
  win32-x64)    ASSET="uv-x86_64-pc-windows-msvc.zip";     EXE="uv.exe";;
  win32-arm64)  ASSET="uv-aarch64-pc-windows-msvc.zip";    EXE="uv.exe";;
  linux-x64)    ASSET="uv-x86_64-unknown-linux-gnu.tar.gz"; EXE="uv";;
  linux-arm64)  ASSET="uv-aarch64-unknown-linux-gnu.tar.gz";EXE="uv";;
  darwin-x64)   ASSET="uv-x86_64-apple-darwin.tar.gz";     EXE="uv";;
  darwin-arm64) ASSET="uv-aarch64-apple-darwin.tar.gz";    EXE="uv";;
  *) echo "unknown target: $TARGET" >&2; exit 1;;
esac
URL="https://github.com/astral-sh/uv/releases/download/${UV_VERSION}/${ASSET}"
DEST="$VSCODE/bin/uv"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
echo "Fetching uv ${UV_VERSION} for ${TARGET} …"
curl -fsSL "$URL" -o "$TMP/$ASSET"
if [[ "$ASSET" == *.zip ]]; then (cd "$TMP" && unzip -oq "$ASSET"); else (cd "$TMP" && tar xzf "$ASSET"); fi
FOUND="$(find "$TMP" -name "$EXE" -type f | head -1)"
[ -n "$FOUND" ] || { echo "uv binary '$EXE' not found in $ASSET" >&2; exit 1; }
mkdir -p "$DEST"
cp "$FOUND" "$DEST/$EXE"
chmod +x "$DEST/$EXE" 2>/dev/null || true
echo "Wrote $DEST/$EXE  (uv ${UV_VERSION}, ${TARGET})"

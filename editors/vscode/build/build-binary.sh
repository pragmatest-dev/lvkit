#!/usr/bin/env bash
# Build the standalone lvkit binary bundled into the VS Code extension.
#
# Produces editors/vscode/bin/lvkit/  (PyInstaller onedir, ~70 MB, PER-PLATFORM:
# run this on each of macOS / Windows / Linux to get that platform's binary).
# The binary carries no Python dependency, so the extension works for users with
# no Python/lvkit installed. Run from anywhere; requires the lvkit package +
# pyinstaller installed in the active environment (CI does this; locally:
# `uv pip install pyinstaller`).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$REPO"

PYI="${PYINSTALLER:-pyinstaller}"
# Keep work/spec dirs INSIDE the repo. PyInstaller does
# os.path.relpath(entry_script, start=specpath), and on Windows that raises
# "path is on mount 'D:', start on mount 'C:'" whenever the two land on
# different drives — which is exactly what happens on GitHub's windows runners:
# the checkout is on D:, but Git Bash has no TMPDIR so "${TMPDIR:-/tmp}" resolves
# to C:\...\Temp. A repo-relative path is always on the same drive as the source.
WORK="editors/vscode/build/.pyi-work"
"$PYI" --onedir --name lvkit --noconfirm \
  --collect-data lvkit \
  --collect-submodules lvkit \
  --collect-all pylabview \
  --collect-submodules networkx \
  --exclude-module tkinter --exclude-module matplotlib \
  --distpath editors/vscode/bin \
  --workpath "$WORK" \
  --specpath "$WORK" \
  editors/vscode/build/lvkit_entry.py

BIN="editors/vscode/bin/lvkit/lvkit"
[ -f "$BIN.exe" ] && BIN="$BIN.exe"
echo "Built $BIN — verifying…"
"$BIN" --version

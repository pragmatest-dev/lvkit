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

# Capture lvkit's cache fingerprints from the LIVE sources and bundle them next
# to the package: a frozen binary ships no .py sources, so it cannot compute them
# at runtime — without this the render/index caches would degrade (blind to code
# changes) or crash. See embed_fingerprints.py + cache_paths._embedded_fingerprints.
# PyInstaller resolves an --add-data SOURCE relative to --specpath (NOT the cwd).
# Write the file under the workdir (= specpath) and pass it as a plain
# specpath-relative "fp/…" path: a repo-relative path would double (and be
# silently skipped — PyInstaller only prints an ERROR yet still exits 0), and an
# absolute git-bash path (/d/a/…) gets mangled to \d\a\ when it reaches the native
# PyInstaller through the "src;dest" arg on Windows. A bare "fp/…" avoids both.
FP_DIR="$WORK/fp"
mkdir -p "$FP_DIR"
python editors/vscode/build/embed_fingerprints.py "$FP_DIR/_build_fingerprints.json"
# PyInstaller's --add-data separator is os.pathsep: ';' on Windows, ':' elsewhere.
case "$(uname -s)" in MINGW*|MSYS*|CYGWIN*) ADSEP=";" ;; *) ADSEP=":" ;; esac

"$PYI" --onedir --name lvkit --noconfirm \
  --collect-data lvkit \
  --collect-submodules lvkit \
  --collect-all pylabview \
  --collect-submodules networkx \
  --exclude-module tkinter --exclude-module matplotlib \
  --add-data "fp/_build_fingerprints.json${ADSEP}lvkit" \
  --distpath editors/vscode/bin \
  --workpath "$WORK" \
  --specpath "$WORK" \
  editors/vscode/build/lvkit_entry.py

# Fail loudly if the build fingerprints didn't bundle (PyInstaller only prints an
# ERROR and still exits 0 on a missing --add-data source). Without this file the
# binary falls back to the code-blind/sentinel path — the very thing this fixes.
BUNDLED_FP="editors/vscode/bin/lvkit/_internal/lvkit/_build_fingerprints.json"
test -f "$BUNDLED_FP" || {
  echo "ERROR: build fingerprints were not bundled ($BUNDLED_FP missing)" >&2
  exit 1
}

# macOS PyInstaller emits a Python.framework bundle containing SYMLINKS THAT
# POINT AT DIRECTORIES (Resources -> Versions/Current/Resources, and
# Versions/Current -> 3.14). vsce's secret scanner (@secretlint/source-creator)
# follows every entry and read()s it; on a directory that throws an unhandled
# EISDIR, which vsce reports as an empty
# "Error occurred while scanning secrets (files):" — no type, no file. It is a
# CRASH, not a detection, which is why --allow-package-all-secrets never helped.
#
# Materialize those symlinks into real directories rather than excluding them:
# Versions/Current is load-bearing for macOS dylib resolution, so dropping it
# would ship a binary that cannot find Python. Copying keeps every path the
# loader needs and costs a few MB of duplicated framework. No-op on Linux and
# Windows, which have no framework bundle and no directory symlinks.
find editors/vscode/bin -type l | while read -r link; do
  if [ -d "$link" ]; then
    target="$(readlink -f "$link")"
    echo "Materializing directory symlink: $link -> $target"
    rm "$link" && cp -R "$target" "$link"
  fi
done

BIN="editors/vscode/bin/lvkit/lvkit"
[ -f "$BIN.exe" ] && BIN="$BIN.exe"
echo "Built $BIN — verifying…"
"$BIN" --version

#!/usr/bin/env bash
# Fetch the self-contained runtime bundled into the VS Code extension
# (editors/vscode/bin/) for one vsce target. TWO per-platform components, both
# git-ignored (bin/ is in .gitignore):
#
#   bin/python/python/   a python-build-standalone "install_only" CPython 3.12
#   bin/libs/            lvkit + its full dependency set, INSTALLED (not wheels)
#
# APPROACH B: the extension launches the bundled python DIRECTLY with lvkit on
# PYTHONPATH — `<bin/python> -m lvkit` with PYTHONPATH=<bin/libs>. No uv, no
# venv, no first-run assembly. Device Guard / Smart App Control evaluate the
# binary being LOADED (python.exe + the .pyd files it imports), not a launcher;
# those are signed / high-reputation python-build-standalone + PyPI wheels (the
# same binaries uv merely spawned in 0.1.8). lvkit is run as a MODULE, so no
# unsigned `lvkit.exe` is ever created or executed.
#
# `pip install --target` unpacks the wheels WITHOUT running the target python,
# so a win32 bundle can be built from Linux — the platform is selected by
# --platform/--python-version/--implementation, not by the host interpreter.
#
# PER-PLATFORM (each VSIX carries its own bin/): pass the vsce target.
#   editors/vscode/build/fetch-bundle.sh win32-x64        # default
#   editors/vscode/build/fetch-bundle.sh linux-x64 | darwin-arm64 | ...
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VSCODE="$(cd "$HERE/.." && pwd)"
TARGET="${1:-win32-x64}"

# Pins — keep these explicit so the bundle is reproducible.
LVKIT_PIN="${LVKIT_PIN:-0.5.6}"
PBS_RELEASE="${PBS_RELEASE:-20260728}"   # python-build-standalone release tag
PY_VERSION="${PY_VERSION:-3.12.13}"      # CPython version within that release
PY_XY="3.12"                             # major.minor for pip --python-version

# Map the vsce target -> (python-build-standalone triple, pip --platform tags).
# pip needs --only-binary + explicit --platform tags to place non-native wheels;
# multiple manylinux/macos tags are passed so both older and newer wheels match.
case "$TARGET" in
  win32-x64)    PBS_TRIPLE="x86_64-pc-windows-msvc";     PIP_PLATFORMS="win_amd64";;
  win32-arm64)  PBS_TRIPLE="aarch64-pc-windows-msvc";    PIP_PLATFORMS="win_arm64";;
  linux-x64)    PBS_TRIPLE="x86_64-unknown-linux-gnu";   PIP_PLATFORMS="manylinux2014_x86_64 manylinux_2_17_x86_64 manylinux_2_28_x86_64";;
  linux-arm64)  PBS_TRIPLE="aarch64-unknown-linux-gnu";  PIP_PLATFORMS="manylinux2014_aarch64 manylinux_2_17_aarch64 manylinux_2_28_aarch64";;
  darwin-x64)   PBS_TRIPLE="x86_64-apple-darwin";        PIP_PLATFORMS="macosx_10_12_x86_64 macosx_11_0_x86_64";;
  darwin-arm64) PBS_TRIPLE="aarch64-apple-darwin";       PIP_PLATFORMS="macosx_11_0_arm64";;
  *) echo "unknown target: $TARGET" >&2; exit 1;;
esac

# uv is NOT part of approach B — make sure a stale bin/uv (or bin/wheels from the
# old approach) from an earlier build can't ride along in the VSIX.
rm -rf "$VSCODE/bin/uv" "$VSCODE/bin/wheels"

# ---- 1. python-build-standalone (install_only CPython) ----------------------
echo "==> python-build-standalone ${PY_VERSION}+${PBS_RELEASE} (${PBS_TRIPLE})"
PBS_ASSET="cpython-${PY_VERSION}+${PBS_RELEASE}-${PBS_TRIPLE}-install_only.tar.gz"
PBS_URL="https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${PBS_ASSET}"
PYDEST="$VSCODE/bin/python"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
curl -fsSL "$PBS_URL" -o "$TMP/py.tar.gz"
rm -rf "$PYDEST"
mkdir -p "$PYDEST"
# The install_only archive extracts to a top-level "python/" dir (contains
# python.exe on Windows; bin/python3 on unix). Land it at bin/python/python/.
tar xzf "$TMP/py.tar.gz" -C "$PYDEST"
# The unix install_only builds ship an ncurses terminfo DB (share/terminfo)
# whose entries collide case-insensitively (h/hp...a vs ...A, p/p9 vs P/P9) —
# the VSIX zip is case-insensitive and REJECTS such a pair. lvkit does no
# terminal I/O, so drop terminfo entirely (also fixes the win/mac builds being
# the only ones that packaged). Harmless if absent (Windows).
rm -rf "$PYDEST/python/share/terminfo"
echo "    -> $PYDEST/python (pruned share/terminfo)"

# ---- 2. libs: lvkit + full dependency set, installed via pip --target --------
echo "==> libs: lvkit==${LVKIT_PIN} + deps into bin/libs (${PIP_PLATFORMS})"
LIBS="$VSCODE/bin/libs"
rm -rf "$LIBS"
mkdir -p "$LIBS"
PLAT_ARGS=()
for p in $PIP_PLATFORMS; do PLAT_ARGS+=(--platform "$p"); done
# Pick a host python that actually has pip (an activated venv without pip, e.g.
# a uv-managed .venv, would fail). Override with PIP_PYTHON if needed.
PIP_PYTHON="${PIP_PYTHON:-}"
if [ -z "$PIP_PYTHON" ]; then
  for cand in python3 /usr/bin/python3 python; do
    if "$cand" -m pip --version >/dev/null 2>&1; then PIP_PYTHON="$cand"; break; fi
  done
fi
[ -n "$PIP_PYTHON" ] || { echo "no python with pip found (set PIP_PYTHON)" >&2; exit 1; }
# --target unpacks wheels without executing the target interpreter, so this
# cross-installs (e.g. a win_amd64 tree from Linux). --implementation cp +
# --python-version + --only-binary pin the wheel selection to the target.
"$PIP_PYTHON" -m pip install "lvkit==${LVKIT_PIN}" \
  --target "$LIBS" \
  --only-binary=:all: \
  --implementation cp \
  --python-version "$PY_XY" \
  "${PLAT_ARGS[@]}"
echo "    -> installed into $LIBS"

# ---- 3. precompile bytecode as HASH-BASED, UNCHECKED ------------------------
# pip --target ships timestamp-based .pyc (or, cross-installed, matching-version
# ones). The VSIX is a zip: extraction RESETS every .py mtime, so timestamp .pyc
# look stale and Python recompiles the ENTIRE tree on first render (~20s). Rebuild
# them as hash-based `unchecked` (PEP 552): Python then trusts the .pyc without
# any mtime/hash check, so extraction can't invalidate them -> instant first run.
# .pyc bytecode is OS-INDEPENDENT (magic is tied to the CPython VERSION only), so
# a host CPython 3.12 can compile a win32/darwin bundle's sources fine.
echo "==> precompiling bytecode (hash-based, unchecked) so first render is instant"
COMPILE_PY="${HOST_PY312:-}"
[ -z "$COMPILE_PY" ] && command -v python3.12 >/dev/null 2>&1 && COMPILE_PY="python3.12"
if [ -z "$COMPILE_PY" ]; then
  # No host 3.12 — fetch a linux-x64 python-build-standalone of the SAME version
  # (exact magic match) just to run compileall. Not shipped.
  echo "    (no host python3.12; fetching one to compile with)"
  HOSTASSET="cpython-${PY_VERSION}+${PBS_RELEASE}-x86_64-unknown-linux-gnu-install_only.tar.gz"
  curl -fsSL "https://github.com/astral-sh/python-build-standalone/releases/download/${PBS_RELEASE}/${HOSTASSET}" -o "$TMP/hostpy.tar.gz"
  tar xzf "$TMP/hostpy.tar.gz" -C "$TMP"
  COMPILE_PY="$TMP/python/bin/python3"
fi
# -f: overwrite the timestamp .pyc pip left. Warnings from deps (e.g. pylabview's
# unescaped-regex SyntaxWarning) are cosmetic; -q keeps them quiet.
"$COMPILE_PY" -m compileall -f -q -q --invalidation-mode unchecked-hash "$LIBS" \
  || echo "    WARNING: compileall failed — first render will recompile (slow) but still works" >&2
echo "    -> $(find "$LIBS" -name '*.pyc' | wc -l) .pyc precompiled"

echo
echo "Bundle ready for ${TARGET} (approach B — no uv, no venv):"
du -sh "$VSCODE/bin/python" "$VSCODE/bin/libs" 2>/dev/null || true

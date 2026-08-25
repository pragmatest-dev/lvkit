#!/usr/bin/env bash
# CI parity guard: the web (Pyodide / wasm) render MUST be byte-identical to the
# native render for every fixture. Determinism holds across interpreters, so any
# divergence means the web target silently drifts from desktop — a release
# blocker. Renders each fixture BOTH ways and diffs, after normalizing the
# path-derived DOM id (the id encodes the input file path, which legitimately
# differs between the native path and the wasm /vi/… path; nothing else may).
#
# Prereq: media/pyodide + media/wheels exist (run `npm run build:web` first).
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$REPO"

# A small, feature-diverse set of committed fixtures (decorations, hidden
# terminals, bundle names, structure-tunnel wires, z-order).
FIXTURES=(
  tests/corpus/issues/32/comments-and-decorations.vi
  tests/corpus/issues/34/hidden-iteration-terminal.vi
  tests/corpus/issues/36/bundle-unbundle-names.vi
  tests/corpus/issues/37/structure-tunnel-wire-bends.vi
  tests/corpus/issues/39/zorder-not-respected.vi
)

OUT="$(mktemp -d)"
norm() { sed -E 's/lv-[a-z0-9-]*-vi/lv-ID/g' "$1"; }
slug() { printf '%s' "$1" | tr -c 'a-zA-Z0-9' '_'; }

echo "native renders (lvkit render --no-cache) …"
for vi in "${FIXTURES[@]}"; do
  uv run lvkit render "$vi" --format svg --no-cache --theme light \
    -o "$OUT/$(slug "$vi").native.svg" >/dev/null
done

echo "wasm renders (Pyodide, self-hosted) …"
node "$HERE/web-parity.cjs" "$OUT" "${FIXTURES[@]}"

echo "comparing (normalized) …"
fail=0
for vi in "${FIXTURES[@]}"; do
  s="$(slug "$vi")"
  if diff <(norm "$OUT/$s.native.svg") <(norm "$OUT/$s.wasm.svg") >/dev/null; then
    echo "  ok   $vi"
  else
    echo "  FAIL $vi — wasm render diverges from native:"
    diff <(norm "$OUT/$s.native.svg") <(norm "$OUT/$s.wasm.svg") | head -20 || true
    fail=1
  fi
done

if [ "$fail" -eq 0 ]; then
  echo "web==native parity holds for ${#FIXTURES[@]} fixtures"
else
  echo "::error::web/wasm render diverges from native — the web target is not faithful"
fi
exit "$fail"

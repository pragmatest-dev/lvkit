#!/usr/bin/env bash
# Assemble the per-target distribution bundles FROM THE ALREADY-BUILT onedir
# binary (editors/vscode/bin/lvkit) — reusing the one build, never rebuilding.
# Unix legs (linux-x64, darwin-x64, darwin-arm64). Windows uses the .ps1 sibling.
#
#   $1 = target  (linux-x64 | darwin-x64 | darwin-arm64)
#   $2 = version (the BUNDLED lvkit version, e.g. 0.5.8)
#
# Produces at the repo root:
#   lvkit-plugin-<target>.zip   Claude Code plugin (skeleton + binary + skills)
#   lvkit-<target>.mcpb         Claude Desktop bundle (darwin targets only)
set -euo pipefail
TARGET="$1"
VER="$2"
# Customer-facing plugin name (must equal the marketplace entry name). TARGET is
# an internal arch triple; the published plugin uses a friendlier name.
case "$TARGET" in
  darwin-arm64) NAME="lvkit-mac-arm64" ;;
  darwin-x64)   NAME="lvkit-mac-intel" ;;
  linux-x64)    NAME="lvkit-linux" ;;
  win32-x64)    NAME="lvkit-windows" ;;
  *)            NAME="lvkit-$TARGET" ;;
esac
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../../.." && pwd)"
cd "$REPO"

BIN="editors/vscode/bin/lvkit"   # onedir bundle dir; executable at $BIN/lvkit
SKILLS=(lvkit lvkit-describe lvkit-query lvkit-convert lvkit-document lvkit-review lvkit-resolve)

# ---- Claude Code plugin archive -------------------------------------------
# Layout at the zip root: .claude-plugin/plugin.json, .mcp.json, skills/, bin/lvkit/.
stage="$(mktemp -d)/plugin"
mkdir -p "$stage/skills" "$stage/bin"
cp -R plugin/.claude-plugin "$stage/"
cp plugin/.mcp.json "$stage/"
cp plugin/README.md "$stage/"
for s in "${SKILLS[@]}"; do
  cp -R "src/lvkit/skill_templates/$s" "$stage/skills/$s"
done
cp -R "$BIN" "$stage/bin/lvkit"
# Stamp the manifest: name matches the marketplace entry, version = bundled lvkit.
node -e "const f='$stage/.claude-plugin/plugin.json',j=require(f);j.name='$NAME';j.version='$VER';require('fs').writeFileSync(f,JSON.stringify(j,null,2)+'\n')"
( cd "$stage" && zip -q -r -y "$REPO/lvkit-plugin-$TARGET.zip" . )
echo "wrote lvkit-plugin-$TARGET.zip"

# ---- Claude Desktop .mcpb (macOS only; Windows in the .ps1) ----------------
case "$TARGET" in
  darwin-*)
    mb="$(mktemp -d)/mcpb"
    mkdir -p "$mb/server"
    cp -R "$BIN/." "$mb/server/"   # onedir CONTENTS -> server/ (executable at server/lvkit)
    node -e "const j=require('$REPO/mcpb/manifest.json');j.version='$VER';j.compatibility=j.compatibility||{};j.compatibility.platforms=['darwin'];require('fs').writeFileSync('$mb/manifest.json',JSON.stringify(j,null,2)+'\n')"
    npx --yes @anthropic-ai/mcpb pack "$mb" "$REPO/lvkit-$TARGET.mcpb"
    echo "wrote lvkit-$TARGET.mcpb"
    ;;
esac

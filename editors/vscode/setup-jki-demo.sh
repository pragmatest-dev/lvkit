#!/usr/bin/env bash
# Prepare a local JKI VI Tester repo with a one-file working-tree change so you
# can test the lvkit VS Code extension end-to-end.
#
#   ./setup-jki-demo.sh [TARGET_DIR]
#
# TARGET_DIR defaults to ~/lvkit-vi-demo. Override the lvkit path with LVKIT=...
set -euo pipefail

TARGET="${1:-$HOME/lvkit-vi-demo}"
OLD_REV=92be264   # parent of the #38 "skip tests from setUp" change
NEW_REV=3fb850d   # the #38 change itself
VI="source/Classes/TestCase/run.vi"

LVKIT="${LVKIT:-$(command -v lvkit || true)}"
[ -z "$LVKIT" ] && LVKIT="/home/ryanf/repos/lvkit/.venv/bin/lvkit"

echo "→ demo repo: $TARGET"
if [ ! -d "$TARGET/.git" ]; then
  echo "→ cloning JKI VI Tester…"
  git clone --quiet https://github.com/JKISoftware/JKI-VI-Tester.git "$TARGET"
fi

# Check out the OLD revision of the whole repo, then drop the NEW run.vi on top
# so exactly one file shows as a working-tree change.
git -C "$TARGET" checkout -fq "$OLD_REV"
git -C "$TARGET" show "$NEW_REV:$VI" > "$TARGET/$VI"

mkdir -p "$TARGET/.vscode"
cat > "$TARGET/.vscode/settings.json" <<EOF
{
  "lvkit.path": "$LVKIT"
}
EOF

echo "→ $VI is now a working-tree change (before=$OLD_REV, after=$NEW_REV)"
echo "→ wrote $TARGET/.vscode/settings.json  (lvkit.path = $LVKIT)"
cat <<'STEPS'

Next:
  1. Open the extension folder (editors/vscode) in VS Code and press F5.
     A new "Extension Development Host" window launches with the extension.
  2. In that window: File > Open Folder > the demo repo above.
  3. Explorer: click any .vi  → it renders (no "binary file" notice).
  4. Source Control: right-click the changed run.vi → "lvkit: Open Visual Diff".
STEPS

#!/usr/bin/env bash
# Regenerate the pragmatest.com lvkit example pages from PUBLIC sources cloned at
# build time — no local corpus, no hand-staged fixtures. Every input is a public
# git ref, so this runs identically on a fresh CI runner and on a dev box.
#
#   scripts/build_example_pages.sh <output-dir>
#
# Produces, under <output-dir>:
#   render/testcase.html  — JKI VI Tester TestCase/run.vi @ tag 3.0.0
#   diff/testcase.html    — that VI 3.0.0 (base) -> 3.0.1 (head)
#   render/md5.html       — OpenG MD5 Message Digest (Binary String)__ogtk.vi
#
# Sources (both OSI-approved, redistributable):
#   JKI-VI-Tester   BSD-3-Clause   github.com/JKISoftware/JKI-VI-Tester
#   OpenG Toolkit   BSD-3-Clause   reproduced from upstream .vip via scripts/
set -euo pipefail

OUT="${1:?usage: build_example_pages.sh <output-dir>}"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$OUT/render" "$OUT/diff"
OUT="$(cd "$OUT" && pwd)"  # absolute — lvk() renders from $ROOT, OUT must not be relative to it
WORK="$(mktemp -d)"
trap 'rm -rf "$WORK"' EXIT

lvk() { ( cd "$ROOT" && uv run lvkit "$@" ); }

# --- JKI VI Tester: TestCase/run.vi at 3.0.0 (base) vs 3.0.1 (head) -----------
# base = the committed tree at tag 3.0.0; head = the same tree at 3.0.1 (JKI
# wrapped several nodes in a new Case between those releases). Both are real
# public commits, checked out whole so every SubVI sits at the path run.vi
# records for it — never hand-edited.
JKI="$WORK/JKI-VI-Tester"
RUNVI="source/Classes/TestCase/run.vi"
echo "clone JKI-VI-Tester (BSD-3-Clause)"
git clone --quiet https://github.com/JKISoftware/JKI-VI-Tester.git "$JKI"
git -C "$JKI" worktree add --quiet --detach "$WORK/base" 3.0.0
git -C "$JKI" worktree add --quiet --detach "$WORK/head" 3.0.1

echo "→ render: testcase  ($OUT/render/testcase.html)"
lvk render "$WORK/base/$RUNVI" --format html \
    --no-cache -o "$OUT/render/testcase.html"

echo "→ diff:   testcase  ($OUT/diff/testcase.html)"
lvk diff "$WORK/base/$RUNVI" "$WORK/head/$RUNVI" --format html \
    --no-cache -o "$OUT/diff/testcase.html"

# --- OpenG MD5 (BSD-3-Clause), reproduced from upstream .vip packages ---------
OPENG="$WORK/openg"
echo "reproduce OpenG corpus (BSD-3-Clause; fetches upstream .vip)"
uv run python "$ROOT/scripts/reproduce_openg_corpus.py" "$OPENG"
MD5="$OPENG/File Group 0/user.lib/_OpenG.lib/md5/md5.llb/MD5 Message Digest (Binary String)__ogtk.vi"

echo "→ render: md5       ($OUT/render/md5.html)"
lvk render "$MD5" --format html --userlib "$OPENG/File Group 0/user.lib" \
    --no-cache -o "$OUT/render/md5.html"

echo "done → $OUT"

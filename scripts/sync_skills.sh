#!/usr/bin/env bash
# Verify the in-repo .claude/skills/<name>/SKILL.md files match their
# packaged templates in src/vipy/skill_templates/claude/<name>/SKILL.md.
#
# vipy ships 5 user-facing skills (resolve-primitive, resolve-vilib,
# describe-vi, convert, idiomatic) via PyPI. The source of truth is the
# packaged template; the in-repo .claude/skills/ copy is a byte-identical
# mirror so vipy maintainers using Claude Code in this repo get the same
# skills downstream users receive.
#
# This hook fails if the two diverge. Fix by copying the template:
#   cp src/vipy/skill_templates/claude/<skill>/SKILL.md \
#      .claude/skills/<skill>/SKILL.md
#
# The two maintainer-only skills (judge-output, trace-bug) are NOT
# packaged and NOT checked here.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_DIR="$REPO_ROOT/src/vipy/skill_templates/claude"
INREPO_DIR="$REPO_ROOT/.claude/skills"

SKILLS=(
    resolve-primitive
    resolve-vilib
    describe-vi
    convert
    idiomatic
)

failed=0

for skill in "${SKILLS[@]}"; do
    template="$TEMPLATE_DIR/$skill/SKILL.md"
    inrepo="$INREPO_DIR/$skill/SKILL.md"

    if [[ ! -f "$template" ]]; then
        echo "ERROR: missing template: $template" >&2
        failed=1
        continue
    fi
    if [[ ! -f "$inrepo" ]]; then
        echo "ERROR: missing in-repo copy: $inrepo" >&2
        failed=1
        continue
    fi

    if ! diff -q "$template" "$inrepo" >/dev/null; then
        echo "ERROR: $skill SKILL.md diverges from template" >&2
        echo "  template: $template" >&2
        echo "  in-repo:  $inrepo" >&2
        echo "  fix:      cp \"$template\" \"$inrepo\"" >&2
        failed=1
    fi
done

if [[ $failed -eq 0 ]]; then
    echo "All 5 user-facing skills match their templates."
fi

exit $failed

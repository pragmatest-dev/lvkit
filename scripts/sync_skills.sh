#!/usr/bin/env bash
# Verify the in-repo .claude/skills/<name>/SKILL.md files match their
# packaged templates in src/vipy/skill_templates/claude/<name>/SKILL.md.
#
# vipy ships user-facing skills via PyPI through the package data at
# src/vipy/skill_templates/. The source of truth is the packaged template;
# the in-repo .claude/skills/ copy is a byte-identical mirror so vipy
# maintainers using Claude Code in this repo get the same skills downstream
# users receive.
#
# Skills that exist only in .claude/skills/ (not under skill_templates/)
# are intentionally maintainer-only — they're not checked by this script.
# That includes judge-output and trace-bug, which test/debug vipy itself
# and have no value to downstream users.
#
# Discovery is automatic: every directory under src/vipy/skill_templates/
# must have a corresponding .claude/skills/ entry. Adding a new template
# requires no script change. The templates are also the source for the
# Copilot install path (built dynamically by install_copilot_instructions
# in project_store.py) — that's why the directory is not named "claude/".
#
# This hook fails if any pair diverges. Fix by copying the template:
#   cp src/vipy/skill_templates/claude/<skill>/SKILL.md \
#      .claude/skills/<skill>/SKILL.md

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TEMPLATE_DIR="$REPO_ROOT/src/vipy/skill_templates"
INREPO_DIR="$REPO_ROOT/.claude/skills"

if [[ ! -d "$TEMPLATE_DIR" ]]; then
    echo "ERROR: template directory missing: $TEMPLATE_DIR" >&2
    exit 1
fi

failed=0
checked=0

for template in "$TEMPLATE_DIR"/*/SKILL.md; do
    if [[ ! -f "$template" ]]; then
        # No skill template directories — nothing to check.
        echo "No skill templates found under $TEMPLATE_DIR" >&2
        exit 0
    fi

    skill="$(basename "$(dirname "$template")")"
    # Skip Python package internals like __pycache__ that the glob picks up.
    case "$skill" in
        _*|.*) continue ;;
    esac
    inrepo="$INREPO_DIR/$skill/SKILL.md"
    checked=$((checked + 1))

    if [[ ! -f "$inrepo" ]]; then
        echo "ERROR: missing in-repo copy for skill template '$skill'" >&2
        echo "  template: $template" >&2
        echo "  expected: $inrepo" >&2
        echo "  fix:      mkdir -p \"$(dirname "$inrepo")\" && cp \"$template\" \"$inrepo\"" >&2
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
    echo "All $checked user-facing skill(s) match their templates."
fi

exit $failed

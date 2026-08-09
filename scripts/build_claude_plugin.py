#!/usr/bin/env python3
"""Assemble the Claude Code plugin's ``skills/`` from the single source of truth.

lvkit ships its five Agent Skills in exactly one place —
``src/lvkit/skill_templates/<name>/SKILL.md`` — which ``lvkit setup claude``
copies into a project's ``.claude/skills/``. The Claude Code *plugin*
(``plugins/lvkit/``) must serve the identical skills, so rather than commit a
second hand-maintained copy that can silently drift, we GENERATE the plugin's
``skills/`` from the templates.

Run this whenever a skill template changes:

    uv run python scripts/build_claude_plugin.py

``tests/test_claude_plugin.py`` asserts the committed plugin skills are
byte-identical to the templates, so CI fails loudly if someone edits a template
without regenerating (the same drift-guard philosophy as the atomic installer in
``project_store.py``).
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
TEMPLATES_DIR = REPO_ROOT / "src" / "lvkit" / "skill_templates"
PLUGIN_SKILLS_DIR = REPO_ROOT / "plugins" / "lvkit" / "skills"


def iter_skill_templates() -> list[Path]:
    """Every ``skill_templates/<name>/`` directory that holds a SKILL.md."""
    return sorted(
        d
        for d in TEMPLATES_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    )


def build(check: bool = False) -> int:
    """Regenerate ``plugins/lvkit/skills/`` from the templates.

    With ``check=True``, write nothing — just report whether the committed
    plugin skills already match (exit non-zero if not). That mode is what the
    drift-guard test and CI use.
    """
    templates = iter_skill_templates()
    if not templates:
        print(f"error: no skill templates found under {TEMPLATES_DIR}", file=sys.stderr)
        return 2

    stale: list[str] = []
    for skill_dir in templates:
        src = skill_dir / "SKILL.md"
        dest = PLUGIN_SKILLS_DIR / skill_dir.name / "SKILL.md"
        want = src.read_text(encoding="utf-8")
        have = dest.read_text(encoding="utf-8") if dest.is_file() else None
        if want == have:
            continue
        stale.append(skill_dir.name)
        if not check:
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(want, encoding="utf-8")

    # Drop plugin skills whose template was removed (keep the mirror exact).
    template_names = {d.name for d in templates}
    if PLUGIN_SKILLS_DIR.is_dir():
        for existing in PLUGIN_SKILLS_DIR.iterdir():
            if existing.is_dir() and existing.name not in template_names:
                stale.append(f"{existing.name} (orphaned)")
                if not check:
                    shutil.rmtree(existing)

    if check:
        if stale:
            print(
                "plugin skills are stale — run "
                "`uv run python scripts/build_claude_plugin.py`:\n  "
                + "\n  ".join(stale),
                file=sys.stderr,
            )
            return 1
        print(f"plugin skills up to date ({len(templates)} skills)")
        return 0

    if stale:
        print(f"regenerated {len(stale)} plugin skill(s): " + ", ".join(stale))
    else:
        print(f"plugin skills already up to date ({len(templates)} skills)")
    return 0


def main() -> int:
    check = "--check" in sys.argv[1:]
    return build(check=check)


if __name__ == "__main__":
    raise SystemExit(main())

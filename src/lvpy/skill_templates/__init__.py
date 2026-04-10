"""Packaged user-facing skill templates.

lvpy ships these as package data so `lvpy init --skills` can install
them into a downstream user's project via importlib.resources. Each
skill lives under `<name>/SKILL.md` directly under this package — they
are the canonical source of truth for both the Claude Code install
path AND the Copilot install path. (Copilot's install builds a single
file dynamically from these templates; see
``project_store._build_copilot_section``.)

The in-repo `.claude/skills/` copies of these skills are byte-identical
mirrors kept in sync by `scripts/sync_skills.sh`. The two maintainer-
only skills (`judge-output`, `trace-bug`) live ONLY in `.claude/skills/`
and are not packaged here.
"""

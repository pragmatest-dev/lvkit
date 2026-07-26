"""Packaged user-facing skill templates.

lvkit ships these as package data so `lvkit init --skills` can install
them into a downstream user's project via importlib.resources. Each
skill lives under `<name>/SKILL.md` directly under this package — they
are the canonical source of truth for the Claude Code, Copilot, and
Codex install paths. Copilot prompts and Codex skills are built
dynamically from these templates; see ``lvkit.project_store``.

The in-repo `.claude/skills/` copies of these skills are byte-identical
mirrors kept in sync by `scripts/sync_skills.sh`. The two maintainer-
only skills (`judge-output`, `trace-bug`) live ONLY in `.claude/skills/`
and are not packaged here.
"""

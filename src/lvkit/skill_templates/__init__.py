"""Packaged user-facing skill templates.

lvkit ships these as package data so `lvkit setup` can install
them into a downstream user's project via importlib.resources. Each
skill lives under `<name>/SKILL.md` directly under this package — they
are the canonical source of truth for the Claude Code, Copilot, and
Codex install paths. Copilot prompts and Codex skills are built
dynamically from these templates; see ``lvkit.project_store``.

These user skills are NOT copied into the repo's own `.claude/skills/` —
that tree is dev-only (`lvkit-eval`, `judge-output`, `trace-bug`,
`lvkit-resolve-primitive`/`-vilib`, …). We exercise the user skills before a
release by installing them with `lvkit setup` into a scratch project, not by
running them against lvkit itself.
"""

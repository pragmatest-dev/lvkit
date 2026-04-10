"""Packaged Claude Code / Copilot skill templates.

vipy ships these as package data so `vipy init --skills` can install
them into a downstream user's project via importlib.resources. The
markdown files under `claude/<name>/SKILL.md` are the source of truth;
the in-repo `.claude/skills/` copies are byte-identical mirrors kept
in sync by `scripts/sync_skills.sh`.
"""

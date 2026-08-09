"""Guards for the in-repo Claude Code plugin (``plugins/lvkit/``).

This repo holds the plugin CONTENT; the marketplace CATALOG that lists it lives
in the shared umbrella repo (``pragmatest-dev/plugins``, marketplace name
``pragmatest``), which references this ``plugins/lvkit/`` cross-repo. So the
guards here are about the plugin itself, not a catalog:

1. ``plugin.json`` is valid JSON with the fields Claude Code requires and the
   MCP server wired to the same entry point the CLI installs.
2. ``plugins/lvkit/skills/`` never drifts from ``src/lvkit/skill_templates/`` —
   the plugin skills are GENERATED from those templates (single source of
   truth), so a template edit without a regenerate is a bug, caught here.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PLUGIN_DIR = REPO_ROOT / "plugins" / "lvkit"
PLUGIN_MANIFEST = PLUGIN_DIR / ".claude-plugin" / "plugin.json"
TEMPLATES_DIR = REPO_ROOT / "src" / "lvkit" / "skill_templates"


def _load_build_module():
    """Import ``scripts/build_claude_plugin.py`` (not on ``sys.path``)."""
    path = REPO_ROOT / "scripts" / "build_claude_plugin.py"
    spec = importlib.util.spec_from_file_location("build_claude_plugin", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules["build_claude_plugin"] = mod
    spec.loader.exec_module(mod)
    return mod


def test_plugin_manifest_wires_the_mcp_server():
    data = json.loads(PLUGIN_MANIFEST.read_text(encoding="utf-8"))
    assert data["name"] == "lvkit"

    server = data["mcpServers"]["lvkit"]
    # Same launch the docs + CLI console-script use: uvx --from lvkit lvkit-mcp.
    assert server["command"] == "uvx"
    assert server["args"] == ["--from", "lvkit", "lvkit-mcp"]


def test_plugin_skills_match_templates_no_drift():
    """The committed plugin skills are byte-identical to the templates.

    Fails if a template changed without re-running
    ``scripts/build_claude_plugin.py`` — the ``--check`` mode returns non-zero.
    """
    build = _load_build_module()
    assert build.build(check=True) == 0, (
        "plugin skills drifted from src/lvkit/skill_templates — run "
        "`uv run python scripts/build_claude_plugin.py`"
    )

    # And every template is actually represented (not an empty pass).
    template_names = {
        d.name for d in TEMPLATES_DIR.iterdir()
        if d.is_dir() and (d / "SKILL.md").is_file()
    }
    plugin_names = {
        d.name for d in (PLUGIN_DIR / "skills").iterdir() if d.is_dir()
    }
    assert plugin_names == template_names
    assert len(plugin_names) == 5

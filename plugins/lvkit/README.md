# lvkit — Claude Code plugin

Installs the [lvkit](https://github.com/pragmatest-dev/lvkit) MCP server **and**
its workflow skills into Claude Code in one step, so an agent can read, diff, and
convert LabVIEW VIs without a LabVIEW license.

```
/plugin marketplace add pragmatest-dev/lvkit
/plugin install lvkit@pragmatest
```

The MCP server is launched via `uvx --from lvkit lvkit-mcp`, so [uv](https://docs.astral.sh/uv/)
must be on the machine; uvx fetches lvkit from PyPI on first use. See
[docs/reference/install.md](../../docs/reference/install.md) for every
distribution channel and which one fits your editor.

## Layout

- `.claude-plugin/plugin.json` — plugin manifest; declares the `lvkit` MCP server.
- `skills/` — the five lvkit workflow skills.

## Do not hand-edit `skills/`

`skills/**/SKILL.md` is **generated** from the single source of truth,
`src/lvkit/skill_templates/` (the same templates `lvkit setup claude` installs).
Edit the template, then regenerate:

```bash
uv run python scripts/build_claude_plugin.py
```

`tests/test_claude_plugin.py` fails if the committed plugin skills drift from the
templates.

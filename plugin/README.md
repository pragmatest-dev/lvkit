# lvkit — Claude Code plugin (source skeleton)

This directory is the **text skeleton** of the lvkit Claude Code plugin. It is NOT the
installable plugin on its own — the release pipeline (`.github/workflows/publish-bundles.yml`)
assembles the per-OS installable archive from this skeleton plus artifacts it builds.

## What ships in the release archive (`lvkit-plugin-<target>.zip`)

CI, per OS/arch target, zips these at the archive **root**:

```
.claude-plugin/plugin.json     # from here — CI stamps `name` = lvkit-<target> to match the
                               #   marketplace entry, and `version` = the bundled lvkit version
.mcp.json                      # from here — on win32 CI rewrites `command` to end in `lvkit.exe`
bin/lvkit/                      # NOT in git — the onedir PyInstaller bundle (the same
                               #   Windows-signed build packaged into the VSIX + standalone zip)
skills/                         # NOT in git — copied at build time from src/lvkit/skill_templates/
                               #   (the 7 user-facing skills), so they are version-paired to the
                               #   bundled binary
```

The MCP server launches the bundled binary (`.mcp.json` → `${CLAUDE_PLUGIN_ROOT}/bin/lvkit/lvkit
mcp`), so a plugin install needs **no Python, uv, or pip**. The bundled skills prefer the MCP
tools (which the plugin provides), so no `lvkit` CLI on PATH is required.

## What `.claude-plugin/marketplace.json` (repo root) points at

Four `archive` entries (`lvkit-<target>`), each pointing at the release asset above. CI keeps
their `url`/`sha256` in sync with each published release. Install:

```
claude plugin marketplace add pragmatest-dev/lvkit
claude plugin install lvkit-<os-arch>@pragmatest-lvkit
```

## Local dogfood (before a real release)

Point the marketplace entry's `source` at a **pre-release** tag's asset, or use a local
marketplace dir with a relative-path source after running the CI copy steps (bin/ + skills/)
by hand.

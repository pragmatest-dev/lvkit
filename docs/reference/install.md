# Installing the MCP server

lvkit's AI-agent surface is a **local, stdio MCP server** — the agent's runtime
launches `lvkit mcp` on your machine and it reads `.vi` files off your disk.
Everything on this page is about getting that server in front of an agent. Pick
the row for your editor; you do not need the others.

## Which path is for you

| Your agent | Install | Why |
|------------|---------|-----|
| **VS Code** (Copilot agent mode, etc.) | The [lvkit VS Code extension](#vs-code-extension) from the Marketplace | Bundles a signed standalone binary (no Python/uv) and auto-registers the MCP server. Zero config. |
| **Claude Code** | The [lvkit plugin](#claude-code-plugin) | One `/plugin install` bundles the MCP server **and** the workflow skills. |
| **Codex** (CLI / IDE / ChatGPT desktop) | [uvx + `.codex/config.toml`](#codex) | Codex has no plugin marketplace; wire the server in by hand. |
| **Any other MCP client** (Cursor, …) | [uvx from PyPI](#any-mcp-client-uvx) | The universal Python path. |
| **Pre-release / a specific branch** | [uvx from git](#pre-release-from-git) | Bleeding edge; not the stable path. |

> **Hosted directories don't apply.** The claude.ai connector directory and the
> ChatGPT Apps directory both host *remote* MCP servers. lvkit reads your local
> VI repo, so there is nothing for a hosted server to see — every path below is
> local by necessity.

## VS Code extension

Install **lvkit** from the VS Code Marketplace. As of extension `v0.1.12` it
auto-registers the bundled `lvkit mcp` server (VS Code ≥ 1.101), so agent mode
gets the lvkit tools with **no `mcp.json` and no Python** — the extension ships a
signed standalone binary. This is the recommended path for LabVIEW developers.

To point the extension at your own lvkit instead of the bundle, set
`lvkit.path` in Settings.

## Claude Code plugin

The lvkit plugin bundles the MCP server and the five workflow skills
(`lvkit-convert`, `lvkit-describe`, `lvkit-resolve-primitive`,
`lvkit-resolve-vilib`, `lvkit-idiomatic`) in one installable unit:

```
/plugin marketplace add pragmatest-dev/lvkit
/plugin install lvkit@pragmatest
```

`@pragmatest` is the marketplace name (the `name` field in the repo's
`.claude-plugin/marketplace.json`), not the repo. The plugin launches the server
with `uvx --from lvkit lvkit-mcp`, so [uv](https://docs.astral.sh/uv/) must be on
the machine — uvx fetches lvkit from PyPI on first use.

This replaces the two manual steps you would otherwise run for Claude Code:
`lvkit setup claude` (skills) plus a separate `claude mcp add` (server).

## Codex

Codex reads `.codex/config.toml` (shared by the Codex CLI, the Codex IDE
extension, and the ChatGPT desktop app):

```toml
[mcp_servers.lvkit]
command = "uvx"
args = ["--from", "lvkit", "lvkit-mcp"]
```

Install the paired workflow skills with `lvkit setup codex`. (`setup` installs
skills only — it does not create or modify `.codex/config.toml`.)

## Any MCP client (uvx)

If lvkit is not already installed, `uvx` fetches and runs it on demand — the
universal path for any MCP client that speaks the standard config shape:

```json
{ "mcpServers": { "lvkit": { "command": "uvx", "args": ["--from", "lvkit", "lvkit-mcp"] } } }
```

If lvkit *is* installed (`uv tool install lvkit`, `pip install lvkit`, or the
bundled binary), point `command` straight at the executable instead:

```json
{ "mcpServers": { "lvkit": { "command": "/abs/path/to/lvkit", "args": ["mcp"] } } }
```

VS Code's own `.vscode/mcp.json` uses a `"servers"` key with `"type": "stdio"`
instead of the `"mcpServers"` shape above.

## Pre-release (from git)

To run the server from an unreleased branch — before its features reach a PyPI
release — point `uvx` at the git ref:

```json
{ "mcpServers": { "lvkit": { "command": "uvx",
  "args": ["--from", "git+https://github.com/pragmatest-dev/lvkit@BRANCH", "lvkit-mcp"] } } }
```

This is a development channel, not the stable path — pin a real release for
day-to-day use.

## Version note

The uvx and plugin paths run the current **PyPI** release. The project-index
tools (`index`, `query`, `get_callers`, `blast_radius`, `visualize_project`)
reach those paths only in a release that carries them — if a tool is missing,
compare `lvkit --version` against the [mcp tool list](mcp.md#tools). The VS Code
extension's bundled binary tracks its own build and may be ahead of PyPI.

## See also

- [mcp](mcp.md) — the server's tools and a worked project-understanding demo.
- [setup](setup.md) — install the workflow skills without the MCP server.

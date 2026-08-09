# Installing the MCP server

lvkit's AI-agent surface is a **local, stdio MCP server** — the agent's runtime
launches lvkit on your machine and it reads `.vi` files off your disk. This page
gets that server in front of your agent.

The recommended path is [`uvx`](https://docs.astral.sh/uv/guides/tools/): with
[uv](https://docs.astral.sh/uv/) installed, **no separate lvkit install is
needed** — uvx fetches lvkit from PyPI and runs it on demand. Don't have uv? See
[Without uv](#without-uv).

## Install uv (recommended)

**macOS / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or via a package manager: `pip install uv`, `brew install uv`, or
`winget install --id=astral-sh.uv -e`. uv is a single self-contained binary and
can manage its own Python, so this is all you need — see the
[uv install docs](https://docs.astral.sh/uv/getting-started/installation/).

## Configure your agent

Every client launches the same server: `uvx --from lvkit lvkit-mcp`. Pick yours.

### Claude Code

```bash
claude mcp add lvkit -- uvx --from lvkit lvkit-mcp
```

### Claude Desktop / Cursor / any client using the standard shape

Add to the client's MCP config (Claude Desktop: `claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "lvkit": {
      "command": "uvx",
      "args": ["--from", "lvkit", "lvkit-mcp"]
    }
  }
}
```

### VS Code

VS Code uses `.vscode/mcp.json` with a `"servers"` key and `"type": "stdio"`:

```json
{
  "servers": {
    "lvkit": {
      "type": "stdio",
      "command": "uvx",
      "args": ["--from", "lvkit", "lvkit-mcp"]
    }
  }
}
```

Or skip the config entirely: the [lvkit VS Code extension](#vs-code-extension)
bundles the server and registers it for you.

### Codex

Codex reads `.codex/config.toml` (shared by the Codex CLI, the Codex IDE
extension, and the ChatGPT desktop app):

```toml
[mcp_servers.lvkit]
command = "uvx"
args = ["--from", "lvkit", "lvkit-mcp"]
```

## Without uv

uvx is the convenience, not a requirement. Two fallbacks:

**Install lvkit with pip** — then point the client's `command` straight at the
installed console script (no uvx):

```bash
pip install lvkit      # or: uv tool install lvkit / pipx install lvkit
```

```json
{
  "mcpServers": {
    "lvkit": {
      "command": "lvkit",
      "args": ["mcp"]
    }
  }
}
```

(`lvkit mcp` and the `lvkit-mcp` console script are the same server.)

**No Python at all** — the VS Code extension ships a signed standalone binary, so
LabVIEW developers on Windows need neither Python nor uv. See below.

## VS Code extension

Install **lvkit** from the VS Code Marketplace. The extension bundles a signed
standalone binary and auto-registers the `lvkit mcp` server (VS Code ≥ 1.101), so
agent mode gets the lvkit tools with **no `mcp.json`, no Python, and no uv**. This
is the recommended path for LabVIEW developers.

To point the extension at your own lvkit instead of the bundle, set `lvkit.path`
in Settings.

## Verify it works

```bash
lvkit mcp --selftest   # initializes the server, lists tools, exits non-zero on failure
```

With uvx: `uvx --from lvkit lvkit-mcp --selftest`.

## Notes

- The claude.ai connector directory and the ChatGPT Apps directory host *remote*
  MCP servers. lvkit reads your local VI repo, so there is nothing for a hosted
  server to see — every path here is local by necessity.
- The workflow skills (`lvkit-convert`, `lvkit-describe`, `lvkit-resolve-primitive`,
  `lvkit-resolve-vilib`, `lvkit-idiomatic`) are separate from the server; install
  them with [`lvkit setup`](setup.md).

## See also

- [mcp](mcp.md) — the server's tools and a worked project-understanding demo.
- [setup](setup.md) — install the workflow skills.

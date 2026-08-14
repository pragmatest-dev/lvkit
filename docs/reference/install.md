# Installing the MCP server

lvkit's AI-agent surface is a **local, stdio MCP server** — the agent's runtime
launches lvkit on your machine and it reads `.vi` files off your disk. This
page gets that server in front of your agent, from a one-click install for a
non-developer to a hand-written config file.

## One-click for your app

No terminal, no Python. Pick your app.

### Claude Desktop

Download the `lvkit-<target>.mcpb` bundle for your machine (`darwin-arm64`,
`darwin-x64`, or `win32-x64`) from the
[GitHub Release](https://github.com/pragmatest-dev/lvkit/releases/latest) or
pragmatest.com, then either double-click it, drag it into Claude Desktop, or
use Settings → Extensions → Install Extension… In the extension's settings,
pick the folder containing your `.vi` files — lvkit reads VIs from there so
you don't have to type full paths.

macOS and Windows only (Claude Desktop has no Linux build). The macOS binary
is unsigned in v1 — if Gatekeeper blocks it, clear the quarantine flag on the
downloaded file:

```bash
xattr -dr com.apple.quarantine lvkit-darwin-*.mcpb
```

### VS Code

Install **LVKit** from the VS Code Marketplace. The extension bundles a
signed standalone binary and auto-registers the `lvkit mcp` server (VS Code
≥ 1.101), so agent mode gets the lvkit tools with no `mcp.json`, no Python,
and no uv. See [VS Code extension](vscode-extension.md).

### Claude Code (plugin)

```bash
claude plugin marketplace add pragmatest-dev/lvkit
claude plugin install lvkit-<os-arch>@pragmatest-lvkit
```

`<os-arch>` is one of `darwin-arm64`, `darwin-x64`, `win32-x64`, `linux-x64` —
pick the one matching your machine. The plugin bundles the `lvkit` binary and
lvkit's five workflow skills (the same ones [`setup`](setup.md) installs), so
it needs no Python, uv, or pip, and no `lvkit` on `PATH`.

## Developer: configure your agent's config file

Every client below points a `command` at lvkit and passes `mcp` as the
argument (some clients spell that as `args: ["mcp"]`, others fold it into
the same invocation). That `command` is either of two equally valid things:

- **`uvx`**, which fetches lvkit from PyPI and runs it on demand — nothing to
  install first if you already have [uv](https://docs.astral.sh/uv/).
- **The downloaded `lvkit` binary**, as an absolute path — the standalone
  `lvkit-mcp-<version>-<target>.zip` from the
  [GitHub Release](https://github.com/pragmatest-dev/lvkit/releases/latest),
  unzipped to `lvkit/lvkit` (`lvkit\lvkit.exe` on Windows). No Python, uv, or
  pip needed; useful for a client or machine that avoids package managers.

Pick whichever you already have. Both are shown for each client below.

### Claude Code

```bash
# uvx
claude mcp add lvkit -- uvx --from lvkit lvkit-mcp

# downloaded binary
claude mcp add lvkit -- /abs/path/to/lvkit mcp
```

### VS Code

`.vscode/mcp.json`:

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

```json
{
  "servers": {
    "lvkit": {
      "type": "stdio",
      "command": "/abs/path/to/lvkit",
      "args": ["mcp"]
    }
  }
}
```

Or skip the config file entirely — the [VS Code extension](vscode-extension.md)
registers the server for you.

### GitHub Copilot CLI

`~/.copilot/mcp-config.json`:

```json
{
  "mcpServers": {
    "lvkit": {
      "type": "local",
      "command": "uvx",
      "args": ["--from", "lvkit", "lvkit-mcp"]
    }
  }
}
```

```json
{
  "mcpServers": {
    "lvkit": {
      "type": "local",
      "command": "/abs/path/to/lvkit",
      "args": ["mcp"]
    }
  }
}
```

Copilot running inside VS Code instead uses the [VS Code extension](vscode-extension.md)'s
zero-config registration — this file is for the standalone Copilot CLI.

### Codex

`.codex/config.toml` (shared by the Codex CLI, the Codex IDE extension, and
the ChatGPT desktop app):

```toml
[mcp_servers.lvkit]
command = "uvx"
args = ["--from", "lvkit", "lvkit-mcp"]
```

```toml
[mcp_servers.lvkit]
command = "/abs/path/to/lvkit"
args = ["mcp"]
```

Codex and Copilot CLI have no one-click bundle — the config file above is
their install path.

### Cursor / any client using the standard `mcpServers` shape

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

## Alternatives

Prefer a regular Python install over `uvx` or the standalone binary?

```bash
pip install lvkit      # or: uv tool install lvkit / pipx install lvkit
```

then point any client's `command` at the installed `lvkit` console script
with `args: ["mcp"]` — the same shape as the downloaded-binary examples
above, just without the absolute path. (`lvkit mcp` and the `lvkit-mcp`
console script are the same server.)

## Verify it works

```bash
lvkit mcp --selftest   # initializes the server, lists tools, exits non-zero on failure
```

With uvx: `uvx --from lvkit lvkit mcp --selftest` (the bare `lvkit-mcp`
script only runs the stdio server and ignores `--selftest`).

## Notes

- The claude.ai connector directory and the ChatGPT Apps directory host
  *remote* MCP servers. lvkit reads your local VI repo, so there is nothing
  for a hosted server to see — every path here is local by necessity.
- The workflow skills (`lvkit`, `lvkit-describe`, `lvkit-query`, `lvkit-convert`,
  `lvkit-document`, `lvkit-review`, `lvkit-resolve`) ship inside the Claude Code
  plugin bundle above; outside the plugin, install them separately with
  [`lvkit setup`](setup.md).

## See also

- [mcp](mcp.md) — the server's tools and a worked project-understanding demo.
- [vscode-extension](vscode-extension.md) — the VS Code extension's view/diff/MCP surface in full.
- [setup](setup.md) — install the workflow skills outside the Claude Code plugin.

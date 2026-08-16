# Set up the MCP server

Give your AI agent live access to your VIs. lvkit's MCP (Model Context Protocol) server lets an agent index a VI repo once and then query it like code — project-wide questions answered from a persisted graph, plus deep single-VI inspection on demand — instead of guessing at what a `.vi` binary contains.

Using Claude Desktop, VS Code, or the Claude Code plugin? Skip straight to the
one-click bundles in [reference/install](../reference/install.md#one-click-for-your-app)
— no config file, no uv, no Python. The rest of this page is the manual,
config-file path for Codex, Copilot CLI, Cursor, or a hand-rolled Claude Code
setup.

## Prerequisites

- [uv](https://docs.astral.sh/uv/) installed (recommended — see [Without uv](#without-uv) if you'd rather not). uv lets every client below run lvkit with **no separate install**: `uvx --from lvkit lvkit-mcp` fetches it from PyPI on demand.
- One of the AI clients below: Claude Code, VS Code, Codex, or Cursor.
- The server reads `.vi` files off your local disk — it's a **local, stdio** server, not a hosted one. There's nothing to sign up for.

## Install uv

**macOS / Linux:**

```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**Windows:**

```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

Or via a package manager: `pip install uv`, `brew install uv`, or `winget install --id=astral-sh.uv -e`.

## Configure your client

Every client launches the same server: `uvx --from lvkit lvkit-mcp`. Pick yours.

### Claude Code

```bash
claude mcp add lvkit -- uvx --from lvkit lvkit-mcp
```

### Cursor / any client using the standard `mcpServers` shape

Add to the client's MCP config:

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

Or skip the config entirely: the [lvkit VS Code extension](../reference/vscode-extension.md) (from the Marketplace) bundles a signed standalone binary and auto-registers the server (VS Code ≥ 1.101) — no `mcp.json`, no Python, no uv.

### Codex

Codex reads `.codex/config.toml` (shared by the Codex CLI, the Codex IDE extension, and the ChatGPT desktop app):

```toml
[mcp_servers.lvkit]
command = "uvx"
args = ["--from", "lvkit", "lvkit-mcp"]
```

## Without uv

uvx is the convenience, not a requirement.

**Install lvkit with pip**, then point the client's `command` straight at the installed console script:

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

**No Python at all** — install the [lvkit VS Code extension](../reference/vscode-extension.md), or the Claude Desktop / Claude Code one-click bundles in [reference/install](../reference/install.md#one-click-for-your-app). Each ships a signed standalone binary, so a LabVIEW developer needs neither Python nor uv.

## Verify it works

```bash
lvkit mcp --selftest
```

This initializes the server and lists its tools without staying resident, printing `MCP selftest OK: server initialized, N tools listed.` and exiting `0`. A broken install (e.g. an incompatible `mcp` package version) prints `MCP selftest FAILED: …` to stderr and exits non-zero instead of silently registering zero tools with your client.

With uvx, no separate install needed — run the `lvkit` CLI through uvx so it parses the flag (the bare `lvkit-mcp` script is only the stdio server and ignores `--selftest`):

```bash
uvx --from lvkit lvkit mcp --selftest
```

After configuring your client, restart it to pick up the new server, then ask it to list its tools (or open the client's MCP panel) and confirm the lvkit tools appear.

## Ask your first question

You don't script these calls yourself — you ask your agent the question in plain language and it picks the right tool. The calls shown below are what it invokes under the hood. Point it at a real VI or repo:

- **Inspect one VI**, deep and on demand — *"what does this VI do?"* → the agent calls `describe(vi_path="…/Some VI.vi")` for the signature, SubVI calls, and control flow. No indexing needed; it loads the one VI live.
- **Ask a project-wide question** — *"what does this project call its error indicators?"* → `query(sql="SELECT name, COUNT(*) AS n FROM terminal WHERE type_descriptor='Error' AND direction='output' GROUP BY name ORDER BY n DESC", project="…")` answers as a small histogram, in one call. (`query` builds/refreshes the project index on first use.)
- **Assess a change** — *"what breaks if I change fail.vi?"* → `query(sql="WITH RECURSIVE deps(p) AS (SELECT vi_path FROM node WHERE callee_path='…/fail.vi' UNION SELECT n.vi_path FROM node n JOIN deps ON n.callee_path=deps.p) SELECT * FROM deps", project="…")`, or `vi.impact_score` for just the count. There's no dedicated MCP tool for this — the CLI's `lvkit blast-radius` command answers the same question directly.

The full 6-tool surface — project index and deep single-VI inspection, all
understanding-only — plus the worked JKI VI Tester (487 VIs) walkthrough, is
in [reference/mcp](../reference/mcp.md).

## Troubleshooting

- **Client shows zero lvkit tools after restart.** Run `lvkit mcp --selftest` (or `uvx --from lvkit lvkit mcp --selftest`) directly — a non-zero exit means the server itself is broken (often an incompatible `mcp` package version), not a client config problem. Fix that first, then re-check the client config against the JSON/TOML above.
- **`uvx` not found.** uv isn't installed or isn't on `PATH` — see [Install uv](#install-uv), or use the [no-uv fallback](#without-uv).
- **Client can't run a subprocess (locked-down environment).** Use the [no-uv fallback](#without-uv): `pip install lvkit`, then point `command` at the `lvkit` console script directly.
- **No Python and no uv available.** Install the [lvkit VS Code extension](../reference/vscode-extension.md) — it bundles a signed standalone binary and registers itself with no config file at all.
- **Results look stale after editing VIs.** The project index is content-hash-keyed but only refreshed on read; `index`/`query` refresh it automatically before each call unless the caller skips that (see [reference/mcp](../reference/mcp.md#notes)).

## See also

- [reference/mcp](../reference/mcp.md) — the server's full tool list (project index, deep single-VI inspection, resolution-gap triage) and notes on how the index is stored and refreshed.
- [reference/install](../reference/install.md) — every install path, one-click bundles first, then this same config-file content as a reference page.
- [reference/vscode-extension](../reference/vscode-extension.md) — the VS Code extension's view/diff/MCP surface and its `lvkit.path` setting.
- [reference/setup](../reference/setup.md) — install AI-agent editor skills (separate from the MCP server) and create the project-local `.lvkit/` resolution store.
- [reference/query](../reference/query.md) — the SQL surface behind the `query` tool, with the full view/column list.

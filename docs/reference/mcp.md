# mcp

Start the lvkit MCP (Model Context Protocol) server over stdio, so an AI agent
can **index a VI repo once and then interrogate it like code** — project-wide
questions answered from a persisted graph, plus deep single-VI inspection on
demand.

## Synopsis

```bash
lvkit mcp
lvkit mcp --selftest   # initialize + list tools, exit non-zero on failure
```

`mcp` takes no arguments beyond `-h` / `--selftest`. It runs the same server as
the `lvkit-mcp` console script installed with the `lvkit` package.

## Setup

The server is **stdio** — your agent's runtime launches it and speaks to it; you
don't run `lvkit mcp` by hand. With [uv](https://docs.astral.sh/uv/) installed,
no separate lvkit install is needed:

```json
{ "mcpServers": { "lvkit": { "command": "uvx", "args": ["--from", "lvkit", "lvkit-mcp"] } } }
```

Or, for Claude Code, `claude mcp add lvkit -- uvx --from lvkit lvkit-mcp`.

Also available one-click, with no config file to write: the Claude Desktop
`.mcpb`, the Claude Code plugin, and the zero-config [VS Code extension](vscode-extension.md).
For every install path — the one-click bundles, and hand-written config for
Claude Code, VS Code, Codex, Copilot CLI, and Cursor — see
**[Installing the MCP server](install.md)**.

## Tools

The server exposes exactly 6 tools, all **understanding-only** — none of them
writes a file. Artifact generation (a Python package, an HTML docs site, a
pyvis dependency graph, a diff, an SVG render) is CLI-only
([generate](generate.md), [docs](docs.md), [visualize](visualize.md),
[diff](diff.md), [render](render.md)): an agent converts a VI by understanding
it with `read_vi`/`query` and writing idiomatic Python itself —
`lvkit generate`'s deterministic AST pipeline is a reference/oracle to verify
against, not something the agent calls through MCP.

### Project index — whole-repo questions in one call

`index` builds a persisted, **path-keyed** facts index for the enclosing project
(so same-named VIs like `setUp.vi` ×17 never collide); the rest read it in sub-ms.
All take a `project` path (any file/dir inside the repo).

| Tool | Description |
|------|-------------|
| `index` | Build/refresh the index for a repo. Returns VI count, collisions handled, and ms. |
| `query` | Run one read-only SQL `SELECT`/`WITH` over the index and get back just the answer (a `GROUP BY` histogram, not a row dump). Queries the curated views `vi`, `terminal`, `constant`, `node`, `type_use`, `class_fact`, `lvproj`. |
| `query_schema` | List the views and their columns, so a query uses real column names. |

Reads of terminals, constants, symbols, and type-uses go through `query` (e.g.
`SELECT * FROM terminal WHERE …`). `node` is one row per block-diagram node (a
primitive, SubVI call, structure, constant, …) — it's grep for VI code
(`kind`, `prim_id`, `qualified_name`, structural `parent_uid`/`frame`), not
wiring; find a pattern there, then `read_vi` the matching VI for its actual
dataflow.

**Call graph and change impact are `query` over `node`, not a separate tool.**
Direct callers of a VI: `SELECT DISTINCT vi_path FROM node WHERE
callee_path='<abs path>'`. Direct callees: `SELECT callee_path FROM node WHERE
vi_path='<X>' AND kind='vi'`. Transitive blast radius: a `WITH RECURSIVE` over
`callee_path`. `vi.callers_count` and `vi.impact_score` are precomputed columns
for the counts, so most impact questions don't need the CTE at all. The CLI's
[`callers`/`callees`/`blast-radius`](callers.md) commands answer the same
questions as one-shot commands instead of SQL — there's no MCP tool twin for
them; run the SQL above from the agent, or shell out to the CLI command.

The same SQL is available on the CLI as [`lvkit query`](query.md).

### Deep single-VI — load one VI live, on demand

Each takes a `vi_path` (a real `.vi`) and loads it live (XML already cached) — no
`load`/`clear` session state.

| Tool | Description |
|------|-------------|
| `describe` | Human-readable purpose, signature, SubVI calls, control flow. |
| `read_vi` | The VI as the canonical **netlist IR** — `{vi, inputs, outputs, components, body}`, faithful type labels, a `kind`-tagged instance/scope body. The structured counterpart to `describe`'s prose. |

### Resolution gaps — triage before converting

| Tool | Description |
|------|-------------|
| `unresolved` | Every unknown primitive / unmapped vi.lib VI under a target (a VI, library, class, or directory), collected in one pass instead of hitting `PrimitiveResolutionNeeded`/`VILibResolutionNeeded` one at a time. Returns a list of `{kind, identifier, name, count, vi_names}` (`kind` is `unknown_primitive` / `unmapped_vilib` / `terminal_mapping`). See [SubVI & vi.lib resolution](subvi-resolution.md). |

## Example — the project-understanding demo

Against JKI VI Tester (487 VIs):

1. `index(project="…/source")` → `{vis: 487, collisions: 65, ms: …}`.
2. `query(project, sql="SELECT name, COUNT(*) AS n FROM terminal WHERE
   type_descriptor='Error' AND direction='output' GROUP BY name ORDER BY n DESC")` →
   *"what does this project call its error indicators?"* as a small histogram, in
   one call. (`query` builds/refreshes the index on first use, so step 1 is
   optional.)
3. `query(project, sql="SELECT DISTINCT vi_path FROM node WHERE
   callee_path='…/fail.vi'")` → *"does this VI have callers?"*
4. `query(project, sql="SELECT name, impact_score FROM vi WHERE
   path='…/fail.vi'")` → *"what breaks if I change it?"* — the precomputed
   transitive-dependent count, no CTE needed.

## Notes

- The index is **project-scoped** (keyed by project root) — an agent can work
  across several repos in one session without a shared global graph to clear.
- The index is stored under `~/.lvkit/cache/index/projects/<slug>/index.db`
  (SQLite/WAL), rebuilt cheaply from the content-hash-keyed extraction cache.
- Before each call that knows a path, the server re-resolves the project's
  `.lvkit/` store (see [setup](setup.md)), so saved primitive/vi.lib mappings apply.
- The server runs on **both** `mcp` 1.x and 2.0. mcp 2.0 renamed the decorator
  server `FastMCP` → `MCPServer` (moving it from `mcp.server.fastmcp` to
  `mcp.server.mcpserver`) with an otherwise identical API; the server imports
  whichever the installed SDK ships. `--selftest` and CI guard against a silent
  API break on either major.

## See also

- [describe](describe.md) — the equivalent one-shot CLI query for a single VI.
- [generate](generate.md) — the equivalent one-shot CLI conversion.
- [setup](setup.md) — install AI-agent skills that pair with this server.
- [vscode-extension](vscode-extension.md) — the VS Code extension that auto-registers this server with no config file.

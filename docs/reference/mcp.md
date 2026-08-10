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

For every client (Claude Code, Claude Desktop, VS Code, Codex, Cursor), the
[no-uv fallbacks](install.md#without-uv), and the zero-config VS Code extension,
see **[Installing the MCP server](install.md)**.

## Tools

The server exposes tools in three groups.

### Project index — whole-repo questions in one call

`index` builds a persisted, **path-keyed** facts index for the enclosing project
(so same-named VIs like `setUp.vi` ×17 never collide); the rest read it in sub-ms.
All take a `project` path (any file/dir inside the repo).

| Tool | Description |
|------|-------------|
| `index` | Build/refresh the index for a repo. Returns VI count, collisions handled, and ms. |
| `query` | Run one read-only SQL `SELECT`/`WITH` over the index and get back just the answer (a `GROUP BY` histogram, not a row dump). Queries the curated views `vi`, `terminal`, `constant`, `call`, `type_use`, `class_fact`. Replaces the older per-question read tools. |
| `query_schema` | List the views and their columns, so a query uses real column names. |
| `get_callers` / `get_callees` | Pure call edges to/from a VI (ownership excluded). |
| `blast_radius` | Transitive dependents of a VI — "what breaks if I change this?" |
| `visualize_project` | A self-contained Mermaid call graph or class tree, with optional blast-radius highlight. |

Reads of terminals, constants, symbols, and type-uses go through `query`
(e.g. `SELECT * FROM terminal WHERE …`); reachability stays as the typed
`get_callers` / `get_callees` / `blast_radius` ops. The same SQL is available on
the CLI as [`lvkit query`](query.md).

### Deep single-VI — load one VI live, on demand

Each takes a `vi_path` (a real `.vi`) and loads it live (XML already cached) — no
`load`/`clear` session state.

| Tool | Description |
|------|-------------|
| `describe` | Human-readable purpose, signature, SubVI calls, control flow. |
| `get_operations` | Execution-ordered operations with nested structures. |
| `get_dataflow` | Wire connections, optionally filtered to one operation. |
| `get_structure` | Detail on one case/loop/sequence structure. |
| `get_constants` | Every constant's name, type, value. |
| `get_context` | The VI as the canonical **netlist IR** — `{vi, inputs, outputs, components, body}`, faithful type labels, a `kind`-tagged instance/scope body. The structured counterpart to `describe`'s prose. |
| `generate_ast_code` | Python for one VI via the deterministic AST pipeline. |

### Stateless generators

| Tool | Description |
|------|-------------|
| `generate_documents` | Generate the static HTML documentation site — same as [`docs`](docs.md). |
| `generate_python` | Generate a Python package — same as [`generate`](generate.md), with a review workflow. |

## Example — the project-understanding demo

Against JKI VI Tester (487 VIs):

1. `index(project="…/source")` → `{vis: 487, collisions: 65, ms: …}`.
2. `query(project, sql="SELECT name, COUNT(*) AS n FROM terminal WHERE
   is_error_cluster=1 AND direction='output' GROUP BY name ORDER BY n DESC")` →
   *"what does this project call its error indicators?"* as a small histogram, in
   one call. (`query` builds/refreshes the index on first use, so step 1 is
   optional.)
3. `get_callers(project, vi="…/fail.vi")` → *"does this VI have callers?"*
4. `blast_radius(project, vi="…/fail.vi")` → *"what breaks if I change it?"*

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

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
don't run `lvkit mcp` by hand. Point the client's `command` at any lvkit that is
on the machine.

**Standalone binary (no Python/uv)** — the signed binary bundled with the VS Code
extension, or a `lvkit-mcp-*.zip` from a GitHub release, is a complete MCP server:

```json
{ "mcpServers": { "lvkit": { "command": "/abs/path/to/lvkit", "args": ["mcp"] } } }
```

VS Code uses `.vscode/mcp.json` with a `"servers"` key and `"type": "stdio"`
instead of the `"mcpServers"` shape above.

**Via uv / pip** — if lvkit is installed as a Python package:

```json
{ "mcpServers": { "lvkit": { "command": "uvx", "args": ["--from", "lvkit", "lvkit-mcp"] } } }
```

**Codex** uses TOML — add the server to the project's `.codex/config.toml`
(shared by the ChatGPT desktop app, Codex CLI, and the Codex IDE extension):

```toml
[mcp_servers.lvkit]
command = "uvx"
args = ["--from", "lvkit", "lvkit-mcp"]
```

`lvkit setup codex` installs the workflow skills only — it does not create or
modify `.codex/config.toml`.

## Tools

The server exposes tools in three groups.

### Project index — whole-repo questions in one call

`index` builds a persisted, **path-keyed** facts index for the enclosing project
(so same-named VIs like `setUp.vi` ×17 never collide); the rest read it in sub-ms.
All take a `project` path (any file/dir inside the repo).

| Tool | Description |
|------|-------------|
| `index` | Build/refresh the index for a repo. Returns VI count, collisions handled, and ms. |
| `find_symbols` | Workspace symbol search — VIs by name substring and/or owning class. |
| `find_terminals` | Controls/indicators across every VI, filtered by direction / error-cluster / type / name. `direction="output"` = indicators. |
| `find_constants` | Constants across every VI, by what their wire feeds (`wired_to="indicator"` …). |
| `find_type_usages` | VIs whose terminals reference a class/typedef (reverse type-usage). |
| `get_callers` / `get_callees` | Pure call edges to/from a VI (ownership excluded). |
| `blast_radius` | Transitive dependents of a VI — "what breaks if I change this?" |
| `get_signatures` | Connector panes of every VI, terminals summarized — bulk classification. |
| `visualize_project` | A self-contained Mermaid call graph or class tree, with optional blast-radius highlight. |

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
| `get_context` | Full structured context (inputs/outputs/operations/wires/constants) as JSON. |
| `generate_ast_code` | Python for one VI via the deterministic AST pipeline. |

### Stateless generators

| Tool | Description |
|------|-------------|
| `generate_documents` | Generate the static HTML documentation site — same as [`docs`](docs.md). |
| `generate_python` | Generate a Python package — same as [`generate`](generate.md), with a review workflow. |

## Example — the project-understanding demo

Against JKI VI Tester (487 VIs):

1. `index(project="…/source")` → `{vis: 487, collisions: 65, ms: …}`.
2. `find_terminals(project, direction="output", is_error_cluster=true)` → tally the
   `name` values: *"what does this project call its error indicators?"* in one call.
3. `get_callers(project, vi="…/fail.vi")` → *"does this VI have callers?"*
4. `blast_radius(project, vi="…/fail.vi")` → *"what breaks if I change it?"*

## Notes

- The index is **project-scoped** (keyed by project root) — an agent can work
  across several repos in one session without a shared global graph to clear.
- The index is stored under `~/.lvkit/cache/index/projects/<slug>/index.db`
  (SQLite/WAL), rebuilt cheaply from the content-hash-keyed extraction cache.
- Before each call that knows a path, the server re-resolves the project's
  `.lvkit/` store (see [setup](setup.md)), so saved primitive/vi.lib mappings apply.
- The server targets `mcp` 1.x (FastMCP). `mcp` 2.0 removed that API surface; the
  `mcp<2` pin is deliberate — `--selftest` and CI guard against a silent break.

## See also

- [describe](describe.md) — the equivalent one-shot CLI query for a single VI.
- [generate](generate.md) — the equivalent one-shot CLI conversion.
- [setup](setup.md) — install AI-agent skills that pair with this server.

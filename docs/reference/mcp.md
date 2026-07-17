# mcp

Start the lvkit MCP (Model Context Protocol) server over stdio, so an AI agent
can load a VI's graph and query it interactively — across multiple VIs in a
single session, rather than one CLI invocation per question.

## Synopsis

```bash
lvkit mcp
```

`mcp` takes no arguments beyond `-h`. It runs the same server as the
`lvkit-mcp` console script installed with the `lvkit` package: `lvkit mcp` is
the subcommand form, `lvkit-mcp` is a standalone entry point that calls the
identical `main()`. Use whichever form your agent's launcher expects —
`lvkit-mcp` is the one tools like `uvx` invoke by name.

## Tools

The server exposes 12 tools, in three groups.

Stateless — no `load` required:

| Tool | Description |
|------|-------------|
| `generate_documents` | Generate the static HTML documentation site for a VI, library, class, or directory — same output as [`docs`](docs.md). |
| `generate_python` | Generate a Python package from a VI — same conversion as [`generate`](generate.md), with an output-review workflow (`needs_review`/`errors` lists) for the calling agent. |

Stateful graph — build and manage the in-memory VI graph for the session:

| Tool | Description |
|------|-------------|
| `load` | Load a VI, and by default its SubVIs, into the session's graph. |
| `list_loaded` | List VIs currently loaded in the graph. |
| `clear` | Clear the graph. |
| `get_context` | Get a loaded VI's full context — inputs, outputs, operations, wires, constants. |
| `generate_ast_code` | Generate Python for a loaded VI via the same AST translation as `generate`. |

Graph exploration — read a VI already in the graph:

| Tool | Description |
|------|-------------|
| `describe` | Human-readable signature, inputs/outputs, SubVI calls, and control flow — same content as [`describe`](describe.md). |
| `get_operations` | Execution-ordered operations, with nested structures. |
| `get_dataflow` | Wire connections between operations, optionally filtered to one operation. |
| `get_structure` | Detail on one case/loop/sequence structure node — selector, tunnels, frame contents. |
| `get_constants` | Every constant's name, type, and value. |

The `get_context`, `generate_ast_code`, `describe`, `get_operations`,
`get_dataflow`, `get_structure`, and `get_constants` tools all read from the
session's graph and require a `load` call first.

## Example

Add lvkit as an MCP server in your agent's configuration:

```json
{
  "mcpServers": {
    "lvkit": { "command": "uvx", "args": ["--from", "lvkit", "lvkit-mcp"] }
  }
}
```

The agent's runtime starts the server itself over stdio and issues tool calls
against it — you don't run `lvkit mcp` / `lvkit-mcp` by hand.

## Notes

- `load` takes the same SubVI resolution parameters as the CLI's resolution
  flags — `search_paths`, `vilib_root`, `userlib_root`, `auto_vilib` — see
  [SubVI & vi.lib resolution](subvi-resolution.md). `auto_vilib` defaults to
  `true`, matching the CLI's default (auto-detection on unless
  `--no-auto-vilib` is passed).
- Before each `load`, `generate_python`, or `generate_documents` call, the
  server re-resolves the project's `.lvkit/` store (see [setup](setup.md))
  from the given VI path, so primitive and vi.lib mappings already saved
  there apply without re-running `setup` per session.

## See also

- [describe](describe.md) — the equivalent one-shot CLI query for a single VI.
- [generate](generate.md) — the equivalent one-shot CLI conversion, including
  `PrimitiveResolutionNeeded`/`VILibResolutionNeeded` behavior that
  `generate_python` surfaces per file.
- [setup](setup.md) — install AI-agent skills that pair with this server.

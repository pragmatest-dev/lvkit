# lvkit

Read, document, diff, and convert LabVIEW VI files — no LabVIEW license required.

lvkit parses `.vi`, `.ctl`, `.lvclass`, and `.lvlib` files directly into queryable dependency and dataflow graphs. Use it to document code, track changes in CI, feed VI structure to AI tools, or generate equivalent Python.

## Contents

- [Quick Start](#quick-start)
- [What you can do with it](#what-you-can-do-with-it)
- [CLI Commands](#cli-commands)
- [How it works](#how-it-works)
- [AI and IDE integration](#ai-and-ide-integration)
- [Cleanroom approach](#cleanroom-approach)
- [Development](#development)

## Quick Start

```bash
pip install lvkit
lvkit init --skills all       # create .lvkit/ + install Claude Code and Copilot skills
```

* Use `--skills claude` or `--skills copilot` to install for one AI agent only.
* Use `lvkit init` alone if you don't want any AI agent skills installed.

## What you can do with it

### Describe what a VI does
Get a human-readable signature, inputs/outputs, operations, and control flow — without opening LabVIEW. Never requires primitive or vi.lib mappings.

```
lvkit describe <path-to.vi> [--search-path <libraries/>] [--chart]
```

`--chart` adds a Mermaid flowchart of the block diagram.

### Generate documentation
Cross-referenced HTML docs for a `.vi`, `.lvlib`, or `.lvclass` — inputs, outputs, operations, wiring diagrams.

```
lvkit docs <input-path> <output-dir> [--search-path <libraries/>]
```

### Diff two versions of a VI
See what changed between two `.vi` files — added/removed terminals, changed operations, rewired connections. Useful in code review and CI.

```
lvkit diff <vi-a> <vi-b> [--long]
```

`--long` gives a structured change report instead of a unified diff.

### Generate Python
Convert a VI, library, or class to Python. Deterministic — same VI in, same Python out, every run, no LLM involved.

```
lvkit generate <input-path> -o <output-dir> [--search-path <libraries>] [--placeholder-on-unresolved]
```

`--placeholder-on-unresolved` lets the build succeed when mappings are missing — unresolved calls become inline `raise PrimitiveResolutionNeeded(...)` in the output so you can track them down at runtime.

Coverage is incremental — see [Cleanroom approach](#cleanroom-approach) for what that means in practice.

## CLI Commands

| Command | Description |
|---------|-------------|
| `lvkit describe` | Human-readable VI description with signature and operations |
| `lvkit docs` | Generate cross-referenced HTML documentation |
| `lvkit diff` | Compare two VI versions — terminals, operations, wiring |
| `lvkit visualize` | Mermaid flowchart or interactive dependency graph |
| `lvkit generate` | Generate Python from a VI, library, or class |
| `lvkit structure` | Inspect `.lvlib` or `.lvclass` structure |
| `lvkit init` | Create `.lvkit/` resolution store; install AI editor skills |
| `lvkit mcp` | Start the MCP server for IDE integration |

`lvkit visualize --format interactive` requires `pip install pyvis`. All other commands work on a bare `pip install lvkit`.

## How it works

lvkit reads VI binaries directly — no LabVIEW installation required. The pipeline has three stages:

1. **Parse** — the VI binary is extracted to XML (via [pylabview](https://github.com/mefistotelis/pylabview)), then parsed into a typed representation of the block diagram: nodes, wires, constants, types, and front panel terminals.

2. **Graph** — all loaded VIs are linked into a graph that captures two things: the dependency tree (which VIs call which) and the dataflow within each VI (how data moves between operations). This is what `describe`, `docs`, `diff`, and `visualize` query — no semantic mappings needed.

3. **Generate** — the graph is walked deterministically to produce Python source, HTML documentation, or flowcharts. Code generation is pure AST construction: same VI in, same output every run, no LLM.

See [`docs/graph-reference.md`](docs/graph-reference.md) for the full graph type reference.

## AI and IDE integration

The CLI works standalone from any terminal or CI script. For deeper IDE integration, lvkit ships two optional layers.

**AI editor skills** — install lvkit's built-in workflows into Claude Code or Copilot so your AI can describe VIs, convert them, and resolve unknowns without you writing prompts. All five workflows call the CLI under the hood — no MCP server required.

```bash
lvkit init --skills claude    # installs .claude/skills/lvkit-*
lvkit init --skills copilot   # installs .github/prompts/ + router instruction
lvkit init --skills all       # both
```

Five workflows ship: `lvkit-describe`, `lvkit-convert`, `lvkit-resolve-primitive`, `lvkit-resolve-vilib`, `lvkit-idiomatic`.

**MCP server** — for interactive IDE sessions where your AI needs to load a graph, walk wires, and ask follow-up questions across multiple VIs:

```json
{
  "mcpServers": {
    "lvkit": { "command": "uvx", "args": ["--from", "lvkit", "lvkit-mcp"] }
  }
}
```

| Tool | Description |
|------|-------------|
| `load` | Load VI into the in-memory graph |
| `list_loaded` | List loaded VIs |
| `get_context` | Full VI context: inputs, outputs, operations, wires |
| `generate_ast_code` | Generate Python from a loaded VI |
| `describe` | Human-readable VI description |
| `get_operations` | List operations in a VI |
| `get_dataflow` | Show wire connections |
| `get_structure` | Inspect a structure node (loop, case, sequence) |
| `get_constants` | List constant values |
| `analyze` | Parse and describe VI structure (stateless) |
| `generate_documents` | Generate HTML docs for VIs/libraries (stateless) |
| `generate_python` | Generate Python from a VI (stateless) |

## Cleanroom approach

lvkit has no access to LabVIEW source code or runtime. LabVIEW's built-in primitives and standard library VIs are **semantically replaced**: each operation is mapped to an equivalent Python implementation in JSON data files (`src/lvkit/data/primitives.json`, `src/lvkit/data/vilib/`). These mappings are built from published documentation and observed behavior.

Coverage is incremental. When `lvkit generate` encounters an unmapped primitive or vi.lib VI, it raises an error with diagnostic context so the mapping can be added. `describe`, `docs`, `diff`, and `visualize` are unaffected — they work from the graph, not the semantic mappings.

### Project-local resolution store (`.lvkit/`)

If you have a LabVIEW license, you can supplement the bundled mappings with a `.lvkit/` directory derived from your own install. lvkit reads `.lvkit/` first and falls back to its bundled data. The skills installed by `lvkit init` detect whether you're a lvkit maintainer or a downstream user and write mappings to the right destination.

When `lvkit generate` hits an unknown, you have two options:

1. **Resolve up front** — install the resolve skills (`lvkit init --skills claude`) and let your AI editor write the mapping into `.lvkit/`.
2. **Defer to runtime** — pass `--placeholder-on-unresolved`. lvkit emits an inline `raise PrimitiveResolutionNeeded(...)` in the generated Python with full diagnostic context. The build succeeds; runtime fails at the unresolved call.

## Development

```bash
uv sync
pytest
ruff check .
python -m pyright src/
```

See [`CLAUDE.md`](CLAUDE.md) for contributor workflow, code style, and how to add primitive or vi.lib mappings.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Further reading

- [`docs/graph-reference.md`](docs/graph-reference.md) — graph type reference (nodes, VIContext, operations, wires)
- [`docs/vi-xml-reference.md`](docs/vi-xml-reference.md) — pylabview XML format reference
- [`docs/highlight-reel.md`](docs/highlight-reel.md) — design narrative and architecture decisions
- [`docs/demo-script.md`](docs/demo-script.md) — 30-minute demo script / tutorial
- [pylabview](https://github.com/mefistotelis/pylabview) — the VI binary parser lvkit builds on

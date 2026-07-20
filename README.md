# lvkit

Read, document, diff, and convert LabVIEW VI files — no LabVIEW license required.

**lvkit never modifies a VI — it reads what LabVIEW writes.**

lvkit parses `.vi`, `.ctl`, `.lvclass`, and `.lvlib` files directly into queryable dependency and dataflow graphs. Use it to document code, track changes in CI, feed VI structure to AI tools, or generate equivalent Python.

> **Reads, never writes.** lvkit only ever reads a VI — it never modifies, re-saves, or edits one. Your files are never touched, and LabVIEW stays the only thing that authors them. This holds across every capability, including convert: generating Python parses the VI and emits a *separate* file — it never edits the source.

> **Independent, clean-room project — not affiliated with NI.** lvkit was built using **only publicly available information**: public NI documentation, the open-source [pylabview](https://github.com/mefistotelis/pylabview) project, and observation of VI files. It was developed **without installing or running LabVIEW or any NI software**, and with **no NI source code, internal or non-public specifications, or confidential or proprietary materials**. LabVIEW, NI, and National Instruments are trademarks of National Instruments Corporation, used here only to identify the format lvkit interoperates with; lvkit is not affiliated with, authorized by, endorsed by, or sponsored by NI. See [Cleanroom approach](#cleanroom-approach), [`NOTICE`](NOTICE), and [`PROVENANCE.md`](PROVENANCE.md).

## Contents

- [Quick Start](#quick-start)
- [What you can do with it](#what-you-can-do-with-it)
- [How it works](#how-it-works)
- [AI and IDE integration](#ai-and-ide-integration)
- [Cleanroom approach](#cleanroom-approach)
- [Development](#development)

## Quick Start

```bash
pip install lvkit
lvkit setup
```

For a global install: `pipx install lvkit` or `uv tool install lvkit`.

`lvkit setup` creates a `.lvkit/` resolution store and installs AI agent skills:

- Auto-detects Claude Code (`CLAUDE.md` / `.claude/`) and Copilot (`.github/copilot-instructions.md` / `.github/instructions/` / `.github/agents.md`)
- Pass `claude`, `copilot`, or `all` to be explicit
- Use `--no-skills` to create the `.lvkit/` store without installing any skills

| Command | Description |
|---------|-------------|
| `lvkit describe` | Human-readable VI description with signature and operations |
| `lvkit docs` | Generate cross-referenced HTML documentation |
| `lvkit diff` | Compare two VI versions — terminals, operations, wiring |
| `lvkit visualize` | Interactive dependency or data flow graph HTML |
| `lvkit generate` | Generate Python from a VI, library, or class (experimental — see [Cleanroom approach](#cleanroom-approach)) |
| `lvkit render` | Render an interactive block diagram SVG from a VI |
| `lvkit structure` | Inspect `.lvlib` or `.lvclass` structure |
| `lvkit setup` | Install AI agent skills; create `.lvkit/` resolution store |
| `lvkit mcp` | Start the MCP server for IDE integration |

`lvkit visualize --format interactive` requires `pip install lvkit[visualize]`. All other commands work on a bare `pip install lvkit`.

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

Coverage is incremental and results will vary — see [Cleanroom approach](#cleanroom-approach) for what that means in practice.

## How it works

lvkit reads VI binaries directly — no LabVIEW installation required. The pipeline has three stages:

1. **Parse** — the VI binary is extracted to XML (via [pylabview](https://github.com/mefistotelis/pylabview)), then parsed into a typed representation of the block diagram: nodes, wires, constants, types, and front panel terminals.

2. **Graph** — all loaded VIs are linked into a graph that captures two things: the dependency tree (which VIs call which) and the dataflow within each VI (how data moves between operations). This is what `describe`, `docs`, `diff`, and `visualize` query — no semantic mappings needed.

3. **Generate** — the graph is walked deterministically to produce Python source, HTML documentation, or flowcharts. Code generation is pure AST construction: same VI in, same output every run, no LLM.

See [`docs/graph-reference.md`](docs/graph-reference.md) for the full graph type reference.

## AI and IDE integration

The CLI works standalone from any terminal or CI script. For deeper IDE integration, lvkit ships two optional layers.

**AI agent skills** — install lvkit's built-in workflows into Claude Code or Copilot so your AI agent can describe VIs, convert them, and resolve unknowns without you writing prompts. All five workflows call the CLI under the hood — no MCP server required.

```bash
lvkit setup           # auto-detect from project layout
lvkit setup claude    # installs .claude/skills/lvkit-*
lvkit setup copilot   # installs .github/prompts/ + router instruction
lvkit setup all       # both
```

Five workflows ship: `lvkit-describe`, `lvkit-convert`, `lvkit-resolve-primitive`, `lvkit-resolve-vilib`, `lvkit-idiomatic`.

**MCP server** — for interactive IDE sessions where your AI agent needs to load a graph, walk wires, and ask follow-up questions across multiple VIs:

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

lvkit has no access to LabVIEW source code or runtime. LabVIEW's built-in primitives and standard library VIs are **semantically replaced**: each operation is mapped to an equivalent Python implementation in open, inspectable JSON data files (`src/lvkit/data/primitives.json`, `src/lvkit/data/vilib/`).

These mappings are **lvkit's own definitions, derived purely by inference** from two public inputs and nothing else:

1. **Public NI documentation** — pages published openly on ni.com, accessed with no login, partner portal, or NDA gate.
2. **The VI XML produced by [pylabview](https://github.com/mefistotelis/pylabview)** — the open-source parser that extracts the VI binary to XML. That XML is lvkit's *only* window into the format; lvkit has no other view of it.

The definitions are open source and fully inspectable, and many carry a note recording how each was inferred (the public doc consulted, the observed terminal signature, a `verified`/`guess_reason` marker). They are **best-effort inferences, not authoritative** — they have been wrong and corrected over time. That imperfection is a direct consequence of working from *only* public documentation and the pylabview XML, with **no access to any internal or authoritative NI specification** — an insider would not need to infer.

### Provenance

lvkit was developed **using only publicly available information** and clean-room methods. It was built **without installing or running LabVIEW or any NI software**, and with **no NI source code, internal or non-public specifications, or confidential or proprietary materials**. Facts about LabVIEW's behavior are used as facts; no NI documentation prose or artwork is copied or redistributed — primitive glyphs are drawn procedurally, and the shipped data contains no NI text or images. See [`PROVENANCE.md`](PROVENANCE.md).

### Trademarks

LabVIEW, NI, and National Instruments are trademarks of National Instruments Corporation. lvkit is an independent project and is not affiliated with, authorized by, endorsed by, or sponsored by NI. Those names are used only to identify the file format and software lvkit interoperates with (nominative use).

Coverage is incremental. When `lvkit generate` encounters an unmapped primitive or vi.lib VI, it raises an error with diagnostic context so the mapping can be added. `describe`, `docs`, `diff`, and `visualize` are unaffected — they work from the graph, not the semantic mappings.

### Project-local resolution store (`.lvkit/`)

You can supplement the bundled mappings with a `.lvkit/` directory in your project root. lvkit reads `.lvkit/` first and falls back to its bundled data.

Run `lvkit setup --no-skills` to create the store with a README that documents the file layout and JSON formats for adding primitive and vi.lib mappings manually.

When `lvkit generate` hits an unknown, you have two options:

1. **Resolve up front** — run `lvkit setup` to install the resolve skills and let your AI agent write the mapping into `.lvkit/`.
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

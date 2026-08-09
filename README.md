# lvkit

Read, render, document, and diff LabVIEW VI files — no LabVIEW license required.

lvkit parses `.vi`, `.ctl`, `.lvclass`, `.lvlib`, and `.lvproj` files directly into queryable dependency and dataflow graphs. From that graph it renders block diagrams, describes what a VI does, generates documentation, and diffs two versions — from a terminal, in CI, or through an AI agent. It can also generate a Python transliteration of a VI, though that path is experimental and how far it gets varies from VI to VI.

> **Reads, never writes.** lvkit only ever reads a VI — it never modifies, re-saves, or edits one. Every capability, including convert, parses the source and writes to a *separate* output file; your `.vi` files are never touched, and LabVIEW stays the only thing that authors them.

> **Independent, clean-room project — not affiliated with NI.** Built from public NI documentation, the open-source [pylabview](https://github.com/mefistotelis/pylabview) parser, and observation of VI files — with no LabVIEW install and no NI source or non-public specifications. Details in [Cleanroom approach](#cleanroom-approach); see also [`NOTICE`](NOTICE) and [`PROVENANCE.md`](PROVENANCE.md).

## Contents

- [Quick Start](#quick-start)
- [What you can do with it](#what-you-can-do-with-it)
- [How it works](#how-it-works)
- [Editor and AI integration](#editor-and-ai-integration)
- [Cleanroom approach](#cleanroom-approach)
- [Development](#development)

## Quick Start

```bash
pip install lvkit
lvkit setup
```

For a global install: `pipx install lvkit` or `uv tool install lvkit`.

`lvkit setup` creates a `.lvkit/` resolution store and installs AI agent skills:

- Auto-detects Claude Code (`CLAUDE.md` / `.claude/`), Copilot (`.github/copilot-instructions.md` / `.github/instructions/` / `.github/agents.md`), and Codex (`AGENTS.md` / `AGENTS.override.md` / `.agents/` / `.codex/`)
- Pass `claude`, `copilot`, `codex`, or `all` to be explicit
- Use `--no-skills` to create the `.lvkit/` store without installing any skills

| Command | Description |
|---------|-------------|
| `lvkit describe` | Human-readable VI summary: signature, operations, dataflow, structures |
| `lvkit render` | Render a VI's block diagram to a faithful SVG (or interactive HTML) |
| `lvkit diff` | Compare two VI versions — operations, structures, wiring, constants |
| `lvkit docs` | Generate cross-referenced HTML documentation |
| `lvkit visualize` | Interactive dataflow or dependency graph (HTML) |
| `lvkit generate` | Generate Python from a VI, library, or class (experimental — see [Cleanroom approach](#cleanroom-approach)) |
| `lvkit index` / `lvkit query` | Index a whole repo, then ask project-wide questions in read-only SQL ([docs](docs/reference/query.md)) |
| `lvkit callers` / `callees` / `blast-radius` | Call graph & change impact: who calls a VI, and what breaks if you change it ([docs](docs/reference/callers.md)) |
| `lvkit structure` | Inspect `.lvproj`, `.lvlib`, or `.lvclass` structure |
| `lvkit detect` | Detect a locally installed LabVIEW and its `vi.lib` / `user.lib` |
| `lvkit setup` | Install AI agent skills; create the `.lvkit/` resolution store |
| `lvkit mcp` | Start the MCP server for IDE integration |

`describe`, `render`, `diff`, `docs`, and `visualize` work from the graph alone and need no semantic mappings. Only `generate` requires primitive/vi.lib mappings. `lvkit visualize` needs the pyvis extra: `pip install lvkit[visualize]`.

## What you can do with it

### Describe what a VI does
A human-readable signature, inputs/outputs, operations, and control flow — without opening LabVIEW.

```
lvkit describe <path-to.vi> [--search-path <libraries/>] [--verbose]
```

`--verbose` adds a full netlist — a text projection of the block diagram.

### Render the block diagram
Render a VI's block diagram to a faithful, self-contained SVG — nodes, wires, structures, and SubVI icons, all drawn from the parsed graph. No LabVIEW, no screenshots.

```
lvkit render <path-to.vi> [--format {svg,html}] [--theme {light,dark,auto}] [--search-path <libraries/>]
```

`--format html` writes an interactive single-VI viewer with zoom/pan and a light/dark diagram-theme toggle; hover a node to see its connector pane and documentation. The same renderer powers `diff --format html` and the VS Code extension.

### Diff two versions of a VI
See what changed between two `.vi` files — added/removed operations and structures, rewired connections, changed constants. Useful in code review and CI.

```
lvkit diff <vi-a> <vi-b> [--format {text,json,html}] [--open]
```

Text (the default) is a concise, logical change summary for stdout or CI. `--format json` emits a UID-correlated change map for scripts or an AI agent. `--format html` (or `--open`) writes an **interactive diff viewer** — synced before/after panes, click a change to spotlight it, deep-linkable.

### Generate documentation
Cross-referenced HTML docs for a `.vi`, `.lvlib`, or `.lvclass` — inputs, outputs, operations, wiring diagrams.

```
lvkit docs <input-path> <output-dir> [--search-path <libraries/>]
```

### Generate Python (experimental)
This is lvkit's least mature capability, and the one most likely to need hand-finishing. It walks the graph to emit a Python transliteration of a `.vi`, `.lvlib`, or `.lvclass`. The transliteration is deterministic (same VI in, same Python out, no LLM), but coverage is incremental: a VI only converts cleanly when every primitive and vi.lib VI it uses is already mapped, so how far it gets varies widely. Expect to resolve unknowns and clean up the output — see [Cleanroom approach](#cleanroom-approach).

```
lvkit generate <input-path> -o <output-dir> [--search-path <libraries>] [--placeholder-on-unresolved]
```

`--placeholder-on-unresolved` lets the build succeed when mappings are missing — unresolved calls become inline `raise PrimitiveResolutionNeeded(...)` in the output so you can track them down at runtime.

## How it works

lvkit reads VI binaries directly — no LabVIEW installation required. The pipeline has three stages:

1. **Parse** — the VI binary is extracted to XML (via [pylabview](https://github.com/mefistotelis/pylabview)), then parsed into a typed representation of the block diagram: nodes, wires, constants, types, and front panel terminals.

2. **Graph** — all loaded VIs are linked into a graph that captures two things: the dependency tree (which VIs call which) and the dataflow within each VI (how data moves between operations). This is what `describe`, `render`, `diff`, `docs`, and `visualize` query — no semantic mappings needed.

3. **Generate** — the graph is walked deterministically to produce Python source, HTML documentation, or diagrams. Code generation is pure AST construction: same VI in, same output every run, no LLM.

See the [command reference](docs/reference/index.md) for full, flag-level docs on every command.

## Editor and AI integration

The CLI works standalone from any terminal or CI script. For deeper integration, lvkit ships three optional layers.

**VS Code extension** — [**LVKit** on the Marketplace](https://marketplace.visualstudio.com/items?itemName=pragmatest.lvkit). Opening a `.vi` renders its block diagram inline instead of the "binary file" placeholder, and clicking a SubVI opens it. **Open Visual Diff** — right-click a changed `.vi` in Source Control, the Explorer, or the editor tab — shows a before/after block-diagram diff for code review. It ships a bundled lvkit, so it works with no separate install; point the `lvkit.path` setting at a project venv or a PATH install to use your own instead. `.vi` files only today.

**AI agent skills** — install lvkit's built-in workflows into Claude Code, GitHub Copilot, or OpenAI Codex so your AI agent can describe VIs, convert them, and resolve unknowns without you writing prompts. All five workflows call the CLI under the hood — no MCP server required.

```bash
lvkit setup           # auto-detect from project layout
lvkit setup claude    # installs .claude/skills/lvkit-*
lvkit setup copilot   # installs .github/prompts/ + router instruction
lvkit setup codex     # installs .agents/skills/lvkit-*
lvkit setup all       # Claude Code, Copilot, and Codex
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
| `load` | Load a VI into the in-memory graph |
| `list_loaded` | List loaded VIs |
| `clear` | Clear the in-memory graph |
| `get_context` | Full VI context: inputs, outputs, operations, wires |
| `describe` | Human-readable VI description |
| `get_operations` | List operations in a VI |
| `get_dataflow` | Show wire connections |
| `get_structure` | Inspect a structure node (loop, case, sequence) |
| `get_constants` | List constant values |
| `generate_ast_code` | Generate Python from a loaded VI |
| `generate_documents` | Generate HTML docs for VIs/libraries (stateless) |
| `generate_python` | Generate Python from a VI (stateless) |

## Cleanroom approach

lvkit has no access to LabVIEW source code or runtime. LabVIEW's built-in primitives and standard library VIs are **semantically replaced**: each operation is mapped to an equivalent Python implementation in open, inspectable JSON data files (`src/lvkit/data/primitives.json`, `src/lvkit/data/vilib/`).

These mappings are **lvkit's own definitions, derived purely by inference** from two public inputs and nothing else:

1. **Public NI documentation** — pages published openly on ni.com, accessed with no login, partner portal, or NDA gate.
2. **The VI XML produced by [pylabview](https://github.com/mefistotelis/pylabview)** — the open-source parser that extracts the VI binary to XML. That XML is lvkit's *only* window into the format; lvkit has no other view of it.

The definitions are open source and fully inspectable, and many carry a note recording how each was inferred (the public doc consulted, the observed terminal signature, a `verified`/`guess_reason` marker). They are **best-effort inferences, not authoritative** — they have been wrong and corrected over time. That imperfection is a direct consequence of working from *only* public documentation and the pylabview XML, with **no access to any internal or authoritative NI specification**.

Coverage is incremental. When `lvkit generate` encounters an unmapped primitive or vi.lib VI, it raises an error with diagnostic context so the mapping can be added. `describe`, `render`, `diff`, `docs`, and `visualize` are unaffected — they work from the graph, not the semantic mappings.

### Provenance

lvkit was developed **using only publicly available information** and clean-room methods. It was built **without installing or running LabVIEW or any NI software**, and with **no NI source code, internal or non-public specifications, or confidential or proprietary materials**. Facts about LabVIEW's behavior are used as facts; no NI documentation prose or artwork is copied or redistributed — primitive glyphs are drawn procedurally, and the shipped data contains no NI text or images. See [`PROVENANCE.md`](PROVENANCE.md).

### Trademarks

LabVIEW, NI, and National Instruments are trademarks of National Instruments Corporation. lvkit is an independent project and is not affiliated with, authorized by, endorsed by, or sponsored by NI. Those names are used only to identify the file format and software lvkit interoperates with (nominative use).

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

- [`docs/reference/`](docs/reference/index.md) — per-command reference for the full CLI
- [`docs/reference/netlist.md`](docs/reference/netlist.md) — the netlist text syntax (`describe --verbose`, `diff`)
- [`docs/reference/subvi-resolution.md`](docs/reference/subvi-resolution.md) — how lvkit finds SubVIs and libraries
- [pylabview](https://github.com/mefistotelis/pylabview) — the VI binary parser lvkit builds on

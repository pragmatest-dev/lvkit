# lvpy

Understand and convert LabVIEW VIs to Python without a LabVIEW license.

lvpy reads `.vi` binary files directly — no LabVIEW installation required. It parses the binary into a queryable dataflow graph, then uses that graph to generate Python code, HTML documentation, human-readable descriptions, and visual flowcharts. Built on [pylabview](https://github.com/mefistotelis/pylabview) for binary parsing and NetworkX for graph representation.

## Cleanroom approach

lvpy is a cleanroom conversion tool — it has no access to LabVIEW source code or runtime. LabVIEW's standard library (vi.lib), built-in primitives, and third-party libraries like OpenG are **semantically replaced**: each operation is mapped to an equivalent Python implementation defined in JSON data files (`src/lvpy/data/vilib/`, `src/lvpy/data/primitives.json`, `src/lvpy/data/openg/`). These mappings are built from published documentation and observed behavior, not from LabVIEW internals.

This means coverage is incremental. When the generator encounters an unmapped primitive or vi.lib VI, it raises an error with diagnostic context so the mapping can be added. The data files grow over time as more VIs are converted.

## Quick Start

```bash
# Install
pip install lvpy

# Check dependencies (pylabview)
lvpy check

# Generate Python from a VI, library, or class
lvpy generate "path/to/file.vi" -o outputs --search-path path/to/libraries
lvpy generate "MyClass.lvclass" -o outputs --search-path path/to/libraries
lvpy generate "MyLib.lvlib"     -o outputs --search-path path/to/libraries

# Generate HTML documentation
lvpy docs "path/to/MyLib.lvlib" outputs/docs --search-path path/to/libraries

# Describe what a VI does (signature, operations, dataflow)
lvpy describe "path/to/file.vi" --search-path path/to/libraries

# Initialize a project-local resolution store + install editor skills
lvpy init --skills all

# Start MCP server for IDE integration
lvpy mcp
```

**Contributors:** `uv sync` then `lvpy check`.

### What it generates

```
$ lvpy generate samples/JKI-VI-Tester/source/Classes/TestCase/TestCase.lvclass \
    -o outputs --search-path samples/OpenG/extracted

  139 VIs loaded — 94 AST files generated — 0 errors
```

Output for a simple VI:

```python
# Get Settings Path.vi → get_settings_path.py
def get_settings_path() -> GetSettingsPathResult:
    result = get_system_directory(directory_type=SystemDirectoryType.PUBLIC_APP_DATA)
    appended_path = Path(result.system_directory_path) / Path("JKI/VI Tester/Settings.ini")
    stripped_path = Path(appended_path).parent
    Path(stripped_path).mkdir(parents=True, exist_ok=True)
    return GetSettingsPathResult(config_path=appended_path)
```

## How it works

```
VI Binary (.vi / .lvlib / .lvclass)
     |
     v  pylabview (subprocess)
XML Files (_BDHb.xml, _FPHb.xml, .xml)
     |
     v  parser/ — XML → typed ParsedVI dataclasses
     |
     v  graph/ — ParsedVI → NetworkX InMemoryVIGraph
     |
     +-> codegen/builder.py  →  Python AST  →  source
     +-> graph/describe.py   →  human-readable descriptions
     +-> docs/               →  HTML documentation
```

- **`parser/`** extracts nodes, wires, constants, and types from the raw XML into `ParsedVI` dataclasses. No external resolution — just what's in the file.
- **`graph/` + `InMemoryVIGraph`** builds a NetworkX multi-digraph across all loaded VIs. `get_vi_context(vi_name)` returns a `VIContext` containing operations, terminals, wires, and types — the input to codegen.
- **`codegen/builder.py:build_module()`** walks `VIContext` and emits a Python `ast.Module` deterministically. No LLM, no sampling — same VI in, same Python out, every time.
- **`pipeline.py`** orchestrates multi-VI loads, dependency ordering, polymorphic wrapper generation, and file output.

See [`docs/graph-reference.md`](docs/graph-reference.md) for the full type reference.

## Project-local resolution store (`.lvpy/`)

lvpy ships cleanroom — its bundled data only contains mappings derived from public documentation. If you have a LabVIEW license, you can populate a project-local `.lvpy/` directory with mappings derived from the real vi.lib, your own LabVIEW sources, or third-party libraries you have rights to use. lvpy reads `.lvpy/` **first** and falls back to its bundled data.

Get started:

```bash
cd your-labview-project
lvpy init                          # Create .lvpy/ + template README
lvpy init --skills claude          # Also install Claude Code skills (.claude/skills/lvpy-*)
lvpy init --skills copilot         # Also install Copilot prompts + router instruction
lvpy init --skills all             # Both
```

Each install creates `lvpy-` prefixed entries so the workflows don't collide with other editor skills. Five workflows ship: `lvpy-describe`, `lvpy-convert`, `lvpy-resolve-primitive`, `lvpy-resolve-vilib`, `lvpy-idiomatic`.

Copilot install lays out:

```
.github/
  prompts/
    lvpy-describe.prompt.md
    lvpy-convert.prompt.md
    lvpy-resolve-primitive.prompt.md
    lvpy-resolve-vilib.prompt.md
    lvpy-idiomatic.prompt.md
  instructions/
    lvpy.instructions.md        # auto-loaded router; lists the 5 prompts
```

The `.lvpy/` directory mirrors lvpy's bundled `data/` layout:

```
.lvpy/
  README.md                 # license-boundary explainer
  primitives.json           # primitive overrides
  vilib/_index.json
  vilib/<category>.json
  openg/
  drivers/
```

lvpy itself **never reads `.lvpy/`** into its bundled data. Anything you put there stays in your project. Consider gitignoring files derived from licensed material before committing.

When `lvpy generate` hits an unknown primitive or vi.lib VI, you have two options:

1. **Resolve up front** — install the resolve skills (`lvpy init --skills claude`) and let your LLM editor write the mapping into `.lvpy/`. The skill detects context (lvpy maintainer vs downstream user) and writes to the right destination.
2. **Defer to runtime** — pass `--placeholder-on-unresolved`. lvpy emits an inline `raise PrimitiveResolutionNeeded(...)` / `raise VILibResolutionNeeded(...)` in the generated Python with full diagnostic context. The build succeeds; runtime fails at the unresolved call.

## CLI Commands

| Command | Description | Example |
|---------|-------------|---------|
| `lvpy check` | Check dependencies (pylabview) | `lvpy check` |
| `lvpy generate` | Generate Python from a VI, library, or class | `lvpy generate MyLib.lvlib -o outputs` |
| `lvpy describe` | Human-readable VI description with signature and operations | `lvpy describe In.vi` |
| `lvpy docs` | Generate cross-referenced HTML documentation | `lvpy docs MyLib.lvlib outputs/docs` |
| `lvpy diff` | Compare two VI versions | `lvpy diff old.vi new.vi` |
| `lvpy visualize` | Mermaid flowchart or dependency graph | `lvpy visualize In.vi -o graph.html` |
| `lvpy structure` | Analyze .lvlib or .lvclass structure | `lvpy structure MyClass.lvclass` |
| `lvpy summarize` | Low-level text summary of VI graph (debug) | `lvpy summarize In_BDHb.xml` |
| `lvpy init` | Create `.lvpy/` resolution store; install editor skills | `lvpy init --skills all` |
| `lvpy mcp` | Start the MCP server for IDE integration | `lvpy mcp` |

`lvpy visualize --format interactive` requires `pip install pyvis`. All other commands work on a bare `pip install lvpy`.

## MCP Server

lvpy exposes an MCP server (`lvpy mcp` or the `lvpy-mcp` console script). Once published to PyPI, run it via `uvx` without installing it permanently:

```json
{
  "mcpServers": {
    "lvpy": {
      "command": "uvx",
      "args": ["--from", "lvpy", "lvpy-mcp"]
    }
  }
}
```

Paste this into your MCP client config (Claude Code's `claude_code_config.json`, Cursor's `~/.cursor/mcp.json`, or equivalent), restart the client, and the 12 lvpy tools become available.

**Stateless tools** (subprocess-based, no shared state):

| Tool | Description |
|------|-------------|
| `analyze` | Parse and describe VI structure |
| `generate_documents` | Create HTML docs for VIs/libraries |
| `generate_python` | AST code generation |

**Stateful tools** (graph persists across calls within a session):

| Tool | Description |
|------|-------------|
| `load` | Load VI into in-memory graph |
| `list_loaded` | List loaded VIs |
| `get_context` | Get full VI context (inputs, outputs, operations, wires) |
| `generate_ast_code` | Generate code from loaded VI |
| `describe` | Human-readable VI description |
| `get_operations` | List operations in a VI |
| `get_dataflow` | Show wire connections |
| `get_structure` | Inspect a structure node (loop, case, sequence) |
| `get_constants` | List constant values |

## Development

```bash
uv sync                              # Install with dev dependencies (creates .venv)
pytest                               # Run all tests
ruff check .                         # Lint
python -m pyright src/               # Type check (basic mode)
pre-commit run --all-files           # Run pre-commit hooks
```

See [`CLAUDE.md`](CLAUDE.md) for contributor workflow, code style, error handling patterns, and adding new primitives or vilib VI mappings.

## License

Apache-2.0. See [LICENSE](LICENSE).

## Further reading

- [`docs/graph-reference.md`](docs/graph-reference.md) — full graph type reference (nodes, VIContext, operations, wires)
- [`docs/vi-xml-reference.md`](docs/vi-xml-reference.md) — pylabview XML format reference
- [`docs/highlight-reel.md`](docs/highlight-reel.md) — detailed design narrative, numbers, and architecture decisions
- [`docs/demo-script.md`](docs/demo-script.md) — 30-minute live demo script / tutorial
- [pylabview](https://github.com/mefistotelis/pylabview) — the VI binary parser lvpy builds on

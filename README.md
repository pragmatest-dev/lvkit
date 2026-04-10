# vipy

Understand and convert LabVIEW VIs without a LabVIEW license.

vipy parses VI binary files into a queryable dataflow graph, then uses that graph to generate Python code, HTML documentation, human-readable descriptions, and visual flowcharts. It uses [pylabview](https://github.com/mefistotelis/pylabview) for binary parsing and builds a NetworkX graph that can be explored via CLI, MCP server, or Python API.

### Cleanroom approach

vipy is a cleanroom conversion tool — it has no access to LabVIEW source code or runtime. LabVIEW's standard library (vi.lib), built-in primitives, and third-party libraries like OpenG are **semantically replaced**: each operation is mapped to an equivalent Python implementation defined in JSON data files (`src/vipy/data/vilib/`, `src/vipy/data/primitives.json`, `src/vipy/data/openg/`). These mappings are built from documentation and observed behavior, not from LabVIEW internals.

This means coverage is incremental. When the generator encounters an unmapped primitive or vi.lib VI, it raises an error with diagnostic context so the mapping can be added. The data files grow over time as more VIs are converted.

## Quick Start

```bash
# Install (editable, from a checkout)
pip install -e ".[dev]"

# Or, once published to PyPI:
#   pip install vipy
#   uvx vipy --help          # one-shot via uv

# Check dependencies (pylabview)
vipy check

# Generate Python from a VI
vipy generate "path/to/file.vi" -o outputs --search-path path/to/libraries

# Generate HTML documentation
vipy docs "path/to/MyLib.lvlib" outputs/docs --search-path path/to/libraries

# Describe what a VI does
vipy describe "path/to/file.vi" --search-path path/to/libraries

# Initialize a project-local resolution store + install LLM editor skills
vipy init --skills all

# Start MCP server for IDE integration
vipy mcp
```

## Project-local resolution store (`.vipy/`)

vipy ships cleanroom — its bundled data only contains mappings derived from public documentation. If you have a LabVIEW license, you can populate a project-local `.vipy/` directory with mappings derived from the real vi.lib, your own LabVIEW sources, or third-party libraries you have rights to use. vipy reads `.vipy/` **first** and falls back to its bundled data.

Get started:

```bash
cd your-labview-project
vipy init                          # Create .vipy/ + template README
vipy init --skills claude          # Also install Claude Code skills
vipy init --skills copilot         # Also install Copilot instructions
vipy init --skills all             # Both
```

The `.vipy/` directory mirrors vipy's bundled `data/` layout:

```
.vipy/
  README.md                 # license-boundary explainer
  primitives.json           # primitive overrides
  vilib/_index.json
  vilib/<category>.json
  openg/
  drivers/
```

vipy itself **never reads `.vipy/`** into its bundled data. Anything you put there stays in your project. Consider gitignoring files derived from licensed material before committing.

When `vipy generate` hits an unknown primitive or vi.lib VI, you have two options:

1. **Resolve up front** — install the resolve skills (`vipy init --skills claude`) and let your LLM editor write the mapping into `.vipy/`. The skill detects context (vipy maintainer vs downstream user) and writes to the right destination.
2. **Defer to runtime** — pass `--placeholder-on-unresolved`. vipy emits an inline `raise PrimitiveResolutionNeeded(...)` / `raise VILibResolutionNeeded(...)` in the generated Python with full diagnostic context. The build succeeds; runtime fails at the unresolved call. Useful when you'd rather fix the gap contextually in the Python.

## MCP server

vipy exposes an MCP server (`vipy mcp` or the `vipy-mcp` console script). Once published to PyPI, you can run it via `uvx` without installing it permanently:

```json
{
  "mcpServers": {
    "vipy": {
      "command": "uvx",
      "args": ["--from", "vipy", "vipy-mcp"]
    }
  }
}
```

The `--from vipy` tells `uvx` which package to install; `vipy-mcp` is the console-script entry point inside that package. Paste this into your MCP client config (Claude Code's `claude_code_config.json`, Cursor's `~/.cursor/mcp.json`, or equivalent), restart the client, and the vipy tools become available — load_vi, describe_vi, get_operations, get_dataflow, get_structure, get_constants, generate_python, generate_documents, and analyze_vi.

## Architecture Overview

```
VI Binary (.vi / .lvlib / .lvclass)
     |
     v  pylabview (subprocess)
XML Files (_BDHb.xml, _FPHb.xml, .xml)
     |
     v  parser/ (nodes, wires, constants, types)
ParsedVI (BlockDiagram, FrontPanel, Metadata)
     |
     v  graph/ (NetworkX multi-digraph)
InMemoryVIGraph (operations, terminals, wires, types)
     |
     +-> agent/codegen/ (deterministic AST-based)
     |        |
     |        v
     |   Python Code
     |
     +-> graph/describe.py (human-readable descriptions)
     |
     +-> docs/ (HTML documentation)
```

## Key Modules

### Parser Layer (`src/vipy/parser/`)

| File | Purpose |
|------|---------|
| `vi.py` | Top-level VI parsing orchestration |
| `models.py` | Core dataclasses: `ParsedNode`, `ParsedWire`, `ParsedConstant` |
| `node_types.py` | Specific node types: `PrimitiveNode`, `SubVINode`, `StructureNode` |
| `nodes/` | Node-specific parsers: `base`, `loop`, `constant`, `sequence`, `case` |
| `type_resolution.py` | Resolve LabVIEW types from XML |
| `type_mapping.py` | Map LabVIEW types to Python types |
| `front_panel.py` | Front panel (controls/indicators) parsing |
| `metadata.py` | VI metadata extraction |

### Graph Layer (`src/vipy/graph/`)

| File | Purpose |
|------|---------|
| `core.py` | `VIGraph` - NetworkX multi-digraph with node/edge operations |
| `construction.py` | Build graph from parsed VI data |
| `loading.py` | Load VIs and resolve dependencies |
| `operations.py` | Convert graph nodes to `Operation` objects for codegen |
| `queries.py` | Query operations, wires, constants from graph |
| `analysis.py` | Graph analysis (parallel branches, topological sort) |
| `describe.py` | Human-readable VI descriptions |
| `flowchart.py` | Mermaid flowchart generation |
| `diff.py` | Compare two VI versions |

### Other Core Modules (`src/vipy/`)

| File | Purpose |
|------|---------|
| `memory_graph.py` | `InMemoryVIGraph` - high-level graph with dependency tracking |
| `graph_types.py` | Pydantic models: `Operation`, `Terminal`, `Wire`, `Constant`, `VIContext` |
| `extractor.py` | Calls pylabview to extract VI to XML, caches results |
| `primitive_resolver.py` | Maps primResID to Python implementation from JSON data |
| `vilib_resolver.py` | Maps vi.lib VIs to Python implementations from JSON data |
| `enum_resolver.py` | Resolve LabVIEW enum typedefs |
| `type_defaults.py` | Default values for LabVIEW types |
| `structure.py` | Parse .lvlib, .lvclass, and project structure |
| `naming.py` | Python name sanitization and conventions |
| `labview_error.py` | LabVIEW error cluster handling |

### Agent Layer (`src/vipy/agent/`)

| File | Purpose |
|------|---------|
| `loop_agent.py` | Main conversion loop - iterates VIs in dependency order |
| `codegen/` | AST-based Python code generator (deterministic, no LLM) |
| `codegen/builder.py` | `build_module()` - entry point for AST generation |
| `codegen/context.py` | Tracks imports, bindings, dataflow during generation |
| `codegen/nodes/` | Node-specific generators (primitives, subvis, structures, loops, cases) |
| `codegen/error_handler.py` | Held-error model for parallel branches |
| `codegen/ast_optimizer.py` | Post-generation AST optimization |
| `skeleton.py` | Generate skeleton code for LLM completion |
| `context_builder.py` | Build context for LLM prompts |
| `validator.py` | Validate generated code (syntax, imports, completeness) |

### Documentation (`src/vipy/docs/`)

| File | Purpose |
|------|---------|
| `generate.py` | Orchestrate HTML doc generation for VIs/libraries |
| `html_generator.py` | Render VI context to HTML pages |
| `utils.py` | Doc generation utilities |

### MCP Server (`src/vipy/mcp/`)

| File | Purpose |
|------|---------|
| `server.py` | MCP server exposing tools to Claude Code |
| `tools.py` | Stateless tool implementations (analyze, generate, docs) |
| `schemas.py` | Pydantic models for tool results |

### Bundled data (`src/vipy/data/`)

| Path | Purpose |
|------|---------|
| `primitives.json` | Primitive mappings: primResID to name, python_code, terminals |
| `vilib/` | vi.lib VI mappings: terminals, python_code per category |
| `drivers/` | NI driver mappings (DAQmx, VISA, NI-DCPower, etc.) |
| `openg/` | OpenG library mappings |
| `labview-enums.json` | LabVIEW enum definitions |
| `labview_error_codes.json` | LabVIEW error code descriptions |

## Data Flow: VI to Python

### 1. Load VI
```python
from vipy.memory_graph import InMemoryVIGraph

graph = InMemoryVIGraph()
graph.load_vi(Path("Main.vi"), search_paths=[Path("libs/")])
```

### 2. Get VI Context
```python
context = graph.get_vi_context("Main.vi")

for op in context.operations:
    print(op.name, op.labels, op.node_type)
```

### 3. Generate Code
```python
from vipy.agent.codegen import build_module

code = build_module(context, "Main.vi")
```

## Key Types

All in `graph_types.py` - Pydantic models, use attribute access:

```python
class Operation(BaseModel):
    id: str
    name: str | None
    labels: list[str]      # ["SubVI"], ["Primitive"], ["Structure", "WhileLoop"]
    terminals: list[Terminal]
    node_type: str | None   # For primitives

class Terminal(BaseModel):
    id: str
    index: int
    direction: str          # "input" or "output"
    name: str | None
    lv_type: LVType | None

class Wire(BaseModel):
    source: WireEnd
    dest: WireEnd

class LVType:
    kind: str               # "primitive", "enum", "cluster", "array", "ring", "typedef_ref"
    underlying_type: str | None
    element_type: LVType | None
    fields: list[ClusterField] | None  # For clusters
```

## CLI Commands

```bash
vipy check                          # Check dependencies
vipy generate <vi> -o dir           # Deterministic AST code generation
vipy describe <vi>                  # Human-readable VI description
vipy docs <vi> <output_dir>         # Generate HTML documentation
vipy diff <vi_a> <vi_b>             # Compare two VI versions
vipy visualize <vi> -o graph.html   # Interactive graph visualization
vipy agent <vi> -o dir              # Full conversion with LLM validation loop
vipy explore [dir]                  # NiceGUI project explorer
vipy structure <path>               # Analyze .lvlib/.lvclass structure
vipy mcp                            # Start MCP server for IDE integration
vipy llm-generate <vi> -o dir       # Generate idiomatic Python via LLM
```

## MCP Tools

When running `vipy mcp`, these tools are available:

**Stateless** (subprocess-based, no shared state):

| Tool | Description |
|------|-------------|
| `analyze_vi` | Parse and describe VI structure |
| `generate_documents` | Create HTML docs for VIs/libraries |
| `generate_python` | AST code generation |

**Stateful** (graph persists across calls):

| Tool | Description |
|------|-------------|
| `load_vi` | Load VI into in-memory graph |
| `list_loaded_vis` | List loaded VIs |
| `get_vi_context` | Get full VI context (inputs, outputs, operations, wires) |
| `generate_ast_code` | Generate code from loaded VI |
| `describe_vi` | Human-readable VI description |
| `get_operations` | List operations in a VI |
| `get_dataflow` | Show wire connections |
| `get_structure` | Inspect a structure node (loop, case, sequence) |
| `get_constants` | List constant values |

## Testing

```bash
pytest                            # All tests
pytest tests/test_ast_builder.py  # AST generator tests
pytest tests/test_e2e_codegen.py  # End-to-end codegen tests
pytest -k "not real_vi"           # Skip tests needing real VIs
```

## Adding New Primitives

1. Run conversion, note the missing primResID in error
2. Look up primitive in LabVIEW documentation
3. Add to `src/vipy/data/primitives.json`:

```json
{
  "1234": {
    "name": "My Primitive",
    "category": "numeric",
    "python_code": "{a} + {b}",
    "inputs": [
      {"index": 0, "name": "a", "type": "DBL"}
    ],
    "outputs": [
      {"index": 2, "name": "result", "type": "DBL"}
    ]
  }
}
```

## Adding New VILib VIs

When `VILibResolutionNeeded` is raised:

1. Check the exception output for terminal names and wire indices
2. The wire indices from the caller show actual terminal positions
3. Add to appropriate `src/vipy/data/vilib/<category>.json`

## File Organization

```
vipy/
  src/vipy/
    parser/             # VI XML parsing (nodes, wires, types)
      nodes/            # Node-specific parsers
    graph/              # NetworkX graph layer
    agent/
      codegen/          # AST code generator
        nodes/          # Node-specific generators
    docs/               # HTML documentation generation
    mcp/                # MCP server
    memory_graph.py     # High-level graph + dependency tracking
    graph_types.py      # Pydantic models (Operation, Terminal, Wire, etc.)
    primitive_resolver.py
    vilib_resolver.py
  data/
    primitives.json
    vilib/              # VILib mappings by category
    drivers/            # NI driver mappings
    openg/              # OpenG library mappings
  scripts/              # Standalone scripts
  tests/                # Test suite
  samples/              # Sample VIs for testing
```

## Development

```bash
pip install -e ".[dev]"
ruff check .                # Lint
ruff format .               # Format
mypy src/                   # Type check
pytest                      # Test
```

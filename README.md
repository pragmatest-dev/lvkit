# vipy

Convert LabVIEW VIs to Python code without a LabVIEW license.

Uses [pylabview](https://github.com/mefistotelis/pylabview) to parse VI binary files into XML, then translates the dataflow graph to Python.

## Quick Start

```bash
# Install
pip install -e ".[dev]"

# Check dependencies (pylabview, ollama)
vipy check

# Convert a VI with all dependencies
vipy agent "path/to/Main.vi" -o outputs --search-path path/to/libraries

# Generate HTML documentation
vipy mcp  # Start MCP server, then use analyze_vi or generate_documents tools
```

## Architecture Overview

```
VI Binary (.vi)
     │
     ▼ pylabview (subprocess)
XML Files (_BDHb.xml, _FPHb.xml, .xml)
     │
     ▼ parser.py + extractor.py
ParsedVI (BlockDiagram, FrontPanel, Metadata)
     │
     ▼ memory_graph.py
InMemoryVIGraph (NetworkX graphs)
     │
     ├─▶ agent/codegen/ (AST-based, deterministic)
     │        │
     │        ▼
     │   Python Code (may have stubs)
     │
     └─▶ agent/claude_agent.py (LLM refinement)
              │
              ▼
         Refined Python Code
```

## Key Modules

### Parsing Layer (`src/vipy/`)

| File | Purpose |
|------|---------|
| `extractor.py` | Calls pylabview to extract VI → XML, caches results |
| `parser.py` | Parses XML into `ParsedVI` dataclass (BlockDiagram, FrontPanel, etc.) |
| `parser/models.py` | Core dataclasses: `ParsedNode`, `ParsedWire`, `ParsedConstant` |
| `parser/node_types.py` | Specific node types: `PrimitiveNode`, `SubVINode`, `StructureNode` |
| `blockdiagram.py` | Decode constants from hex, parse connector pane |

### Graph Layer (`src/vipy/`)

| File | Purpose |
|------|---------|
| `memory_graph.py` | `InMemoryVIGraph` - NetworkX-based VI graph with dependency tracking |
| `graph_types.py` | Typed dataclasses: `Operation`, `Wire`, `Terminal`, `Constant`, `LVType` |
| `primitive_resolver.py` | Maps primResID → Python implementation from `data/primitives/*.json` |
| `vilib_resolver.py` | Maps vi.lib VIs → Python implementations from `data/vilib/*.json` |

### Agent Layer (`src/vipy/agent/`)

| File | Purpose |
|------|---------|
| `loop.py` | Main conversion loop - iterates VIs in dependency order |
| `codegen/` | AST-based Python code generator (deterministic, no LLM) |
| `codegen/builder.py` | `build_module()` - entry point for AST generation |
| `codegen/context.py` | Tracks imports, bindings, dataflow during generation |
| `codegen/nodes/` | Node-specific generators (primitives, subvis, structures) |
| `claude_agent.py` | Anthropic API client with tool-use for refinement |
| `skeleton.py` | Generate skeleton code for LLM to complete |
| `validator.py` | Validate generated code (syntax, imports, completeness) |
| `strategies/` | Different conversion strategies (baseline=AST, skeleton=LLM, etc.) |

### MCP Server (`src/vipy/mcp/`)

| File | Purpose |
|------|---------|
| `server.py` | MCP server exposing tools to Claude Code |
| `tools.py` | Tool implementations (analyze_vi, generate_python, etc.) |
| `schemas.py` | Shared tool definitions, Pydantic models for results |

### Data Files (`data/`)

| Directory | Purpose |
|-----------|---------|
| `primitives/` | JSON mappings: primResID → name, python_code, terminals |
| `vilib/` | JSON mappings: vi.lib VI names → terminals, python_code |

## Data Flow: VI to Python

### 1. Load VI
```python
from vipy.memory_graph import InMemoryVIGraph

graph = InMemoryVIGraph()
graph.load_vi(Path("Main.vi"), search_paths=[Path("libs/")])
```

### 2. Get VI Context
```python
# Returns dict with dataclass instances (NOT dicts!)
context = graph.get_vi_context("Main.vi")

# Access with attributes, not .get()
for op in context["operations"]:
    print(op.name, op.labels, op.primResID)
```

### 3. Generate Code
```python
from vipy.agent.codegen import build_module

code = build_module(context, "Main.vi")
```

## Key Dataclasses

All in `graph_types.py` - use attribute access, not `.get()`:

```python
@dataclass
class Operation:
    id: str
    name: str | None
    labels: list[str]      # ["SubVI"], ["Primitive"], ["Structure", "WhileLoop"]
    terminals: list[Terminal]
    primResID: int | None  # For primitives

@dataclass
class Terminal:
    id: str
    index: int | None
    direction: str         # "input" or "output"
    name: str | None
    type: str | None
    lv_type: LVType | None

@dataclass
class Wire:
    id: str
    from_terminal_id: str
    to_terminal_id: str
    from_parent_id: str | None
    to_parent_id: str | None

@dataclass
class LVType:
    base: str              # "I32", "DBL", "String", "Cluster", "Array"
    element_type: LVType | None
    fields: list[tuple[str, LVType]] | None  # For clusters
```

## Primitive Resolution

Primitives are LabVIEW's built-in operations (Add, Subtract, BuildPath, etc.).

```python
from vipy.primitive_resolver import get_resolver

resolver = get_resolver()
prim = resolver.resolve(1419)  # Build Path primitive
print(prim.name)        # "Build Path"
print(prim.python_code) # "Path(base) / name"
```

Data lives in `data/primitives/*.json`:
```json
{
  "1419": {
    "name": "Build Path",
    "python_code": "Path({base_path}) / {name}",
    "inputs": [
      {"index": 0, "name": "base_path", "type": "Path"},
      {"index": 1, "name": "name", "type": "String"}
    ],
    "outputs": [
      {"index": 2, "name": "built_path", "type": "Path"}
    ]
  }
}
```

## VILib Resolution

vi.lib VIs are LabVIEW's standard library.

```python
from vipy.vilib_resolver import get_resolver

resolver = get_resolver()
vi = resolver.resolve("Error Cluster From Error Code.vi")
print(vi.python_code)  # Template or None
for term in vi.terminals:
    print(term.name, term.index, term.direction)
```

## Adding New Primitives

1. Run conversion, note the missing primResID in error
2. Look up primitive in LabVIEW documentation
3. Add to appropriate `data/primitives/*.json`:

```json
{
  "1234": {
    "name": "My Primitive",
    "category": "numeric",
    "python_code": "{a} + {b}",
    "inputs": [
      {"index": 0, "name": "a", "type": "DBL"},
      {"index": 1, "name": "b", "type": "DBL"}
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
3. Add to appropriate `data/vilib/*.json`:

```json
{
  "Some VI.vi": {
    "category": "error",
    "terminals": [
      {"index": 0, "name": "error in", "direction": "input", "type": "ErrorCluster"},
      {"index": 1, "name": "error out", "direction": "output", "type": "ErrorCluster"}
    ],
    "python_code": "error_out = error_in"
  }
}
```

## Strategies

Different approaches to code generation (`agent/strategies/`):

| Strategy | Description |
|----------|-------------|
| `baseline` | AST-based deterministic (default) - always valid syntax |
| `skeleton` | Generate skeleton, LLM fills in logic |
| `tool_calling` | LLM can call tools to gather info |
| `rich_feedback` | Include SubVI code when errors reference them |
| `two_phase` | Phase 1: describe dataflow, Phase 2: write code |

## CLI Commands

```bash
vipy check              # Check dependencies
vipy agent <vi> -o dir  # Full conversion with agent loop
vipy summarize <xml>    # Debug: show VI summary
vipy explore            # NiceGUI explorer for outputs
vipy mcp                # Start MCP server for IDE integration
```

## MCP Tools

When running `vipy mcp`, these tools are available:

| Tool | Description |
|------|-------------|
| `analyze_vi` | Parse VI structure (stateless) |
| `generate_documents` | Create HTML docs (stateless) |
| `generate_python` | AST code generation (stateless) |
| `load_vi` | Load VI into graph (stateful) |
| `list_loaded_vis` | List loaded VIs (stateful) |
| `get_vi_context` | Get VI context (stateful) |
| `generate_ast_code` | Generate code from loaded VI (stateful) |

## Testing

```bash
pytest                           # All tests
pytest tests/test_ast_builder.py # AST generator tests
pytest -k "not real_vi"          # Skip tests needing real VIs
```

## Common Issues

### "Operation object has no attribute 'get'"
You're using dict access on a dataclass. Use `op.name` not `op.get("name")`.

### Missing SubVI
Add `--search-path` pointing to the library directory.

### Unknown primitive
Check `data/primitives/` for the primResID. Add it if missing.

### VILib resolution needed
Check exception output for terminal indices from caller's dataflow. Add to `data/vilib/`.

### Ollama timeout
The LLM-based strategies need Ollama running. Use `baseline` strategy for no LLM.

## File Organization

```
vipy/
├── src/vipy/
│   ├── parser.py           # VI XML parsing
│   ├── memory_graph.py     # In-memory graph
│   ├── graph_types.py      # Core dataclasses
│   ├── primitive_resolver.py
│   ├── vilib_resolver.py
│   ├── agent/
│   │   ├── loop.py         # Main conversion loop
│   │   ├── codegen/        # AST code generator
│   │   ├── claude_agent.py # Anthropic API
│   │   └── strategies/     # Conversion strategies
│   └── mcp/
│       ├── server.py       # MCP server
│       └── tools.py        # Tool implementations
├── data/
│   ├── primitives/         # Primitive mappings
│   └── vilib/              # VILib mappings
├── scripts/                # Standalone scripts
├── tests/                  # Test suite
└── samples/                # Sample VIs for testing
```

## Development

```bash
pip install -e ".[dev]"
ruff check .                # Lint
ruff format .               # Format
mypy src/                   # Type check
pytest                      # Test
```

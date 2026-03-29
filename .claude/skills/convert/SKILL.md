---
name: convert
description: Convert LabVIEW VI files to Python using vipy. Generates mechanical translation, resolves all errors, then cleans up to idiomatic Python. Also handles documentation generation and MCP server.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# vipy - LabVIEW VI to Python Conversion

Convert LabVIEW VI files to Python without requiring a LabVIEW license.

## Workflow

The conversion is a **loop**: generate → resolve unknowns → re-generate → clean up. Repeat until 0 errors, then make it idiomatic.

### Step 1: Generate Python (mechanical translation)

```bash
# Single VI
vipy generate "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted

# LabVIEW class
vipy generate "path/to/MyClass.lvclass" -o outputs --search-path samples/OpenG/extracted

# LabVIEW library
vipy generate "path/to/MyLib.lvlib" -o outputs --search-path samples/OpenG/extracted

# Directory of VIs
vipy generate "path/to/vi_folder/" -o outputs --search-path samples/OpenG/extracted
```

Check the summary at the end: `error: N`. If N > 0, proceed to Step 2.

### Step 2: Resolve unknowns (loop until 0 errors)

If the error summary shows errors, resolve them ONE AT A TIME:

- `PrimitiveResolutionNeeded` → invoke `/resolve-primitive` skill with the primResID
- `TerminalResolutionNeeded` → invoke `/resolve-primitive` skill (terminal mismatch on known primitive)
- `VILibResolutionNeeded` → invoke `/resolve-vilib` skill with the VI name
- `TypeResolutionNeeded` → investigate nMux field indexing (flattened depth-first index vs typedef fields)

After resolving each unknown, re-run `vipy generate`. Repeat until `error: 0`.

**Note:** Resolving one error may uncover NEW errors from VIs that previously couldn't proceed. This is expected — keep looping.

### Step 3: Clean up to idiomatic Python

After 0 errors, the generated code is correct but mechanical. For each generated `.py` file:

1. **Get the VI description** for context:
```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi, describe_operations
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_vi(g, list(g.list_vis())[0]))
"
```

2. **Read the generated Python file**

3. **Rewrite idiomatically** following the rules below

4. **Validate**: `ast.parse(code)` succeeds, same function signature

### Safe to change (cosmetic):
- **Variable names** — `daqmx_create_task_task_out` → `task`
- **Garbled unicode names** — fix encoding artifacts
- **Unused imports** — remove
- **Add docstrings** — describe what the function does
- **String formatting** — `500 / 1000` → `0.5`
- **Context managers** — wrap resource lifecycle in `try/finally` or `with`
- **List comprehensions** — replace explicit loops where clear
- **Exception handling** — replace held-error patterns with try/except

### NEVER change (behavioral):
- **Parallel branches** — `ThreadPoolExecutor` blocks represent real LabVIEW parallelism
- **Operation order** — the topological sort is correct
- **Loop structure** — `while not stop` preserves stop terminal semantics
- **Function parameters** — front panel controls, don't change types/defaults
- **Return values** — front panel indicators, don't remove outputs
- **Error cluster handling** — if present, the held-error pattern is intentional

### Step 4: Generate documentation (optional)

```bash
vipy docs "path/to/file.vi" outputs/docs --search-path samples/OpenG/extracted
```

Creates a browsable HTML site with cross-referenced VI documentation.

## Commands

```bash
vipy generate <path> -o dir       # AST-based Python generation (primary)
vipy llm-generate <path> -o dir   # LLM-based idiomatic generation
vipy docs <path> <dir>            # HTML documentation
vipy check                        # Check dependencies
vipy structure <path>             # Show project structure
vipy mcp                          # Start MCP server for IDE integration
```

## MCP Tools (for IDE integration)

The MCP server (`vipy mcp`) provides tools for interactive exploration:

**Session:**
- `load_vi` — Load VI into persistent graph
- `list_loaded_vis` — List loaded VIs

**Exploration:**
- `describe_vi` — Human-readable VI overview (signature, SubVIs, control flow)
- `get_operations` — Execution order with nested structures
- `get_dataflow` — Wire connections, optionally filtered by operation
- `get_structure` — Case/loop/sequence details
- `get_constants` — Constant values

**Generation:**
- `generate_ast_code` — Deterministic Python from loaded VI
- `generate_python` — Full pipeline: load + generate + write files
- `get_vi_context` — Raw VI context as JSON

**Documentation:**
- `generate_documents` — Create HTML documentation
- `analyze_vi` — Parse and return VI structure

## Related Skills

- `/resolve-primitive` — Resolve unknown LabVIEW primitives
- `/resolve-vilib` — Resolve unknown vilib VIs
- `/describe-vi` — Describe a VI's graph (CLI-based, no MCP)
- `/idiomatic` — Rewrite mechanical Python to idiomatic code
- `/judge-output` — Evaluate generated code quality
- `/trace-bug` — Debug codegen issues from output to root cause
- `/explore-vi` — Interactive VI exploration

## Troubleshooting

- **Missing SubVI**: Add `--search-path` pointing to the VI's library directory
- **JKI naming**: VIs named `Name__LibName.vi` — add the library source as a search path
- **Type errors**: Check that code uses dataclass attributes, not `.get()`
- **Import issues**: Check the generated `__init__.py` and import paths

---
name: lvpy-convert
description: Convert LabVIEW VI files to Python using lvpy. Generates mechanical translation, resolves all errors, then cleans up to idiomatic Python. Also handles documentation generation and MCP server.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# lvpy - LabVIEW VI to Python Conversion

Convert LabVIEW VI files to Python without requiring a LabVIEW license.

## Workflow

The conversion is a **loop**: generate → resolve unknowns → re-generate → clean up. Repeat until 0 errors, then make it idiomatic.

Substitute the placeholders below with the user's actual paths:

- `<vi-path>` — the .vi, .lvclass, .lvlib, or directory you're converting
- `<output-dir>` — where generated Python should land
- `<library-path>` — additional search path for SubVIs (repeat the flag for multiple)

### Step 1: Generate Python (mechanical translation)

```bash
# Single VI
lvpy generate "<vi-path>" -o "<output-dir>" --search-path "<library-path>"

# LabVIEW class
lvpy generate "<vi-path>.lvclass" -o "<output-dir>" --search-path "<library-path>"

# LabVIEW library
lvpy generate "<vi-path>.lvlib" -o "<output-dir>" --search-path "<library-path>"

# Directory of VIs
lvpy generate "<vi-folder>/" -o "<output-dir>" --search-path "<library-path>"
```

Check the summary at the end: `error: N`. If N > 0, proceed to Step 2.

### Step 2: Resolve unknowns (loop until 0 errors)

If the error summary shows errors, resolve them ONE AT A TIME:

- `PrimitiveResolutionNeeded` → invoke `/lvpy-resolve-primitive` skill with the primResID
- `TerminalResolutionNeeded` → invoke `/lvpy-resolve-primitive` skill (terminal mismatch on known primitive)
- `VILibResolutionNeeded` → invoke `/lvpy-resolve-vilib` skill with the VI name
- `TypeResolutionNeeded` → investigate nMux field indexing (flattened depth-first index vs typedef fields)

After resolving each unknown, re-run `lvpy generate`. Repeat until `error: 0`.

**Note:** Resolving one error may uncover NEW errors from VIs that previously couldn't proceed. This is expected — keep looping.

**Alternative — soft mode:** if you'd rather defer all unknowns to runtime instead of fixing them up front, pass `--placeholder-on-unresolved` to `lvpy generate`. Each unknown primitive or vi.lib VI becomes an inline `raise PrimitiveResolutionNeeded(...)` / `raise VILibResolutionNeeded(...)` in the generated Python with full diagnostic context. The build succeeds; runtime fails on the unresolved call. Useful if you want to fix the gaps contextually in the Python rather than via JSON mappings.

### Step 3: Clean up to idiomatic Python

After 0 errors, the generated code is correct but mechanical. For each generated `.py` file, invoke `/lvpy-idiomatic` to rewrite it.

If you want context first:

```bash
lvpy describe "<vi-path>" --search-path "<library-path>"
```

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
lvpy docs "<vi-path>" "<output-dir>/docs" --search-path "<library-path>"
```

Creates a browsable HTML site with cross-referenced VI documentation.

## Commands

```bash
lvpy generate <path> -o dir       # AST-based Python generation (primary)
lvpy llm-generate <path> -o dir   # LLM-based idiomatic generation
lvpy docs <path> <dir>            # HTML documentation
lvpy describe <path>              # Human-readable VI overview
lvpy diff <vi_a> <vi_b>           # Compare two VI versions
lvpy visualize <path>             # Interactive graph visualization
lvpy structure <path>             # Show project structure
lvpy check                        # Check dependencies
lvpy init                         # Create .lvpy/ project store
lvpy mcp                          # Start MCP server for IDE integration
```

## MCP Tools (for IDE integration)

The MCP server (`lvpy mcp`) provides tools for interactive exploration:

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

- `/lvpy-resolve-primitive` — Resolve unknown LabVIEW primitives
- `/lvpy-resolve-vilib` — Resolve unknown vilib VIs
- `/lvpy-describe` — Describe a VI's graph (CLI-based, no MCP)
- `/lvpy-idiomatic` — Rewrite mechanical Python to idiomatic code

## Troubleshooting

- **Missing SubVI**: Add `--search-path` pointing to the VI's library directory
- **JKI naming**: VIs named `Name__LibName.vi` — add the library source as a search path
- **Type errors**: Check that code uses dataclass attributes, not `.get()`
- **Import issues**: Check the generated `__init__.py` and import paths

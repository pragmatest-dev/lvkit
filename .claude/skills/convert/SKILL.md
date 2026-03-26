---
name: convert
description: Convert LabVIEW VI files to Python using vipy. Use when converting VIs, running the agent, analyzing VI structure, or generating documentation. Also handles MCP server for IDE integration.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# vipy - LabVIEW VI to Python Conversion

Convert LabVIEW VI files to Python without requiring a LabVIEW license.

## Workflow

The conversion is a **loop**: generate → resolve unknowns → re-generate → polish. Repeat until 0 errors.

### Step 1: Generate Python

```bash
# Single VI
vipy generate "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted

# LabVIEW class
vipy generate "path/to/MyClass.lvclass" -o outputs --search-path samples/OpenG/extracted

# Directory of VIs
vipy generate "path/to/vi_folder/" -o outputs --search-path samples/OpenG/extracted
```

### Step 2: Resolve unknowns (loop until 0 errors)

If the error summary shows errors, resolve them ONE AT A TIME:

- `TerminalResolutionNeeded` → invoke `/resolve-primitive` skill with the primResID
- `VILibResolutionNeeded` → invoke `/resolve-vilib` skill with the VI name

After resolving each unknown, re-run `vipy generate`. Repeat until 0 errors.

### Step 3: Polish

Only after 0 errors. Read the generated code and improve cosmetics (see Polishing Rules below). NEVER change execution semantics.

### Step 4: Generate documentation

```bash
vipy docs "path/to/file.vi" output_dir --search-path samples/OpenG/extracted
```

## Commands

```bash
vipy generate <path> -o dir   # AST-based Python generation (primary)
vipy docs <path> <dir>        # HTML documentation
vipy check                    # Check dependencies (pylabview, etc.)
vipy structure <path>         # Show project structure
vipy mcp                      # Start MCP server for IDE integration
vipy explore                  # NiceGUI explorer for converted VIs
vipy agent <path> -o dir      # Legacy: LLM validation loop (fallback)
```

## MCP Tools

The MCP server (`vipy mcp`) provides tools for IDE integration:
- `load_vi` — Load VI into persistent in-memory graph
- `list_loaded_vis` — List loaded VIs
- `get_vi_context` — Get full VI context (inputs, outputs, operations, wires)
- `generate_ast_code` — Generate Python from loaded VI
- `generate_python` — Full pipeline: load + generate + write files
- `generate_documents` — Create HTML documentation
- `analyze_vi` — Parse and return VI structure

## Key Data Structures

All types in `src/vipy/graph_types.py`:
```python
VIContext    # Complete VI context: name, inputs, outputs, operations, constants
Operation   # SubVI or primitive: id, name, labels, terminals, case_frames
Terminal    # Connection point: id, index, direction, name, lv_type
Wire        # Edge: source (WireEnd), dest (WireEnd)
Constant    # Value: id, value, lv_type, raw_value
```

**Everything is typed dataclasses.** Use attribute access (`ctx.operations`, `op.name`), never `.get()`.

## Output Expectations

Generated Python is a **functional transliteration**, not idiomatic code:
- Preserves LabVIEW's dataflow semantics
- Verbose variable names from terminal labels
- Explicit parallel branches with `concurrent.futures`
- Shift registers become explicit assignments
- Flat sequences become sequential code blocks

**This is intentional.** Working-but-awkward Python is easier for AI to refactor than generating from scratch. The workflow: VI → AST → working Python → AI cleanup → idiomatic code.

## Polishing Rules

When improving generated code, **preserve execution semantics**:

### Safe to change (cosmetic):
- **Variable names** — `daqmx_create_task_task_out` → `task`
- **Garbled unicode names** — fix encoding artifacts in field/variable names
- **Unused imports** — remove imports nothing references
- **Add docstrings** — describe what the function does
- **String formatting** — `500 / 1000` → `0.5`
- **Context managers** — wrap task.start()/stop()/close() in `try/finally` or `with`

### NEVER change (behavioral):
- **Parallel branches** — `ThreadPoolExecutor` blocks represent real LabVIEW parallelism. Do NOT serialize them. Independent operations within a tier execute concurrently.
- **Operation order** — the topological sort is correct. Do NOT reorder operations.
- **Loop structure** — `while not stop` preserves LabVIEW's stop terminal semantics. Do NOT add defaults like `stop=False` or restructure the loop.
- **Function parameters** — these are front panel controls. Do NOT add defaults, remove params, or change types.
- **Return values** — these are front panel indicators. Do NOT remove outputs.
- **Error cluster handling** — if error terminals are present, the held-error pattern is intentional.

### Judgment calls (ask if unsure):
- **Removing a `time.sleep` that looks unnecessary** — it might be a deliberate delay for hardware timing
- **Simplifying a case structure** — the branches may have side effects
- **Inlining a SubVI call** — the SubVI may be reused elsewhere
- **Changing data types** — LabVIEW types map to specific Python types for a reason

## Resolving Primitives

Use the `/resolve-primitive` skill. It has the complete step-by-step process for identifying and verifying unknown primitives against the LabVIEW documentation.

## Resolving vilib VIs

Use the `/resolve-vilib` skill. It has the complete step-by-step process for looking up vilib VI terminals in the LabVIEW documentation.

## Troubleshooting

- **Missing SubVI**: Add `--search-path` pointing to the VI's library directory
- **Type errors**: Check that code uses dataclass attributes, not `.get()`
- **Import issues**: Check the generated `__init__.py` and import paths

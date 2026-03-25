---
name: convert
description: Convert LabVIEW VI files to Python using vipy. Use when converting VIs, running the agent, analyzing VI structure, or generating documentation. Also handles MCP server for IDE integration.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# vipy - LabVIEW VI to Python Conversion

Convert LabVIEW VI files to Python without requiring a LabVIEW license.

## Workflow

The conversion is a two-step process: **deterministic AST codegen** produces working Python, then **AI review** improves it.

### Step 1: Generate Python

```bash
# Single VI
vipy generate "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted

# LabVIEW class
vipy generate "path/to/MyClass.lvclass" -o outputs --search-path samples/OpenG/extracted

# Directory of VIs
vipy generate "path/to/vi_folder/" -o outputs --search-path samples/OpenG/extracted
```

### Step 2: Review & improve

Read the generated output. The AST codegen produces **working but non-idiomatic Python**. Improve it:

1. **Check the error summary** — 0 errors means clean generation
2. **Read the generated files** — look for awkward variable names, verbose patterns
3. **Handle unresolved VIs** — if errors mention `VILibResolutionNeeded` or `TerminalResolutionNeeded`:
   - Read the diagnostic info (terminal names, wire types, indices)
   - Add the missing VI info to `data/vilib/` or `data/primitives-codegen.json`
   - Re-run `vipy generate`
4. **Refactor** — simplify logic, improve variable names, use Pythonic idioms
5. **Verify** — run the generated code, check imports resolve

### Step 3: Generate documentation

```bash
vipy docs "path/to/file.vi" output_dir --search-path samples/OpenG/extracted
```

Produces HTML with Mermaid dataflow diagrams, parameter tables, and cross-references.

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

## Resolving Unknowns

When generation encounters unknown VIs or primitives:
1. `VILibResolutionNeeded` — missing vilib VI terminal definitions
   - Check `data/vilib/*.json` for the VI
   - Use the reported wire types and indices to fill in terminal info
2. `TerminalResolutionNeeded` — missing primitive terminal mapping
   - Check `data/primitives-codegen.json` for the primResID
   - Look up in `docs/labview_programming_reference_manual.pdf`
3. Re-run `vipy generate` after adding the data

## Troubleshooting

- **Missing SubVI**: Add `--search-path` pointing to the VI's library directory
- **Type errors**: Check that code uses dataclass attributes, not `.get()`
- **Import issues**: Check the generated `__init__.py` and import paths

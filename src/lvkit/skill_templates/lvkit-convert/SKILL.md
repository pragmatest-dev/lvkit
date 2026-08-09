---
name: lvkit-convert
description: Convert LabVIEW VI files to Python using lvkit. Generates mechanical translation, resolves all errors, then cleans up to idiomatic Python. Also handles documentation generation and MCP server.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# lvkit - LabVIEW VI to Python Conversion

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
lvkit generate "<vi-path>" -o "<output-dir>" --search-path "<library-path>"

# LabVIEW class
lvkit generate "<vi-path>.lvclass" -o "<output-dir>" --search-path "<library-path>"

# LabVIEW library
lvkit generate "<vi-path>.lvlib" -o "<output-dir>" --search-path "<library-path>"

# Directory of VIs
lvkit generate "<vi-folder>/" -o "<output-dir>" --search-path "<library-path>"
```

Check the summary at the end: `error: N`. If N > 0, proceed to Step 2.

### Step 2: Resolve unknowns (loop until 0 errors)

If the error summary shows errors, resolve them ONE AT A TIME:

- `PrimitiveResolutionNeeded` → invoke `/lvkit-resolve-primitive` skill with the primResID
- `TerminalResolutionNeeded` → invoke `/lvkit-resolve-primitive` skill (terminal mismatch on known primitive)
- `VILibResolutionNeeded` → invoke `/lvkit-resolve-vilib` skill with the VI name
- `TypeResolutionNeeded` → investigate nMux field indexing (flattened depth-first index vs typedef fields)

After resolving each unknown, re-run `lvkit generate`. Repeat until `error: 0`.

**Note:** Resolving one error may uncover NEW errors from VIs that previously couldn't proceed. This is expected — keep looping.

**Alternative — soft mode:** if you'd rather defer all unknowns to runtime instead of fixing them up front, pass `--placeholder-on-unresolved` to `lvkit generate`. Each unknown primitive or vi.lib VI becomes an inline `raise PrimitiveResolutionNeeded(...)` / `raise VILibResolutionNeeded(...)` in the generated Python with full diagnostic context. The build succeeds; runtime fails on the unresolved call. Useful if you want to fix the gaps contextually in the Python rather than via JSON mappings.

### Step 3: Clean up to idiomatic Python

After 0 errors, the generated code is correct but mechanical. For each generated `.py` file, invoke `/lvkit-idiomatic` to rewrite it.

If you want context first:

```bash
lvkit describe "<vi-path>" --search-path "<library-path>"
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
lvkit docs "<vi-path>" "<output-dir>/docs" --search-path "<library-path>"
```

Creates a browsable HTML site with cross-referenced VI documentation.

## Commands

```bash
lvkit generate <path> -o dir       # AST-based Python generation (primary)
lvkit llm-generate <path> -o dir   # LLM-based idiomatic generation
lvkit docs <path> <dir>            # HTML documentation
lvkit describe <path>              # Human-readable VI overview
lvkit diff <vi_a> <vi_b>           # Compare two VI versions
lvkit visualize <path>             # Interactive graph visualization
lvkit structure <path>             # Show project structure
lvkit check                        # Check dependencies
lvkit init                         # Create .lvkit/ project store
lvkit mcp                          # Start MCP server for IDE integration
```

## MCP Tools (for IDE integration)

**A LabVIEW project (`.vi`/`.lvclass`/`.lvlib`/`.lvproj`) is a binary format —
`grep`/`cat`/`find` and ad-hoc scripts return nothing usable.** lvkit is the
only way to read it, so for any question about a LabVIEW repo reach for lvkit
first; never grep a `.vi`.

When the lvkit MCP server (`lvkit mcp`) is connected, **prefer these tools over
the CLI**; otherwise use the `lvkit …` commands above (each has a CLI
equivalent — including `lvkit index` and `lvkit query` for the project index).

**Project index** — index a repo once (`index(project)`), then ask project-wide
questions in one call each (no per-VI round trips, no name-collision loss):
- `query(sql)` — read-only SQL over the curated views (`vi`, `terminal`,
  `constant`, `call`, `type_use`, `class_fact`); `query_schema()` lists the
  columns. Returns the answer (a `GROUP BY` histogram), not a row dump — this
  replaces the old `find_*`/`get_signatures` read tools. Also available as
  `lvkit query <path> "<SELECT>"` on the CLI.
- `get_callers` / `get_callees` / `blast_radius` — call graph & change impact
  (CLI equivalents: `lvkit callers` / `callees` / `blast-radius`)
- `visualize_project` — self-contained Mermaid call graph / class tree

**Deep single-VI** — pass a `vi_path`, loaded on demand (no `load` step):
- `describe` / `get_operations` / `get_dataflow` / `get_structure` /
  `get_constants` / `get_context`
- `generate_ast_code` — deterministic Python for one VI

**Generation:**
- `generate_python` — full pipeline + a needs-review workflow
- `generate_documents` — static HTML documentation site

## Related Skills

- `/lvkit-resolve-primitive` — Resolve unknown LabVIEW primitives
- `/lvkit-resolve-vilib` — Resolve unknown vilib VIs
- `/lvkit-describe` — Describe a VI's graph (CLI-based, no MCP)
- `/lvkit-idiomatic` — Rewrite mechanical Python to idiomatic code

## Troubleshooting

- **Missing SubVI**: Add `--search-path` pointing to the VI's library directory
- **JKI naming**: VIs named `Name__LibName.vi` — add the library source as a search path
- **Type errors**: Check that code uses dataclass attributes, not `.get()`
- **Import issues**: Check the generated `__init__.py` and import paths

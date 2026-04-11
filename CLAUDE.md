# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

lvpy converts LabVIEW VI files to Python code without requiring a LabVIEW license. It uses [pylabview](https://github.com/mefistotelis/pylabview) as the core parser for reading VI file formats.

## Commands

Always use `uv run` — it automatically activates the project venv without a separate activation step.

```bash
# Install with dev dependencies
uv sync

# Run all tests
uv run pytest

# Run a single test
uv run pytest tests/test_parser.py::test_parse_vi

# Lint
uv run ruff check .

# Type check
uv run python -m pyright src/

# Scripts
uv run python scripts/generate_python.py "path/to/file.vi" -o outputs
```

## Architecture

The conversion pipeline:

1. **Binary extraction**: pylabview (subprocess) reads the VI binary → XML files (`_BDHb.xml`, `_FPHb.xml`, `.xml`)
2. **Parsing** (`parser/`): `parse_vi()` converts XML → `ParsedVI` dataclasses (nodes, wires, constants, types, front panel)
3. **Graph construction** (`graph/`): `ParsedVI` → `InMemoryVIGraph` NetworkX multi-digraph. `get_vi_context()` returns `VIContext`.
4. **Code generation** (`codegen/`): `build_module(vi_context, vi_name)` walks `VIContext` → Python `ast.Module` → source string
5. **Orchestration** (`pipeline.py`): multi-VI load ordering, dependency resolution, file output

### Key Modules

- `src/lvpy/parser/` — XML → `ParsedVI` dataclasses (nodes, wires, constants, types)
- `src/lvpy/graph/` — `InMemoryVIGraph`, graph construction, queries, operations
- `src/lvpy/models.py` — shared type definitions used by parser, graph, and codegen (`LVType`, `Operation`, `Frame`, `Terminal`, `Tunnel`, etc.)
- `src/lvpy/graph/models.py` — graph/codegen-only types (`GraphNode` hierarchy, `VIContext`, `Wire`, query/info types, `BranchPoint`)
- `src/lvpy/codegen/builder.py` — `build_module()` entry point for AST generation
- `src/lvpy/pipeline.py` — orchestrates multi-VI generation
- `src/lvpy/cli.py` — command-line interface
- `src/lvpy/mcp/` — MCP server (12 tools)

### Standard Test Command

```bash
# Single VI
python scripts/generate_python.py "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted

# LabVIEW class (.lvclass)
python scripts/generate_python.py "path/to/MyClass.lvclass" -o outputs --search-path samples/OpenG/extracted

# LabVIEW library (.lvlib)
python scripts/generate_python.py "path/to/MyLib.lvlib" -o outputs --search-path samples/OpenG/extracted

# Directory of VIs
python scripts/generate_python.py "path/to/vi_folder/" -o outputs --search-path samples/OpenG/extracted
```

## Error Handling Strategy

LabVIEW uses error clusters passed through wires. Python uses exceptions. The conversion strategy:

1. **No error clusters → Natural Python exceptions** - Just let exceptions propagate
2. **Error clusters + parallel branches → Held error model**

### Held Error Model

When a VI has parallel branches AND error terminals, we use this pattern:

```python
def my_vi(input_data):
    _held_error = None  # Track errors from branches

    # Parallel branch 0
    try:
        branch_0_result = branch_0_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None

    # Parallel branch 1
    try:
        branch_1_result = branch_1_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None

    # Merge point - raise first error
    if _held_error:
        raise _held_error

    return result
```

This preserves LabVIEW's semantics where:
- Branches continue executing even if one errors
- First error is preserved and raised at merge point
- All branches get a chance to clean up

Implementation: `src/lvpy/codegen/error_handler.py`

## Adding New Primitives

LabVIEW primitives are identified by `primResID`. When a conversion fails with `PrimitiveResolutionNeeded`, add an entry to `src/lvpy/data/primitives.json`:

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

Use the caller's dataflow in the exception output to determine correct terminal indices — do not guess.

## Adding New VILib VIs

When a conversion fails with `VILibResolutionNeeded`, add the VI to the appropriate `src/lvpy/data/vilib/<category>.json`. The exception output shows terminal names from XML and actual wire indices from the caller — use those indices to fill in the `"index"` field for each terminal.

**Workflow:**
1. Run the code generator; note the exception output
2. Match "Wire types from dataflow" indices to the terminal names listed
3. Add entries to the vilib JSON with the correct `"index"` values
4. Re-run to verify

## VILib Terminal Resolution Workflow

When the code generator encounters a vilib VI with missing terminal indices, it raises a `VILibResolutionNeeded` exception. This is intentional - use the caller's dataflow info to fill in the missing indices.

**Workflow:**
1. Run the code generator (e.g., `scripts/generate_python.py`)
2. When a VI lacks terminal index info, exception is raised with:
   - Terminal names from the vilib JSON
   - Wire types from the caller's dataflow (shows actual indices being used)
   - PDF documentation reference
3. Use the **caller's dataflow** to determine correct indices - DO NOT GUESS
4. Update `data/vilib/<category>.json` with the correct terminal indices
5. Re-run to verify

**Example exception output:**
```
VILib resolution needed for 'Error Cluster From Error Code.vi'.

Terminal names from XML:
  - is warning? (False)
  - error code (0)
  ...

Wire types from dataflow:
  - idx_0 (input)    <- These are the actual indices from the caller
  - idx_1 (output)
  ...
```

The "Wire types from dataflow" section shows what terminal indices the caller is actually using. Match these to the terminal names and add `"index": N` to each terminal in the JSON.

## Code Style

- Python 3.10+ required
- Ruff for linting (rules: E, F, I, UP)
- mypy with strict mode for type checking
- Line length: 88 characters
- **Prefer dataclasses over dicts** - Use typed dataclasses from `models.py` or `graph/models.py` instead of raw dictionaries. Use attribute access (`obj.field`) not `.get("field")`

## Output Directory

**ALWAYS use `outputs/` for generated code.** NEVER use `/tmp/` or any other temporary directory.

```bash
# CORRECT - outputs go to outputs/ folder in the repo
python scripts/generate_python.py "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted

# WRONG - never use /tmp/
python scripts/generate_python.py "path/to/file.vi" -o /tmp/test
```

The `outputs/` folder is in the repo and accessible from the editor. Temporary directories are not.

## Bash Commands

**NEVER use combined commands.** Always use single commands, one per Bash call.

Bad:
```bash
cd /tmp && python app.py
rm -rf /tmp/foo; python script.py
```

Good:
```bash
# First call
rm -rf /tmp/foo
# Second call
python script.py
```

This ensures permission patterns match correctly.

## Plan Execution Rules

**After a plan is approved, it is a contract.** If ANY aspect of execution differs from the approved plan — different approach, different file structure, different abstraction — you MUST:
1. STOP writing code immediately
2. Re-enter plan mode
3. Explain what you found that changes the approach
4. Get a new approval before continuing

NEVER silently change an approved plan. NEVER say "actually this is simpler" and keep going. The user approved a specific design. Changing it without discussion wastes hours.

## Planning Quality Rules

**During planning, READ the actual code before proposing changes.** Do not describe what you think the code looks like — read it. Specifically:
- If the plan says "convert class to function" — read the class first. Is it 10 lines or 400 lines with 15 methods?
- If the plan says "rename X to Y" — grep for X first. Is it in 5 files or 50?
- If the plan says "add field Z" — check if Z already exists elsewhere under a different name
- If the plan creates new types — check for existing types that do the same thing

**Run /design-review on proposed changes during planning.** Catch god objects, duplicate types, wrong naming, and code smells BEFORE the plan is approved, not after execution begins.

## Commit Rules

**NEVER commit broken, regressed, or non-working code.** Verify generation output is equal or better than the last working state before committing. If changes regress, fix the regression first. "Commit and fix later" is never acceptable.

## Temp Scripts

Never use multi-line inline `python3 -c` calls. Write scripts to `.tmp/` (gitignored) and run them.

# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

vipy converts LabVIEW VI files to Python code without requiring a LabVIEW license. It uses [pylabview](https://github.com/mefistotelis/pylabview) as the core parser for reading VI file formats.

## Commands

```bash
# Install with dev dependencies
pip install -e ".[dev]"

# Run all tests
pytest

# Run a single test
pytest tests/test_vipy.py::test_version

# Lint
ruff check .

# Type check
mypy src/
```

## Architecture

The conversion pipeline has two stages:

1. **Structural parsing** (Python): pylabview extracts VI → XML, then `parser.py` extracts nodes/wires/constants into a graph
2. **Semantic translation** (LLM): `summarizer.py` creates a human-readable summary, sent to local Ollama model (qwen2.5-coder:7b) to generate Python

### Modules

- `src/vipy/parser.py` - Parse pylabview XML output into structured `BlockDiagram` (nodes, wires, constants)
- `src/vipy/summarizer.py` - Generate human-readable VI summaries for LLM input
- `src/vipy/llm.py` - Ollama integration for code generation
- `src/vipy/converter.py` - Main conversion orchestration
- `src/vipy/cli.py` - Command-line interface

### CLI Usage

```bash
# Check dependencies
vipy check

# Show VI summary (for debugging)
vipy summarize path/to/vi_BDHb.xml --main-xml path/to/vi.xml
```

## Current Development Focus: generate_python.py

**We are testing `scripts/generate_python.py`'s ability to generate deterministic, working Python from various VI formats.**

The goal is clean, syntactically valid Python output. Eventually an agent will improve the generated code, but for now ALL testing uses generate_python.py.

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

Implementation: `src/vipy/agent/codegen/error_handler.py`

### Key Data Structures

- `Node`: SubVI call (`iUse`) or primitive (`prim`) with uid, name, primIndex
- `Wire`: Connection between terminals (from_term → to_term)
- `Constant`: Value on diagram (hex-encoded, needs decoding)

### Primitive Mapping

LabVIEW primitives are identified by `primResID`. Known mappings in `summarizer.py:PRIMITIVE_MAP`:
- 1419 → Build Path
- 1420 → Strip Path

This table needs expansion as more VIs are encountered.

## VILib Terminal Resolution Workflow

When the code generator encounters a vilib VI with missing terminal indices, it raises a `VILibResolutionNeeded` exception. This is intentional - use the caller's dataflow info to fill in the missing indices.

**Workflow:**
1. Run the code generator (e.g., `scripts/ast_only.py`)
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
- **Prefer dataclasses over dicts** - Use typed dataclasses from `graph_types.py` instead of raw dictionaries. Use attribute access (`obj.field`) not `.get("field")`

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

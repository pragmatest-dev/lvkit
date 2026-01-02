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

# Convert VI to Python
vipy convert path/to/file.vi
vipy convert path/to/vi_BDHb.xml --main-xml path/to/vi.xml -o output.py

# Agent conversion (uses in-memory graph, no Neo4j required)
# THE STANDARD TEST COMMAND - use this for testing/evaluating strategies:
vipy agent "samples/JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi" -o outputs --search-path samples/OpenG/extracted --generate-ui
```

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

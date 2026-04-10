# lvpy Architecture

This document describes the technical architecture, conventions, and design decisions in the lvpy project.

## Overview

lvpy converts LabVIEW VI files to Python code without requiring a LabVIEW license. The system uses a two-stage pipeline: structural parsing (Python) followed by semantic translation (AST-based code generation or LLM-assisted).

## Technology Stack

### Core Dependencies
- **pylabview**: VI file format parser (extracts VI → XML)
- **Python 3.10+**: Modern Python with type hints
- **mypy**: Static type checking with strict mode
- **ruff**: Linting (rules: E, F, I, UP)
- **pytest**: Testing framework
- **NiceGUI**: Web UI framework for generated Python explorers

### Optional Dependencies
- **Ollama**: Local LLM for legacy code generation (qwen2.5-coder:7b)
- **Mermaid**: Flowchart visualization in documentation

## Architecture

### Two-Stage Conversion Pipeline

```
┌─────────────────────────────────────────────────────────────┐
│ Stage 1: Structural Parsing (Python)                        │
├─────────────────────────────────────────────────────────────┤
│ VI File → pylabview → XML → parser.py → BlockDiagram        │
│                                                              │
│ Output: Graph of nodes, wires, constants                    │
└─────────────────────────────────────────────────────────────┘
                            ↓
┌─────────────────────────────────────────────────────────────┐
│ Stage 2: Semantic Translation                               │
├─────────────────────────────────────────────────────────────┤
│ Option A: AST-based (Deterministic)                         │
│   BlockDiagram → AST Builder → Python AST → Code            │
│                                                              │
│ Option B: LLM-assisted (Legacy)                             │
│   BlockDiagram → Summarizer → Ollama → Python Code          │
└─────────────────────────────────────────────────────────────┘
```

### Data Flow

1. **VI Input**: LabVIEW VI file or block diagram XML
2. **XML Extraction**: pylabview extracts VI structure to XML
3. **Graph Building**: `parser.py` creates structured graph (nodes, wires, constants)
4. **In-Memory Graph**: `InMemoryVIGraph` loads VI and all dependencies
5. **Context Extraction**: `get_vi_context()` returns typed dataclasses
6. **Code Generation**: AST builder produces executable Python
7. **UI Generation**: Optional NiceGUI interface for interactive execution

## Key Modules

### Core Parsing (`src/lvpy/parser.py`)
- Parses pylabview XML output into structured `BlockDiagram`
- Extracts nodes (SubVIs, primitives), wires, constants
- Identifies terminal connections and data flow
- Returns typed dataclasses (not dicts)

### Memory Graph (`src/lvpy/memory_graph.py`)
- Central data structure: `InMemoryVIGraph`
- Loads VI and all SubVI dependencies recursively
- Provides graph queries: inputs, outputs, operations, wires, constants
- Handles polymorphic VI metadata and grouping
- **Critical**: `get_vi_context()` returns dataclass instances, NOT dicts

Key methods:
```python
def get_vi_context(vi_name: str) -> dict[str, Any]:
    """Returns dict with dataclass lists (not serialized dicts)."""
    return {
        "inputs": list[FPTerminalNode],      # Dataclasses
        "outputs": list[FPTerminalNode],     # Dataclasses
        "constants": list[Constant],         # Dataclasses
        "operations": list[Operation],       # Dataclasses
        "data_flow": list[Wire],             # Dataclasses
    }
```

### AST Code Generation (`src/lvpy/agent/codegen/`)
- **`ast_builder.py`**: Builds Python AST from VI graph
- **`context.py`**: Context tracking for code generation
- Expects dataclass inputs from `get_vi_context()`
- Deterministic, type-safe code generation
- Handles SubVI calls, primitives, data flow

### Documentation Generator (`src/lvpy/docs/`)
- **`html_generator.py`**: Generates static HTML documentation
- **`template.css`**: Extracted CSS stylesheet (287 lines)
- Supports polymorphic VI documentation with variant comparison
- Generates cross-referenced documentation with Mermaid diagrams
- Organizes output by library (OpenG/, vi.lib/, etc.)

### Summarizer (`src/lvpy/summarizer.py`)
- Legacy LLM-based generation support
- Creates human-readable VI summaries
- Primitive mapping table (primResID → function name)
- Used for debugging and LLM input

### CLI Interface (`src/lvpy/cli.py`)
- Command-line interface
- Commands: check, summarize, convert, agent, analyze

### Explorer (`src/lvpy/explorer.py`)
- NiceGUI-based interactive UI for generated Python
- Tree navigation of VIs organized by library
- Tabbed interface for executing VIs
- Copied to output directory during conversion
- Font size: 16px for tree and content (readability)

## Data Structures

All data structures are **typed dataclasses** from `src/lvpy/parser/models.py`:

### Core Graph Types

```python
@dataclass
class FPTerminalNode:
    """Front panel terminal (input/output)."""
    id: str
    name: str
    type_name: str
    default_value: Any
    is_input: bool
    labels: list[str]

@dataclass
class Operation:
    """Operation node (SubVI call or primitive)."""
    id: str
    name: str | None
    labels: list[str]
    prim_index: int | None
    inner_nodes: list[Operation]  # For loops, case structures
    properties: dict[str, Any]

@dataclass
class Wire:
    """Connection between terminals."""
    from_terminal_id: str
    to_terminal_id: str
    from_parent_id: str | None
    to_parent_id: str | None
    from_parent_name: str | None
    to_parent_name: str | None
    from_parent_labels: list[str]
    to_parent_labels: list[str]

@dataclass
class Constant:
    """Constant value on diagram."""
    id: str
    value: Any
    type_name: str
```

### Design Rule: Dataclasses Over Dicts

**CRITICAL**: Always use dataclasses with attribute access, never raw dicts:

```python
# ✅ CORRECT
wire = Wire(from_terminal_id="...", to_terminal_id="...")
dest_id = wire.to_terminal_id

# ❌ WRONG
wire = {"from_terminal_id": "...", "to_terminal_id": "..."}
dest_id = wire.get("to_terminal_id")
```

**Why**: Type safety, IDE support, catching errors at static analysis time.

## Conversion Pipeline Details

### Standard Test Command

```bash
lvpy agent "path/to/file.vi" \
    -o outputs \
    --search-path samples/OpenG/extracted \
    --generate-ui
```

This is the primary command for testing and evaluating conversion strategies.

### AST-Based Generation (Current Standard)

1. Parse VI to graph
2. Load all dependencies into InMemoryVIGraph
3. Extract context as dataclasses via `get_vi_context()`
4. Build Python AST using `ASTBuilder`
5. Generate Python code from AST
6. Optionally generate NiceGUI wrapper
7. Write to organized library directories

### Output Organization

Generated code is organized by library:

```
outputs/
├── package_name/
│   ├── graphicaltestrunner/      # .lvlib VIs
│   │   ├── get_settings_path.py
│   │   └── get_settings_path_ui.py
│   ├── vilib/                    # VI.lib (default)
│   │   ├── get_system_directory.py
│   │   └── get_system_directory_ui.py
│   ├── openg/                    # OpenG (__ogtk suffix)
│   │   ├── build_path.py
│   │   └── build_path_ui.py
│   ├── app.py                    # Explorer (copy of src/lvpy/explorer.py)
│   └── __init__.py
```

Library detection logic:
- `.lvlib:` or `.lvclass:` qualified → extract library name
- `__ogtk` suffix → "openg"
- Everything else → "vilib" (default)

## Documentation Generation

### HTML Documentation

Generated documentation is organized identically to code:

```
docs_output/
├── index.html                    # Grouped by library
├── style.css                     # Extracted stylesheet
├── OpenG/
│   └── Build_Path_ogtk.html
├── vi.lib/
│   └── Get_System_Directory.html
└── GraphicalTestRunner_lvlib/
    └── Get_Settings_Path.html
```

### Polymorphic VI Support

Polymorphic VIs are detected and documented specially:

1. **Index Page**: Only show wrapper VIs (hide variants)
2. **Polymorphic Pages**: Include ⚡ indicator and variant count
3. **Parameter Comparison**: Table showing which params are on "All" vs "Some" variants
4. **Variant Links**: Links to all child implementations
5. **Specific Calls**: When a VI calls a specific variant, link directly to it

### Relative Linking Rules

- **Same library**: Use filename only (`Build_Path_ogtk.html`)
- **Cross-library**: Use relative path (`../OpenG/Build_Path_ogtk.html`)
- **Index/CSS**: Always use `../` prefix from library subdirectories

## File Organization Conventions

### Library Name Extraction

Consistent logic across documentation and code generation:

```python
def extract_library_group(vi_name: str) -> str:
    """Extract library name for grouping.

    Examples:
        "GraphicalTestRunner.lvlib:Get Settings Path.vi"
            -> "GraphicalTestRunner_lvlib"
        "Build Path__ogtk.vi"
            -> "OpenG"
        "Get System Directory.vi"
            -> "vi.lib"
    """
    if ".lvlib:" in vi_name:
        library = vi_name.split(":")[0]
        return library.replace(".", "_")
    elif ".lvclass:" in vi_name:
        library = vi_name.split(":")[0]
        return library.replace(".", "_")
    elif "__ogtk" in vi_name:
        return "OpenG"
    else:
        return "vi.lib"
```

### Display Name Extraction

Clean names for display (remove library prefixes/suffixes):

```python
def extract_display_name(vi_name: str) -> str:
    """Remove library qualifiers for display.

    Examples:
        "GraphicalTestRunner.lvlib:Get Settings Path.vi"
            -> "Get Settings Path.vi"
        "Build Path__ogtk.vi"
            -> "Build Path.vi"
    """
    if ":" in vi_name:
        return vi_name.split(":", 1)[1]
    return vi_name.replace("__ogtk", "")
```

## Code Style & Conventions

### Python Style
- **Python Version**: 3.10+ (required for modern type hints)
- **Line Length**: 88 characters (Black-compatible)
- **Type Hints**: Required everywhere (mypy strict mode)
- **Imports**: Sorted (ruff rule I)
- **Modern Syntax**: Use Python 3.10+ features (ruff rule UP)

### Linting Rules (ruff)
- **E**: Pycodestyle errors
- **F**: Pyflakes
- **I**: Import sorting
- **UP**: Upgrade to modern Python syntax

### Type Checking (mypy)
- **Strict Mode**: Enabled
- **No Implicit Optional**: Required
- **Warn Unused Ignores**: Enabled
- **Disallow Untyped Defs**: Required

### Dataclass Usage

**Rule**: Prefer dataclasses over dicts everywhere.

```python
# ✅ CORRECT - Dataclass with types
@dataclass
class Operation:
    id: str
    name: str | None
    labels: list[str]

op = Operation(id="n1", name="SubVI", labels=["SubVI"])
name = op.name

# ❌ WRONG - Raw dict
op = {"id": "n1", "name": "SubVI", "labels": ["SubVI"]}
name = op.get("name")
```

**Why**: Type safety, IDE autocomplete, refactoring support, compile-time error detection.

### Import Organization

```python
# Standard library
from __future__ import annotations
import sys
from pathlib import Path
from typing import Any

# Third-party
from nicegui import ui

# Local
from lvpy.parser.models import Operation, Wire
from lvpy.memory_graph import InMemoryVIGraph
```

### Naming Conventions

- **Functions/Methods**: `snake_case`
- **Classes**: `PascalCase`
- **Constants**: `UPPER_SNAKE_CASE`
- **Private**: Leading underscore (`_private_method`)
- **Module Names**: Match VI structure (e.g., `get_settings_path.py`)

## Bash Command Rules

**CRITICAL**: Never use combined bash commands.

```bash
# ❌ WRONG - Combined commands
cd /tmp && python app.py
rm -rf /tmp/foo; python script.py

# ✅ CORRECT - Single commands only
# First call:
cd /tmp
# Second call:
python app.py
```

**Why**: Permission patterns and tool safety checks work on individual commands.

## Testing

### Test Structure
```
tests/
├── test_lvpy.py              # Basic functionality
├── test_ast_builder.py       # AST generation
└── samples/                  # Test data
    ├── OpenG/
    └── JKI-VI-Tester/
```

### Running Tests

```bash
# All tests
pytest

# Specific test
pytest tests/test_ast_builder.py::test_build_module_real_vi -v

# With full traceback
pytest tests/test_ast_builder.py -v --tb=long

# Short traceback
pytest tests/test_ast_builder.py -v --tb=short
```

## VILib Terminal Resolution Workflow

When code generator encounters vilib VI with missing terminal indices:

1. **Exception Raised**: `VILibResolutionNeeded` with terminal names and caller dataflow
2. **Analyze Caller**: Look at "Wire types from dataflow" to see actual indices used
3. **Match to Names**: Correlate caller's indices with terminal names from JSON
4. **Update JSON**: Add `"index": N` to each terminal in `data/vilib/<category>.json`
5. **Re-run**: Verify the fix works

**DO NOT GUESS** terminal indices. Always use caller's actual dataflow.

## Development Workflow

### Standard Development Cycle

1. **Make Changes**: Edit source files
2. **Run Tests**: `pytest` to verify no breakage
3. **Type Check**: `mypy src/` for type safety
4. **Lint**: `ruff check .` for style issues
5. **Test Conversion**: Run on sample VIs
6. **Commit**: Descriptive commit message

### Testing Code Generation

```bash
# Standard test command
lvpy agent "samples/JKI-VI-Tester/source/User Interfaces/Graphical Test Runner/Graphical Test Runner Support/Get Settings Path.vi" \
    -o outputs \
    --search-path samples/OpenG/extracted \
    --generate-ui

# Run the generated UI
cd outputs/get_settings_path
/path/to/.venv/bin/python app.py --port 8080
```

### Documentation Generation

```bash
# Generate docs for a VI and its dependencies
python scripts/generate_docs.py \
    "path/to/file.vi" \
    -o /tmp/docs \
    --search-path samples/OpenG/extracted
```

## Key Design Decisions

### Why Dataclasses Over Dicts?

**Decision**: Use typed dataclasses throughout, not dicts.

**Rationale**:
- Type safety catches errors at static analysis time
- IDE autocomplete improves development speed
- Refactoring is safe (rename field, find all usages)
- Self-documenting (types visible in code)
- No `.get()` fallback bugs

### Why Separate CSS File?

**Decision**: Extract CSS to `template.css`, not inline in Python.

**Rationale**:
- Generator code was ~1000 lines, mostly CSS
- CSS is still written to output (not external dependency)
- Easier to maintain and edit styles
- Cleaner Python code

### Why Library-Based Organization?

**Decision**: Organize both code and docs by library (OpenG/, vi.lib/, etc.).

**Rationale**:
- Matches LabVIEW's mental model
- Explorer UI groups VIs logically
- Easier to find related VIs
- Prevents root directory clutter
- Dependencies organized below target VI

### Why AST-Based Generation?

**Decision**: Prefer AST builder over LLM for code generation.

**Rationale**:
- Deterministic output
- No LLM dependency for core functionality
- Faster execution
- Type-safe from the start
- Easier to debug and maintain

## Common Patterns

### Loading VI Context

```python
graph = InMemoryVIGraph()
graph.load_vi_and_dependencies(vi_path, search_paths)

# Get context as dataclasses (NOT dicts)
context = graph.get_vi_context(vi_name)

# Access typed dataclasses
inputs: list[FPTerminalNode] = context["inputs"]
outputs: list[FPTerminalNode] = context["outputs"]
operations: list[Operation] = context["operations"]
wires: list[Wire] = context["data_flow"]
constants: list[Constant] = context["constants"]
```

### Working with Wires

```python
# ✅ CORRECT - Dataclass attribute access
for wire in wires:
    src = wire.from_terminal_id
    dest = wire.to_terminal_id
    parent = wire.from_parent_name

# ❌ WRONG - Dict access
for wire in wires:
    src = wire.get("from_terminal_id")
    dest = wire["to_terminal_id"]
```

### Collecting SubVI Names

```python
def collect_subvi_names(operations: list[Operation]) -> list[str]:
    """Recursively collect SubVI names from operations.

    Args:
        operations: List of Operation dataclasses
    """
    names = []
    for op in operations:
        # Access dataclass attributes directly
        if "SubVI" in op.labels and op.name:
            names.append(op.name)
        # Recurse into inner nodes (loops, cases)
        if op.inner_nodes:
            names.extend(collect_subvi_names(op.inner_nodes))
    return names
```

### Library Detection

```python
def to_library_name(vi_name: str) -> str | None:
    """Extract library name for directory organization."""
    if ".lvlib:" in vi_name:
        library = vi_name.split(":")[0].replace(".lvlib", "")
        result = library.lower().replace(" ", "_").replace("-", "_")
        return "".join(c for c in result if c.isalnum() or c == "_")

    if ".lvclass:" in vi_name:
        library = vi_name.split(":")[0].replace(".lvclass", "")
        result = library.lower().replace(" ", "_").replace("-", "_")
        return "".join(c for c in result if c.isalnum() or c == "_")

    if "__ogtk" in vi_name:
        return "openg"

    return "vilib"
```

## Primitive Mapping

LabVIEW primitives identified by `primResID`. Known mappings:

```python
PRIMITIVE_MAP = {
    1419: "Build Path",
    1420: "Strip Path",
    # ... more mappings added as encountered
}
```

Located in `summarizer.py`. Expand as more VIs are converted.

## Future Considerations

### Potential Improvements
- Expand primitive mapping table
- Add more vilib terminal definitions
- Support more LabVIEW structures (event structures, property nodes)
- Performance optimization for large projects
- Incremental compilation support

### Maintenance Notes
- Keep dataclass types synchronized with parser output
- Update library detection logic as new libraries encountered
- Maintain CSS consistency in template.css
- Document new primitive mappings as found

## References

- **pylabview**: https://github.com/mefistotelis/pylabview
- **NiceGUI**: https://nicegui.io
- **Mermaid**: https://mermaid.js.org
- **LabVIEW Wiki**: labviewwiki.org (primitive reference)

## Summary

lvpy follows these core principles:

1. **Type Safety**: Dataclasses everywhere, mypy strict mode
2. **Deterministic**: AST-based generation over LLM when possible
3. **Organized**: Library-based file organization
4. **Clean**: Extracted templates, single-purpose modules
5. **Tested**: Pytest for verification
6. **Documented**: Self-documenting code with type hints

The architecture prioritizes maintainability, type safety, and clear separation of concerns.

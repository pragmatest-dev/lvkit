---
name: convert
description: Convert LabVIEW VI files to Python using vipy. Use when converting VIs, running the agent, analyzing VI structure, or generating documentation. Also handles MCP server for IDE integration.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# vipy - LabVIEW VI to Python Conversion

Convert LabVIEW VI files to Python without requiring a LabVIEW license.

## Quick Start

```bash
# Standard conversion with dependencies
vipy agent "path/to/file.vi" -o outputs --search-path samples/OpenG/extracted --generate-ui

# Generate HTML documentation
vipy mcp  # Then use analyze_vi or generate_documents tools
```

## Architecture

Two-stage pipeline:
1. **Structural Parsing**: VI → pylabview → XML → parser → typed dataclasses
2. **Code Generation**: AST-based deterministic generation (preferred) or LLM-assisted

Key principle: **Everything is dataclasses, not dicts**. Use attribute access (`op.name`), never `.get()`.

## Commands

```bash
vipy check              # Check dependencies
vipy agent <vi> -o dir  # Full conversion with validation loop
vipy mcp                # Start MCP server for IDE integration
vipy summarize <xml>    # Debug: show VI summary
vipy explore            # Run NiceGUI explorer for converted VIs
```

## MCP Tools

The MCP server provides:
- `analyze_vi` - Parse VI and return structure (inputs, outputs, dataflow graph)
- `generate_documents` - Create HTML documentation for VIs/libraries
- `generate_python` - AST-based Python code generation

## Key Data Structures

```python
# All from src/vipy/graph_types.py
Operation   # SubVI or primitive: id, name, labels, terminals, primResID
Terminal    # Terminal on operation: id, index, direction, name, type
Wire        # Connection: from_terminal_id, to_terminal_id, from_parent_*
Constant    # Value: id, value, lv_type, raw_value, name
FPTerminalNode  # Front panel: id, name, type, lv_type, default_value
```

## Context Access Pattern

```python
# get_vi_context() returns dict with DATACLASS instances
context = graph.get_vi_context(vi_name)
for op in context["operations"]:    # Operation dataclass
    if "SubVI" in op.labels:        # Attribute access
        name = op.name              # NOT op.get("name")
```

## Output Structure

```
outputs/
├── package_name/
│   ├── openg/           # OpenG VIs (__ogtk suffix)
│   ├── vilib/           # vi.lib VIs
│   ├── libraryname/     # .lvlib VIs
│   ├── primitives.py    # Generated primitive wrappers
│   └── app.py           # NiceGUI explorer
```

## Output Expectations

Generated Python is a **functional transliteration**, not idiomatic code. It preserves LabVIEW's dataflow semantics but reads like code from a non-native Python speaker:

- **Loops**: Shift registers become explicit variable assignments (`shift_reg_0 = ...`), tunnels become intermediate variables
- **Data flow**: Explicit wiring becomes verbose variable passing
- **Naming**: Terminal labels become variable names (may be awkward)
- **Patterns**: Won't use Pythonic idioms (comprehensions, context managers, unpacking)
- **Classes**: `.lvclass` will produce granular, poorly-architected Python - many tiny accessor methods, no cohesive design

**This is intentional.** In an agent-in-the-loop workflow, the awkward-but-working Python becomes the artifact to refine. The theory: even bad Python is more easily understood and corrected than graph descriptions or dataflow diagrams.

Workflow: VI → stilted Python → agent/human refactors → idiomatic code

## Troubleshooting

- **Missing SubVI**: Add `--search-path` to VI library directory
- **Unknown primitive**: Check `data/primitives/` for primResID
- **Type resolution needed**: Check `data/vilib/` for terminal indices
- **Agent error**: Check that code uses dataclass attributes, not `.get()`

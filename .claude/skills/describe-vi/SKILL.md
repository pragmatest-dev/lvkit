---
name: describe-vi
description: Describe what a LabVIEW VI does by loading its graph and presenting human-readable operations, inputs/outputs, and control flow. Works via CLI — no MCP needed.
allowed-tools: Bash, Read, Grep
---

# Describe VI

Understand what a LabVIEW VI does without converting it.

## Usage

```
/describe-vi path/to/file.vi
```

## How It Works

Load the VI into the graph and print its description:

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi, describe_operations, describe_constants

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
vi_name = list(g.list_vis())[0]  # Use first loaded VI
print(describe_vi(g, vi_name))
print()
print(describe_operations(g, vi_name))
print()
print(describe_constants(g, vi_name))
"
```

Replace `VI_PATH` with the user's VI path. Add search paths as needed for dependencies.

For classes:
```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi, describe_operations

g = InMemoryVIGraph()
g.load_lvclass('LVCLASS_PATH', search_paths=[Path('SEARCH_PATH')])
for vi in sorted(g.list_vis()):
    if '.lvclass:' in vi:
        print(describe_vi(g, vi))
        print()
"
```

## MCP Alternative

If MCP is available, use the tools directly:
1. `load_vi` — load the VI
2. `describe_vi` — get the overview
3. `get_operations` — see execution flow
4. `get_constants` — see constant values
5. `get_dataflow` — trace wire connections
6. `get_structure` — inspect case/loop/sequence details

## Output

The description shows:
- Function signature with types
- Inputs/outputs (error clusters marked as exception-handled)
- SubVI calls with descriptions from vilib
- Control flow structures (case, loop, sequence)
- Operation count, constant count, parallel branch detection

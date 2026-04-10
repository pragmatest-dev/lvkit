---
name: vipy-describe
description: Describe what a LabVIEW VI does — signature, operations, dataflow, structures, constants. Works via CLI or MCP.
allowed-tools: Bash, Read, Grep
---

# Describe VI

Understand what a LabVIEW VI does without converting it.

Substitute the placeholders below with the user's actual paths:

- `<vi-path>` — the .vi file you want to describe
- `<library-path>` — additional search path for SubVI resolution (repeat for multiple)
- `<vi-name>` — the canonical VI name as it appears in the loaded graph (e.g. `MyLib.lvlib:Foo.vi`)
- `<operation-id>` — the ID of a specific operation node from the describe output

## Quick path: CLI

The `vipy describe` CLI is the simplest entry point.

```bash
vipy describe "<vi-path>" --search-path "<library-path>"
```

Add `--chart` to also print a Mermaid flowchart of the dataflow:

```bash
vipy describe "<vi-path>" --search-path "<library-path>" --chart
```

## Programmatic path: Python

For deeper exploration (drilling into a specific operation, listing class methods, etc.) use the graph API directly.

### Overview

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi, describe_operations, describe_constants

g = InMemoryVIGraph()
g.load_vi('<vi-path>', search_paths=[Path('<library-path>')])
vi = list(g.list_vis())[0]
print(describe_vi(g, vi))
print()
print(describe_operations(g, vi))
print()
print(describe_constants(g, vi))
"
```

### Dataflow for a specific operation

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_dataflow

g = InMemoryVIGraph()
g.load_vi('<vi-path>', search_paths=[Path('<library-path>')])
print(describe_dataflow(g, '<vi-name>', '<operation-id>'))
"
```

### Structure details (case/loop/sequence)

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_structure

g = InMemoryVIGraph()
g.load_vi('<vi-path>', search_paths=[Path('<library-path>')])
print(describe_structure(g, '<vi-name>', '<operation-id>'))
"
```

### Classes — list all methods

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi

g = InMemoryVIGraph()
g.load_lvclass('<lvclass-path>', search_paths=[Path('<library-path>')])
for vi in sorted(g.list_vis()):
    if '.lvclass:' in vi:
        print(describe_vi(g, vi))
        print()
"
```

## MCP alternative

If MCP tools are available, use them directly instead of the CLI/Python paths:

- `load_vi` → `describe_vi` → `get_operations` → `get_dataflow` → `get_structure` → `get_constants`

## Note

`vipy describe` and the underlying graph functions never require resolution to succeed. Unknown primitives and vi.lib VIs render as `[prim N]` / their bare name — you can describe a VI even if vipy has no mapping for some of its operations.

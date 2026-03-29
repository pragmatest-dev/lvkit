---
name: explore-vi
description: Interactively explore a LabVIEW VI's graph — load it, describe it, trace wires, inspect structures. Works via CLI or MCP.
allowed-tools: Bash, Read, Grep
---

# Explore VI

Interactive VI exploration. Load a VI and ask questions about it.

## Quick Start

### Via MCP (if available)

1. `load_vi` with the VI path and search paths
2. `describe_vi` — overview
3. `get_operations` — execution flow
4. `get_constants` — literal values
5. `get_dataflow` — wire connections (optionally filtered by operation_id)
6. `get_structure` — case/loop/sequence details

### Via CLI

```bash
# Overview
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_vi(g, list(g.list_vis())[0]))
"

# Operations
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_operations
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_operations(g, list(g.list_vis())[0]))
"

# Dataflow for a specific operation
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_dataflow
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_dataflow(g, list(g.list_vis())[0], 'OPERATION_ID'))
"

# Structure details (case/loop/sequence)
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_structure
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_structure(g, list(g.list_vis())[0], 'OPERATION_ID'))
"

# Constants
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_constants
g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH')])
print(describe_constants(g, list(g.list_vis())[0]))
"
```

## Exploring Classes

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
g = InMemoryVIGraph()
g.load_lvclass('LVCLASS_PATH', search_paths=[Path('SEARCH')])
for vi in sorted(g.list_vis()):
    print(vi)
"
```

Then explore individual methods with any of the commands above.

## Generating Documentation

```bash
vipy docs "INPUT_PATH" output_dir --search-path samples/OpenG/extracted
```

Creates a browsable HTML site with cross-referenced VI documentation.

---
name: describe-vi
description: Describe what a LabVIEW VI does — signature, operations, dataflow, structures, constants. Works via CLI or MCP.
allowed-tools: Bash, Read, Grep
---

# Describe VI

Understand what a LabVIEW VI does without converting it.

## Usage

```
/describe-vi path/to/file.vi
```

## Overview

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi, describe_operations, describe_constants

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
vi = list(g.list_vis())[0]
print(describe_vi(g, vi))
print()
print(describe_operations(g, vi))
print()
print(describe_constants(g, vi))
"
```

## Drill Down

### Dataflow for a specific operation
```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_dataflow

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
print(describe_dataflow(g, 'VI_NAME', 'OPERATION_ID'))
"
```

### Structure details (case/loop/sequence)
```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_structure

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
print(describe_structure(g, 'VI_NAME', 'OPERATION_ID'))
"
```

### Classes — list all methods
```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_vi

g = InMemoryVIGraph()
g.load_lvclass('LVCLASS_PATH', search_paths=[Path('SEARCH_PATH')])
for vi in sorted(g.list_vis()):
    if '.lvclass:' in vi:
        print(describe_vi(g, vi))
        print()
"
```

## MCP Alternative

If MCP tools are available, use them directly:
- `load_vi` → `describe_vi` → `get_operations` → `get_dataflow` → `get_structure` → `get_constants`

---
name: trace-bug
description: Debug a codegen issue by tracing from wrong Python output back through the graph to the root cause. Identifies whether the bug is in primitive definitions, type resolution, or codegen logic.
allowed-tools: Bash, Read, Grep, Glob
---

# Trace Bug

Debug why generated Python is wrong by tracing from output to root cause.

## Input

User provides:
- The generated Python line/block that's wrong
- The VI name
- What's wrong (expected vs actual behavior)

## Step 1: Identify the operation

Which graph operation produced the wrong code? Check:
- Is it a primitive? Which primResID?
- Is it a SubVI call? Which VI?
- Is it a structure (case/loop/sequence)?
- Is it a constant value?

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph
from vipy.graph.describe import describe_operations

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
print(describe_operations(g, 'VI_NAME'))
"
```

## Step 2: Check the data source

### For primitives — check the JSON definition

```bash
python3 -c "
import json
with open('data/primitives-codegen.json') as f:
    data = json.load(f)
p = data['primitives'].get('PRIM_ID')
if p:
    print(json.dumps(p, indent=2))
else:
    print('NOT FOUND')
"
```

Verify:
- Terminal indices match the graph
- Direction (in/out) is correct
- `python_code` expression is semantically correct
- Default values make sense

### For SubVIs — check terminal resolution

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
# Find the SubVI call node
for nid in g._vi_nodes.get('VI_NAME', set()):
    node = g._graph.nodes[nid].get('node')
    if node and 'SUBVI_NAME' in (node.name or ''):
        for t in node.terminals:
            print(f'idx={t.index} dir={t.direction} name={t.name} type={t.lv_type}')
"
```

### For structures — check frame/tunnel resolution

Use `get_structure` (MCP) or:
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

### For type issues — check typedef field resolution

```bash
python3 -c "
from pathlib import Path
from vipy.graph.core import InMemoryVIGraph

g = InMemoryVIGraph()
g.load_vi('VI_PATH', search_paths=[Path('SEARCH_PATH')])
# Check what fields the type resolves to
for nid in g._vi_nodes.get('VI_NAME', set()):
    node = g._graph.nodes[nid].get('node')
    if node and 'NODE_UID' in node.id:
        for t in node.terminals:
            if t.lv_type and t.lv_type.fields:
                print(f'{t.name}: {len(t.lv_type.fields)} fields')
                for i, f in enumerate(t.lv_type.fields):
                    print(f'  [{i}] {f.name}')
"
```

## Step 3: Trace the wire

Follow the data flow to/from the problematic operation:

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

## Step 4: Identify root cause

The bug is in ONE of:
1. **Primitive definition** (`data/primitives-codegen.json`) — wrong template, wrong indices
2. **vilib entry** (`data/vilib/*.json`) — wrong terminal mapping
3. **Type resolution** (`graph/loading.py`, `graph/construction.py`) — wrong typedef fields
4. **Codegen logic** (`agent/codegen/nodes/*.py`) — wrong AST generation for this pattern
5. **Graph construction** (`graph/construction.py`) — wrong wiring/terminal assignment

## Step 5: Report

State clearly:
- **What's wrong**: the specific output that's incorrect
- **Root cause**: which file and what data is wrong
- **Fix**: what needs to change (JSON entry, code logic, or both)

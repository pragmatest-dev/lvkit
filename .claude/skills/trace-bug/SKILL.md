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
lvkit describe "VI_PATH" --search-path "SEARCH_PATH"
```

## Step 2: Check the data source

### For primitives — check the JSON definition

```bash
python3 -c "
import json
with open('data/primitives.json') as f:
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

Use MCP `get_dataflow` with the SubVI operation ID to see what terminals are wired and at what indices.

### For structures — check frame/tunnel resolution

Use MCP `get_structure` with the structure operation ID.

### For type issues — check typedef field resolution

Read the parsed XML directly from the extracted VI files (in the `_BDHb.xml` or typedef `.ctl` file) and grep for the field names.

## Step 3: Trace the wire

Use MCP `get_dataflow` with the operation ID to follow data flow to/from the problematic operation.

## Step 4: Identify root cause

The bug is in ONE of:
1. **Primitive definition** (`data/primitives.json`) — wrong template, wrong indices
2. **vilib entry** (`data/vilib/*.json`) — wrong terminal mapping
3. **Type resolution** (`graph/loading.py`, `graph/construction.py`) — wrong typedef fields
4. **Codegen logic** (`agent/codegen/nodes/*.py`) — wrong AST generation for this pattern
5. **Graph construction** (`graph/construction.py`) — wrong wiring/terminal assignment

## Step 5: Report

State clearly:
- **What's wrong**: the specific output that's incorrect
- **Root cause**: which file and what data is wrong
- **Fix**: what needs to change (JSON entry, code logic, or both)

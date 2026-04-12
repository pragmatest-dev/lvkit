---
name: judge-output
description: Evaluate generated Python code quality by comparing it against the VI's graph. Checks signatures, control flow, SubVI calls, and parallel branches.
allowed-tools: Bash, Read, Grep
---

# Judge Output

Evaluate whether generated Python correctly represents a LabVIEW VI.

## Input

User provides:
- Path to generated Python file
- VI name or path (to load the graph for comparison)

## Step 1: Load the VI graph

```bash
lvkit describe "VI_PATH" --search-path "SEARCH_PATH"
```

## Step 2: Read the generated Python

Read the generated `.py` file.

## Step 3: Check each dimension

### Signature match
- Same function name (or reasonable Python equivalent)
- Same input parameters (names may differ, types must match)
- Same return type/fields
- Error clusters → exceptions (not passed as parameters)

### Control flow match
- Case structures → `if`/`elif`/`else` or `match`
- While loops → `while` with correct stop condition
- For loops → `for` with correct iteration
- Flat sequences → sequential blocks
- Nested structures preserved

### SubVI calls present
- Every SubVI listed in `describe_vi` output appears as a function call
- Arguments wired correctly (check against `get_operations` output)

### Parallel branches preserved
- If VI has `Parallel branches: yes`, the Python should have `ThreadPoolExecutor` or equivalent
- Independent operations in the same tier should NOT be serialized

### Constants correct
- Constant values match (check `get_constants` output)
- No hardcoded wrong values

### Error handling correct
- Error clusters become exceptions, not dict passing
- Merge Errors → try/except aggregation
- Clear Errors → except/pass pattern

## Step 4: Report

For each dimension, report:
- PASS — correct
- WARN — minor issue (naming, style)
- FAIL — semantic mismatch (wrong behavior)

Include specific lines and suggested fixes for FAIL items.

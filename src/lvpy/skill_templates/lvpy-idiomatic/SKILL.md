---
name: lvpy-idiomatic
description: Rewrite AST-generated Python into idiomatic, human-readable code while preserving LabVIEW semantics. The LLM in the editor does the rewrite — no API key needed.
allowed-tools: Bash, Read, Write, Edit, Grep
---

# Rewrite to Idiomatic Python

Take mechanically-generated Python (from `lvpy generate`) and make it read like a human wrote it.

## Input

The user provides either:
- A generated Python file to rewrite
- A VI path (run `lvpy generate` first, then rewrite)

## Step 1: Get the VI description

Substitute `<vi-path>` and `<library-path>` with the user's actual paths.

The simplest path is the CLI:

```bash
lvpy describe "<vi-path>" --search-path "<library-path>"
```

Or programmatically if you need the operations list separately:

```bash
python3 -c "
from pathlib import Path
from lvpy.graph.core import InMemoryVIGraph
from lvpy.graph.describe import describe_vi, describe_operations

g = InMemoryVIGraph()
g.load_vi('<vi-path>', search_paths=[Path('<library-path>')])
vi_name = list(g.list_vis())[0]
print(describe_vi(g, vi_name))
print()
print(describe_operations(g, vi_name))
"
```

## Step 2: Read the generated code

Read the AST-generated Python file. This is the **correct reference** — same behavior required.

## Step 3: Rewrite

Using the VI description and the reference code, rewrite the Python to be idiomatic. You ARE the LLM — just do the rewrite.

### Safe to change:
- **Variable names**: `daqmx_create_task_task_out` → `task`
- **Garbled unicode**: fix encoding artifacts
- **Unused imports**: remove
- **Docstrings**: add clear descriptions
- **String formatting**: `500 / 1000` → `0.5`
- **Context managers**: wrap resource lifecycle in `try/finally` or `with`
- **List comprehensions**: replace explicit loops where appropriate
- **Exception handling**: replace held-error patterns with try/except
- **Naming**: use domain-appropriate names (not LabVIEW terminal labels)

### NEVER change:
- **Function signature**: same inputs/outputs, same types
- **Parallel branches**: ThreadPoolExecutor represents real parallelism
- **Operation order**: topological sort is correct
- **Loop structure**: while/for semantics are correct
- **Return values**: front panel indicators must be returned

### Judgment calls (prefer conservative):
- Removing `time.sleep` — might be hardware timing
- Simplifying case structures — branches may have side effects
- Inlining SubVI calls — SubVI may be reused elsewhere

## Step 4: Validate

After rewriting:
1. `ast.parse(code)` — must be valid syntax
2. Same function name and parameter list
3. Same return type/fields
4. All SubVI calls still present

## Step 5: Write

Write the idiomatic version to the same output location, replacing the mechanical translation.

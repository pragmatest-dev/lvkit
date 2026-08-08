---
name: lvkit-describe
description: Describe what a LabVIEW VI does — signature, operations, dataflow, structures, constants. Works via CLI or MCP.
allowed-tools: Bash, Read, Grep
---

# Describe VI

Run `lvkit describe` on the VI:

```bash
lvkit describe "<vi-path>" --search-path "<library-path>"
```

To see the VI's faithful block diagram, render it to SVG:

```bash
lvkit render "<vi-path>" --search-path "<library-path>" -o "<vi>.svg"
```

**Report to the user using this format:**

```
# <VI name>

**What it does:** <1-2 sentence interpretation — purpose, key behavior, notable observations>

**Signature:** `<function signature>`

| Input | Type | Default |
|---|---|---|
| <name> | <type> | <default or —> |

| Output | Type |
|---|---|
| <name> | <type> |

**Control flow:** <brief description — frames, loops, cases>
<bulleted breakdown if the structure has meaningful steps>

| Constant | Type | Value |
|---|---|---|
| <inferred name or purpose> | <type> | <value> |

| Dependency | Description |
|---|---|
| <VI name> | <what it does> |

**Notable:** <surprising things, naming quirks, caveats — omit section if nothing to say>
```

Rules:
- Omit any table that has no rows (e.g. no inputs → no inputs table)
- Collapse repeated dependencies: `DAQmx Write.vi ×3`
- Use judgment on Constants — infer purpose from context, omit trivial ones
- Interpretation leads; raw data follows

## MCP alternative (prefer when the lvkit MCP server is connected)

The MCP tools take a VI path directly — no `load`/`clear` session step:

- **One VI:** `describe(vi_path)` → `get_operations(vi_path)` → `get_dataflow(vi_path)` → `get_structure(vi_path, operation_id)` → `get_constants(vi_path)`
- **Whole project** (index once with `index(project)`): `query(sql)` — read-only SQL over the curated views (`vi`, `terminal`, `constant`, `call`, `type_use`, `class_fact`); call `query_schema()` for the columns. It returns the *answer*, e.g. the error-indicator names as a histogram: `SELECT name, COUNT(*) AS n FROM terminal WHERE is_error_cluster=1 AND direction='output' GROUP BY name ORDER BY n DESC`. Reachability stays typed: `get_callers`/`get_callees`, `blast_radius`, `visualize_project`.

If the server isn't connected, use the `lvkit …` CLI above.

## Note

`lvkit describe` never requires resolution to succeed. Unknown primitives and vi.lib VIs render as `[prim N]` / their bare name.

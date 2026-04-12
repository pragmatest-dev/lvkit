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

Add `--chart` to also print a Mermaid flowchart:

```bash
lvkit describe "<vi-path>" --search-path "<library-path>" --chart
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

## MCP alternative

If MCP tools are available, use them directly:

- `load` → `describe` → `get_operations` → `get_dataflow` → `get_structure` → `get_constants`

## Note

`lvkit describe` never requires resolution to succeed. Unknown primitives and vi.lib VIs render as `[prim N]` / their bare name.

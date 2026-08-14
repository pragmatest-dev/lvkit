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

## Getting the facts

Prefer the MCP tools when the lvkit MCP server is connected — they take a VI
path directly, loaded on demand, no session `load`/`clear` step:
`describe(vi_path)` → `get_operations(vi_path)` → `get_dataflow(vi_path)` →
`get_structure(vi_path, operation_id)` → `get_constants(vi_path)`. For a
program (not a person) to parse the same facts, `get_context(vi_path)`
returns the structured netlist IR (`{vi, inputs, outputs, components, body,
properties, health}`) in one call instead of five.

Otherwise use the `lvkit describe` CLI above — same facts, prose form.
`-v/--verbose` adds the full netlist section.

To see the VI's faithful block diagram, render it to SVG (CLI-only, no MCP
twin):

```bash
lvkit render "<vi-path>" --search-path "<library-path>" -o "<vi>.svg"
```

For a repo-wide question instead of one VI, use `/lvkit-query`.

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

## Note

`lvkit describe` never requires resolution to succeed. Unknown primitives and vi.lib VIs render as `[prim N]` / their bare name.

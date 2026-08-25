---
name: lvkit-describe
description: Use when the user asks what a LabVIEW VI does, or wants its signature/inputs/outputs/dataflow/structures/constants explained — "what does this VI do", "describe this VI", "what are the inputs to X.vi". Single-VI, deep inspection; not for whole-repo questions (see lvkit-query).
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
`describe(vi_path)` returns the prose form; `read_vi(vi_path)` returns the
structured netlist IR (`{vi, inputs, outputs, components, body, properties,
health}`) in one call — operations, wiring, structures, and constants
together, not five separate reads.

No MCP server connected? The CLI `lvkit describe` above is the twin of both:
plain (no `--format`) prints the same prose as MCP `describe`; `-v/--verbose`
shows full detail within each section (every VI Property, Health, typed
terminals) without changing its shape; `--format netlist` prints the VI
dataflow netlist body on its own (add `-v` for a typed `## Components`
section first); `--format json` emits the identical structured payload MCP
`read_vi` returns, for a program instead of a person to parse.

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

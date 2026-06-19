---
name: lvkit-codegen-freeform
description: Generate idiomatic Python from a LabVIEW VI by feeding the graph as JSON context to the LLM. Bypasses lvkit's deterministic AST builder — output is non-deterministic and may vary between runs. Use for exploration or when the AST output is hard to clean up.
allowed-tools: Bash, Read, Write
---

# lvkit - Free-form Codegen from Graph JSON

Convert a LabVIEW VI to Python by reading its graph as JSON and writing the Python directly. Skips the deterministic AST builder used by `/lvkit-convert`.

**When to prefer this over `/lvkit-convert`:**
- You want cleaner output than the mechanical AST translation
- You're exploring what an LLM can do with the graph as raw context
- The AST output is too convoluted to clean up with `/lvkit-idiomatic`

**When to prefer `/lvkit-convert`:**
- You need reproducible output (this skill is non-deterministic)
- The VI is large and accuracy matters more than ergonomics
- You're producing code that will be tested against the LabVIEW source

## Workflow

Substitute placeholders below with the user's actual paths:
- `<vi-path>` — the .vi file
- `<output-dir>` — where the generated Python should land (use `outputs/`)
- `<search-path>` — additional search path for SubVIs (repeatable)

### Step 1: Export the graph

```bash
lvkit export "<vi-path>" --graph dataflow --pretty \
    -o .tmp/graph.json --search-path "<search-path>"
```

This writes a JSON document with the schema `lvkit-graph-v1`.

**Default behavior (focused on one VI):** when the input is a single `.vi`, the export emits **the focused VI in full** plus **signature-only views** of every SubVI it calls (`--depth 1`). That's the sweet spot for agent codegen: you get everything needed to write Python for this one VI without dragging in unrelated bodies.

Override with `--depth`:
- `--depth 0` — just this VI, no SubVI signatures
- `--depth 2` — focused VI + full bodies of called SubVIs + their SubVI signatures
- `--depth inf` — every transitively-reachable VI in full (heavy)

For class/lib/dir inputs, the default is `--depth inf` (no natural focus). Pin a depth explicitly if you want sliced output.

### Step 2: Read the JSON

Read `.tmp/graph.json`. The structure:

```
{
  "format": "lvkit-graph-v2",
  "vi_root": "<root VI name>",
  "types": {                          // type table — dereference lv_type refs here
    "t0": {kind, underlying_type, ...},
    "t3": {kind: "cluster", fields: [{name, type: "t0"}, ...]},
    ...
  },
  "dataflow": {
    "<vi_name>": {
      "vi": "...",
      "library": "...",
      "qualified_name": "...",
      "has_parallel_branches": <bool>,
      "signature": { "inputs": [Terminal, ...], "outputs": [Terminal, ...] },
      "nodes":     [GraphNode, ...],   // typed by `kind`
      "edges":     [Wire, ...],         // {source: WireEnd, dest: WireEnd}
      "subvi_calls": [SubVICall, ...],
      "constants":   [Constant, ...]
    }
  }
}
```

**Important conventions:**
- **Type references**: every `lv_type` field is a **string ID** like `"t3"`. Look it up in `doc["types"][id]` to get the actual LVType definition. Cluster fields' `type` and array `element_type` fields use the same convention.
- **ID prefix stripping**: within each `dataflow[<vi>]` entry, every node/terminal/wire ID is *local to that VI* — the `<vi>::` prefix has been stripped. To get the fully-qualified ID (rarely needed), prepend the dict key. E.g. inside `dataflow["TestCase.lvclass:run.vi"]`, ID `"43"` is shorthand for `"TestCase.lvclass:run.vi::43"`.
- **Compact edges**: each entry in `edges` is a two-element tuple `[src_terminal_id, dst_terminal_id]`. To find the owning node of a terminal, scan `nodes[*].terminals[*]` (do this once and build a `term_to_node` map).
- **Containment**: nodes inside a structure (case/loop/sequence) carry `parent: <structure_id>` and `frame: <frame_uid>`. The structure node's `frames[].inner_node_uids` lists the IDs of contained nodes — fetch the full node from the top-level `nodes` list.
- **Missing keys = null**: fields that would be null are stripped (`exclude_none`). A missing `description` means "no description," not "unknown."

Each node carries a `kind` discriminator:
- `primitive` — LabVIEW primitive (Add, Index Array, Divide, ...). Look at `name`, `prim_id`, `operation`, `terminals`.
- `vi`        — a SubVI call. Look at `name` and `qualified_name`. Treat as a function call.
- `constant`  — a literal. Look at `value`, `lv_type`, `label`.
- `structure` — a control-flow structure. Look at `node_type` for the sub-kind: `caseSelect`, `whileLoop`, `forLoop`, `flatSequence`, `stackedSequence`, `inPlaceElement`. Inner nodes have `parent` set to this node's id.

Edges (`Wire`) connect terminals: `source.terminal_id` → `dest.terminal_id`. Look at `source.node_id` / `dest.node_id` to find the producing and consuming nodes.

### Step 3: Translate to Python

Apply LabVIEW semantics:

**Dataflow execution.** Nodes execute when all input wires have a value. The graph is a DAG within a frame. Walk it topologically. Free-form generation: collapse intermediates into expressions where it improves readability, but preserve the topological order between observable side effects.

**Signature.**
- `signature.inputs` → function parameters (use `Terminal.name` for the parameter name, `Terminal.lv_type` to inform the type hint)
- `signature.outputs` → return values. Multiple outputs return a tuple.

**Constants.** Inline directly. Use `Constant.value` literally; use `Constant.lv_type` to choose representation (e.g., enum value → enum name).

**Primitives.** Each primitive node carries a `recipe` field (when known) with a `python_code` template and `imports`:
```json
{ "kind": "primitive", "name": "Wait (ms)", "prim_id": 1302,
  "recipe": { "name": "Wait (ms)",
              "python_code": { "_import": "import time",
                               "_body": "time.sleep(in_1 / 1000)" },
              "imports": [], "inline": true, "confidence": "exact_id" } }
```
Use the recipe verbatim when available — substitute `in_N` with the source-wire values and `_import` adds to the module's imports. Recipes resolve about 64% of typical primitives. For the rest, fall back to the `name` field:
- Arithmetic → operators (`Divide` → `/`, `Add` → `+`, ...)
- `Index Array` → subscript
- String primitives → `str` methods or f-strings
- `Node Multiplexer` (nMux) → cluster field access via `nmux_field_index` on the terminal
- `Property Node` / `Invoke Node` → `obj.prop` / `obj.method(...)` using `name`/`method_name`

**SubVI calls.** Emit a function call by `qualified_name` (or `name` if no qname). Input terminals → positional args by index; output terminals → return values. Three layers of info to draw on, in this order:
1. **If the SubVI is in `dataflow`** (most useful): full signature, body, and recipes are right there.
2. **Else if the node has `vilib_layout`** (very common for `<vilib>/...` and `<userlib>/...` refs): use the layout's `terminals`, `python_code` template, and `imports`. The layout's `python_code` is usually a working one-liner you can substitute wire values into. Example: `Error Cluster From Error Code.vi` → `if {error_code_0} != 0: raise LabVIEWError(...)`.
3. **Else fall back to terminal types and name.** The terminals on the VINode carry types from the caller's wiring even when the layout is unknown.

**Polymorphic VIs.** When `poly_info` is set on a VI's dataflow entry, the VI dispatches to one of `variants` based on `selectors`. Translate as a runtime dispatch (e.g., on the input type) or as separate Python functions per variant — agent's choice.

**Class fields and inheritance.** Class nodes in `deps` carry both `fields` (own only) and `fields_with_inheritance` (full chain). Use the latter when generating attribute access on instances.

**Generation order.** The top-level `generation_order` field gives a topological order over all loaded entities (typedefs first, then VIs callee-before-caller). When translating multiple VIs to a Python package, follow this order so the file you're writing only references things that already exist.

**Structures.**
- `caseSelect` → `if/elif/else`. Selector wire feeds the condition. Each `CaseFrame` holds nodes that run in that case.
- `whileLoop` → `while True: ... if stop: break` (or refactor to a cleaner Python loop if the stop condition is obvious)
- `forLoop` → `for i in range(N):`
- `flatSequence` / `stackedSequence` → sequential statements, one block per frame
- `inPlaceElement` → unwrap. Python objects are mutable references; IPES is a no-op structurally.

**Error clusters & parallel branches.** If `has_parallel_branches: true` AND error terminals are present, use the **held-error model** (see `CLAUDE.md` for the canonical pattern): each branch is wrapped in `try/except LabVIEWError`, errors are stored in `_held_error`, and the first error is re-raised at the merge point. If no parallel branches or no error clusters, just let exceptions propagate normally.

**Naming.** Use `Terminal.name` and `Constant.label` when they're meaningful. Convert "Some Control Name" → `some_control_name`. Strip LV cruft (`__ogtk`, `.vi`, etc.).

### Step 4: Write Python

Write the result to `<output-dir>/<vi_module_name>.py`. The module name should be a snake_case version of `vi_root` without the `.vi` suffix.

Example skeleton:

```python
def my_vi(input_a: float, input_b: int) -> tuple[float, str]:
    """<one-line description inferred from the VI's purpose>."""
    # ... generated body ...
    return result, status
```

### Step 5: Verify (signature only)

We don't have a LabVIEW runtime, so only check that the module imports and the function signature matches expectations:

```bash
python -c "
import sys
sys.path.insert(0, '<output-dir>')
import <module_name>
import inspect
print(inspect.signature(<module_name>.<fn>))
"
```

## Notes

- This is **non-deterministic**. Two runs may produce different Python. If you want reproducibility, use `/lvkit-convert`.
- The JSON schema is `lvkit-graph-v1`. If `format` is different, the schema may have changed — re-read this skill.
- If `dataflow` has many VIs (class/lib/dir input), pick the one matching `vi_root` as the entry point. Others are SubVIs you may want to translate too, in separate modules.

## Related Skills

- `/lvkit-convert` — Deterministic AST-based generation (preferred for accuracy)
- `/lvkit-describe` — Quick read-only summary of a VI's graph
- `/lvkit-idiomatic` — Rewrite mechanical AST output to idiomatic Python

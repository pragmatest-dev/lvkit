---
name: lvkit-convert
description: Hand-write an idiomatic Python (or other language) port of a LabVIEW VI from its lvkit facts, then verify it against the deterministic `lvkit generate` oracle. Teaches the LabVIEW-to-code gotchas an AI gets wrong by default. Works via CLI or MCP.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Convert a VI

```bash
lvkit describe "<vi-path>" --search-path "<library-path>" -v
```

Read the netlist section this prints (inputs, outputs, wiring, structures).
Write `<vi-path>.py` by hand from those facts, applying the guardrails
below. Then verify it against the mechanical oracle:

```bash
lvkit generate "<vi-path>" -o outputs --search-path "<library-path>"
```

```
  vilib: 3
  ast:   12
  stub:  0
  error: 0
```

Run both your hand-written port and `outputs/<vi>/<vi>.py` with the same
inputs and diff the results — behavioral equivalence, not a source-code
comparison. Any target language works this way; Python is the worked
example below because it's the only language `lvkit generate` emits today.

## Getting the facts

Prefer `get_context(vi_path)` over MCP — it returns the netlist IR in one
call: `{vi, inputs, outputs, components, body, properties, health}`.
Boundary `inputs`/`outputs` carry the FAITHFUL LabVIEW type (`"error
cluster"`, `"TestCase.lvclass"`, `"MethodEnum{setUp, tearDown}"`), never a
Python annotation. Each `output` also carries a `source` — the net that
drives that indicator (`{node, port, bare}`, or `null` if unwired) — so
read the VI's return wiring from there; don't infer which producer feeds an
output by type or position. `body` is a `kind`-tagged tree of `instance`/`scope`
nodes — scopes (loops, cases, sequences) nest their frames' bodies, and
wiring is expressed as `port -> source.net` bindings, not source order.
`components` lists each distinct subVI/primitive's typed port interface
once. This one call subsumes `get_operations`/`get_dataflow`/
`get_structure`/`get_constants` — reach for those individually only when you
need one slice in isolation.

No MCP server connected: `lvkit describe <vi-path> -v` prints the same
netlist IR as text (the `## Netlist` section). `lvkit render <vi-path> -o
<vi>.svg` (CLI-only, no MCP twin) gives the faithful block-diagram picture
when the text form is ambiguous.

For facts about how OTHER VIs call this one, or what a typedef's fields are
project-wide, use `/lvkit-query` (`query`/`get_callers` — cross-VI facts
`get_context` doesn't carry, since it's scoped to one VI).

## The guardrails

Any agent can turn a dataflow graph into a function. What it gets wrong,
reliably, is the LabVIEW-isms below — that's the value of this skill.

**Wiring is truth, not source order.** LabVIEW has no statement order;
`body`'s `port -> source.net` bindings ARE the execution order. Two
operations with no wire between them ran in parallel in LabVIEW — don't
invent a sequence.

**Preserve real parallelism.** Independent branches (no wire between them)
execute concurrently in LabVIEW; port that as real concurrency (e.g.
`concurrent.futures.ThreadPoolExecutor` in Python), not a serialized
sequence — and don't over-parallelize branches that DO share a hidden
dependency (a global, a reference, a queue) the wiring alone won't show you.

**Held-error model, if the VI has error clusters AND parallel branches.**
LabVIEW keeps running every parallel branch even after one raises, and
raises the FIRST error at the merge point. Port that shape explicitly —
don't let a bare `try/except` short-circuit the branches that still need to
run:

```python
def my_vi(input_data):
    _held_error = None
    try:
        branch_0_result = branch_0_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_0_result = None
    try:
        branch_1_result = branch_1_operations()
    except LabVIEWError as e:
        _held_error = _held_error or e
        branch_1_result = None
    if _held_error:
        raise _held_error
    return result
```

If the VI has no error clusters, just let exceptions propagate naturally —
don't manufacture a held-error scaffold where LabVIEW had none.

**Value-copy at a wire branch — the aliasing trap.** LabVIEW arrays and
clusters are value semantics: branching a wire to two consumers copies the
value, so one consumer mutating "its" copy never affects the other. Python
is reference semantics — the same object handed to two consumers aliases,
and an in-place mutation in one leaks into the other. This only bites where
your target language does in-place mutation (array-subset replace, in-place
element update, etc.); copy at the branch point whenever a downstream
consumer mutates, and don't blindly copy every branch (read-only branches
can share). **`lvkit generate`'s own oracle has this same gap** — it's
patched at exactly one site (the Formula Node backend copies its array
args), not generally. Don't trust the oracle blindly on a VI with array/
cluster branches feeding an in-place-mutating operation; verify by
executing both and asserting the source isn't mutated.

**Loops.** A shift register (`lSR`/`rSR` tunnel) is an accumulator carried
across iterations — its initial value comes from the outer wire if present
(`sr_initialized`), else the type default. An auto-indexing tunnel
(`TunnelMode.INDEXING`) builds/consumes one array element per iteration —
port to `enumerate()`/indexed iteration, not a manual list-append you
invented. A `LAST_VALUE` tunnel passes only the final iteration through, not
every value. For-loop iteration count is `min(len(array), ..., N)` across
every auto-indexed input plus the `N` terminal if wired; a while loop's stop
terminal has a polarity (stop-if-true vs. continue-if-true) — read it, don't
assume. A For Loop's optional conditional terminal (LabVIEW 2012+) is
tested at the END of each iteration — the stopping iteration still
contributes its output.

**Case default + unwired-tunnel default.** An unwired output tunnel on a
case frame doesn't mean "no value" — LabVIEW emits that terminal's TYPE
default (`0`/`False`/`""`/...) for any frame that doesn't wire it. Port a
default value, not `None`. A case structure's `Default` frame is a real
fallback branch, not a placeholder to drop.

**Coercion → explicit casts.** LabVIEW silently coerces numeric types at a
wire junction (marked with a coercion dot on the receiving terminal in the
diagram). Python has no implicit numeric coercion — where `get_context`
shows a wire's producer and consumer typed differently (e.g. `I32` into a
`DBL` terminal), insert the explicit cast LabVIEW performed silently.

**Clusters/typedefs → dataclasses.** A LabVIEW cluster (and a strict
typedef control) is a fixed set of named, typed fields — port it to a
dataclass with the same field names and faithful types, not a bare dict
(`class_fact.private_data` from `/lvkit-query` gives you a class's full
field list, inherited fields included).

## Unknowns are data, not errors

A query-driven port never throws `PrimitiveResolutionNeeded`/
`VILibResolutionNeeded` — those are `lvkit generate`'s exceptions, raised
only by the deterministic oracle. In the facts (`get_context`/`describe`),
an unresolved primitive shows as `[prim N]` and an unmapped vi.lib VI shows
as its bare filename. Identify it inline the same way `/lvkit-resolve` does
— the primitive's full terminal signature (every terminal, wired or not,
with its type) plus public NI documentation — and either write the correct
code or leave a clearly marked `# TODO: unresolved [prim N]` and move on.

`lvkit unresolved <path>` batch-collects every resolution gap in a VI or
library up front (each unknown primitive's terminal signature, each
unmapped vi.lib VI's caller context) — run it before converting a large
library instead of hitting gaps one at a time. `/lvkit-resolve` is the
*optional* path for persisting a mapping to `.lvkit/` so it's resolved for
every future VI, not a required step in this loop.

## Cleaning up existing generated code

If you're starting from `lvkit generate`'s mechanical output instead of
writing from facts, the same guardrails apply as edits, plus:

**Safe to change (cosmetic):** variable names (`daqmx_create_task_task_out`
→ `task`), garbled-encoding artifacts, unused imports, docstrings, literal
simplification (`500 / 1000` → `0.5`), context managers for resource
lifecycle, list comprehensions for clear loops.

**Never change (behavioral):** operation order, parallel branches, loop
structure, function parameters (front-panel controls — don't change types
or defaults), return values (front-panel indicators — don't drop outputs),
held-error handling if present.

## Optional: soft-fail the oracle instead of fixing gaps up front

`lvkit generate --placeholder-on-unresolved` emits an inline `raise
PrimitiveResolutionNeeded(...)`/`raise VILibResolutionNeeded(...)` for each
gap instead of failing the build — useful for generating an oracle to diff
against even when it isn't complete yet.

## Related

- `/lvkit-query` — cross-VI facts (callers, typedef fields, project-wide search)
- `/lvkit-resolve` — persist a primitive/vi.lib mapping so it never recurs
- `/lvkit-document` — generate a documentation site instead of code

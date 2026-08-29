# God-module decomposition + a shared graph-generator abstraction

Status: DESIGN (2026-08-29). Drives refactor pass "C". Not yet implemented.

## Why

The graph is processed by several **generators** (codegen → Python AST, lvnet →
netlist text/IR, the visual renderer → SVG scene, describe → text, diff → change
tree, and soon **FROG** → a geometry-bearing target). Each independently
hand-rolls the SAME skeleton — walk the graph, dispatch per node type/subtype,
recurse into structure bodies, handle an unsupported type — over a ~13-member
type universe, in ~5 god-modules. That duplication is the slowness/complication
this pass removes.

## Verified findings (the analysis behind this design)

- **Two parallel model hierarchies.** `Operation` (13 subtypes, `models.py:680`)
  is a **lossy, dataflow-ordered VIEW** *projected from* the source-of-truth
  `GraphNode` graph (13 subtypes, `graph/models.py:36`) by
  `get_operations`/`get_vi_context` (`queries.py:624`). The projection adds
  execution order, nesting, and kind filtering — and DROPS things (e.g. local
  variables: `_OPERATION_KINDS`, `core.py:70`, omits them, so the graph-native
  netlist builder must re-add them — `_GRAPH_NETLIST_NODE_KINDS =
  (*_OPERATION_KINDS, "local_variable")`, `netlist_build.py:1973`).
- **Who consumes which model.** `Operation` → codegen (all), `describe`, `diff`,
  `docs/generate`, the *old* netlist path. `GraphNode` directly → the visual
  renderer (`render/nodes.py`, `composite.py`) AND the graph-native netlist path
  (`_build_items_gn`). Newer consumers skip the Operation projection; codegen is
  the deep holdout (it genuinely needs the dataflow ordering).
- **Why lvnet didn't use Operation.** lvnet's contract is graph-REHYDRATION
  (lossless); Operation is a codegen-shaped LOSSY view (drops local vars, labels;
  normalizes order). So lvnet walks `GraphNode` directly.
- **codegen is already the target shape.** `codegen/nodes/` is a per-type handler
  PACKAGE (`case.py`/`loop.py`/`in_place.py`/`nmux.py`/`subvi.py`…), with a
  `match node` dispatch, a `_PRIM_CODEGEN` **subtype registry** keyed by
  `node_type`/`primResID` (`nodes/__init__.py:98`), and a first-class
  **unsupported → GenericHandler** path (`_generate_unknown`/`UnknownNodeError`).
- **What's genuinely bespoke vs shared.**
  - Bespoke (must stay per-generator): the OUTPUT type; each handler's BODY; the
    visitation ORDER (codegen = dataflow, lvnet = net-following, render = spatial).
  - Shared (currently duplicated ~5×): per-(type,subtype) DISPATCH, the RECURSION
    contract, and UNIFORM unsupported-type handling.

## Target abstraction

**One model (`GraphNode`) + shared graph-helper services + a shared dispatch
skeleton + a per-generator context and handler table.**

1. **Shared skeleton** (`graph/visit/` — new, small). Generic over context and
   output:

   ```
   Handler = Callable[[GraphNode, TCtx], TOut | None]
   walk(nodes, ctx, table) -> list[TOut]      # dispatch each node to table[type],
                                              # recurse structure bodies via the
                                              # SAME walk, route unsupported types
                                              # to table.unsupported(node, ctx)
   ```
   It owns ONLY: dispatch (primary by node class + secondary by
   subtype/`node_type`/`primResID`), the recursion contract (a structure handler
   recurses its body through `walk`), and uniform unsupported-type handling.
   It does NOT own order (caller supplies the node list already ordered), NOR
   the output type, NOR whether a handler returns or accumulates.

2. **Return type is optional.** `TOut | None`. A return-style generator
   (codegen → `CodeFragment`) returns; an accumulate-style generator (lvnet text →
   append to `lines`; visual render → draw into the scene; FROG → emit shapes)
   side-effects through `ctx` and returns `None`. The skeleton collects returns
   and ignores `None`.

3. **Base context = the useful part of Operation, UN-BUNDLED into graph-helper
   services** over `GraphNode`, available to every handler:
   - containment / traversal (the scope tree — every generator recurses it),
   - identity (uids — already on `GraphNode`),
   - resolved terminals + wiring,
   - **optional** dataflow-execution ordering (codegen requests it; lvnet/render
     don't).
   `Operation` the TYPE becomes retire-able — codegen keeps calling the ordering
   helper, just no longer through a second hierarchy.

4. **Per-generator context extends the base** with its own services:
   - codegen: symbol tables, error-model state;
   - lvnet: net-naming / handle assignment;
   - render + **FROG**: **geometry** (layout coords, bounds, icon rasters) — a
     shared capability built once from the parsed layout, consumed by the visual
     generators, ignored by the rest. (This is the case that proves the design:
     a handler "goes off and gets geometry" purely through its context; the
     skeleton never knows geometry exists.)

5. **Each generator = a handler table `{NodeType → handler}` + its output type.**
   Adding a node type = one handler file per generator. A new generator (FROG) =
   a handler table + a declared subset; the skeleton emits uniform placeholders
   for the rest (the FROG-subset = a partial table, exactly like lvnet's
   `# TODO(lvnet)` today).

## Two directions: export-only vs. lossless round-trip

Not every generator is one-directional. Split the formats by direction:

- **Export-only** (graph → output): codegen, the visual renderer, describe,
  diff, **FROG**. One handler table (forward). No reverse.
- **Lossless round-trip** (graph → output → graph): **lvnet** and **JSON**.
  These ALSO import — they parse their own output back and reconstruct the
  graph (lvnet's `lvnet_parse` + `lvnet_reconstruct`; the JSON equivalent). So a
  lossless format is TWO handler tables over the shared skeleton: a **forward**
  (emit) table AND a **reverse** (parse/reconstruct) table, tied by the same
  per-(type,subtype) key so a type is added to both halves together. The
  round-trip gate (`reconstruct(parse(emit)) == graph` by identity) is the
  losslessness contract; export-only formats have no such gate.

This is why lvnet is more code than codegen: it carries the reverse half.

## Module inventory

### Category A — generator / graph-processing god-modules (adopt the abstraction)
Decompose into a shared `graph/visit/` core + per-generator handler packages:
- `graph/netlist_build.py` (3577) — has BOTH an Operation `match` and a GraphNode
  `isinstance` dispatch in one file.
- `graph/render_lvnet.py` (1869), `graph/lvnet_parse.py` (2572),
  `graph/lvnet_reconstruct.py` (1153) — lvnet render/parse/reconstruct.
- `graph/diff.py` (3140), `graph/describe.py` (985) — Operation-dispatch consumers.
- `render/scene.py` (2035), `render/draw.py` (1317), `render/nodes.py` (1253) —
  the visual generator (already GraphNode-native; mostly needs the handler-table
  shape + geometry context).
- `codegen/builder.py` (780) + `codegen/nodes/*` (already decomposed — the
  reference shape; align it to the shared core, don't rewrite it).
- `graph/op_walk.py` (747), `graph/queries.py` (1329) — the Operation
  projection + graph queries → become the base-context helper services.

### Category B — other god-modules (plain mechanical single-responsibility split)
No shared abstraction; just package + facade re-export (the `netlist.py` →
7-module pattern already done this session):
- `cli.py` (2321), `mcp/server.py` (798), `pipeline.py` (783)
- `parser/vi.py` (1659), `parser/node_types.py` (1312), `parser/layout.py` (764)
- `graph/construction.py` (1631), `graph/loading.py` (1549)
- `structure.py` (1323), `vilib_resolver.py` (1190), `primitive_resolver.py` (711)
- `docs/html_generator.py` (923), `index/store.py` (916), `index/build.py` (708)
- `codegen/class_builder.py` (793)
- model files (`models.py`, `graph/models.py`, `netlist_models.py`) — split by
  concern only if it helps; models are lower-priority.

## Maximal parallelism (the whole point)

The enabler of conflict-free parallelism is a **REGISTRY**, not a central switch.
If each handler self-registers (`@register(LoopNode)` / auto-discovery of
`nodes/loop.py`) instead of being added to a central `match`/dict, then N
worktrees each adding ONE type's handler touch DISJOINT files and never collide
on a shared dispatch table. C0 MUST establish this registry (replacing codegen's
central `match` + `_PRIM_CODEGEN` dict). With it:

### Three waves, two of them massively parallel

- **Wave 1 — starts NOW, fully parallel, independent of everything else.**
  Category-B mechanical splits (cli, parser/*, construction, loading, structure,
  resolvers, docs, index, mcp, pipeline, class_builder). Each = its own worktree,
  package + facade re-export, DISJOINT files from every other B module AND from
  the Category-A / `graph/visit/` files C0 touches. So **Wave 1 runs concurrently
  with C0.** ~15 modules → batches of worktrees up to the concurrency cap
  (`min(16, CPUs-2)`).

- **C0 — serial + gated, runs CONCURRENTLY with Wave 1 (disjoint files).** Land
  `graph/visit/` (skeleton + base-context graph helpers + the **registry**) and
  the per-generator handler-package SKELETONS (empty `nodes/` dirs + a registry
  hookup for lvnet-forward, lvnet-reverse, render, describe, diff; codegen's
  already exists — align it). Convert ONE type end-to-end through every generator
  as the proof. Gate: full suite green + byte-identical output. This is the ONLY
  behavior-touching, non-parallel step.

- **Wave 2 — after C0, fully parallel: ONE WORKTREE PER NODE TYPE.** Each of the
  ~13 types (Loop, Case, Sequence, InPlace, Disable, Event, SubVI, Property,
  Invoke, Constant, Label, LocalVariable, Formula, Feedback, + primitive
  families) gets a worktree that moves THAT type's handler into every generator's
  `nodes/<type>.py` (forward + lvnet/json reverse), self-registering. Because
  each type's files are disjoint and registration is decentralized, these
  worktrees never conflict. ~13-15 worktrees, batched to the cap.

### Answers to the parallelization questions
- **How much can we parallelize?** Nearly all of it. Only C0 is serial; Wave 1
  and Wave 2 are each ~15-wide, and Wave 1 overlaps C0.
- **Break up the big modules simultaneous to C?** Yes — Wave 1 (Category B) runs
  concurrently with C0; disjoint files.
- **A worktree per type once the shared graph services exist?** Yes — that IS
  Wave 2, made conflict-free by the registry from C0.

### Merge / integration
Each worktree lands its own commit(s) on its branch; integrate by merging each
back (mechanical, API-preserving ⇒ near-zero textual conflict). The registry
means two type-worktrees never edit the same dispatch file. Run the full suite
once more at the integration point.

## Invariants for every step
- Mechanical = **behavior-preserving**: public import surface unchanged (facade
  re-export), output byte-identical where a golden exists.
- One responsibility per module; no file over ~700 lines afterward.
- Adding a node type must become a one-file-per-generator change.
- The shared core must have first-class unsupported-type handling (FROG subset).

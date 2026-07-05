# Production Design — LabVIEW Block-Diagram Renderer (graph-driven, extensible)

*Hardened after a Fable architecture review; the 5 Critical fixes are folded in below.*

## Context

We are turning the rendering POC into a **production, extensible renderer** that reproduces a
VI's block diagram faithfully and **replaces the Mermaid diagrams in the HTML docs**. The POC
(`src/lvkit/render/`) worked but committed an architectural sin: `heap_scene.py` **re-derived
semantics the graph already owns** — wire connectivity (`signalList`), node classification,
primitive names, structure kinds. That duplicates the dataflow network we invested in and drifts.

**Corrected principle (non-negotiable):** the existing **graph is the single source of truth for
semantics**; the raw heap XML supplies **only geometry** (positions the parser discards); the
renderer is a **pure view** joining the two by UID. Everything must be **extensible over time** —
new node glyphs, structures, wire styles, and output backends added declaratively, not by surgery.

### Verified join contract (with review corrections)
- Every graph id = `"{vi}::{heapUID}"` (`core.py:181` `_qid`). Strip prefix to match heap UIDs.
- **Node enumeration EXCLUDES the VI-definition node** whose id == `vi_name` (it has no diagram
  geometry — `construction.py:342-349`). Enumerate the rest via a **new public** `iter_nodes(vi)`
  (wraps `_vi_nodes` + `get_graph_node`; sorted — see Determinism). Do **not** use
  `VIContext.operations` (codegen view: lossy, reordered, constants dropped).
- **Front-panel controls/indicators are NOT graph nodes** — they are `FPTerminal`s on the VINode
  (`FPTerminal.id = "{vi}::{fpUID}"`, joins to heap `fPTerm` geometry). They get a first-class
  `RenderFPTerminal` in the scene (carries `name` → the on-diagram label, `control_type` → the
  `[1.23]` glyph, `is_indicator`).
- Terminals: `Terminal.id = "{vi}::{heapTermUID}"` → join terminal geometry.
- Wires: `graph.get_wires(vi)` returns edges. It **includes internal tunnel/sRN self-loop edges
  first** (`queries.py:664-670`) — expose that distinction **publicly** (`get_wires(vi,
  include_internal=False)` or an `internal`/`tunnel_type` field on `Wire`); the Scene draws
  **external edges only**. Tunnels render as border glyphs, not wires.
- **Wire type / color / coercion**: a wire carries no type; read the `lv_type` at each terminal.
  Color from the **source** terminal. Detect coercion by comparing a **normalized type key**
  `(kind, underlying_type, dimensions, recurse(element_type))` — ignore `description`/`values`/
  `fields`/typedef provenance (they differ per-side via `_enrich_type`, `core.py:186-227`) and
  treat `None` on either side as "no dot." Render LabVIEW's coercion dot at the receiving terminal.
- Containment: `GraphNode.parent` (qualified structure UID) + `.frame`; frame metadata from
  `CaseStructureNode.frames` / `SequenceNode.frames`.
- Display data all from the graph: `VINode.name` (real SubVI name), `PrimitiveNode.name/operation`,
  `ConstantNode.value/label/lv_type`, `Terminal.name`, `Terminal.lv_type`.

## Architecture — layers, each with one job

```
1 SEMANTIC MODEL   InMemoryVIGraph (EXISTING). Untouched except small PUBLIC accessors (below).
2 LAYOUT           render/layout.py   — heap XML → geometry ONLY: {heapUID→Rect}, {termUID→Point},
                                        structure border-terminal rects BY UID. No semantics.
3 SCENE            render/scene.py    — join graph⊕layout by UID → backend-agnostic view model
4 RENDER           render/backend.py  — Backend protocol + SvgBackend
  (pluggable)      render/nodes.py    — NodeGlyphResolver chain (the scalable node system)
                   render/structures.py — StructureRenderer registry
                   render/style.py    — Theme + wire_style(lv_type)->WireStyle
                   render/glyphs.py   — glyph asset library + JSON `icon` schema
                   render/icons.py    — icon data-URI + transparency + IconResolver chain (KEEP)
                   render/wire_router.py — hybrid router (KEEP)
5 ENTRY/INTEGRATE  render/__init__.py — render_vi(graph, vi_name)->str|None ; render_vi_file(path)
                   CLI `lvkit render` ; docs pipeline ; visualize (P3)
```

### Scene view model (corrected)
```python
RenderFPTerminal(fp: FPTerminal, center: Point, glyph: Glyph)   # controls/indicators (C1)
RenderNode(node: AnyGraphNode, bounds: Rect, terminals: list[RenderTerminal], glyph: Glyph)
RenderStructure(node: StructureNode, bounds: Rect, border_terminals: [...], shown_frame: Frame|None)
RenderWireNet(source_term, branches: list[list[Point]], style, junctions: list[Point])  # nets (S10)
Scene(bounds, fp_terminals, nodes, structures, wire_nets)
```
Wire **nets** (not per-edge pairs): group `get_wires` edges by source terminal, route trunk +
branches, mark junction dots at splits — so a branched wire isn't drawn as duplicate trunks.

### Coordinate spaces
Child-diagram geometry is relative to its structure's origin; the join accumulates the structure's
outer top-left as the offset (as the POC does). **This has been validated on zero nested samples** —
P0 must include a VI with a nested structure as a golden test (S14).

## Extensibility contract (the point)

**Node glyphs — a resolver chain** (`render/nodes.py`): first resolver returning a `Glyph` wins.
`ExtractedIconResolver` (real `_ICON.png`, transparency) → `JsonGlyphResolver` (optional `icon`
field on primitive/vilib JSON) → `GeneratedGlyphResolver` (arithmetic triangle, build/index array,
bundle…) → `PdfIconResolver` (later; strip wire stubs) → `FallbackBoxResolver` (labeled box in the
LabVIEW font; always succeeds). **Add a visual = register a resolver / add JSON / drop an SVG asset.**
`Glyph` scales to the node's heap **bounds** (growable nodes — Build Array, N-input compound arith —
come free); it declares no intrinsic size, and **heap `termBounds` centers always win** for wire
anchoring (glyph anchors are advisory only) (S6).

**Structures — a registry** keyed by kind. Each draws border + border-terminals (by UID) + frame
chrome, and applies the **single-frame display policy** for case/stacked (render one frame; exclude
hidden-frame nodes and any wire with an endpoint inside a hidden frame, via `parent`/`.frame`) (C2).

**Wire styling — `Theme` + a table**: `wire_style(lv_type)` branches on `LVType.kind` then
`underlying_type` (DBL orange, int blue, bool green, string pink, array thicker, cluster brown,
error dark). All colors live in one `Theme` object so dark-mode/doc theming is one swap, not a
cross-cutting edit (N16).

**Backends — one op vocabulary** (`rect/path/text/image/polygon/circle/line` + `measure_text`):
glyphs and structure renderers emit the SAME backend ops (no imperative side channel); `Backend`
also provides **text measurement** so `_fit_label` stops being a 5px/char heuristic and the future
PNG backend truncates identically (S7). `SvgBackend` now; `PngBackend`/`CanvasBackend` later.

## Robustness, determinism, performance
- **Fail-closed fallback** (S8): if any *semantically-required* node lacks geometry, `render_vi`
  returns `None` and logs the missing UIDs → docs use Mermaid. No per-node auto-layout engine
  (that would be a heuristic — `feedback_no_heuristics`).
- **Decoration pass** (C5): comments / free labels carry no dataflow semantics, so reading them
  from heap XML is *consistent with* the geometry-only rule; they render as heap-only decorations.
  Documented so nobody "corrects" it later.
- **Determinism** (S9): sort nodes by `_node_order_key`; sort wire nets by
  `(_node_order_key(src.node), src.terminal-key, …)`. Resolver iteration is a fixed list. Golden
  test renders twice under different `PYTHONHASHSEED`; pin Pillow version for icon-data-URI fixtures.
- **Performance** (S11): per-VI wall-clock budget (exceed → Mermaid, logged); coarsen router grid
  above a canvas-size threshold; docs-side SVG cache keyed by VI mtime (extraction is already cached).

## Fidelity to ground truth (`.tmp/array average 1.png`) — all graph-sourced
Control/indicator **labels** ← FP `Terminal.name`; constant **"0"** ← `ConstantNode.value`; **wire
colors** ← source `Terminal.lv_type`; **coercion dots** ← normalized source≠dest; **SubVI names** ←
`VINode.name`; control **[1.23]** ← `control_type`.

## Integration (Mermaid replacement)
- **Docs**: render SVG in the pipeline where the graph + VI path exist —
  `docs/generate.py::_prepare_vi_documentation_data` sets `vi_data["diagram_svg"] =
  render_vi(graph, vi_name)` (or `None`). `html_generator._render_vi_page` injects it at
  `<section id="dataflow">` (replacing the `MermaidRenderer.render` call at ~L337-338; the head
  mermaid `<script>` L370-378 drops when SVG present). **Mermaid stays as fallback** when `None`.
- **Delete the duplicate pipeline** `scripts/generate_docs.py` (make it a thin shim over
  `lvkit.docs.generate`) *before* P3 — "keep in sync" violates the repo's DRY rule (S12).
- **CLI** `lvkit render <vi> -o out.svg` → `render_vi_file` (build graph → `render_vi`).
- **visualize** (`graph/flowchart.py`) switched to SVG in P3. Drop the 32×32 canvas-icon overlay
  (not part of a real BD; page header already shows it) (N15). Add `<title>/<desc>` + per-node
  `aria-label` from graph names (N17).

## Small PUBLIC graph additions (stop building on privates — S13)
`graph/queries.py`: `iter_nodes(vi_name)` (typed, sorted, excludes the vi-def node),
`get_terminal(terminal_id)`, and `get_wires(vi, include_internal=False)` (or `Wire.internal`).
Aligns with the pending MCP stateless refactor.

## Files
- **New:** `render/layout.py`, `render/scene.py`, `render/backend.py`, `render/nodes.py`,
  `render/structures.py`, `render/style.py`, `render/glyphs.py`, `data/glyphs/*.svg`.
- **Keep:** `render/wire_router.py`, `render/icons.py`.
- **Refactor/remove:** `render/heap_scene.py`→`layout.py` (geometry only); `render/svg.py`→backend+renderers.
- **Edit:** `render/__init__.py`; `cli.py` (`render` cmd → graph-driven); `graph/queries.py` (public
  accessors + wire internal flag); `docs/generate.py` + `docs/html_generator.py` (SVG + Mermaid
  fallback); `scripts/generate_docs.py` → shim; P2 `icon` schema on primitive/vilib models.
- **Tests:** rework `tests/test_render.py` around the graph join (build a small graph, assert scene
  join, FP-terminal labels, colors, coercion key, external-only wires, determinism). Keep router/icon units.

## Phasing (corrected — P0/P2 no longer overlap)
- **P0 — Graph-driven foundation, plain dispatch (no chain yet):** `layout.py` + `scene.py`
  (join incl. FP terminals + vi-node exclusion + single-frame policy + external-only wires) +
  `SvgBackend` + a **dispatch dict** node/structure drawer reproducing today's look from the graph.
  `render_vi(graph, vi_name)`. Delete heap_scene semantics. **Acceptance:** render every VI in
  `samples/` — no exceptions, scene node/wire counts vs `iter_nodes`/external `get_wires`, zero
  unresolved endpoints; explicit accepted-loss list; include a **Case** VI and a **nested-structure**
  VI. Suite green.
- **P1 — Fidelity:** FP labels + `[1.23]`, constant values, per-type wire colors, coercion dots,
  wire nets + junction dots, real SubVI names → match the ground-truth PNG.
- **P2 — Scalable node system:** migrate the dispatch dict into the **resolver chain** + JSON `icon`
  schema + generated glyph library + fallback box + LabVIEW font.
- **P3 — Docs integration:** SVG in docs pipeline + Mermaid fallback + golden tests; delete-dup done;
  switch `visualize`.
- **P4 — Extensions:** PDF/subVI icon extraction, more structures, PNG backend, per-frame case tabs.

## Verification
- `uv run lvkit render ".tmp/array average 1.vi"` → SVG matching the ground-truth PNG (FP labels,
  For-Loop N·i·SR·tunnels, Add+Divide, orange DBL wires); compare in an Artifact.
- `uv run lvkit docs <vi>` → faithful SVG at the Block Diagram section; Mermaid still renders for a
  geometry-less input.
- Corpus test (P0 acceptance above) + determinism test (two `PYTHONHASHSEED`s) + a known-coercion VI.
- `uv run pytest -q`, `uv run ruff check .`, `uv run python -m pyright src/`.

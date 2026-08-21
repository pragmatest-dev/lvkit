# Module Decomposition Plan (PARKED)

> **Status: PARKED — not executed.** Derived 2026-08 alongside the MCP render/diff
> surface. Land the MCP surface first (low cost), then execute this. Goal stated by
> the maintainer: **separation of concerns, kill god-modules, reduce circular
> dependencies, easier testing, easier to change over time.**
>
> ⚠️ Line numbers/sizes below are a snapshot to locate seams. **Re-read from source
> before executing** — do not trust these numbers after any edit.

---

## Organizing principle

1. **Modules are artifact-producers on the shared graph, named by what they
   PRODUCE.** Everything is `graph → X`, so "analyzes over the graph" categorizes
   nothing — the graph is the *foundation*, not a differentiator. (Same empty-category
   trap as naming a layer `core/`: a label that includes everything is useless.)
2. **Keep one boundary visible: PURE producers** (`graph → artifact`) **vs IMPURE
   surface-glue** (`path → load → dispatch → body`). That pure/impure line is the one
   worth seeing; naming glue like an engine hides it.
3. **Formats are earned by what a result can meaningfully be** — reject a
   format-matrix (docs=site only, netlist=data only, render=svg[+html wrap],
   diff=prose/data/visual because a change-set genuinely is all three).
4. **God-modules split INTERNALLY by responsibility/family**, never one dispatch
   file. Litmus (maintainer's example): `glyph.py` must not hold every typed glyph in
   one file — split by glyph FAMILY.

---

## Part A — Top-level relayering: lift the engines OUT of `graph/`

`graph/` has absorbed its own consumers. `netlist`, `describe`, `diff` are engines
that USE the graph to produce distinct artifacts (IR, prose, change-set) — the SAME
category as `render` (→svg) and `codegen` (→python), which correctly live *outside*
`graph/`. Lift them out to be producer siblings.

**`graph/` keeps only the graph proper:** `core` (InMemoryVIGraph), `queries`
(`QueryMixin` = "ask the graph about itself"), `construction`, `loading`, `builders/`,
`parallel_parse`, `models`, `op_walk`, `analysis`, `interface_order`.

**Target top level:**
```
parser/  graph/                                   ← foundation (structure + intrinsic API)
netlist/  describe/  diff/  render/  codegen/      ← engines: graph → {IR, prose, change-set, svg, python}
viewer/  docs/                                     ← page/site producers (compose render + engines)
cli/  mcp/                                         ← surfaces
```

**The DAG (currently flattened into one folder — make it cross-module & one-way, so
cycles become impossible to write):**
```
graph  ←  netlist  ←  { describe, diff }
```
Fix the leak: engines reach `graph.core._uid_of` (a private helper) — promote to
public graph API.

---

## Part B — God-module internal splits (grounded seams)

**Two kinds of split — don't confuse them:**
- **LIFT OUT** → a new **top-level** package, only for engines mis-filed in `graph/`
  that produce a distinct artifact: `netlist/`, `describe/`, `diff/` (Part A).
- **DECOMPOSE IN PLACE** → a **subpackage of the current parent**, for a god-module
  that's correctly located but too big: `render/glyph/`, `render/draw/`,
  `render/scene/`, `render/nodes/`, and `cli/`. Glyphs/draw/scene are pipeline stages
  of graph→SVG — subordinate to `render/`, never top-level siblings.

### `graph/diff.py` (~3135) → **top-level** `diff/` package (lifted)
Currently: change-set model + element matching + wire/constant/frame/terminal/property
diffing + text + json + netlist-rows, all in one file.
- `diff/model.py` — `ElementChange`, `ChangeMap`, `_ElemInfo`
- `diff/elements.py` — `_collect_elements`, `_incident`, `_match_elements`, `_effective_sinks`
- `diff/wires.py` — `_wire_path`, `_incident_wires`, `_chain_paths`, `_transition`, `_unstable_endpoint`, `_wire_changes`
- `diff/constants.py` — `_constant_locality`, `_const_consumers`, `_constant_changes`
- `diff/frames.py` — `_pair_frames_by_content`, `_struct_frame_changes`, `_matched_struct_pairs`, frame helpers
- `diff/terminals.py` — `_fp_terminals`, `_correlate_by_keys`, `_terminal_changes`, `_node_terminal_changes`
- `diff/properties.py` — `_diff_vi_properties` (back half)
- `diff/engine.py` — `diff_uid` (assembles ChangeMap from the above)
- `diff/text.py` — `format_diff`
- `diff/json.py` — `diff_to_dict`
- `diff/netlist_rows.py` — `netlist_diff_rows`, `rows_to_json`

### `graph/netlist.py` (~2519) → **top-level** `netlist/` package (lifted)
- `netlist/model.py` — the `Netlist*`/`Gamma/Mu/Eta*`/`NetRef`/`NetlistModule` dataclasses (~88–570)
- `netlist/naming.py` — `_tunnel/_gamma/_mu/_eta_net_name`, `_eta_index_mode`
- `netlist/resolve.py` — `_resolve_source`, `_resolve_or_default`, `_input_ref`, `_selector_ref`, `_term_ref`
- `netlist/defaults.py` — `_default_literal`, `_type_default`
- `netlist/build.py` — `_walk_flat`, `_assign_occurrences/_assign_sequential_ids`, `_build_instance/_feedback/_items/_property_accesses`, `build_netlist`
- `netlist/serialize.py` — `netlist_to_dict`, `render_netlist`, `component_line`

### `graph/describe.py` (~983) → **top-level** `describe/` package (lifted) *(borderline — could stay one module)*
- `describe/vi.py` — `describe_vi` + facet dispatchers (`describe_operations/dataflow/structure/constants`)
- `describe/signature.py` — `_format_signature`, pane-term lines, type labels
- `describe/properties.py` — `_describe_properties/_health/_flag_group`
- `describe/class_context.py` — `_describe_class_context`
- `describe/structures.py` — `_describe_case_structure/_loop/_sequence`, `_collect_structures`, frame body
- `describe/ops.py` — `_describe_op_list/_single_op`, `_find_operation`, subvi name/desc

### `render/glyph.py` (~2147) → `render/glyph/` **subpackage, by FAMILY** (the litmus; subordinate to render)
- `glyph/base.py` — `Glyph` protocol, `fit_label/fit_value/wrap_label/fit_wrapped/_truncate`, `WrappedBoxGlyph`, `draw_split_box`, shared `_draw_*`
- `glyph/formula.py` — `FormulaNodeGlyph`
- `glyph/variable.py` — `LocalVariableGlyph`, `ControlRefConstGlyph`
- `glyph/arithmetic.py` — `ArithGlyph`, `CompoundArithGlyph`, `BooleanGateGlyph`
- `glyph/cluster.py` — `BundleGlyph`, `UnbundleGlyph`, `BundleByNameGlyph`, `EventDataGlyph`, `ClusterConstantGlyph`, `ErrorClusterGlyph`
- `glyph/array.py` — `Array{Size,Reverse,Search,Sort,Split,Build}Glyph`, `ConvertGlyph`, `InPlaceElementGlyph`
- `glyph/property_invoke.py` — `PropertyNodeGlyph`, `InvokeNodeGlyph`, `_draw_drawer_row`
- `glyph/constant.py` — `ConstantGlyph`, `BooleanConstantGlyph`
- `glyph/image.py` — `IconImageGlyph`, `CenteredSvgGlyph`, `VariantGlyph`, `InlineSvgGlyph`

### `render/draw.py` (~2249) → `render/draw/` subpackage
- `draw/node.py` — `draw_node` + invert-bubbles/formula/glyph-bounds/labels/identity/tooltip/doc_url/help
- `draw/pane.py` — `_draw_connector_panel` + pane label/term/spread/stub, `draw_help_overlay`
- `draw/structure.py` — `draw_structure` + frame/loop/sequence/selector/menu/event-band, `_SelectorGeom`
- `draw/fp_terminal.py` — `draw_fp_terminal` + value-cell/array-index/type-label
- `draw/border.py` — `_draw_border_terminal`, for/while loop borders
- `draw/layers.py` — `_draw_layer_content`, coercion dots

### `render/scene.py` (~1896) → `render/scene/` subpackage
- `scene/model.py` — `Render{Terminal,FPTerminal,Label,Node,BorderTerminal,Structure,WireNet,CoercionDot}`, `Scene`
- `scene/frames.py` — frame-path, `encode_frame_path`, `_frame_compatible/_default_visible`, `_frame_info`
- `scene/geometry.py` — string/cluster const geom, formula centers, `_drawn_bounds`
- `scene/terminals.py` — `_render_terminals`, `_reposition_mux_terminals`, `_structure_borders`
- `scene/wires.py` — wire routing (`_wire_path`, `_build_wire_nets`, edge/entry/exit, containers, roles) — the big chunk
- `scene/build.py` — `build_scene` (entry)

### `render/nodes.py` (~1192) → `render/nodes/` subpackage *(already resolver-structured)*
Split the resolver classes + per-node builders: `nodes/resolvers.py`
(`{Extracted,Json,Original,Generated,Fallback}GlyphResolver`, `resolve_glyph`),
`nodes/builders.py` (`_bundle_by_name/_event_data/_property_node/_invoke_node/_leaf_const/_cluster_const_glyph`),
`nodes/consts.py` (const formatting).

### `cli.py` (~2107) → `cli/` package (one module per command)
- `cli/main.py` — `main()` + parser wiring
- `cli/args.py` — `_add_{load_mode,theme,project_root,library_root}_arg`, `_parse/_configure_library_roots`, `_configure_resolvers`, `_auto_search_paths`, `_resolve_load_mode`
- `cli/describe.py` · `cli/render.py` (+`_build_render_body`,`_emit_render`,`_theme_mode`) · `cli/diff.py` (+`_build_diff_body`,`_emit_diff`) · `cli/index.py` (index/query/`_print_table`) · `cli/graph_op.py` · `cli/structure.py` · `cli/generate.py` · `cli/docs.py` · `cli/visualize.py` · `cli/setup.py` (setup/detect) · `cli/mcp.py`

### Not yet inspected — confirm seams from source when resuming
`parser/vi.py` (1651), `graph/construction.py` (1558), `graph/loading.py` (1449),
`structure.py` (1323), `parser/node_types.py` (1309), `vilib_resolver.py` (1190),
`graph/queries.py` (1168 — likely stays, it's the graph's API), `graph/models.py` (961),
`models.py` (943), `index/store.py` (939), `docs/html_generator.py` (923).

### Small DRY, independent of the big lifts — a shared bundled-JSON loader
Low-priority cleanup surfaced during the #36 design review (do opportunistically,
not worth its own issue). `labview_error_codes._load_codes` and
`measure_data._load_table` share a near-verbatim skeleton: module-global `None`
sentinel → `data_dir() / <file>.json` → `.exists()` guard → `read_text(encoding=
"utf-8")` + `json.loads` → degrade-to-empty. Extract `_data.load_json(filename)
-> object | None` (parsed object, or `None` when the file is absent); each caller
keeps only its own validation/shaping/caching. **Scope is exactly those two** —
`primitive_resolver._load_codegen` and the `vilib_resolver` loaders only share the
3-line `if path.exists(): json.load` core; their real bodies do project-vs-bundled
merging + multi-index building, so the helper replaces their read-prologue at most,
not the duplication that matters.

---

## Part C — `viewer/` extraction + `diff` dissolution

- **Extract `viewer/` from `render/`** (they share the kit — both viewers import
  `theme_control`, `help_tip`, `properties_panel`, `connector_pane_panel`): move
  `render_viewer`, `diff_viewer`, `theme_control`, `help_tip`, `properties_panel`,
  `connector_pane_panel` → `viewer/`. (Verify `theme_control` imports `theme_web`, not
  the reverse, so the arrow is viewer→render.)
- **`render/` stays the pure renderer** (graph→SVG), incl. `theme_web` (the palette the
  renderer needs) and `connector_pane` (pane geometry, *not* the HTML panel).
- **Dissolve the diff orchestrator by artifact:** engine → `diff/`, viewer →
  `viewer/diff_viewer`, page-orchestrator → `viewer/diff_page`. So the current loose
  `vi_diff.py` becomes `viewer/diff_page` (html) + direct `graph.diff` calls (text/json).
  Symmetric `viewer/render_page` sits beside it. **No `core/`, no loose orchestrator.**
- **`docs/` stays its own producer** (unique output = multi-page linked site). It shares
  nothing with viewers today but the raw SVG renderer. DRY win: docs should source its
  palette from `theme_web` (the one cross-cutting primitive) and later reuse the
  connector-pane/properties PANELS. Shared "html kit" = **tokens + panels, NOT a
  generator** — don't merge the assemblers.

---

## Part D — Migration order (keep tests green throughout)

1. **`viewer/` extraction** — pure mechanical move (6 files) + repoint importers
   (cli/mcp/`vi_diff`). Lands `diff_viewer` out of `render/`. Low risk.
2. **`diff` dissolution** — add `viewer/render_page`+`viewer/diff_page`; point surfaces
   at them; text/json → `graph.diff` direct; delete `vi_diff.py`.
3. **Lift engines out of `graph/`** — move `netlist`/`describe`/`diff` to top-level
   packages, enforce the `graph ← netlist ← {describe,diff}` DAG, promote `_uid_of`.
4. **Split god-modules internally** — one package at a time, tests green after each:
   `glyph/` first (self-contained, clear families), then `diff/`, `netlist/`, `draw/`,
   `scene/`, `cli/`. Each split is move-symbols + re-export shim + repoint + drop shim.
5. **Palette DRY** — publish `theme_web` tokens; docs adopts.

Each step is independently shippable and reversible. Run `uv run ruff check`,
`uv run python -m pyright src/`, `uv run pytest -q` after every step.

---

## Rejected options (do not revisit without new evidence)

- **`core/` package** — empty name; `graph`/`parser` ARE the core.
- **Naming glue like engines** — it's the *opposite* of an engine (impure vs pure).
- **Per-feature viewers** (`diff/viewer` + `render/viewer`) — they share the kit, so
  this just re-creates the shared folder (= `viewer/`) plus cross-feature coupling.
- **Format-matrix** (every capability × every format) — formats are earned.
- **Combined HTML generator** producing both viewers and docs — different artifact
  classes (single interactive page vs linked site); only the palette/panels are shared.

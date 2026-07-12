# Modularization proposal: splitting the god-files

A plan to mechanically break up lvkit's largest modules along
single-responsibility seams. The goal is **low-risk, reviewable moves** — extract
cohesive groups of functions into focused modules, re-export from the original
module so no import breaks, and gate every step on the existing test suite plus
the byte-identical render/codegen checks.

## Why

Seven modules exceed 1,000 lines and several more sit at 700–900. Size alone
isn't the problem; the problem is that each mixes several responsibilities, so a
change to (say) selector chrome forces a reader through wire routing and FP
terminals in the same file. The worst case is a single **~885-line method**
(`ConstructionMixin._add_vi_to_graph`, construction.py:265–1150).

| File | Lines | Distinct responsibilities today |
|------|------:|--------------------------------|
| `render/draw.py` | 1664 | node draw · connector-pane/help overlay · structure borders + selector chrome · FP terminals · scene orchestration |
| `graph/construction.py` | 1644 | constant decode · **one 885-line `_add_vi_to_graph`** · subVI linking · type propagation · structure terminals |
| `render/scene.py` | 1520 | render dataclasses · frame-path logic · structure borders · wire-net building/routing glue · constant-geometry trim · `build_scene` |
| `cli.py` | 1207 | argparse wiring · per-subcommand handlers |
| `vilib_resolver.py` | 1164 | (assess separately) |
| `parser/vi.py` | 1135 | parse orchestration · node/wire/terminal extraction · **default-data byte decoding** · subVI info |
| `graph/queries.py` | 1039 | (assess separately) |
| `parser/node_types.py` | 978 | node dataclasses · ~25 handler classes · registry |
| `render/glyph.py` | 930 | ~22 glyph dataclasses |

## Principles (clean-code, applied mechanically)

1. **One concern per module.** A module should have a single reason to change.
   Target ≲ 500 lines, but cohesion — not a line count — is the real criterion.
2. **Moves, not rewrites.** Each step is cut-a-group-of-functions → paste into a
   new module → fix imports. No behavioural change; diffs read as pure relocation.
3. **Preserve the public surface.** The original module keeps working by
   re-exporting the moved names (`from .wiring import build_wire_nets  # noqa: F401`).
   Callers and tests don't change in the same commit as the move.
4. **Extract shared helpers downward to a leaf.** When two new modules both need a
   helper, push it to an existing leaf (`render/geometry.py`, `parser/utils.py`) —
   never create an import cycle (this is why `default_value_expr` moved to the
   leaf `ast_utils`, not `builder`).
5. **Split god-methods before god-files.** A 100-line function in a 1,600-line
   file is fine; an 885-line method is not. Decompose the method first, then the
   surrounding functions fall into obvious groups.
6. **Package, don't proliferate.** When a file yields 4+ children, make it a
   package (`render/draw/…`, `parser/nodes/handlers/…`) with the orchestrator as
   `__init__` or a thin top-level module.

## The safe refactor recipe (per move)

```
1. Pick one cohesive group (below).
2. Create the new module; move the functions verbatim + their private helpers.
3. In the old module: `from .new_module import <names>  # re-export`.
4. uv run pytest -q            # green
5. uv run ruff check . && uv run python -m pyright src/
6. Byte-identical gate: render + codegen a fixed VI set, diff SVG/py hashes
   against pre-move (these checks already exist — see #64/#65). Must be identical.
7. Commit. One group per commit — each independently revertable.
Later, sweep call sites to import from the new module and drop the re-export.
```

## Proposed splits

### `render/draw.py` (1664) → a `render/draw/` package
Five self-contained drawing concerns already delimited by the current section
comments:

| New module | Moves (draw.py functions) | ~lines |
|-----------|---------------------------|-------:|
| `draw/nodes.py` | `draw_node`, `_glyph_bounds`, `_expand_axis`, `_inset`, `_draw_owned_label`, `_draw_invert_bubbles`, `_draw_formula_node`, `_draw_formula_tunnel` | ~230 |
| `draw/connector_pane.py` | `_PaneLabel`, `_pane_label`, `_term_side_and_frac`, `_spread_1d`, `_pane_stub_points`, `_draw_connector_panel`, `draw_help_overlay`, `_node_tooltip`, `_terminal_help_lines`, `_terminal_*` | ~350 |
| `draw/structures.py` | `_draw_border_terminal`, `_draw_for_loop_border`, `_draw_while_loop_border`, `_SelectorGeom`, `_selector_geom`, `_draw_frame_*`, `draw_structure`, `_draw_sequence_border`, `_error_border_color` | ~430 |
| `draw/fp_terminals.py` | `_fp_type_label`, `_draw_fp_value_cell`, `_draw_array_index_column`, `draw_fp_terminal` | ~140 |
| `draw/scene.py` | `draw_scene`, `_draw_layer_content`, `_draw_layer_coercion_dots`, `_is_interactive_structure` | ~120 |

`render/draw.py` becomes the package `__init__` re-exporting `draw_scene`,
`draw_node`, `draw_structure`, `draw_fp_terminal`, `draw_help_overlay`.

### `graph/construction.py` (1644) → decompose the 885-line method first
`_add_vi_to_graph` is the priority. It is a sequence of `if node.node_type == …`
branches, each building one graph-node kind. Extract each branch to a builder:

| New module | Content |
|-----------|---------|
| `graph/build/node_builders.py` | one `build_<kind>(...)` per branch (primitive, subvi, case, loop, sequence, property/invoke, nmux, formula, …); `_add_vi_to_graph` shrinks to a dispatch loop |
| `graph/build/constants.py` | `decode_constant`, `_lv_type_category`, `_dispatch_class_names` |
| `graph/build/subvi_linking.py` | `_connect_subvi_calls`, `resolve_dispatch_qnames`, `_resolve_terminal_indices` |
| `graph/build/type_propagation.py` | `_propagate_types_and_rematch` |
| `graph/build/structure_terminals.py` | `_build_structure_terminals`, `_enrich_nmux_terminals` |

`ConstructionMixin` stays in `construction.py` but its methods become thin calls
into `graph/build/…`. (The mixin pattern is preserved — see
`InMemoryVIGraph(LoadingMixin, ConstructionMixin, …)`.)

### `render/scene.py` (1520) → a `render/scene/` package
| New module | Moves |
|-----------|-------|
| `scene/models.py` | `RenderTerminal`, `RenderFPTerminal`, `RenderLabel`, `RenderNode`, `RenderBorderTerminal`, `RenderStructure`, `RenderWireNet`, `RenderCoercionDot`, `Scene` |
| `scene/frames.py` | `_frame_path`, `encode_frame_path`, `_frame_compatible`, `_is_default_visible`, `_frame_info`, `_selector_label`, `_format_ranges`, `_is_stacked_sequence` |
| `scene/borders.py` | `_structure_borders`, `_reposition_mux_aggregates`, `_render_terminals`, `_formula_border_centers` |
| `scene/wiring.py` | `_build_wire_nets`, `_wire_path`, `_exit_side`, `_stub`, `_wire_edge_point`, `_entry_edge_point`, `_endpoint_containers`, `_wire_exempt_structures`, `_innermost_common_container`, `_router_for`, `_wire_role`, `_wire_carrier_type` |
| `scene/const_geom.py` | `_trim_string_const_geom`, `_string_const_lines` |
| `scene/build.py` | `build_scene`, `_arith_coercion_dots`, `_drawn_bounds` (orchestration) |

`_strip_prefix` and small shared helpers → `render/geometry.py` (leaf).

### `parser/vi.py` (1135)
| New module | Moves |
|-----------|-------|
| `parser/default_data.py` | `_decode_default_data`, `_decode_path_default`, `_decode_string_default`, `_decode_numeric_default`, `_decode_element`, `_get_numeric_size`, `_parse_ddo` (~300 lines of byte decoding — a clear unit) |
| `parser/subvi_info.py` | `_extract_subvi_info`, `_resolve_qualified_name` |
| `parser/vi.py` (stays) | `parse_vi` + `_parse_*` orchestration, node/wire/terminal extraction |

### `parser/node_types.py` (978) → `parser/nodes/handlers/` + models
- `parser/node_models.py` — the ~12 `ParsedNode` subclasses.
- `parser/nodes/handlers/` — handler classes grouped (subvi/dispatch, array, loop/select, property/invoke, …) with `NODE_HANDLERS` assembled in `handlers/__init__.py`.

### `render/glyph.py` (930) → `render/glyphs/` (lower priority)
Cohesive already (all `Glyph` dataclasses), but splittable by family:
`glyphs/text.py` (Labeled/Wrapped/Constant/Boolean), `glyphs/structures.py`
(Formula/LocalVar), `glyphs/mux.py` (Bundle/Unbundle/ByName/Arith/Compound),
`glyphs/misc.py` (Icon/Svg/Bracket/Variant/ErrorCluster). Do this last.

### Not yet analysed
`cli.py` (1207 — likely one handler per subcommand → `cli/commands/`),
`vilib_resolver.py` (1164), `graph/queries.py` (1039), `graph/loading.py` (976),
`docs/html_generator.py` (923). Same recipe; assess after the top four land.

## Sequencing (highest value / lowest risk first)

1. **`construction._add_vi_to_graph`** — decompose the 885-line method into
   `graph/build/node_builders.py`. Biggest readability win; the byte-identical
   codegen gate makes it safe.
2. **`draw.py` → package** — five clean seams, purely additive re-exports.
3. **`scene.py` → package** — `models` + `wiring` are the two heavy, independent
   chunks; extract those first, then `frames`/`borders`.
4. **`vi.py` default-data decode** — a tidy, isolated ~300-line lift.
5. **`node_types.py`, `glyph.py`** — mechanical, do when convenient.

## Guardrails

- **Never in the same commit:** move code *and* change its logic. Relocation
  commits must be behaviour-preserving; the byte-identical render/codegen hashes
  are the proof.
- **No new cycles.** `render/` leaf order: `geometry` ← `glyph`/`backend` ←
  `scene/*` ← `draw/*`. `graph/build/*` may import models + parser, never
  `queries`/`loading`. If a shared helper would cross a layer, push it to a leaf.
- **Determinism preserved.** Sorted iteration and `_node_order_key` usage move
  verbatim; the determinism tests (`test_render` hash-seed sweeps) stay green.
- **Re-export, then sweep.** Keep the old public names importable until a
  follow-up sweep updates call sites — decouples the risky move from the noisy
  import churn.

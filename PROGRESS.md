# Deterministic codegen build-out — progress log

Branch `feat/codegen-op-registry`. Overnight autonomous run (started 2026-09-09).
Goal: find/fix codegen-correctness issues, get more OpenG VIs to actually
**execute**, and drive front panels from each VI's real front-panel layout.
Bias: simple generic reusable rules over specific spot fixes
([[feedback_codegen_generic_over_specific]]).

## Acceptance harness
`.tmp/acceptance.py` generates each VI and *executes* its main function with
type-appropriate dummy args, clustering failures by bug class. It's an
approximate finder (dummy args can't be perfect); every real "works" claim is
confirmed by direct execution. Baseline on 60 VIs (string/numeric/file/array):
**9/60 execute.**

## Done
- **op-registry seam** (commit `1938829`) — neutral `op` tag + auto-discovering
  per-language handler registry; target-language translation lives in the
  generator, not the JSON. First handlers: string↔bytes, string subset, max&min.
- **For-loop auto-index fix** (`a938eee`) — input-array classification computed
  once and reused (was derived twice and diverged, leaking the output
  accumulator into the loop range). Net −18 lines. Build Path array instances
  now generate + execute; loop suite 44/44.
- **Tier-A op handlers** — to-lower, reverse-string, byte-array→string,
  quotient&remainder, not-XOR (bool), to-U32. Generation gaps 29→17 on the 60-VI
  set (those VIs now generate; several then hit the runtime bugs below).
- **Runnable vertical** `examples/build-path-nicegui/` — OpenG Build Path →
  pure `logic.py` + NiceGUI panel bound to it.
- **Constant formatting, single source of truth** — `_format_constant` gained an
  array/list case (validates via literal_eval → canonical literal); `constant.py`
  routes its non-pre-bound path through the SAME formatter instead of a broken
  value-derived name. Concrete win: empty array constants now render as real
  `[]` lists, not the string `"[]"` (fixed a test that had enshrined the bug).
- **Missing math import (prim 1076)** — declared `import math` the same way 1904
  already does (fixes `undefined name 'math'`).

## Known bug classes (next targets, generic fixes)
1. **Constant feeding a loop auto-index tunnel** — a nested array constant is
   correctly formatted now, but when it drives a loop as an auto-indexed array
   via a TUNNEL, `ctx.resolve(outer_terminal)` returns an auto-derived name
   instead of tracing to the constant's literal binding. Deeper resolution/edge
   issue than formatting (Trim Whitespace still fails on this).
2. **Dropped intermediate (`product`) — single-use inlining × parallel tiers.**
   Highest cascade value (unblocks the Random Number VIs). In
   `Random Number - Within Range` the chain Subtract→Multiply→Add is inlined
   into the return, but Multiply keeps its output NAME `product` with no
   assignment emitted (Add reads `low + product`; `product` is undefined; the
   parallel-tier `number` is also left dead). Root cause is the interaction
   between single-use expression inlining and the parallel-tier / cross-tier
   value passing in `builder.generate_body` / primitive output binding —
   touches core logic, so fix it deliberately with the loop/case rigor, not a
   spot patch. **Cross-branch UnboundLocal (`long_integer`) is likely the same
   family.**
3. **Constant feeding a loop auto-index tunnel** (see above) — resolution/edge
   issue; blocks Trim Whitespace.
4. **Empty body** — logic dropped entirely (Coerce to Enum, Timestamp Constant;
   "Comment" is legitimately empty).
5. **Coverage:** 1166 Type Cast, 3914 Search&Replace (Tier-B, deferred — need
   real semantics, not a hack); several `Build Error Cluster`/vilib terminal gaps.

### Cascade note
Most run-failures on the sample are IMPORT cascades: VIs importing a broken subVI
(`trim_whitespace`, `to_proper_case`, `random_number`, `compare_two_paths`). So
fixing #2 and #3 (the roots) unblocks the most VIs at once — attack those first.

## Metric
Acceptance harness (`.tmp/acceptance.py`, 60-VI sample): started **9/60 execute**,
now **10/60**, with generation gaps down 29→17. The bug fixes above are mostly
prerequisites that unblock generation; the RUN count climbs once the two cascade
roots (#2, #3) land.

## How to review
- Panels: `uv run python scripts/gen_panel.py <vi> -o outputs/panels/<name>` then
  `uv run --with nicegui python outputs/panels/<name>/app.py` → http://localhost:8080.
  Point it at a CONCRETE variant .vi (not a polymorphic wrapper) for clean widgets.
- Tweak layout: `panel.py` positions are plain px from the VI's bounds in
  `panelgen/panel_gen.py::_render_container` — one place to adjust.
- Commits this run are on `feat/codegen-op-registry`.

## Front-panel-driven panels (DONE — replaces hand-picked layouts)
`scripts/gen_panel.py` + `scripts/panelgen/` generates `logic.py` (via
`build_module`) + `state.py` (dataclass per control/indicator) + `panel.py` +
`app.py` for a VI. **panel.py lays widgets out by the VI's real FP `bounds`**
(absolute px, normalized to a 16px margin), widget type by `control_type`,
indicators disabled, Run handler matched by name to the logic signature. Verified
serving (HTTP 200) for Build Path and a synthetic all-widget-types panel.
Run: `uv run python scripts/gen_panel.py <vi> -o <dir>`, then
`uv run --with nicegui python <dir>/app.py`.

Two FP-data gaps found (parser work to make cluster layouts exact):
- **Cluster-child bounds aren't panel-absolute** — the nested cluster pane's
  `origin`/`docBounds` are discarded when recursing children
  (`parser/vi.py:1386-1412`); children carry local (sometimes negative) coords,
  so the generator flows cluster fields in a column instead of placing them.
  Adding the nested pane origin to `ParsedFPControl` would fix it.
- **Cluster children never get a decoded default** — `_parse_ddo`
  (`parser/vi.py:1394-1400`) recurses children with `default_data=None`.

Known limitation: a polymorphic VI's wrapper has empty `inputs`, so the generator
picks the alphabetically-first variant (and says so); point it at a concrete
variant .vi for a stable panel. Array-typed controls (`indArr`) fall back to a
text input with a TODO.

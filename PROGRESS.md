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
- **Case-structure default emitted last** (`7028e21`) — a bare `case _:` matches
  everything, so Python rejects any case after it; the default frame's case is
  now appended after the rest regardless of frame order. Convert File Extension
  (and any VI whose Default frame isn't last) now compiles. Case/e2e: 46 passed.
- **FP-layout-driven panel generator** (`114d8b9`) — `scripts/gen_panel.py` +
  `scripts/panelgen/` emit logic/state/panel/app; **panel widgets are positioned
  from the VI's real FP `bounds`** (this replaces the hand-picked layouts).
  Verified serving HTTP 200. Two FP-parser gaps found (see below).
- **Output-return expression parsed, not a bare Name** (`49d477d`) —
  HIGH-LEVERAGE. `build_return_stmt` wrapped the resolved output in
  `ast.Name(id=value)`, but `value` is often a compound expression string (e.g.
  `"low + product"`); the malformed Name hid the loads, so dead-code elimination
  deleted the assignments the return depended on. `parse_expr` fixes it for every
  VI with a compound output. Random Number - Within Range now runs (0,10 → 7.08).
  Broad codegen suite: 499 passed.

## Known bug classes (next targets, generic fixes)
1. **subVI import-name mismatch (single-VI generation) — highest cascade value.**
   When a VI is generated on its own, the entry function is named by full path
   (`homeryan…random_number___within_range__ogtk`) but a caller imports the SHORT
   name (`from ..openg.<mod> import random_number___within_range__ogtk`) → the
   subVI module doesn't define that name → ImportError in every importer. This is
   what still blocks the Random Number, Trim Whitespace, To Proper Case, Compare
   Two Paths importers even though those subVIs' own logic is now correct. Likely
   a naming inconsistency between entry-VI vs dependency-VI function naming in the
   single-VI path (pipeline/multi-VI e2e tests pass, so the multi-VI path is
   consistent — diff the two). Fix unblocks the biggest cluster.
2. **Constant feeding a loop auto-index tunnel** — a nested array constant is
   formatted correctly now, but when it drives a loop as an auto-indexed array via
   a TUNNEL, `ctx.resolve(outer_terminal)` returns an auto-derived name instead of
   tracing to the constant's literal binding (resolution/edge issue, not
   formatting). Blocks Trim Whitespace.
3. **Cross-branch UnboundLocal (`long_integer`)** — a var assigned in one
   parallel-tier branch is read in another; the branch return/capture wiring
   doesn't surface it. (Related family to the return-expression fix, but distinct.)
4. **Empty body** — logic dropped entirely (Coerce to Enum, Timestamp Constant;
   "Comment" is legitimately empty).
5. **Coverage:** 1166 Type Cast, 3914 Search&Replace (Tier-B, deferred — need
   real semantics, not a hack); several `Build Error Cluster`/vilib terminal gaps.

FIXED this run: the `product` dropped-intermediate bug (was the parallel-tier
theory — the actual root cause was the return-expression Name bug, #49d477d).

## Metric — read with care
Acceptance harness (`.tmp/acceptance.py`, 60-VI sample) reports **10/60 execute**,
generation gaps down **29→17**. BUT the harness UNDERCOUNTS real progress:
- it passes `"test.txt"` for un-inferrable arg types, so numeric VIs fail with
  `'<' not supported between str and int` etc. — a harness artifact, not a codegen
  bug;
- the subVI import-name mismatch (#1) fails every importer VI even when the subVI
  itself is correct.
Directly verified working this run (execution, not the harness): Build Path
(scalar + array), Random Number - Within Range, Convert File Extension compiles.
The harness RUN count will jump once #1 lands.

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

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

## Design decisions made (review these — some I was unsure about)
- **Pure independent tiers emit SEQUENTIALLY** (not a ThreadPoolExecutor). The
  executor is now reserved for the held-error model. Rationale: by-value LabVIEW
  branches are order-independent, sequential is a valid serialization, it's far
  more idiomatic, and it removed the cross-branch wiring bug class. Determinism
  preserved. **If you want actual concurrency preserved for some VIs, this is the
  lever to revisit.**
- **Entry function named by the VI's display name**, not its file path (single-VI
  generation was producing giant path-mangled names that callers couldn't import).
- **Op-registry**: target-language translation lives in the generator keyed by a
  neutral `op` tag, not as Python strings in primitives.json.
- **String Subset → `s[offset:][:length]`** (idiomatic; handles unwired length
  with no None-comparison warning).
- **Array/list constant feeding a loop is materialized into a real local** before
  the loop (was an undefined value-derived name).
- **Functional regression suite** (`tests/test_corpus_functional.py`, marker
  `functional`): executes generated code and asserts real outputs — the guardrail
  that a working VI stays correct. `pytest -m functional`.

## Fixed this run (correctness, each verified by executing generated code)
Entry-function + poly-wrapper naming (single-VI generation named by full path →
ImportError in every caller; now the display name); return-expression parsed as
real AST (dead-code elim was deleting output producers); For-loop iteration index
`i` bound; PASSTHROUGH input tunnels no longer auto-indexed (Reorder family now
correct); poly wrapper passes only each variant's params; array + Path constants
formatted correctly; array literal feeding a loop materialized; case default
emitted last; pure tiers emit sequentially (idiomatic, no ThreadPoolExecutor);
+8 Tier-A op handlers.

**Confirmed correct + locked in the functional suite (`-m functional`):** Random
Number - Within Range, String to Character Array, Strip Path - Traditional, VI
Library, Reorder 1D Array (I32 / DBL / String).

## Known bug classes (next targets — each needs generic, not spot, fixes)
1. **Element-wise primitive over an array** — a scalar primitive (e.g. Boolean To
   (0,1)) wired to an ARRAY should map over elements (`[int(bool(x)) for x in a]`),
   but the elementwise machinery only broadcasts operators, not function templates
   → `sum(int)` etc. Blocks the Conditional Auto-Indexing Tunnel family (~25).
2. **Deep-chain complex VIs** — Trim Whitespace, To Proper Case, Compare Two Paths,
   Number to Proper Engl Text each have several compounding issues (byte/char
   coercion, nested lookup-table indexing, cross-node value drops). Fix by tracing
   each to its generic root, not per-VI patches.
3. **Empty body** — logic dropped entirely (Coerce to Enum / Timestamp Constant use
   Variant/Timestamp ops that resolve to nothing; "Comment" is legitimately empty).
4. **Coverage (Tier-B):** 1166 Type Cast, 3914 Search&Replace, To/From Variant,
   Flatten/Unflatten — a real type-descriptor (de)serialization subsystem; several
   `Build Error Cluster`/`Get File System …` vilib terminal-mapping gaps.

## Metric — read with care
Acceptance harness (`.tmp/acceptance.py`) executes each VI with dummy args over 6
libs; the raw number stays ~17/140 because it UNDERCOUNTS badly and the remaining
libs are the complex families:
- it passes type-guessed dummies, so many "RUN-FAIL"s are arg-type mismatches
  (a numeric VI given a string), not codegen bugs;
- each complex-family VI (sort/reorder/tunnel) has a CHAIN of bugs — a fix moves it
  one link forward without flipping "RUN" until the whole chain clears (the reorder
  family did clear and is confirmed correct, but the harness masks that behind
  poly-wrapper dummy shapes).
The trustworthy signal is the **functional suite (7 VIs, exact-output asserted)**
plus the direct executions cited above. The harness is a bug-finder, not a
scoreboard.

## How to review
- Panels: `uv run python scripts/gen_panel.py <vi> -o outputs/panels/<name>` then
  `uv run --with nicegui python outputs/panels/<name>/app.py` → http://localhost:8080.
  Point it at a CONCRETE variant .vi (not a polymorphic wrapper) for clean widgets.
- Tweak layout: `panel.py` positions are plain px from the VI's bounds in
  `panelgen/panel_gen.py::_render_container` — one place to adjust.
- Commits this run are on `feat/codegen-op-registry`.

## NEXT overnight session — planned, ready to go
**`docs/_internal/design/nicegui-control-library-plan.md`** — build a typed
control library (real ARRAY control with add/delete/modify; numeric/bool/enum/
cluster controls), size widgets from the VI's bounds (not just position), and
wire front-panel default values. Current panels render arrays as 15-char string
inputs (the string fallback) and don't size by bounds — the plan fixes that.

## Front-panel-driven panels (v1 done — bounds-positioned, string-fallback controls)
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
variant .vi for a stable panel.

### Update (2026-09-10) — real 1D array control (plan Phase 2 done)
`indArr`/`array` controls now render a real, editable array control
(`scripts/panelgen/controls_runtime.py::array_control`) composed from native
NiceGUI (`ui.column`/`ui.row`/`ui.input`/`ui.button`, `@ui.refreshable`) — add,
delete, edit elements — **not** the string-input fallback. `generate.py` copies
it into each panel as `controls.py`. Array *indicators* refresh after Run
(`_w_<field>()`; the control isn't `bind_value`-driven). Fixed a latent
dataclass crash: `list` state fields now use `field(default_factory=…)`
(`state_gen._field_default_rhs`) instead of a bare mutable `[]`.
Guarded by `tests/test_panel_gen.py`; verified on Reorder 1D Array2 (I32)
(round-trips `[10,20,30]`+`[2,0,1]`→`[30,10,20]`, serves HTTP 200).
Remaining plan phases (typed scalar library, parser defaults + array
element_type, size-from-bounds *height*, cluster control, 2D arrays) still open —
see `docs/_internal/design/nicegui-control-library-plan.md`.

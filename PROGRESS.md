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

## Known bug classes (next targets, generic fixes)
1. **Missing import (`undefined name 'math'`)** — a template emits `math.*`
   without emitting `import math`.
2. **Cross-branch UnboundLocal (`long_integer`)** — a var assigned in one
   parallel-tier branch is read in another.
3. **Dropped intermediate (`product`)** — a multiply feeding an output is
   dropped (Random Number - Within Range).
4. **Array-constant → undefined identifier** — a boolean/array constant renders
   as a name (`false_false_..._true`) instead of a list literal (Trim Whitespace).
5. **match-case wildcard ordering** — `case _:` emitted before other cases
   (Convert File Extension).
6. **Empty body** — logic dropped entirely (Coerce to Enum, Timestamp Constant;
   "Comment" is legitimately empty).
7. **Coverage:** 1166 Type Cast, 3914 Search&Replace (Tier-B, deferred — need
   real semantics, not a hack); several `Build Error Cluster`/vilib terminal gaps.

## Front-panel-driven panels (planned)
Parsed FP model has per-control `bounds (top,left,bottom,right)`, `control_type`,
`is_indicator`, `name`, `enum_values`, cluster `children` — enough to position
NiceGUI widgets by the VI's real panel geometry. The NiceGUI generator will emit
`logic.py` + `state.py` + `panel.py`, panel laid out from those bounds so it's
tweakable and matches the VI (replacing the hand-picked layouts).

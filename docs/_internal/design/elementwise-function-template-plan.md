# Element-wise FUNCTION-template broadcast — execution plan

**Status:** planned, not started. This is the single highest-leverage codegen
unlock: probing (2026-09-10) shows most remaining OpenG VIs fail the SAME way —
a scalar *function* template applied to an array stays scalar. Needs a maintainer
decision on approach (below) before executing, per the bug-gate.

## Problem (grounded)

`src/lvkit/codegen/elementwise.py` broadcasts only arithmetic/comparison/unary
**operators** into `_lv.*` calls (`_BINOP`/`_CMP` maps + `_ArrayifyBase` visiting
`BinOp`/`UnaryOp`/`Compare`). A primitive whose scalar template is a **function**
— e.g. Boolean To (0,1) = `int(bool(in_1))`, or `round(...)`, `len(...)` — wired
to an array is emitted scalar. Evidence, each executed:

- `Conditional Auto-Indexing Tunnel (I32)`: emits
  `zero_or_one = int(bool(elements_to_keep))` (bool of a whole list → `True` → 1),
  then `sum_ = sum(zero_or_one)` → `TypeError: 'int' object is not iterable`.
- `1D Array to String` (["a","b","c"], ",") → `delimited_string=''`.
- `Compute 1D Index` ([1,2],[3,4]) → `TypeError: 'int' object has no len()`.
- `Remove Duplicates from 1D Array` [1,2,2,3] → `output_array=[]`.

## Seam

`src/lvkit/codegen/nodes/primitive.py:197`
`arrayify_ops = bool(resolved.elementwise and _has_array_input(node))` — flows
into `_build_dict_hint`/`_build_string_hint`, which call `elementwise.arrayify()`
on the template AST. `_has_array_input` (primitive.py:31) already uses the graph's
real per-terminal `lv_type.kind == LVTypeKind.ARRAY`, so "is this input an array"
is known. Two gaps: (a) the primitive may not be flagged `elementwise`; (b) even
when flagged, `arrayify` can't broadcast a `Call`.

## Decision needed — pick the broadcast shape (A or B)

**A. Emit an idiomatic comprehension** (preferred for "idiomatic Python"):
for a template `T(in_1, in_2)` where in_1 is an array and in_2 scalar →
`[T(x, in_2) for x in in_1_var]`. Multiple array inputs → `zip`:
`[T(a, b) for a, b in zip(in_1_var, in_2_var)]`. Requires substituting each
array input's var with a fresh loop var in the template AST.

**B. A runtime `_lv.vmap(fn, *args)` helper** + rewrite the template into a lambda:
`int(bool(in_1))` → `_lv.vmap(lambda _a: int(bool(_a)), in_1_var)`, where `vmap`
maps `fn` over the array arg(s) and broadcasts scalars. Uniform, less idiomatic.

Both need the same LabVIEW-semantics confirmation: LV polymorphic prims broadcast
element-wise with **scalar broadcasting** (a scalar operand pairs with every
element) and **zip** semantics for equal-length arrays. Confirm against the graph
(the per-terminal types say which inputs are arrays) — never open LabVIEW.

## Steps (once A or B is chosen)

1. Generalize the array-input detection already at `primitive.py:197` into a
   "this template must broadcast" signal: `_has_array_input(node)` AND the
   substituted template contains a construct `arrayify` leaves scalar (a `Call`,
   `Subscript` with int index over a scalar, etc.). Do NOT gate on a hand-list of
   primitive names — drive it from the graph's terminal types + the template AST.
2. Implement the chosen rewrite in `elementwise.py` (a new `broadcast_call` /
   comprehension builder), applied at the same seam `arrayify` is today.
3. Ensure the primitive carries the element-wise intent generically: prefer
   deriving it from "scalar template + array-typed input" over a per-prim
   `elementwise:true` flag, so a new prim needs no JSON edit. (If a flag is kept,
   document why.)
4. Guard: `pytest -m functional` (the 14 locked VIs must stay green — none use the
   elementwise-primitive path today, so this is additive), plus the full codegen
   suite. Add functional locks as VIs start working: Conditional Auto-Indexing
   Tunnel (I32) → filter-by-mask; 1D Array to String → join; Compute 1D Index →
   row-major linear index; Remove Duplicates.
5. Re-run the acceptance harness (`.tmp/acceptance.py`) — expect a jump in the
   array/string/lvdata families.

## Files
- `src/lvkit/codegen/elementwise.py` (the broadcast builder).
- `src/lvkit/codegen/nodes/primitive.py` (the gate at ~197 + how the template is
  handed to the broadcaster).
- `src/lvkit/runtime/lv.py` (only if approach B — the `vmap` helper).
- `tests/test_corpus_functional.py` (lock the newly-working VIs).

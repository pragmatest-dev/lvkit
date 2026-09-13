# Codegen fix session — design assumptions

Running log of design decisions made autonomously while finding/fixing codegen
bugs (maintainer directed: "find bugs, fix bugs, expand to more VIs, repeat for
hours; document design assumptions you have to make"). Each entry: the decision,
why, and how it was verified. Revisit any of these if they prove wrong.

## Verification protocol (every fix)
1. Root-cause from the graph/parser (no guessing, no string-matching).
2. Implement a principled, generic fix.
3. Verify: EXECUTE the generated code for the target VI against known output;
   regen a corpus slice and diff (must be equal-or-better); full `pytest`.
4. Commit only when green.

## Decisions

<!-- append entries below -->

### Polymorphic SubVI wrapper: imports, variant names, positional dispatch
`_generate_polymorphic_module` (pipeline.py) emitted a broken poly module three
ways: (1) a variant body that calls a SIBLING variant hoisted an import for it as
a standalone module — but siblings are DEFINED inline, so no such module exists
(`ModuleNotFoundError`); (2) the wrapper referenced variants by their full-path
name while `build_module` DEFINES them by basename (`NameError`); (3) the
wrapper passed caller kwargs by NAME, but a caller names args by the poly VI's
own connector pane, which need not match a variant's parameter names (e.g. a
scalar `string` vs an array `strings` → `unexpected keyword`/`missing arg`).
Fixed: drop hoisted imports whose bound name is an inline variant; name variant
funcs by basename (matching build_module); make the wrapper `(*args, **kwargs)`
that collects values in call order and dispatches POSITIONALLY via `_lv_dispatch`
(mapping to the chosen variant's own param names by slot). Verified: To Proper
Case (String Array) poly now runs — `['hi there','foo BAR']`→`['Hi there','Foo
bar']`, `'hi there'`→`'Hi there'`. Full suite green (2041).

### OPEN (poly): auto-adapt variant selection by wired type
Filter 1D Array (I32) still crashes: it calls the Remove Duplicates poly wrapper,
which dispatches on `isinstance(_vals[0], (list, tuple))` and so picks the FIRST
array variant (Path) — runtime Python type can't tell an I32-list from a
Path-list, and the caller then hits `AttributeError` on the wrong variant's
result class. The `polyIUse` call node has `preferredInstIndex=FF` (auto-adapt: no
stored instance), so LabVIEW resolves the variant by the WIRED input type at edit
time — info the graph has (the input terminal's lv_type) but the SubVI codegen
doesn't use (poly_variant_name is None). Fix direction: for a polyIUse call,
resolve the variant by matching the wired input type to the variant whose input
type matches, and emit a DIRECT call to it (not the runtime-dispatch wrapper).
Deeper subvi-codegen work; the wrapper stays the fallback for a genuinely dynamic
call.

### Case structure ↔ selector table correlated DIRECTLY via tdOffset (the real link)
The positional case↔table correlation (sort cases by VCTP index, sort tables by
DataFill TypeID, zip) broke whenever a deleted case left an ORPHAN table, and no
heuristic (literal-vs-symbolic, single-case gating) resolved the multi-case case
safely. The real link was in the binary all along and the parser discarded it: a
case's select node carries **`tdOffset`**, a client index into the dataspace type
map, and **`tdOffset + TM80.IndexShift` = the TypeID of that case's DataFill
selector table** (verified: Remove Dup 11+2=13; To Camel Case 8+6=14; Trim
Whitespace's two cases 9+2=11 and 11+2=13, orphan type_id=29 referenced by
neither). `_apply_selector_tables` now correlates each case to its table by this
direct TypeID (positional zip kept only as a fallback for a case with no
tdOffset). This fixes MULTI-case VIs the heuristic couldn't: Trim Whitespace's
leading/trailing/both modes are now each correct (the enum really is 0=leading,
1=trailing, 2=both — the old boolean-guess accidentally matched a WRONG test that
assumed 0=both; test corrected), and Sort's `method_magnitude` recovers its third
mode (0=real, 1=imag, 2=magnitude→`abs`) that the boolean guess dropped. Parser
threads the TM80 IndexShift through; the now-unused `has_open_bound` heuristic was
removed. Verified by execution + full suite green (the earlier single-case
literal/gating machinery is superseded by this).

### Case output tunnel seeds its default in a pass/unwired frame
`_resolve_output_tunnels` (codegen/nodes/case.py) aliased an output tunnel
straight to a single produced value when every frame had an inner terminal —
but a `pass`/unwired frame HAS an inner terminal that RESOLVES to None (LabVIEW
"use default if unwired"). Aliasing to the other frames' variable then produced
`UnboundLocalError` when the unwired frame ran (Trim Whitespace's leading/
trailing-only modes; String to 1D Array). Fixed by requiring every frame to
resolve to a real value before aliasing; otherwise the divergent path runs — a
merge variable pre-declared to the type default before the match, so an unwired
frame yields that default. Verified: String to 1D Array now runs
(`"a,b,c"`,`,` → `['a','b','c']`); Trim Whitespace no longer raises (mode 0
correct; leading/trailing-only still under-trim — that's the DEFERRED multi-case
selector-value identity, not this tunnel bug). Contained: 6 OpenG VIs changed, 0
syntax errors, full suite green.

### For-loop lpTun input-vs-output classified by DIRECTION, not resolve-truthiness
`To Proper Case (String Array)` (and every OpenG `(… Array)` map variant that
runs a SubVI per element and auto-indexes the result) generated a broken loop:
`for i in range(min(len(strings), len(concatenated_string))): ...` — the
per-element result never appended, the accumulator never initialised, and
`return ... =concatenated_string[i]`. loop.py classified an lpTun as INPUT
whenever `ctx.resolve(outer_term)` was truthy; an OUTPUT tunnel's outer can also
resolve (through the loop to the body value), so the accumulator was treated as
an input array — bound as `acc[i]`, leaked into the `min(len(...))` bound — and
step 2's accumulator branch (`if not outer_var:`) was skipped, emitting no
`acc=[]`/`.append`. Fixed with `_tunnel_is_input`: classify by the OUTER
terminal's DIRECTION (input vs output), applied at all three sites (step 1 input
bind, step 2 accumulator/last-value creation, step 3 input-array classification).
Now the map generates `acc=[]; for … enumerate(strings): acc.append(result); return
acc`. Full suite green.

### Delete From Array (aDelete) deleted-portion shape is type-driven (op handler)
The static template always sliced `array[index:index+length]` for the "deleted
portion". But that output is polymorphic: a single-element delete (length
unwired) returns the ELEMENT (scalar), a run delete returns a subarray. In a
per-index delete loop (OpenG Delete Elements from 1D Array) the scalar case was
emitted as a 1-element slice, which the auto-indexing tunnel accumulated into a
2-D array instead of a flat 1-D one. Moved to op `DELETE_FROM_ARRAY`: reads the
deleted-portion terminal's type — subarray keeps the slice; scalar emits
`_lv.index_array(array, index, <default>)` (guarded like Index Array, since the
array shrinks each pass and a stale index must yield the element default, not
raise — the slice form silently returned []). Verified: Delete Elements now
returns a flat `deleted_elements` with correct values and a correct trimmed
array. (Separate, pre-existing: the deleted-elements ORDER is
descending-by-index; its sort_pointers/reorder_array2 restore-order chain does
not reorder — a SubVI-chain issue, not aDelete.)

### SubVI call arguments are parsed to real AST (not wrapped in one Name)
`subvi._to_ast_value` turned every argument string into a single
`ast.Name(id=value)` — so a dotted argument like `result.field` became a Name
whose id was the whole `"result.field"` string, not `Attribute(Name('result'),
'field')`. It unparsed fine, but dead-code elimination matches by `Name.id`, so
the load of `result` was hidden inside that opaque id: DCE saw `result` as
unused, dropped its assignment (keeping the call bare for side effects), and the
consumer then referenced an unbound name (`NameError`). Concrete: Delete
Elements from 1D Array (DBL) crashed with `NameError: sort_array__ogtk_result_385`.
Fixed by parsing the value with `parse_expr`; DCE now sees the real load. General
— affects any SubVI argument that is a dotted/complex expression feeding another
node. (Separate, pre-existing: that VI's `deleted_elements` output is nested/mis-
ordered via its sort+reorder path — masked by the crash before, not this fix.)

### Spreadsheet String To Array (1539) is dimensionality-aware (op handler)
The static template only did the 2-D string case (`[r.split(d) for r in
s.splitlines()]`), so a 1-D `array type` (To Camel Case, String to 1D Array)
got a nested `[[...]]` that broke downstream joins. Replaced with op
`SPREADSHEET_STRING_TO_ARRAY` (codegen/nodes/ops/) + runtime helper
`_lv.spreadsheet_string_to_array(s, delim, ndims, elem)`: the op reads the
OUTPUT terminal's `dimensions` and element type (a wire can't carry them) and
emits ndims + elem kind as literals. 1-D splits on the delimiter OR an EOL; 2-D
splits rows by EOL and columns by delimiter; fields convert per element type
(str/int/float). To Camel Case now yields a flat word list and runs end to end.

### Boolean case frame values come from the dataspace selector table
`to_camel_case_(string)` returned `helloworld` not `helloWorld`: its boolean
Case Structure ran To Proper Case only when `size <= 1 and no_change` was TRUE
(single word), passing raw words through otherwise — inverted. The BD XML has NO
per-frame selector string, so `_extract_frame` fell back to `"True" if index ==
1 else "False"`; these frames are ordered `[index0 = passthrough, index1 =
proper-case]`, and the index guess assumes diagram order `[False, True]` — but
this VI is authored True-first, so the guess was backwards. The reliable signal
is the DATASPACE selector table (a `DataFill` cluster): here `type_id=14
ranges=[(0,0,1),(1,1,0)]` = value 0 (False) at diagram 1, value 1 (True) at
diagram 0 — the exact inverse of the guess, matching the algorithm. But
`_apply_selector_tables` EXCLUDED boolean cases ("their frames are implicit"),
so the table was never applied. Fix: include booleans, correlating
boolean-INCLUSIVE first and falling back to boolean-EXCLUDED (so a table-less
boolean can't abort a tabled non-boolean case's correlation); convert a
boolean table's 0/1 point-range to False/True in `_apply_one_table`; and treat
`displayed_frame < 0` as the "none displayed" sentinel (this boolean table has
`displayed=-1`) rather than a range failure. Full suite green.

### Multi-output dict `python_code` paired to outputs by INDEX ORDINAL, not wired position
`_build_dict_hint`/`_detect_passthroughs` (codegen/nodes/primitive.py) paired the
dict's expressions to wired outputs by position in the *wired* list. An unwired
earlier output shifted every later expression onto the wrong terminal (Search/Split
String's unused `offset of match` pushed `before`/`rest` onto To Upper/To Lower
wrong). Fixed: pair each wired output with the expression at its ordinal among ALL
outputs by index (`_output_index_ordinals`), since dict exprs are authored in
output-terminal order. Verified by To Proper Case executing correctly.

### Concatenate Strings (`concat`) handler added
`node_type="concat"` was known-but-unimplemented (emitted empty → dropped the node
and its downstream). Added `compound.generate_concat_strings`: joins string inputs
in terminal order (`+`), a 1D-string-array input contributes `"".join(arr)`, no
inputs → `""`. Registered in nodes/`_PRIM_CODEGEN`.

### Terminal-order audit vs nodes.json (VI-Scripting export) — 3 real swaps fixed
The gitignored `.tmp/nodes.json` (695-node VI-Scripting export) is the authoritative
per-terminal source, but `import_nodes_primitives.py`'s PRESERVE branch NEVER
overwrites terminals when the entry name matches — so hand-authored entries kept
stale terminal orders. Audited all shipped primitives against nodes.json:
- **nodes.json indices are DENSE (0..N); the parser's `terminal.index` is the real
  parmIndex, which is SPARSE for panes with error clusters** (verified: prim 8003
  Variant To Data parser=`0,3,8,9,11`, nodes=`0-4`). So nodes.json's order is only
  applicable when the current index SET matches it; for the 36 sparse ops
  (file/queue/notifier/VI-server/variant) the current entries are parser-correct and
  nodes.json's numbering must NOT be encoded.
- Same-index-set + genuine role permutation → real swap. Found 3: 1538 Search/Split
  String (in string/search + out before/rest), 1908 Split 1D Array (out first/second),
  1341 Two Button Dialog (in T/F button name; code is `pass`, data-only). All fixed
  from nodes.json + geometry + (for 1538/1908) execution/wiring.
- parmIndex within a connector-pane column runs bottom-to-top vs NI doc reading order,
  so same-typed terminal PAIRS (two strings, two arrays) are what earlier resolutions
  mis-ordered; type-distinguishable terminals were fine. The remaining ~42
  name-differences are vocabulary-only labels on type-distinct terminals (code
  correct); the 14 binary arith/compare ops were verified correct (idx1=y, idx2=x).

### OPEN BUG (found by executing, not yet fixed): polymorphic SubVI call kwarg-name mismatch
A caller of a polymorphic SubVI emits `wrapper(<poly-pane-name>=value)` but the
generated poly wrapper's parameter is named from the VARIANTS, so the names
disagree and the call raises `TypeError: unexpected keyword argument`. Concrete:
Filter 1D Array (I32) calls `remove_duplicates_from_1d_array__ogtk(array=...)`
but the wrapper is `def remove_duplicates_from_1d_array__ogtk(input_array=None)`.
Root: subvi call codegen takes the kwarg name from the caller's enriched
terminal (the POLY VI's connector-pane name, "array"); `_generate_polymorphic_
module` (pipeline.py) names the wrapper params from the union of VARIANT inputs
("input array"). NOTE: the poly VI's OWN `get_vi_context(...).inputs` is empty,
so naming the wrapper params from the poly pane is NOT available — the viable fix
is to emit POSITIONAL args when calling a poly wrapper (order by slot), which is
robust to the name divergence. Affects any poly SubVI whose pane terminal name
!= variant input name. Also observed nearby (separate, bigger concern): the
wrapper ALWAYS dispatches to the FIRST variant (`_path__ogtk`) regardless of
input type — `_lv_dispatch` only filters kwargs, it does not select a variant by
type, so every poly SubVI call runs variant #1.

### 5. Attribute access on a substituted literal (bit_length templates)
Number To Boolean Array (prim 1814) and Join Numbers (1171) used
`in_1.bit_length()`; when `in_1` substitutes to an integer LITERAL the result is
`0.bit_length()`, which Python parses as a malformed float (`0.` + `bit_length`)
— the template fails to parse and falls back. Fixed by parenthesizing in the
template: `(in_1).bit_length()`. KNOWN separate issue (not fixed): `bit_length()`
yields the VALUE's significant-bit count, not the integer TYPE's width, so
Number To Boolean Array produces a too-short array for non-zero values (a U8 5
gives 3 bits, not 8). The accurate width comes from the input terminal's integer
type (see `uint_mask` in primitive.py for the type→width pattern); lower these as
ops that inject the type width. Deferred.

### 6. Conversion primitives + bitwise operators broadcast over arrays
`arrayify` broadcast numeric OPERATORS (`+ - * …`) but NOT unary conversion
CALLS (`int(bool(x))`, `int(round(x))`) or BITWISE operators (`x & 0xFFFFFFFF`).
When a conversion/mask's input is an ARRAY (LabVIEW conversions are polymorphic,
applying element-wise), codegen emitted the scalar form and crashed: OpenG
Conditional Auto-Indexing Tunnel / Filter 1D Array (~48 VIs) count kept elements
via `sum(int(bool(elements_to_keep)))` — `bool()` of a list is a scalar, so
`sum(<int>)` raised "'int' object is not iterable". Fixed by extending arrayify:
(a) a `visit_Call` rewrites `int/bool/round/float/abs` over an array-valued arg
into recursively-broadcasting `_lv.int_/bool_/round_/float_/abs_` (nested calls
compose, since an `_lv.*` result is array-valued); (b) bitwise `& | ^ << >>` join
the broadcast operator map (`_lv.bitand/bitor/bitxor/lshift/rshift`); (c) VI input
parameters whose lv_type is an array are now tracked in `array_vars` (they were
not — only primitive array OUTPUTS were), so the module arrayify pass fires on
them. Conditional Auto-Indexing now filters correctly; guarded by an executing
test. Impact is contained to the ~48 array-conversion VIs; full corpus regen has
0 syntax errors and the full suite is green.

### 4. Index Array out-of-range → element default
`aIndx` (Index Array) emitted `array[int(index)]`, which raises `IndexError` on
an out-of-range index; LabVIEW instead returns the element type's DEFAULT and
never raises (Trim Whitespace indexes a 33-entry whitespace table by a raw byte
0-255). Fixed via the existing generator-owned "op" mechanism: aIndx now carries
`op: "INDEX_ARRAY"`, whose handler injects the element's REAL default (from the
output terminal's lv_type, via `default_value_expr`) into a runtime helper call
`_lv.index_array(array, index, <default>)` — accurate, not a `type(arr[0])()`
guess, and single-eval (no duplicated array expression). Multi-dimensional
indexing (element type is itself an array) keeps a plain subscript, since the
expandable machinery composes the dimensions. Two supporting fixes were needed:
(a) `primitive.py` was discarding a node_type resolution with no `python_code`
BEFORE the op handler could supply it — now it keeps op-carrying resolutions;
(b) the polymorphic module builder dropped variant import lines — now it hoists
them into the shared header (so `_lv` is imported). Verified: Search/Sort still
correct; Trim's LEADING trim now works. (Trim's TRAILING trim + its use of
primitive 1126 "Lexical Class" remain separate open bugs.)

### 3. For-loop output tunnels honor indexing mode (last-value vs accumulate)
A For-loop output tunnel (lpTun, outer dir=output) was ALWAYS lowered as an
auto-indexed accumulator (`var = []; var.append(...)`), ignoring the tunnel's
mode. A tunnel with indexing DISABLED (`TunnelMode.PASSTHROUGH`) is a
LAST-VALUE tunnel — it carries the value from the final iteration, a scalar —
so accumulating it into a list is wrong (e.g. Trim Whitespace's leading/trailing
scan loops output a stop index that downstream arithmetic negates: `-values`
crashed on a list). Fix: branch on `tunnel.mode` — INDEXING accumulates into a
list; PASSTHROUGH assigns `var = <inner value>` each iteration (final = last),
pre-seeded to the type default so a 0-iteration loop yields the default (LabVIEW
"tunnel default" semantics). Mirrors the existing INPUT-tunnel PASSTHROUGH
handling in the same file.

### Negative case selector value — fixed for SINGLE-case over-subscription
Remove Duplicates from 1D Array (*, ~18 variants) rendered `case 1:` where the
intent was `case -1:` (the "not found" frame of a Search → case). The case has
ONE structure but TWO dataspace selector tables: the real one
(`ranges=[(-1,-1,diag=1)]`, literal) and an orphan left by a deleted case
(`(INT_MIN, 0, startType=3, endType=1, diag=0)` — a symbolic one-sided OPEN
range). The count mismatch (2 tables ≠ 1 case) aborted correlation → wrong
fallback. `_decode_selector_table` now reads the U8 range-type fields and flags a
table with a symbolic-filler endpoint (`SelectorTable.has_open_bound`);
`_apply_selector_tables`, when a SINGLE case is over-subscribed and exactly one
table is fully literal, correlates that literal table. This resolves Remove Dup
(now `case -1`, verified: `[1,2,2,3,1]`→`[1,2,3]`, removed `[2,4]`).

GATED TO SINGLE CASE ON PURPOSE: with MULTIPLE case structures, dropping the
symbolic tables misaligns the positional zip and applies the wrong table to the
wrong case — observed miscorrelating Trim Whitespace's two cases (broke the
`test_trims_leading_and_trailing_whitespace` e2e test). Multi-case
over-subscription still needs a reliable case↔table identity beyond
count/ordering and stays deferred. (Separately, Trim Whitespace's leading/
trailing-only modes still raise UnboundLocalError — a case OUTPUT-tunnel default
bug: a `pass`/unwired frame doesn't seed the tunnel var's type default before
the match. Independent of selector-value identity.)

### 1. Positional Bundle/Unbundle field indices (parser)
`class="mux"`/`"demux"` (classic positional Bundle/Unbundle) were inheriting the
by-name field-index reader (read `<i>`, default 0), so every field collapsed to
index 0 — Sort's Unbundle emitted `.numeric` for both value and pointer. Fix:
the handlers already carried a docstring saying they're positional; added a
`positional_fields` flag on the `NMuxHandler` strategy (False for nMux/decompose
= by-name, True for mux/demux = positional) and, when positional, the field
index is the drawer's position in `dcoList`. General rule, no per-VI logic.
Verified: Sort's Bundle/Unbundle list terminals now index [0, 1].

### 2. Anonymous clusters are positional tuples (codegen)
A cluster with no `typedef_name` and no `classname` (LabVIEW's on-diagram
anonymous cluster, e.g. Sort's "value+pointer" bundle — its fields are
placeholders like `field_1`) is represented in generated Python as a **tuple**,
accessed positionally (`c[0]`, `c[1]`). Rationale: (a) tuples compare/sort
lexicographically, which is exactly LabVIEW's cluster sort/compare semantics, so
`Sort 1D Array` (prim 1120) lowers to `sorted(...)` for free; (b) no synthetic
NamedTuple type has to be generated/named for an unnamed cluster. Named/typedef
clusters keep attribute access (they have real field names and a class). This
applies at BOTH ends: a positional Bundle with no incoming cluster wire
CONSTRUCTS the tuple; Unbundle of an anonymous cluster indexes it. Assumption to
revisit: a named/typedef cluster CONSTRUCTED by Bundle (no incoming wire) is not
yet handled (falls through as before) — only anonymous construction is added
here.

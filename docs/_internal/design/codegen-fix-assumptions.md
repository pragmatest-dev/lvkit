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

### DEFERRED (needs maintainer / more investigation, NOT fixed): negative case selector value dropped
Remove Duplicates from 1D Array (*, ~18 variants) and build_error_cluster render
`case 1:` where the intent is `case -1:` (the "not found" frame of a Search →
case). Root cause: the case has ONE structure but TWO dataspace selector tables
(`parse_selector_tables`): the real one (`ranges=[(-1,-1,diag=1)]`) and a
spurious `(INT_MIN, 0, startType=3, endType=1, diag=0)`. `_decode_selector_table`
IGNORES the two U8 range-type fields (fields[2]/[3]) that mark symbolic/open
filler — so the symbolic table decodes as a literal one. `_apply_selector_tables`
then aborts on `len(tables)=2 != len(cases)=1`, leaving a wrong fallback value.
A fix (honor the range-type fields to drop wholly-symbolic tables from the
literal correlation, mirroring the SelectRangeArray32 symbolic handling) was
INVESTIGATED and rejected: the orphan table's range is `(INT_MIN, 0, startType=3,
endType=1)`, which SelectRangeArray32's own logic reads as a LEGITIMATE one-sided
OPEN range (`x <= 0`: INT_MIN start is filler, `0` is a real endpoint) — NOT
wholly-symbolic junk. So a "drop symbolic tables" rule would silently break real
open-range selector cases elsewhere; the orphan is indistinguishable from a valid
open-range table by its content alone. The real signal is that the table
correlates to NO surviving case (it was left behind by a case removed at edit
time). A safe fix needs a reliable case<->table identity beyond count/ordering
(the correlation currently zips cases-by-vctp_index with tables-by-TypeID and
aborts on any mismatch). Left deferred — needs that identity, verified corpus-
wide. `SelectorTable.ranges` also drops the start/end range-type fields today.

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

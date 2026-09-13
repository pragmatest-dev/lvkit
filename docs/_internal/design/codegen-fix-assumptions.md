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

### DEFERRED (systemic, needs careful arrayify work): conversion primitives don't broadcast over arrays
`arrayify` (codegen/elementwise.py) broadcasts numeric OPERATORS (`+ - * …`) into
`_lv.*` calls when an operand is an array, but NOT primitives whose template is a
function CALL — e.g. Boolean To (0,1) `int(bool(in_1))` (prim 1167), To Long
Integer `int(round(in_1))` (1142), To U32, etc. When such a conversion's input is
an ARRAY (LabVIEW applies it element-wise, producing an array), codegen emits the
scalar template and crashes: OpenG "Conditional Auto-Indexing Tunnel (*)" (~24
VIs) counts kept elements via `sum(int(bool(elements_to_keep)))` -> `bool()` of a
list is a scalar -> `sum(<int>)` raises "'int' object is not iterable". Fix
direction: give these conversion prims an element-wise broadcast (a runtime
helper like `_lv.index_array`, applied when the input terminal's lv_type is an
array — the op handler can see the type), or extend arrayify to rewrite the
registered conversion templates the way it rewrites operators. Systemic (several
prims + the broadcast decision) and touches the widely-used conversion path, so
verify with a full corpus regen. Not attempted here.

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
literal correlation, mirroring the SelectRangeArray32 symbolic handling) is
plausible but touches selector-table correlation used corpus-wide, incl. error
clusters — HIGH regression risk. Left for a dedicated, carefully-verified pass.
Evidence lives in the graph: `SelectorTable.ranges` carries start/end/diag but
NOT the range-type; that decode gap is the concrete thing to fix.

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

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

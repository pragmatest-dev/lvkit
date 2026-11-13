# A variant's LabVIEW type in generated code (Get TDEnum from Data and other type readers)

SUPERSEDED 2026-11-12 by `variant-values.md` (a type wrapper only at type readers fails for Format
Variant Into String, `.tmp/design_audit_typedany.md`; a trace closes 0 of 17 corpus Get TDEnum calls,
`.tmp/tdenum/summary.md`). Kept as the record of that option.

PROPOSED 2026-11-12. Replaces the rejected option A (classify the Python value when the code runs;
audit `.tmp/design_audit_tdenum.md`: ambiguous for None, enums, integer width and sign, waveform vs
cluster, empty arrays). Maintainer's idea: "a special typedAny and instantiate at call sites", only
"at calls where we need variants in this way". To be audited before code.

## What LabVIEW represents

- A LabVIEW variant holds a value AND its exact type. Where a typed wire becomes a variant (To
  Variant, or a typed wire wired into a Variant terminal, shown with a coercion dot), LabVIEW records
  that wire's type in the variant.
- Most diagrams never ask a variant its type: they carry it (a cluster field, a queue element), turn
  it back into data with Variant To Data (whose `type` input names the type), or use it as a lookup
  table (attributes).
- Some do ask, through a **type reader**: OpenG `Get TDEnum from Data__ogtk.vi` (returns the type's
  code; `codegen/nodes/vilib_op.py:5-12`), Variant To Flattened String's `type string` output
  (`codegen/nodes/ops/variant_to_flattened_string.py`), and OpenG VIs built on those (Variant to
  Header Info, Format Variant Into String, which branch on the code with a Case Structure).
- Every wire's type is in the VI (maintainer, 2026-11-10: "LV is so type strict... LabVIEW knows the
  interfaces and types"; task list: "trace the variant/refnum back to its typed source").

## The design: two steps, the first one always tried first

1. **Trace at generation time (no wrapper).** A type reader whose variant wire, followed upstream
   through tunnels, shift registers and wires inside the VI, starts at a To Variant or a coercion of
   ONE recorded type gets that type as a constant, exactly as a statically typed wire does today
   (`vilib_op._type_descriptor_code` constant; `_variant_flat.variant_static_type`, which today looks
   one wire back only). This extends `variant_static_type` into the one upstream walk; it does not add
   a second one (reuse the walk of `graph/variant_source.py`, which already follows tunnels, shift
   registers via `pair_by_position`, and loaded subVIs).
2. **Wrap only where the trace ends at a connector-pane Variant control of a VI that reads its type.**
   Such a VI (OpenG Format Variant Into String) is one Python function for every caller, and each
   caller passes a different type, so no constant fits. Then, and only then:
   - The generated package gets one helper class (generated support module, like `notifiers.py`;
     needs the maintainer's approval as a run-time helper, CLAUDE.md): `TypedAny(value, type_string)`,
     `type_string` the LabVIEW type string as a list of 16-bit numbers from the existing
     `type_string.encode` (`src/lvkit/type_string.py:92`).
   - Each CALLER that wires a typed wire into that Variant parameter (directly, or through a chain of
     VIs whose parameters pass it on untouched) writes `TypedAny(<value>, [<numbers>])` at the call.
     This is the "instantiate at call sites".
   - Inside the reading VI, a type reader on that parameter reads `.type_string` (Get TDEnum: the code
     is its first descriptor's type code; Variant To Flattened String: `type string` is it and the data
     is flattened by it, i.e. LabVIEW's format, not pickle).
   - Every node that uses the variant's VALUE (Variant To Data, a pass-through to a subVI that does not
     read the type, comparison) uses `.value`. Which nodes those are is known at generation time from
     the same trace, so no run-time `isinstance` check is written.

   Everywhere else a variant stays the Python value itself, as today.

## Where the trace cannot see

The value reaches the type reader through something the generation-time trace does not follow: a
queue, notifier or user event, a value set through a control reference or Ctrl Val.Set, a VI
reference called with a path known only when the code runs, a variant constant read from a file.
Then the reader is a gap (CLAUDE.md "ONE rule for every gap"): warning at generation, raise at run
time, naming the VI, the node and that the variant's type was not traceable. It never classifies the
Python value.

## Type strings lvkit cannot write

`type_string.encode` raises `TypeStringError` for types with no public type-string layout (refnums,
LabVIEW classes; `_require_known`, `type_string.py:230`). A wrap of such a type is a gap at that
caller, with the type's name.

## Recorded design this touches

| Recorded | Where | Here |
|---|---|---|
| "A variant is the Python value itself" | CLAUDE.md:75 | EXTENDED, needs the maintainer's yes by name: a variant is the Python value itself, EXCEPT a variant passed into a VI that reads its type, which is `TypedAny(value, type_string)` from the caller to that reader |
| A variant carries no LabVIEW type at run time; the type is known only from the graph | `codegen/nodes/ops/_variant_flat.py:1-8` | EXTENDED: still known only from the graph; the generator writes it into the call where one function serves callers of different types |
| A run-time-only type is pickled; a known type keeps LabVIEW's flattened format (2026-11-08) | CLAUDE.md:75; `variant_to_flattened_string.py:9-13` | KEPT; a wrapped variant's type is known, so it flattens in LabVIEW's format |
| Get TDEnum: static type gives a constant, a variant is a gap | `vilib_op.py:5-12`, `:61-66`; ARCHITECTURE.md:257 | EXTENDED: traced variant gives a constant; a Variant parameter of a reading VI gives the wrapped type; untraceable stays a gap. ARCHITECTURE.md:257 updated |
| Trace the variant back to its typed source (2026-11-10) | task list | KEPT: step 1 is that trace |
| Variant as lookup table = `dict`, decided at generation from the empty-variant source | CLAUDE.md:75; `graph/variant_source.py:1-9` | KEPT; the same upstream walk is reused, not copied. A variant that is both a lookup table and read for its type: TO SETTLE in audit (expected none in corpus) |
| Approved generated helpers listed by name (`errors.py`, `notifiers.py`, `panels.py`) | CLAUDE.md:75 | EXTENDED: `TypedAny` needs the maintainer's approval (Python has no value that carries a LabVIEW type) |
| One gap rule | CLAUDE.md:77 | KEPT for untraceable variants and un-encodable types |
| Generated code imports nothing from lvkit | CLAUDE.md | KEPT: `TypedAny` is generated into the package |
| General converter, never sample-specific | CLAUDE.md | KEPT: the rule is "a VI that reads a Variant parameter's type", not OpenG's VIs by name |

## Open questions for the audit

- How a coercion into a Variant terminal appears in the graph (a node, or only a type difference on
  the wire); `codegen/nodes/ops/coerce_to_type.py` is the place to check.
- Whether "VI reads the type of its Variant parameter" should be a per-VI fact computed once (like
  `variant_lookup_parameters`) and read by callers, so caller and callee always agree.
- Corpus count: how many Get TDEnum / Variant To Flattened String readers each step closes (TestCase
  has Get TDEnum 2 and Flattened String To Variant 1 in its 5 gaps).

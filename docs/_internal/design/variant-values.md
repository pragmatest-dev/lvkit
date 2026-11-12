# A variant in generated Python is a `Variant`: value, type, attributes

PROPOSED 2026-11-12 (maintainer: "design a fix for true variant code"). Revision 3, after the design
audits `.tmp/design_audit_variant_values.md` and `_r2.md` (both: build with changes). Every finding is
taken except r2's "make Variant frozen": the maintainer's rule is that every data wire branch copies
through the one copy rule, and a frozen dataclass's dict can still be changed anyway.
Supersedes `variant-type-at-run-time.md` (trace-only closes 0 of 17 corpus Get TDEnum calls:
`.tmp/tdenum/summary.md`; a wrapper only at type readers fails for Format Variant Into String:
`.tmp/design_audit_typedany.md`). Nothing here is built.

## What LabVIEW represents

- A LabVIEW variant is one value of three parts: the data, its exact LabVIEW type, and named attributes
  (each a variant). An empty variant has no data and no type, and may still have attributes (LabVIEW's
  "variant as lookup table").
- A variant is made where a typed wire meets a Variant terminal: To Variant, or a typed wire wired into
  a Variant input, cluster field, array element or indicator (a coercion dot). The type it records is
  that wire's type, which is always in the VI.
- Nodes that read a variant: its data (Variant To Data, with error 91 when the types are not
  compatible; Equal?), its type (Get TDEnum from Data, Variant To Flattened String's `type string`,
  OpenG lvdata VIs), its flattened data (Variant To Flattened String's `data string`), its attributes
  (Set / Get / Delete Variant Attribute), whether it is empty (Empty String/Path?: NI, "This function is
  also designed to work with variants"; JKI failUnlessEqual uses it to tell an unwired `delta`).

## Why this is not emulation

CLAUDE.md "NO LABVIEW IN IT" bans re-implementing LabVIEW's run time in Python. A `Variant` is not
run-time machinery: it is a plain data value holding what the LabVIEW value holds, the same standing as
the generated `LabVIEWError` for an error cluster. Generation still decides every conversion; the
generated code only reads fields. It needs one class identity across all modules of the package (a
`Variant` made in one module is compared, stored and read in another), which is why it lives in one
generated support module, `variants.py`, beside `errors.py` (needs the maintainer's approval as a
generated helper).

## Evidence from the corpus

- Get TDEnum from Data: 17 calls in 15 distinct VIs (OpenG, JKI-EasyXML, JKI VI Tester): none has a
  static or traceable source; 8 read a connector-pane Variant input (3 also read its attributes), 9 come
  from arrays of variants, subVI outputs, an empty Variant constant.
- OpenG Format Variant Into String reads one Variant input three ways (Get Variant Attribute
  "Format String", Get TDEnum, Variant To Data per type frame) and calls the Refnum, Waveform and
  Timestamp helpers (`.tmp/fvis.txt:4-8,21-53`).
- JKI TestCase: both Get TDEnum gaps run only when an equality assertion fails.

## The design (first step: pure data, no flatten)

### `variants.py` (generated once into the package)

```python
@dataclass(frozen=True)
class LVType:                  # data only: equal types compare equal from any module
    code: int                  # type-descriptor code (lv_type_codes.type_descriptor_code)
    name: str                  # LabVIEW's type name, for messages ("I32", "Path", "TestCase.lvclass")
    type_string: tuple[int, ...] | None = None   # type_string.encode, None where it has no layout

@dataclass
class Variant:
    value: Any = None
    type: LVType | None = None                       # None: an empty variant
    attributes: dict[str, "Variant"] = field(default_factory=dict)

    def __copy__(self) -> "Variant":                 # one level: its own attribute dict
        return Variant(self.value, self.type, dict(self.attributes))
```

- A `Variant` is a value on a data wire like any other, so it follows the one copy rule every data
  wire already has (ARCHITECTURE.md:111, `codegen/value_copy.py`): a node changes it in place and works
  on a copy only when the graph says another reader can still see it (`owned_input`,
  `InMemoryVIGraph.value_is_shared`, both decided at generation time). `value_copy.is_by_value_object`
  gains the Variant type. The copy is the rule's own `copy.copy` (`value_copy.py:66,83`); `__copy__`
  makes the attribute dict part of the Variant's one level, so a copied Variant never shares a dict
  with the original and an owned Variant's attribute write is `v.attributes[name] = x` with no dict
  copy. Every existing `copy.copy` site (`owned_input`, `own_path`, `user_events.py:156`'s
  `copy.deepcopy`) works unchanged.
- Not frozen: `frozen=True` only stops reassigning a field; `v.attributes[name] = x` still changes the
  dict, so it would protect nothing and add a rebuild per write. The dataclass's own `__eq__` compares
  value, type and attributes, which is LabVIEW's Equal? on variants (below).
- Nothing changes `v.value` in place: a node that reads it (Variant To Data) gets a value on a new
  wire, and that wire follows the copy rule like any other (next section).
- `code` comes from `lv_type_codes.type_descriptor_code`, which covers Path, Refnum, Timestamp and
  Waveform (audit finding 1); `type_string` is separate and optional. A type with no descriptor code
  at all is a generation-time gap at the node that makes the variant (CLAUDE.md:77: warn, then raise).
- Scalar types are written once in `variants.py` (`I32 = LVType(0x03, "I32", (...))`), imported where
  used and by Python callers (`from <package>.variants import I32`). A composite type (cluster, array,
  class) is written inline where the variant is made. `LVType` holds data only, so equal types compare
  equal from any module (r1 finding 2, r2 finding 5).
- Flatten / unflatten by a variant's type is NOT in this step. Variant To Flattened String keeps
  today's rule: a type known from the graph gives LabVIEW's format from that static type (`type
  string` the literal from the graph, as today); otherwise the whole `Variant` is pickled (value, type
  and attributes), so Flattened String To Variant of pickled data gives back the same `Variant`
  (r2 finding 4). A later step may add flattening by the run-time type, with its own note.

### Where a variant is made

There is no single choke point today (audit finding 3). The smallest set:

1. `CodeGenContext.wired_input` (`codegen/context.py:338-377`): when the input terminal is Variant and
   the source terminal is not, it makes one call into `codegen/variant_values.py` and returns its
   `Variant(<copied value>, <T>)`. This covers subVI arguments, queue and notifier elements, Ctrl Val
   and catalog templates. Bundle and Build Array: NOT SHOWN that their input terminal carries the
   field or element type (`graph/construction.py:383-391` sets only `nmux_role` /
   `nmux_field_index`); a tiny diagram (typed wire into a Variant cluster field) settles it before the
   brief, and if not, they are listed here as their own sites. A template that uses one input twice
   builds two equal Variants, which is harmless.
2. The sites that call `ctx.resolve` directly and can write into a Variant terminal: `local_variable.py:57`,
   `local_indicators.py:125,215`, `output_binding.py:112`, `builder.py:410`. Each calls the same
   helper (new module `codegen/variant_values.py`: one function `as_variant(ctx, expr, source_type,
   target_type)`).
3. To Variant (8201) and Variant constants (`constant_value._variant`).
4. Tunnels and shift registers: a coercion at a structure border is checked (both sides' types) in the
   brief's first step; if the graph shows one, it goes through the same helper.

A polymorphic "anything" input is NOT a Variant terminal and makes no variant: the VI records it with
the wired type (measured 2026-11-12, `.tmp/tdenum/variant_inputs.py`: in 15 OpenG VIs and the 31
TestCase VIs, Flatten To String's input is I32 / DBL / complex, Equal?'s an enum / I32 / refnum), so
the plain Python value passes through as today. Only a terminal whose own type is Variant wraps: in
TestCase that is 6 of 79 Variant inputs (a refnum into `Get LVClass Name from TD.vi`'s Variant
control, a cluster or refnum into an Invoke Node's Variant value); the other 73 are already wired
from a Variant.

A Variant-typed source passes through unchanged. The value put into a new Variant is copied one level
by `as_variant` when it is a list or a cluster (`copy.copy`): To Variant copies its data in LabVIEW,
and nothing in the copy rule copies an array or an anonymous cluster today (`value_copy.py:36-47`).
This is a new rule, stated here, in one place.

### The copy rule's holes (general, not only variants)

`value_is_shared` (`graph/wire_queries.py:438-480`) treats a value as shared when another reader can
see it, when it is a field of a container (`_is_field_of_container`, `:491-497`: Unbundle By Name /
In Place outputs, `nmux_role == "list"` only) or when it enters a loop through an input tunnel
(`lpTun`). Not covered today:

| Value | Why it is shared | Affects today |
|---|---|---|
| An element taken out by Index Array | still held in the array | FIXED 2026-11-12 (`b33215bb`: `Terminal.held_in` from the catalog's `part_of`); an auto-indexed loop tunnel was already covered by the input-tunnel case (test pins it) |
| A shift register's value | — | NOT A HOLE: already copied when something else reads it (`29a40634`, `tests/test_value_shared_shift_register.py`) |
| Get Variant Attribute's value | still held in the parent's `attributes` | new with this design |
| Variant To Data's data | still held in the Variant | class objects, named clusters (via today's plain variant) |

Each is one more "field of a container" case in `value_is_shared`, so `owned_input` copies before a
change; no per-type rule. Maintainer's choice 2026-11-12 (option A): a held value is always copied
before it is changed, exactly as a cluster field is today; that is LabVIEW's behaviour whatever the
order or branching. "Held by an input" is a fact of the wire model, so the graph build writes it once
on the output terminal (the input terminal that holds it), as it writes the Bundle/Unbundle field
roles (`graph/construction.py:383-391`), and `value_is_shared` follows that one field. Each source
sets it from its own data: Unbundle By Name / In Place fields from their field roles, Index Array's
element (later Get Variant Attribute's value, Variant To Data's data) from a catalog `part_of` on the
terminal, a loop's indexing input tunnel from the tunnel. Never a list of node names.

Later optimisation (maintainer: "Once the base copy is in place can we consider optimizations?"):
skip the copy when nothing reads the container after the change, by following the held value back
to its container's wire and asking the same "read later?" question `value_is_shared` asks of a plain
wire. It only removes copies, so it can come after the base copy; In Place Element fields need care
(writing through them is meant to change the container). Pinned by a test that expects no copy when
nothing else reads the array. First step: a tiny test that shows the Index Array hole for a class object
(element taken out, changed by a setter, the array read afterwards), then the same for each row.
Fixed as part of this work because the Variant design relies on it.

### Values the diagram did not make (audit finding 4)

- Defaults of a Variant type (`type_defaults.py:131`, `ast_utils.py:48-50`, `type_default.py`,
  `graph/construction.py:250`): an empty variant, through the existing rule for mutable defaults
  (ARCHITECTURE.md:274, `input_default.py:1-19`): the `def` default stays `None` (annotation
  `Variant | None`) and the body writes `if x is None: x = Variant()`, so no call shares one object;
  `is_none_defaulted` becomes true for Variants. A module-level variable (shift register seed, global)
  is made after the read as `state_seed` does.
- Panels: the panel keeps a Variant control's value as a `Variant`; the read-only view
  (`panel/runtime/widgets_opaque.py`, lvkit's own package, so it cannot import the generated class)
  shows `repr(value)` today and will show the `.value` part by duck typing (`getattr(v, "value", v)`).
  Ctrl Val.Set / Get on a Variant control store and return the `Variant`.
- Python callers: a Variant parameter is annotated `Variant`. A caller passes `Variant(3, I32)` with
  the public type constants of the module it calls. A plain value is a `TypeError` naming the parameter
  (explicit; never a guessed type).

### Readers

| Node | Generated |
|---|---|
| Variant To Data | the `type` input's static type against `v.type`: same code (and name for typedefs/classes) → `v.value`; integer to another numeric representation → LabVIEW's numeric conversion (NI Variant To Data: "If the data is an integer, you can coerce the data to another numeric representation"); a class: exact class only (NI: "variants do not recognize inheritance hierarchies"; "If you wire a child data type ... LabVIEW returns an error"); else error 91. Empty → error 91 (today's message). Replaces the Python-class guess (`variant_to_data.py:225`). NOT SETTLED by public sources: float to another numeric, typedef vs base, enum vs its integer, cluster field-name differences: each stays today's behaviour until a source settles it (`.tmp/variant_behaviour_search.md`) |
| Get TDEnum from Data | `v.type.code` (Void for an empty variant) |
| Variant To Flattened String | static type from the graph: LabVIEW format of `v.value`, `type string` the graph's literal (today); otherwise pickle the whole `Variant` |
| Flattened String To Variant | static type string (today) → `Variant(<unflattened>, T)`; pickled data → the pickled `Variant` |
| Set / Get / Delete Variant Attribute | in place: `v.attributes[name] = value` / `v.attributes.get(name, default)` / `v.attributes.pop(name, None)`, `v` from `owned_input` (a copy only when the wire forks; today's lookup table always rebuilds, `{**table, name: value}`, `set_variant_attribute.py:67`); Get with `name` unwired returns names sorted ascending (`cluster-class-variant.json:820`) |
| Equal? / Not Equal? on variants (`condition_builder.py:166-167`) | `Variant.__eq__`: type, value, attribute names and attribute values all equal. NI, "Comparing Variants" (docs-be.ni.com labview/using-comparison-functions.html): "When comparing variant data, the data type, value, attribute name(s), and attribute value(s) must match" |
| Empty String/Path? on a Variant | `v.type is None`: TRUE for an empty variant (JKI failUnlessEqual's unwired `delta`), FALSE for a variant holding `""` (NI forum "How to detect an empty Variant", m-p/4149850: "Empty String/Path is false if the empty string/Path is converted to a variant first"). Not settled: an empty variant that has attributes (this rule says TRUE), a variant holding 0 (this rule says FALSE). See the passIfEqual bug fix, which comes first and alone |
| Format Into String and other string nodes with a Variant argument | `.value`, where the catalog accepts a Variant (to enumerate in the brief) |
| Catalog VIs with a Variant terminal and no `python_code` (`net.json:75`, `shared-variable.json:210`, `datasocket.json`, …) | unchanged: gaps |

## Recorded design this touches

| Recorded | Where | Here |
|---|---|---|
| A variant is the Python value itself | CLAUDE.md:75 | REPLACED (needs yes) |
| Lookup table = `dict`, decided at generation | CLAUDE.md:75; `graph/variant_source.py:1-9`; `_variant_lookup.py`; `front_panel.py:80`; tests `test_variant_lookup`, `test_variant_subvi`, `test_set_variant_attribute` | REPLACED (needs yes): attributes on every `Variant`; the generation-time decision is deleted with every branch ported |
| A variant carries no LabVIEW type at run time | `_variant_flat.py:1-8` | REPLACED |
| Run-time-only type → pickle; known type → LabVIEW format | CLAUDE.md:75; `variant_to_flattened_string.py:10-14`; `test_variant_flatten` | KEPT in this step |
| Get TDEnum on a variant is a gap | `vilib_op.py:5-12,66-71`; ARCHITECTURE.md:257; `tests/test_unresolved.py:189-219`; `test_type_descriptor_code` | REPLACED |
| Empty variant constant = `None` | `codegen/context.py:1491-1492`; `constant_value.py:81-95`; `test_empty_variant_constant`, `test_empty_string_path_variant` | REPLACED: `Variant()`; Empty String/Path? reads `v.type is None` |
| Variant terminals on polymorphic catalog VIs and front-panel defaults | `test_polymorphic_catalog_variant`, `test_polymorphic_variant_keys`, `test_polymorphic_variants_used`, `test_default_data_controls`, `test_lv8_default_data`, `test_panel_opaque` | ported, each test updated to the `Variant` value |
| Variant To Data judged on the Python class | `variant_to_data.py:225`; `test_variant_to_data` | REPLACED: judged on the variant's type |
| Default of a type written in the body (`default_if_none`) | ARCHITECTURE.md:274 | EXTENDED: Variant default is `Variant()` |
| Panel shows a variant as read-only text | task list 2026-11-10; `widgets_opaque.py`; `test_panel_opaque` | EXTENDED: shows the value part |
| Approved generated helpers listed by name | CLAUDE.md:75 | EXTENDED: `variants.py` (needs yes) |
| NO LABVIEW IN IT / never emulate | CLAUDE.md | KEPT: argued above (a data value, like `LabVIEWError`) |
| Generated code imports nothing from lvkit | CLAUDE.md | KEPT |
| A by-value object is changed in place, copied only where its wire forks | ARCHITECTURE.md:111; `codegen/value_copy.py:1-17,36-70` | EXTENDED: Variant joins class objects and named clusters in `is_by_value_object` (also reaches `subvi.py:470`, `method_call.py:145`, `user_events.py:152`: a Variant passed to a subVI or method is copied when shared, one sent by a user event is deep-copied, as for class objects); `value_is_shared` gains the four container cases above |
| A mutable default is `None` in the `def`, made in the body | `input_default.py:1-19`, `type_default.py`; ARCHITECTURE.md:274 | EXTENDED: Variant |
| Old TypedAny note | `variant-type-at-run-time.md` | marked SUPERSEDED by this note |
| One gap rule | CLAUDE.md:77 | KEPT: a type with no descriptor code is a gap warned at generation |
| General converter | CLAUDE.md | KEPT: rules per node kind, no VI named |

## Before the brief

1. DONE 2026-11-12 (`.tmp/variant_behaviour_search.md`): Equal? compares attributes (settled, NI);
   Empty String/Path? is FALSE for a variant holding `""` (settled, forum); Variant To Data rules
   partly settled (integer coercion, no inheritance), the rest written into the Readers table.
2. Check tunnels/shift registers for coercions in the graph, and which type a Bundle / Build Array
   input terminal carries (tiny diagrams).
3. The copy rule's holes: a tiny test per row of that table, the Index Array one first; fix them in
   `value_is_shared`.
4. Measure the size: generated lines changed in TestCase and OpenG lvdata (before/after), from a
   prototype on a scratch branch.

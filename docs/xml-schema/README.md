# LabVIEW VI XML Schema Reference

This directory documents lvkit's current understanding of the XML that
[pylabview](https://github.com/mefistotelis/pylabview) extracts from a LabVIEW
`.vi` binary. It is a "genome project" for these archaic, binary-derived fields:
for each field, *what it is* and *what we currently believe it controls*, with
the evidence and a confidence level.

> **Grounding rule.** Every claim here is traced to lvkit parser code, a
> pylabview enum/comment, a concrete extracted XML sample, or a confirmed
> session finding. Where we genuinely do not know a field's meaning, it is
> marked **confidence = unknown** rather than guessed. (This repo has a hard
> "never fabricate looked-up facts" rule.)

## The three files

`extractor.extract_vi_xml(vi_path)` runs one pylabview subprocess per VI and
writes three sibling XML files next to the `.vi` (plus a pile of `*.bin`
side-blobs and `*.png` icons):

| File | Nickname | Contents | Doc |
|---|---|---|---|
| `<name>_BDHb.xml` | **BD** — block-diagram heap | nodes, wires, structures, terminals, constants | [block-diagram.md](block-diagram.md) |
| `<name>_FPHb.xml` | **FP** — front-panel heap | controls/indicators, connector pane, default data | [front-panel.md](front-panel.md) |
| `<name>.xml` | **Base / main** | VI settings (LVSR/LVIN), type list (VCTP), dataspace default data (DFDS), link/dependency tables | [dataspace.md](dataspace.md) |

Both `_BDHb.xml` and `_FPHb.xml` are **heaps**: recursively nested
`<SL__arrayElement class="...">` objects, each carrying a `uid`, that model the
on-screen object tree. The base `.xml` is different in shape — it is a set of
named binary *sections* (LVSR, VCTP, DFDS, LIvi, LIbd, ...) that pylabview
renders as structured XML plus `<!-- ... -->` comments.

Suffix constants live in `src/lvkit/parser/constants.py:165-167`
(`BD_XML_SUFFIX`, `FP_XML_SUFFIX`, `MAIN_XML_SUFFIX`), with
`derive_fp_xml_path` / `derive_main_xml_path` / `derive_vi_name` helpers
(`constants.py:191-204`).

## How the parser reads them

Pipeline (`src/lvkit/parser/vi.py:101` `parse_vi`):

1. `_parse_metadata(main_xml)` — LVSR/LVIN qualified name, the **type map**
   (`parse_type_map_rich`), and SubVI/dependency link tables. (`vi.py:170`)
2. `_parse_selector_tables(main_xml)` — case-structure per-frame selector
   values from the DFDS dataspace. (`vi.py:223`)
3. `_parse_block_diagram(bd_xml, fp_xml, type_map, selector_tables)` — nodes,
   wires, constants, terminals, loops, cases, sequences, IPES. (`vi.py:235`)
4. `_parse_front_panel(fp_xml, ...)` and `parse_connector_pane(fp_xml)`.

## Two SEPARATE type namespaces (they do NOT cross-reference)

This is the single most important thing to internalize:

- **BD heap `typeDesc` `TypeID(N)`** — an index into the **VCTP** (the
  consolidated type list in the base `.xml`). Terminals, constants, tunnels and
  FP DCOs all carry `<typeDesc>TypeID(N)</typeDesc>`; `N` is a *heap* TypeID,
  resolved through the chain
  `Heap TypeID → Consolidated TypeID → FlatTypeID → VCTP TypeDesc`
  (`parser/type_mapping.py:17` `parse_type_map_rich`,
  `parser/type_resolution.py:53` `resolve_type_rich`).
- **Dataspace `DataFill TypeID="N"`** — an index into the **DFDS** (dataspace)
  default-data list. Same integer space *shape*, totally different assignment.

**A `DataFill TypeID` and a BD `typeDesc TypeID` with the same number are NOT
the same type.** They are two independent enumerations. The case-selector
correlation (see [dataspace.md](dataspace.md)) exploits the fact that, *within
each namespace*, LabVIEW assigns indices in the same DCO-enumeration order — so
you sort each side by its own index and zip. You never equate the two integers
directly. (`parser/nodes/case.py:513` `_apply_selector_tables`; session
finding, confirmed on "Draw Image from File__ogtk.vi".)

## Coordinate spaces

- All heap rects are LabVIEW `(top, left, bottom, right)` — note the order
  (`render/layout.py:109` `_rect` reorders them to `x1,y1,x2,y2`).
- A node's `<bounds>` is absolute (relative to the diagram it lives in; the
  renderer accumulates diagram origins while recursing —
  `render/layout.py:383` `walk`).
- A terminal's `<termBounds>` is **relative to the node's icon origin**, and a
  primitive's icon is *centered* within its clickable `<bounds>` — so the
  renderer computes a centering offset before placing terminals
  (`render/layout.py:252-280` `_map_terms`). Constant part bounds are relative
  to the constant DDO's own top-left (`render/layout.py:118` `_const_value_box`).
- Selector/label geometry lives in the heap; **selector VALUES do not** — they
  are in the DFDS dataspace (see below).

## A note on pylabview `OF__*` enums

pylabview's `LVheap.py` defines `OBJ_FIELD_TAGS` with members like
`OF__objFlags = 172`, `OF__bounds = 14`, `OF__termBounds = 266`
(`LVheap.py:75-360`). **These integers are attribute-tag IDs** — they identify
*which serialized attribute* a value belongs to inside the binary heap, and
they become the XML element *name* (`<objFlags>`, `<bounds>`, ...). They are
**NOT** bit positions within the `objFlags` integer. Do not conflate the two.
The bit meanings of `objFlags` are documented per-element in the field docs and
are lvkit session findings — pylabview does not decode them.

## Field docs

- [block-diagram.md](block-diagram.md) — `_BDHb.xml`
- [front-panel.md](front-panel.md) — `_FPHb.xml`
- [dataspace.md](dataspace.md) — base `.xml` (metadata + VCTP + DFDS + links)

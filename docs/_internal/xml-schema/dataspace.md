# Base / Main VI XML — `*.xml`

The base `.xml` (no `_BDHb`/`_FPHb` suffix) is not a heap. It is a set of named
binary **sections** that pylabview renders as structured XML plus
`<!-- ... -->` comments. It carries VI settings, the consolidated **type list**
(VCTP), the **dataspace** default data (DFDS), and the link/dependency tables
that name SubVIs and typedefs.

See [README.md](README.md) for the two type namespaces (VCTP vs DFDS). Related
code: `parser/vi.py:170-232` (`_parse_metadata`, `_parse_selector_tables`),
`parser/type_mapping.py`, `parser/type_resolution.py`, `parser/nodes/case.py`.

---

## VI settings — `<LVSR>`, `<LVIN>`

| Element | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `LVIN` `Unk1` (attr) | `.//LIvi/Section/LVIN` | `Unk1="Draw Image from File__ogtk.vi"` | The VI's qualified/display name (preferred source). | `parser/vi.py:186-189`; sample `.xml:27` | confirmed |
| `LVSR/Section` `Name` (attr) | `.//LVSR/Section` | | Fallback qualified name if `LVIN.Unk1` is empty. | `parser/vi.py:190-193` | probable |
| `LVSR` (section) | root | | VI Save Record — execution/appearance settings (priority, reentrancy, etc.). lvkit reads only `Name`; the rest is **unknown**. | sample `.xml:3` | unknown |
| `LVIN` `Unk2` (attr) | LVIN | `Unk2=""` | Unknown second field, usually empty. | sample `.xml:27` | unknown |

---

## Type list — `<VCTP>` (namespace #1: BD/FP `typeDesc TypeID`)

The VCTP is the consolidated type table that every `typeDesc TypeID(N)` in the
BD and FP heaps resolves against. Resolution is a 3-hop chain
(`parser/type_mapping.py:17` `parse_type_map_rich`):

```
Heap TypeID N  --comment-->  Consolidated TypeID M  --TopLevel-->  FlatTypeID F  --VCTP Section-->  TypeDesc
```

### Type comments

| Comment | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<!-- Heap TypeID N = Consolidated TypeID M: Name -->` | `Heap TypeID  6 = Consolidated TypeID 184: Boolean` | Maps a heap TypeID (what `TypeID(N)` means) to a consolidated id + coarse type name. | `type_mapping.py:39-51`; sample `.xml:2578` | confirmed |
| `<!-- TypeID N: Name -->` | `<!-- TypeID 12: Cluster -->` | Direct type-name comment (used when no heap→consolidated line exists). | `type_mapping.py:53-60`; sample `.xml:160` | confirmed |

### VCTP structure

| Element | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<TopLevel>/<TypeDesc Index=.. FlatTypeID=..>` | `.//VCTP//TopLevel` | Consolidated `Index` → `FlatTypeID` map. | `type_mapping.py:70-74`; sample `.xml:3830` | confirmed |
| `<VCTP>/<Section>/<TypeDesc>` | `.//VCTP/Section` | The flat type descriptors, in order — position = FlatTypeID (`type_mapping.py:199-204`). | `type_mapping.py:201`; sample `.xml:2876` | confirmed |
| `TypeDesc` `Type` (attr) | | `Cluster`, `Array`, `SubArray`, `TypeDef`, `Refnum`, `NumInt16`, `Boolean`, `String`, `EnumU16`, `UnitUInt16`... — the type kind. | `type_mapping.py:226-369` | confirmed |
| `TypeDesc` `Label` (attr) | | Field/type name (cluster fields read their name from the referenced type's `Label`). | `type_mapping.py:237-244` | confirmed |
| nested `<TypeDesc TypeID=..>` | inside Cluster/Array | Field / element type references (recursively resolved). | `type_mapping.py:231-273` | confirmed |
| `<Dimension>` | inside Array | Array dimension count. | `type_mapping.py:261` | confirmed |
| `TypeDesc[@Nested='True']` | inside TypeDef | The typedef's underlying type. | `type_mapping.py:96-97,277` | confirmed |
| `<EnumLabel>` | inside enum TypeDesc | Ordered enum value labels (index = value). | `type_mapping.py:290-296,327-334` | confirmed |
| `Refnum` `RefType` (attr) + `<Item Text>` chain | Refnum TypeDesc | Refnum kind; `UDClassInst` → fully-qualified class name from `<Item>` chain. | `type_mapping.py:342-362` | confirmed |
| `Label Text=".ctl"` | TypeDef | The typedef's `.ctl` filename; joined with VICC path to a qualified name. | `type_mapping.py:86-94,133-157` | confirmed |

`SubArray` (output of Reverse/Rotate/Split) is parsed identically to `Array` —
same element type + dimensions (`type_mapping.py:253-273`).

---

## Dataspace default data — `<DFDS>` (namespace #2: `DataFill TypeID`)

The DFDS holds each dataspace entry's default value as a `<DataFill TypeID="N">`
whose child is the decoded value tree (`<Cluster>`, `<Array>`, `<I32>`,
`<String>`, ...). **`DataFill TypeID` is the DFDS index, a DIFFERENT namespace
from the VCTP TypeID** — see [README.md](README.md).

| Element | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `<DataFill TypeID="N">` | `.//DFDS` | `TypeID="31"` | One dataspace default-value entry, keyed by DFDS index. | `parser/nodes/case.py:460-463`; sample `.xml:492` | confirmed |
| value children | inside DataFill | `<Cluster>`, `<Array><dim>`, `<I32>`, `<U8>`, `<I16>`, `<String>`, `<Ptr>`, `<SpecialDSTMCluster>` | The decoded value, shape-mirroring its VCTP type. `<dim>` = array length. | sample `.xml:624-660` | confirmed |

### Case-structure selector tables (the key DFDS use lvkit decodes)

Case-structure **per-frame selector values are stored ONLY here**, not in the BD
heap (for the OpenG corpus). lvkit decodes any `DataFill` whose `Cluster` has
the selector-table shape (`parser/nodes/case.py:477-510` `_decode_selector_table`):

```
Cluster {
  I32 displayed_frame,           # frame LabVIEW last showed (answers "saved displayed frame")
  I32 range_count,
  Array[ Cluster{ I32 start, I32 end, U8 startRangeType, U8 endRangeType, I16 diagram_idx } ],
  Array[ String ] value_strings, # present only for STRING selectors
  Cluster trailer
}
```

Real example — the string-selector table from "Draw Image from File__ogtk.vi"
(`.xml:661-718`, file-extension switch, 6 values → 4 frames):

```xml
<DataFill TypeID="33">
  <Cluster>
    <I32>4</I32>              <!-- displayed_frame -->
    <I32>6</I32>             <!-- range_count -->
    <Array><dim>6</dim>
      <Cluster><I32>0</I32><I32>0</I32><U8>0</U8><U8>0</U8><I16>0</I16></Cluster>  <!-- "bmp" -> frame 0 -->
      <Cluster><I32>1</I32><I32>1</I32><U8>0</U8><U8>0</U8><I16>2</I16></Cluster>  <!-- "gif" -> frame 2 -->
      <Cluster><I32>2</I32><I32>2</I32><U8>0</U8><U8>0</U8><I16>1</I16></Cluster>  <!-- "jpe"  -> frame 1 -->
      <Cluster><I32>3</I32><I32>3</I32><U8>0</U8><U8>0</U8><I16>1</I16></Cluster>  <!-- "jpeg" -> frame 1 -->
      <Cluster><I32>4</I32><I32>4</I32><U8>0</U8><U8>0</U8><I16>1</I16></Cluster>  <!-- "jpg"  -> frame 1 -->
      <Cluster><I32>5</I32><I32>5</I32><U8>0</U8><U8>0</U8><I16>3</I16></Cluster>  <!-- "png"  -> frame 3 -->
      </Array>
    <Array><dim>6</dim>
      <String>bmp</String><String>gif</String><String>jpe</String>
      <String>jpeg</String><String>jpg</String><String>png</String>
      </Array>
    ...
```

| Field | Meaning | Evidence | Confidence |
|---|---|---|---|
| `displayed_frame` (leading I32) | The frame LabVIEW last displayed → `ParsedCaseStructure.displayed_frame`. | `case.py:483-509`; memory `reference_case_selector_dataspace.md` | confirmed |
| range `start`/`end` (I32,I32) | For STRING selectors: indices into `value_strings` (a frame can match several — jpe/jpeg/jpg all → frame 1). Else: literal numeric/enum values. | `case.py:490-503,555-587` | confirmed |
| `startRangeType`/`endRangeType` (U8,U8) | Range-type flags (matches pylabview `OBJ_SELECTOR_RANGE_TAGS.OF__startRangeType/endRangeType`, `LVheap.py:1341`). lvkit does not interpret them (0 in all samples). | `case.py:494`; `LVheap.py:1341-1347` | probable |
| `diagram_idx` (I16) | Which case frame this range routes to. Frame in NO range = implicit Default. | `case.py:498,558-566` | confirmed |
| `value_strings` array | Selector labels for string cases; empty (`<dim>0</dim>`) for numeric. | `case.py:503,569-577` | confirmed |
| trailer Cluster | Two I32 + Ptrs; purpose not decoded. | sample `.xml:653-658` | unknown |

**Duplication:** pylabview emits each selector table **twice** (an edit-time and
a run-time copy). lvkit dedupes by content (`case.py:459-472`).

**Correlation to case nodes** (`case.py:513-552` `_apply_selector_tables`;
memory `reference_case_selector_dataspace.md`): sort case nodes by their
`caseSel` `typeDesc` **VCTP TypeID** and sort unique tables by their `DataFill`
**DFDS TypeID**, then zip — both indices are assigned in the same
DCO-enumeration pass, so the orders agree *within each namespace* even though the
two TypeIDs are never equal. Gated: apply only if counts match AND every zipped
pair is kind-consistent (string table ⟺ string case) AND every frame/displayed
index is in range; any mismatch aborts the whole application (keeps fallbacks).
Boolean cases store no table (True/False implicit) and are excluded.

---

## Link / dependency tables — `<LIvi>`, `<LIbd>`

These name the VI's SubVIs, typedefs, and other saved references, and give the
relative paths used to locate them on disk.

| Element / attr | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `LinkSaveQualName/String` | many link elems | The referenced object's qualified name parts (joined with `:`). | `parser/vi.py:716-730` | confirmed |
| `LinkSaveFlag` (attr) | link elem | `"2"` = same-library reference — qualify with the caller's library. | `parser/vi.py:726-729` | confirmed |
| `LinkSavePathRef/String` | link elem | Relative path tokens to the file. Empty `<String/>` = `..` navigation marker (preserved). `<vilib>`/`<userlib>` are special roots. | `parser/vi.py:770-791`; `type_resolution.py:247-250` | confirmed |
| link tags under `.//LIvi//` | LIvi | `VIVI`, `VIPI`, `VIPV`, `VICC`, `IUVI`, `DDPI`, `FPPI`, `BSVR`, `SVVI`... — the saved-reference table. `VIVI/VIPI/DyOM/VIPV` = SubVI calls; `VICC` = typedef `.ctl` refs; `BSVR` = statVIRef targets. | `parser/vi.py:754-799` | confirmed |
| `IUVI`/`PUPV` under `.//LIbd//BDHP/` | LIbd | Block-diagram iUse instance records; `LinkOffsetList/Offset` (hex) maps an iUse UID → qualified name. `PUPV` = polymorphic wrapper (overwritten by `IUVI`). | `parser/vi.py:801-810` | confirmed |
| `VICC/LinkSavePathRef` | LIvi | Typedef `.ctl` path — joined with the VCTP `.ctl` filename to build a qualified typedef name. `<vilib>`-rooted refs feed `parse_typedef_refs`. | `type_mapping.py:104-122`, `type_resolution.py:217-254` | confirmed |
| Function `TypeDesc` `Flags` (attr) | `.//TypeDesc[@Type='Function']` | Connector-pane wiring rules — bits 8-9 per terminal (Required/Recommended/Optional/Dynamic). See [front-panel.md](front-panel.md). | `parser/front_panel.py:266-287` | probable |

### Fallback: `_LIbd.bin`

For pre-LV9 VIs, pylabview cannot parse `LIbd`, so BDHP/IUVI elements are absent.
lvkit then reads the raw `<name>_LIbd.bin` side-blob directly to recover the
iUse UID → qualified-name map (`parser/vi.py:205-208`, `parser/metadata.py`
`parse_iuse_from_libd`).

---

## Side-blob files (not XML)

Beside the three XML files, pylabview writes many `*.bin` blobs and `*.png`
icons (e.g. `_CNST.bin`, `_DSIM*.bin`, `_HIST.bin`, `_ICON.png`,
`_VICD_code.bin`). Most are **not parsed** by lvkit; the exceptions are
`_LIbd.bin` (above) and the `_ICON.png` (rendered as the VI's connector-pane
icon, `parser/layout.py:496`). The rest are **unknown** to lvkit.

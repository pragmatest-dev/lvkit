# Front Panel Heap — `*_FPHb.xml`

The front-panel heap models the VI's user interface: the controls (inputs) and
indicators (outputs), their default values, and the **connector pane** (which
controls are exposed as VI terminals and in what slots). Like the block-diagram
heap it is a tree of `<SL__arrayElement class="...">` objects with `uid`s.

See [README.md](README.md) for the two type namespaces and coordinate spaces.
Related code: `parser/front_panel.py`, `parser/vi.py:277-352` (`_parse_front_panel`).

---

## Panel-level elements

| Element | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `<pBounds>` | root | `(0, 0, 400, 600)` | Panel window bounds `(top,left,bottom,right)`. Defaulted to `(0,0,400,600)` if absent. | `parser/vi.py:302-306` | confirmed |
| `<conPane class="conPane">` | root | `uid="100"` | The connector pane (see below). | `front_panel.py:203` | confirmed |

---

## Front-panel DCOs — `<SL__arrayElement class="fPDCO">`

Each `fPDCO` is one control or indicator. lvkit reads them twice: once for
type + direction (`extract_fp_dco_info`, `front_panel.py:64`) and once for the
visible control tree (`_parse_ddo`, `parser/vi.py:830`).

Real example (`Draw Image..._FPHb.xml:91-96`):

```xml
<SL__arrayElement class="fPDCO" uid="102">
  <objFlags>66048</objFlags>
  <typeDesc>TypeID(1)</typeDesc>
  <ddo class="stdClust" uid="105">
    <objFlags>6295556</objFlags>
    <bounds>(326, 195, 399, 273)</bounds>
    <partsList elements="4">
      ...
```

| Field | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `uid` (attr) | `fPDCO` | `102` | DCO id. The connector pane and `ctlRefConst`/`gRef` nodes reference it. | `parser/vi.py:310` | confirmed |
| `<objFlags>` | `fPDCO` | `66048` | Control bitfield. **Bit 0 (`0x1`) = indicator** (VI output) vs control (input). Authoritative even when unwired. | `front_panel.py:46-51,82-88` | confirmed |
| `<typeDesc>` | `fPDCO` | `TypeID(1)` | Control data type, a **VCTP** heap TypeID (namespace #1). | `front_panel.py:74-80`, `vi.py:339` | confirmed |
| `<ddo class>` | `fPDCO/ddo` | `stdClust`, `stdString`, `stdNum`/`stdNumeric`, `stdBool`, `typeDef` | The data-display-object — the actual widget kind (`std*`). `typeDef` wraps a real `std*` inside. | `parser/vi.py:313-320,837-853` | confirmed |
| `<DefaultData>` | `fPDCO` | quoted byte string | The control's default value (binary, big-endian). Decoded type-aware via the resolved VCTP type (`_decode_default_data` → `_decode_element`, `vi.py:895-1120`). Strip only wrapping quotes — do NOT `clean_labview_string` (it deletes the `&#xNN;` length-prefix bytes). | `parser/vi.py:324-342` | confirmed |
| `<bounds>` (on ddo) | `fPDCO/ddo` | `(326, 195, 399, 273)` | Widget rect `(top,left,bottom,right)`, panel-absolute. | `parser/vi.py:855-860` | confirmed |

### FP control-direction note

The FP DCO's `objFlags` bit 0 is the primary source of control/indicator
designation (`front_panel.py:82-88`). ~8% of DCOs carry no `objFlags`; for those
lvkit falls back to wire direction — a terminal that *receives* a wire is an
indicator (`front_panel.py:153-162`). Historic constant `FP_IS_INDICATOR =
0x10000` (`flags.py:12`) is the older bit-16 interpretation used by
`is_indicator` for raw ddo flags in `_parse_ddo` (`vi.py:869`); the fPDCO-node
path uses bit 0, which is the confirmed-correct one.

---

## ddo parts — `<partsList>`

A ddo owns `partsList` entries (`SL__arrayElement class="label"|"cosm"|
"numLabel"|...`) that make up the widget's visual parts. Each part carries its
own `<bounds>` **relative to the ddo's top-left origin**.

| Part `class` | Meaning | Evidence | Confidence |
|---|---|---|---|
| `label` | The control's owned label. `<textRec><text>"..."</text>` holds the caption; `objFlags` bit `0x8` = hidden. | sample `:98-114`; `parser/layout.py:161-174` | confirmed |
| `cosm` | Cosmetic/graphic part (the drawn widget body). | sample `:116-127` | probable |
| `numLabel` | Numeric label carrying a `<format>` display-format string. | `parser/nodes/constant.py:53` | confirmed |
| `<partID>` / `<masterPart>` | Numeric part ids linking parts of a compound widget. Not interpreted by lvkit. | sample `:100-101` | unknown |
| `<howGrow>` | Resize/anchor behavior flags. Not interpreted. | sample `:102` | unknown |
| `<fgColor>`/`<bgColor>` | Hex ARGB-ish color words (`01000000` = transparent/default). | sample `:107-108` | probable |

`stdClust` ddos recurse: child `std*` parts become nested `ParsedFPControl`
children (`parser/vi.py:873-882`).

---

## Block-diagram FP terminals — `<fPTerm>`

Note: the *terminal* that a wire connects to lives on the **block diagram**
(`class="fPTerm"`), and links back to its FP DCO by uid. lvkit reads these in
`extract_fp_terminals` (`front_panel.py:93-185`), pulling the DCO's type + flag
from the FP heap.

| Field | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<dco uid>` | BD `fPTerm/dco` | Points at the FP DCO uid (`fp_dco_uid`) — the join key between the two heaps. | `front_panel.py:130-131` | confirmed |
| `.//label/textRec/text` | BD `fPTerm` | Terminal name (the control's label). | `front_panel.py:133-138` | confirmed |

---

## Connector pane — `<conPane class="conPane">`

Defines which controls/indicators are exposed as VI terminals and their slot
positions. Real example (`Draw Image..._FPHb.xml:2510-2517`):

```xml
<conPane class="conPane" uid="100">
  <conId>4815</conId>
  <cons elements="12">
    <SL__arrayElement class="ConpaneConnection">
      <ConnectionDCO uid="90" />
      </SL__arrayElement>
    <SL__arrayElement class="ConpaneConnection" index="8">
      <ConnectionDCO uid="68" />
      </SL__arrayElement>
```

| Field | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `<conId>` | conPane | `4815` | Connector-pane **pattern id** (which terminal layout, e.g. 4x2x2x4). Stored as `pattern_id`. | `front_panel.py:207-210` | confirmed |
| `<cons>` | conPane | `elements="12"` | The ordered slot list. | `front_panel.py:213-214` | confirmed |
| `ConpaneConnection` `index` (attr) | slot | `index="8"` | Slot index override; when absent, slots number sequentially from the previous. A connected slot carries a `<ConnectionDCO uid>`; an unconnected slot has none. | `front_panel.py:216-229` | confirmed |
| `<ConnectionDCO uid>` | slot | `uid="90"` | The FP DCO wired into this slot; empty for an unused slot. | `front_panel.py:221-222` | confirmed |

### Wiring rules (from the base `.xml`, not the FP heap)

The **required/recommended/optional** designation of each connector-pane
terminal is NOT in the FP heap — it is in the base `.xml`'s Function `TypeDesc`
`Flags` (bits 8-9), matched to slots by index
(`parse_connector_pane_types`, `front_panel.py:234-289`):

| Flags bits 8-9 value | Meaning | Evidence | Confidence |
|---|---|---|---|
| 0 | Invalid wire rule | `front_panel.py:243-248` | probable |
| 1 | Required | `front_panel.py:244` | probable |
| 2 | Recommended | `front_panel.py:245` | probable |
| 3 | Optional | `front_panel.py:246` | probable |
| 4 | Dynamic Dispatch | `front_panel.py:247` | probable |

Extraction: `get_wiring_rule(flags) = (flags >> 8) & 0x03` (`flags.py:34-36`).
See [dataspace.md](dataspace.md) for the Function TypeDesc.

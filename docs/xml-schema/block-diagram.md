# Block Diagram Heap — `*_BDHb.xml`

The block-diagram heap models the wiring diagram: nodes (primitives, SubVI
calls, structures), the terminals on each node, the wires between them, and
inline constants. It is a recursively nested tree of
`<SL__arrayElement class="...">` objects, each with a `uid`.

See [README.md](README.md) for the two type namespaces and coordinate spaces.
Related code: `parser/vi.py`, `parser/constants.py`, `parser/nodes/*.py`,
`parser/node_types.py`, `render/layout.py`.

---

## Container elements (the diagram tree)

| Element | Where | Format / example | What it holds | Evidence | Confidence |
|---|---|---|---|---|---|
| `<zPlaneList>` | top of each diagram | `<zPlaneList elements="9">` | The real node **definitions** — each child is a full node (class + `<bounds>` + `<termList>`). Z-order = paint order. | `render/layout.py:384`; sample `Draw Image..._BDHb.xml:5` | confirmed |
| `<nodeList>` | inside a diagram | children are bare `<SL__arrayElement uid="..."/>` (class=None) | uid **references** into the zPlaneList — plus this is where `sRN`, `bDConstDCO` constants and `signalList` live. Constants are found via `.//nodeList//SL__arrayElement[@class='term']` (`parser/nodes/constant.py:106`). | session finding; `parser/vi.py:399` iterates all `SL__arrayElement` | confirmed |
| `<diagramList>` | on a structure | `<SL__arrayElement class="diag">` children | Inner diagrams of a structure: one per case frame / stacked-seq frame, exactly one for a loop or IPES. | `parser/nodes/case.py:123`, `loop.py:68` | confirmed |
| `<sequenceList>` | on a `flatSequence` | `<SL__arrayElement class="sequenceFrame">` children | Flat-sequence frames (film-strip). Each frame has its own `<bounds>` + `<diagramList>`. | `parser/nodes/sequence.py:62`, `render/layout.py:449` | confirmed |
| `<termList>` | on any node | `<SL__arrayElement class="term">` children | The node's terminals. Order = natural terminal index when no explicit index field. | `parser/vi.py:462-534` | confirmed |
| `<signalList>` | inside a diagram | `<SL__arrayElement class="signal">` | The wires. | `parser/vi.py:412` | confirmed |

---

## Node elements

A node is a `zPlaneList` element carrying a `class`, a `<bounds>`, and a
`<termList>`. lvkit's node factory (`parser/node_types.py:949` `parse_node`)
dispatches on `class`. The full class→handler table is `_HANDLERS`
(`node_types.py:897`); the allowlist that `_extract_nodes` scans is
`OPERATION_NODE_CLASSES` (`constants.py:56`).

### Common node fields

| Field | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `class` (attr) | `class="prim"` | Node kind (see class table below). | `node_types.py:958` | confirmed |
| `uid` (attr) | `uid="623"` | Heap-unique id; wires and refs point at it. Graph qualifies as `"{vi}::{uid}"`. | `render/layout.py:14` | confirmed |
| `<bounds>` | `(286, 44, 318, 76)` | Node rect `(top,left,bottom,right)`, absolute in its diagram. | `render/layout.py:109`; sample `:193` | confirmed |
| `<objFlags>` | `16843392` | Per-element bitfield; meaning depends on node class (see objFlags section). | session finding | confirmed |
| `<label>` | nested `textRec/text` | Owned label. `objFlags` bit `0x8` on the label = HIDDEN. | `render/layout.py:193-206`; session finding | confirmed |
| `<clumpNum>` | `196611` | Execution-clump id (LabVIEW's parallelism scheduler grouping). lvkit does not use it. | sample `:195` | unknown |
| `<shortCount>` | `4` | Terminal/short count hint. Unused by lvkit. | sample `:194` | unknown |

### Node classes (from `constants.py` + `node_types.py`)

| `class` | Display name | Handler → ParsedNode | Notes | Evidence |
|---|---|---|---|---|
| `prim` | Primitive | `PrimitiveNode` (adds `primIndex`, `primResID`) | The generic primitive. | `node_types.py:199` |
| `iUse` | SubVI | `SubVINode` | Static SubVI call. | `node_types.py:225` |
| `polyIUse` | Polymorphic SubVI | `SubVINode` + `poly_variant_name` | Variant from `instanceSelector`/`menuInstanceUsed` (hex). | `node_types.py:236-282` |
| `dynIUse` | Dynamic Dispatch VI | `SubVINode` | Class method; Python MRO handles dispatch. | `node_types.py:285` |
| `callParentDynIUse` | Call Parent Method | `SubVINode` | Emits `super().method()`. | `node_types.py:301` |
| `callByRefNode` | Call By Reference | `CallByRefNode` (`frame_terminal_uids`) | 4 `hGrowCItem` frame DCOs (error/VI-ref in+out) from `permDCOList`. | `node_types.py:316-347` |
| `cpdArith` | Compound Arithmetic | `CpdArithNode` (`operation`) | `dcoFiller`: 1=or, 2=and, 256=add. Per-terminal invert = DCO `objFlags` bit 16. | `node_types.py:350-383` |
| `aBuild` | Build Array | `ArrayBuildNode` | | `node_types.py:386` |
| `aInit` | Initialize Array | `ArrayInitNode` | element + N size inputs → N-D array. No numeric primResID. | `node_types.py:397` |
| `aDelete`/`aIndx`/`subset`/`aReplace`/`aInsert`/`aReshape`/`concat`/`mergeErrors`/`oHExt` | (various) | `PrimitiveNode` | Built-in primitives with their own XML class; some get a synthetic `primResID` (`_BuiltinPrimitiveHandler`, `node_types.py:874-943`). `concat` etc. resolve by class in primitives.json — do NOT borrow a numeric resID. | `node_types.py:928-943` |
| `whileLoop`/`forLoop` | While/For Loop | `LoopNode` (`loop_type`) | See Structures. | `node_types.py:408-443` |
| `select` | Select / Case | `SelectNode` | Also handles `caseStruct` in the case parser. | `node_types.py:446` |
| `caseStruct` | Case Structure | (case parser) | See Structures. | `parser/nodes/case.py:83` |
| `nMux`/`mux`/`demux` | Bundle/Unbundle | `SelectNode` (`dco_agg_uid`, `dco_list_uids`, `dco_field_index`) | Field index from `<i>` (nMux) or list position (mux/demux). | `node_types.py:606-685` |
| `propNode` | Property Node | `PropertyNode` (`object_name`, `properties[]`) | `propItemInfo` children = property name+code; drawers execute **sequentially**. | `node_types.py:476-504` |
| `invokeNode` | Invoke Node | `InvokeNode` (`method_name`, `method_code`) | | `node_types.py:507-526` |
| `flatSequence` | Flat Sequence | `ParsedNode` | See Structures. | `node_types.py:529` |
| `seq` / `sequence` | Stacked Sequence | `ParsedNode` | `sequence` = older-LV alias of `seq`. | `node_types.py:546-571` |
| `printf` / `scanf` | Format/Scan String | `PrimitiveNode` | Variable terminal count; walked generically. | `node_types.py:573-604` |
| `ctlRefConst` | Control Ref Constant | `CtlRefConstNode` (`ddo_uid`) | ddo_uid set → aliases an FP terminal. | `node_types.py:687` |
| `gRef` | Local Variable | `GRefNode` (`param_idx`) | `paramIdx` = connector-pane slot of the referenced FP control. | `node_types.py:700-722` |
| `statVIRef` | Static VI Reference | `StatVIRefNode` | Name from label. | `node_types.py:725` |
| `fBox` | Formula Node | `FormulaNode` (`script`) | Script under `formula/text` (restricted C-like: `**`, `int8`...). | `node_types.py:835-851` |
| `decomposeRecomposeStructure` | In-Place Element Structure | (decompose parser) | See Structures. | `parser/nodes/decompose.py:34` |
| `decomposeClusterNode`/`decomposeArrayNode` | Decompose Cluster/Array | `SelectNode` (`poser_uid`, `<index>`) | `poser` links decompose↔recompose. | `node_types.py:736-804` |
| `decomposeDataValRefNode`/`decomposeMatchNode` | Decompose DVR / Match | `ParsedNode` (stub) | Terminals parsed; codegen deferred. | `node_types.py:806-832` |
| `commentNode` | (annotation) | skipped | In `SKIP_NODE_CLASSES`. | `constants.py:52` |
| **Generically captured** | — | `GenericHandler` | Any node-shaped element (class + bounds + termList) not above and not `sRN`/`sequenceFrame`/`commentNode`. Corpus set: `extFunc`, `exprNode`, `decimate`, `interLeave`. Renders as a labelled box; **codegen fails loudly**. | `parser/vi.py:367-405`; memory `project_unhandled_nodes.md` |

**`prim`-specific fields** (`node_types.py:205-222`):

| Field | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<primResID>` | `1189` | Primitive resource id — the key into `data/primitives.json`. | `node_types.py:214`; sample `:196` | confirmed |
| `<primIndex>` | `25` | Secondary primitive-group index (also appears on each terminal's DCO). Not used for resolution. | sample `:178,197` | probable |

### Unknown / unhandled node classes (open questions)

From the corpus audit (`memory/project_unhandled_nodes.md`): `extFunc`,
`exprNode`, `decimate`, `interLeave` (captured generically, no codegen yet);
`eventRegNode`, `hiddenFBNode`, `slaveFBInputNode`, `xNode` (feedback/event/XNode
— semantics **unknown**, likely untranslatable); `dynLink` (a child of
`dynIUse`, **not** a standalone node).

---

## Terminals — `<SL__arrayElement class="term">`

Every operation node's `<termList>` holds `term` elements. Each `term` wraps a
`<dco>` (data-context object) that carries the real type/index/geometry. lvkit
reads terminals in `parser/vi.py:451-599` (`_process_element_terminals`).

Real example (`Draw Image..._BDHb.xml:170-198`, an "Index Array"-style prim):

```xml
<SL__arrayElement class="term" uid="630">
  <objFlags>36864</objFlags>
  <dco class="parm" uid="628">
    <objFlags>65536</objFlags>
    <typeDesc>TypeID(41)</typeDesc>
    <termBounds>(0, 0, 11, 12)</termBounds>
    <primIndex>25</primIndex>
    <parmIndex>1</parmIndex>
    </dco>
  </SL__arrayElement>
```

| Field | Where | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|---|
| `uid` (attr) | `term` | `630` | Terminal id; wire endpoints reference it. | `parser/vi.py:467` | confirmed |
| `<dco class>` | `term/dco` | `parm`, `caseSel`, `csTun`, `selTun`, `lSR`, `bDConstDCO`, `gRefDCO`, `hGrowCItem`... | The terminal's role. `parm`/`iUseDCO`=ordinary I/O; others are structural (see Tunnels). | `parser/vi.py:473` | confirmed |
| `<parmIndex>` | `term/dco` | `1` | Primitive terminal index. **Omitted when 0** (`prim`); its absence on a prim means index 0, not "unknown". | `parser/vi.py:481-494` | confirmed |
| `<paramIdx>` | `term/dco` | `1` | Same, for SubVI (`iUse`) callee terminals — the connector-pane slot. Omitted ⇒ 0. | `parser/vi.py:481-494` | confirmed |
| `<typeDesc>` | `term/dco` | `TypeID(41)` | Terminal type, a **VCTP** heap TypeID (namespace #1). Some node classes (e.g. `aReshape`) put the real typeDesc at node level and leave a bare uid-ref `<dco>` — followed by uid (`vi.py:555-568`). | `parser/vi.py:550`; `resolve_type_rich` | confirmed |
| `<termBounds>` | `term/dco` | `(0, 0, 11, 12)` | Terminal rect **relative to the node icon origin** (icon is centered in bounds). `(top,left,bottom,right)`. | `render/layout.py:252-280` | confirmed |
| `<objFlags>` (on `term`) | `term` | `36864` | Terminal bitfield. Bit 0 (`0x1`) = isIndicator ⇒ **output** — used only as a fallback when the terminal is unwired (`is_output_terminal`). | `parser/flags.py:19`, `constants.py:151-155`, `vi.py:537-547` | confirmed |
| `<objFlags>` (on `dco`) | `term/dco` | `65536` | DCO bitfield. Bit 16 (`0x00010000`) = **inverted ("Not")** — but ONLY meaningful on `cpdArith` terminals (other prims reuse the bit). | `parser/flags.py:9,24`, `vi.py:585-589` | confirmed |
| `<dcoFiller>` | `term/dco` (cpdArith 1st term) | `1` | Compound-arith operation code: 1=or, 2=and, 256=add. | `node_types.py:357-382` | confirmed |
| `<i>` / `<index>` | `nMux` / decompose list DCO | `0` | Cluster field index for bundle/unbundle. `<i>` on `nMux`, `<index>` on decompose. | `node_types.py:648,774` | confirmed |
| `<inplace>` | `term/dco` | `2` | In-place/buffer-reuse hint. Unused by lvkit. | sample `:179` | unknown |
| `<dsw>` | `term/dco` (tunnels) | `2048` | Appears on tunnel DCOs; purpose not decoded. | sample `:231` | unknown |
| `<termBMPs>` | `caseSel` dco | `5` | Count of selector bitmaps/frames-ish; not used. | sample `:215` | unknown |

**Terminal index resolution order** (`parser/vi.py:479-534`):
`parmIndex`/`paramIdx` → named DCO-ref map from `primitives.json` node_types
(via `_NODE_DCO_MAP`, `vi.py:71-98`) → `callByRefNode` frame terminals get
negative indices → **list position** as last resort.

**Terminal direction** (`parser/vi.py:537-547`): wire connectivity is
authoritative (source uid ⇒ output; sink uid ⇒ input); only an unwired terminal
falls back to the `objFlags` bit-0 flag.

---

## Structures (loops, cases, sequences, IPES)

Structures are nodes with inner diagram(s). Their border terminals live in the
structure's own `<termList>`, and each has a **tunnel DCO** whose `<termList>`
maps the outer face to per-frame inner faces.

### Tunnel DCO layout — the universal rule

A tunnel DCO's `<termList>` is `[inner_frame0, inner_frame1, ..., outer]`: the
**OUTER face is the LAST element**, every preceding element is a per-frame inner
face (`parser/nodes/base.py:47-97` `extract_tunnel_mapping`). A 2-element list
(loop / flat-seq / single case-frame) → one Tunnel; an N+1 element list
(stacked sequence, multi-frame) → N Tunnels sharing the outer. Real example
(`Draw Image..._BDHb.xml:220-234`, a 5-frame case's `selTun`):

```xml
<dco class="selTun" uid="543">
  <termList elements="6">
    <SL__arrayElement uid="544" /> <!-- frame 0 inner -->
    ... 4 more inners ...
    <SL__arrayElement uid="545" /> <!-- outer (== parent term uid) -->
    </termList>
  <typeDesc>TypeID(43)</typeDesc>
```

### Tunnel / shift-register DCO classes (`constants.py:117-139`)

| `class` | Structure | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `lSR` / `rSR` | loop | Left/right shift register (input/output); paired by order (`loop.py:127`). | `constants.py:118-119` | confirmed |
| `lpTun` | loop | Loop tunnel (pass-through). Auto-indexing marked by `<TunnelType>`/`innerLpTunDCO` (`render/layout.py:208-229`). | `constants.py:120` | confirmed |
| `lMax` | loop | Accumulator/max output tunnel. | `constants.py:121` | probable |
| `csTun` | case | Case tunnel — simple `[inner, outer]`. | `parser/nodes/case.py:15,318` | confirmed |
| `selTun` | case | Per-frame case tunnel (one inner per frame). | `case.py:21,321-347` | confirmed |
| `caseSel` | case | The **selector** terminal DCO; also routes shift values across the case boundary. | `case.py:18,165-180` | confirmed |
| `commentTun` | case | Comment/annotation pass-through; same layout as selTun. | `case.py:184-199` | confirmed |
| `seqTun` / `flatSeqTun` | sequence | Sequence pass-through (flatSeqTun has a mate linking frames). | `constants.py:122-123` | confirmed |
| `decomposeRecomposeTunnel` | IPES | Cluster/array/DVR field tunnel; `[inner, outer]`. | `parser/nodes/decompose.py:51` | confirmed |

### Loops (`whileLoop`, `forLoop` — `parser/nodes/loop.py`)

| Field / element | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `loopTestDCO` `class="lTst"` | loop element | While-loop conditional (stop) terminal; its `<termList>` first entry = the terminal receiving the stop boolean. | `loop.py:92-99` | confirmed |
| `loopTestDCO/<objFlags>` bit 16 | | **Conditional polarity.** Bit 16 SET ⇒ Stop-if-True (default, e.g. a Stop button wired straight in); CLEAR ⇒ Continue-if-True. (Opposite sense of the cpdArith invert bit.) pylabview does NOT decode this. | `loop.py:101-111`; session finding | confirmed |
| `loopIndexDCO` / `loopLimitDCO` | loop element | Iteration terminal `i` / count terminal `N` — geometry only in the renderer (`i`/`N` glyphs). | `render/layout.py:34-47` | confirmed |

### Case structures (`caseStruct`, `select` — `parser/nodes/case.py`)

| Field / element | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `caseSel` DCO `<typeDesc>` | selector term | Selector type as a **VCTP TypeID** — this is what cases are sorted by for DFDS correlation (`selector_vctp_index`). | `case.py:145,533-537` | confirmed |
| `<objFlags>` bit 24 on `select` | case node | **"Case Insensitive Match"** on a STRING case (LabVIEW 2015+ draws an "A=a" badge). Only applied when selector is string. pylabview does NOT decode this. | `case.py:24,120,300`; session finding | confirmed |
| `SelectRangeArray32` / `SelectStringArray` / `SelectDefaultCase` | case node | In-heap selector ranges/labels/default. **Present in some VIs, ABSENT in the OpenG corpus** — where absent, the real values come from the DFDS dataspace (see below). | `case.py:215-256`; session finding | confirmed |
| **Per-frame selector VALUES** | **NOT in the BD heap** | Live in the base `.xml` DFDS as a `DataFill` cluster; correlated back by sorting cases by `caseSel` VCTP TypeID and tables by `DataFill` TypeID and zipping. See [dataspace.md](dataspace.md). | `case.py:447-587`; memory `reference_case_selector_dataspace.md` | confirmed |
| `<diagramList>` frames | case node | Each `class="diag"` child is one case frame. Frame with no selector range = implicit Default. | `case.py:262-292` | confirmed |

### Sequences (`flatSequence`, `seq`/`sequence` — `parser/nodes/sequence.py`)

| Field | Meaning | Evidence | Confidence |
|---|---|---|---|
| `<sequenceList>`/`sequenceFrame` (flat) vs `<diagramList>`/`diag` (stacked) | Frame containers differ by type; both enforce sequential execution. Flat frames sit side-by-side (each `sequenceFrame` `<bounds>` gives its absolute x — used for film-strip dividers, `render/layout.py:449-478`). | `sequence.py:61-67` | confirmed |
| `<dIdx>` on a stacked seq | **NOT a frame index.** A 3-frame stacked sequence carried `dIdx=17`, resolving to a diagram OUTSIDE its own frames. The true displayed-frame source is **unknown**. | `render/scene.py` + session finding | unknown |
| Flat sequence executes ALL contained nodes, even unwired ones. | | memory `feedback_sequence_frames.md` | confirmed |

### In-Place Element Structure (`decomposeRecomposeStructure` — `parser/nodes/decompose.py`)

One inner diagram; `decomposeRecomposeTunnel` DCOs at the boundary
(`[inner, outer]`). Inner decompose/recompose nodes pair via `<poser uid>`.

---

## Wires — `<SL__arrayElement class="signal">`

Real example (`Draw Image..._BDHb.xml:538-546`):

```xml
<SL__arrayElement class="signal" uid="575">
  <termList elements="2">
    <SL__arrayElement uid="1818" />   <!-- source (first) -->
    <SL__arrayElement uid="573" />    <!-- sink -->
    </termList>
  <state>1</state>
  <compressedWireTable>040800001E07</compressedWireTable>
  <lastSignalKind>517</lastSignalKind>
  </SL__arrayElement>
```

| Field | Example | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<termList>` | 2+ uids | Connected terminal uids: **first = source, rest = sinks**. lvkit fans out a multi-sink signal into one `ParsedWire` per sink (`from_term=source`). | `parser/vi.py:412-429` | confirmed |
| `<compressedWireTable>` | `040800001E07` | Hex blob = the wire's LabVIEW-routed bend geometry. **Decoded** for 2-endpoint wires (see below); lvkit can draw either these faithful paths or its own auto-router (switchable). | task #84; `render/wire_table.py` | confirmed (2-endpoint); fan-out undecoded |
| `<state>` | `1` | Wire state flag; unused. | sample `:534` | unknown |
| `<lastSignalKind>` | `517` | Last-known signal/data-kind hint; unused. | sample `:536` | unknown |

### `compressedWireTable` encoding (2-endpoint wires)

Decoded and validated on 5,742 corpus wires (task #84). A single-net wire is an
orthogonal polyline; the blob stores it compactly:

| Bytes | Meaning |
|---|---|
| `byte[0]` | vertex count **V** → `V-1` segments (`02`=straight, `03`=one bend, `04`=two bends, …) |
| `byte[1]` | segment-0 direction, full quadrant: `08`=E (+x), `04`=S (+y), `02`=W (−x), `01`=N (−y). Screen coords, y **down**. |
| next `V-2` | per-bend **sign** bits: `00`=+axis (E if the segment is horizontal, S if vertical), `01`=−axis (W / N). Segment axes just **alternate** H↔V from segment 0, so only a 1-bit sign is stored per bend — this is the "compression". |
| next `V-2` | **lengths** (px) of segments `0 … V-3`. The last segment's length is implied by the endpoint. |

Invariant: `total_bytes = 2V − 2`. Example `040800001E07`: V=4 (2 bends), seg-0
dir `08`=E; sign bytes `00 00`; lengths `1E`=30, `07`=7 → East 30px, then South
7px (first bend, sign `00`), then East to the sink (implied last segment).

Reconstruct: start at the source, walk the first `V-1` stored segments, then run
the final segment to the sink. **Fan-out signals (>2 termList uids) are NOT
decoded** — `byte[0]` becomes a vertex-total over a shared-trunk tree and the
`2V-2` invariant breaks; lvkit falls back to its auto-router for those. Decoder:
`render/wire_table.py::decode_wire_mid`.

---

## Constants — `<dco class="bDConstDCO">`

Found via `.//nodeList//SL__arrayElement[@class='term']` whose `<dco>` is
`bDConstDCO` (`parser/nodes/constant.py:95-130`).

| Field | Where | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `<typeDesc>` | dco | Constant's type (VCTP TypeID). | `constant.py:113` | confirmed |
| `<ConstValue>` or `<DefaultData>` | dco | The value, as hex. `ConstValue` = literal hex (old exports); `DefaultData` = quoted string mixing printable bytes + `&#xNN;` entities — must be entity-decoded, NOT `clean_labview_string`'d. | `constant.py:18-40` | confirmed |
| `<ddo>/partsList/.../class="label"` | dco ddo | The constant's visible **caption** (free label). `objFlags` bit `0x8` set = hidden auto-label (LabVIEW auto-labels + hides on "Create Constant"). Only 0x8-clear captions are shown. | `constant.py:60-92` | confirmed |
| `ddo/partsList/.../numLabel/format` | dco ddo | Display format string (e.g. `%.0x` for hex). Scoped one level to avoid a cluster picking up a nested field's format. | `constant.py:43-57` | confirmed |

---

## Enum / ring labels — `<multiLabel>`

`<buf>` holds a buffer like `(10)"Label1""Label2"...`; lvkit regex-extracts the
quoted strings, keyed by the multiLabel uid (`parser/vi.py:432-448`).

---

## objFlags — confirmed bit meanings

`objFlags` is a per-element bitfield whose meaning **depends on the element
type**. pylabview stores it (`OF__objFlags = 172` is just the tag id) but does
NOT decode the bits. Confirmed this session:

| Bit | Element | Meaning | Evidence | Confidence |
|---|---|---|---|---|
| `0x1` (bit 0) | terminal / FP DCO | isIndicator ⇒ output (terminal) / indicator (FP control). | `constants.py:151-155`, `front_panel.py:46-51` | confirmed |
| `0x8` (bit 3) | a node's / constant's `<label>` part | Owned label HIDDEN ("label visible" off). | `render/layout.py:193-206`, `constant.py:84-87` | confirmed |
| `0x00010000` (bit 16) | `cpdArith` terminal DCO | Invert this terminal ("Not"). Only on cpdArith. | `parser/flags.py:9`, `vi.py:585-589` | confirmed |
| bit 16 | `loopTestDCO` objFlags | While-loop conditional polarity (SET ⇒ Stop-if-True). | `loop.py:101-111` | confirmed |
| bit 24 | `select`/case node objFlags | Case Insensitive Match (string case). | `case.py:24,120` | confirmed |
| other bits | any | **Unknown.** e.g. `objFlags=16843392` on a case node mixes bit 24 with other set bits we do not decode. | — | unknown |

# LabVIEW VI XML Structure Reference

A deep technical reference for the XML format produced by [pylabview](https://github.com/mefistotelis/pylabview) when extracting LabVIEW `.vi` files. This documents every element, attribute, and relationship needed to parse VI files programmatically.

## Table of Contents

1. [File Extraction](#1-file-extraction)
2. [Root Structure](#2-root-structure)
3. [Block Diagram (`_BDHb.xml`)](#3-block-diagram-bdhbxml)
4. [Nodes](#4-nodes)
5. [Terminals and DCOs](#5-terminals-and-dcos)
6. [Wires (Signals)](#6-wires-signals)
7. [Type System](#7-type-system)
8. [Structure Nodes](#8-structure-nodes)
9. [Front Panel (`_FPHb.xml`)](#9-front-panel-fphbxml)
10. [Main Metadata XML](#10-main-metadata-xml)
11. [Constants](#11-constants)
12. [SubVI References](#12-subvi-references)
13. [Object Flags](#13-object-flags)
14. [UID Reference System](#14-uid-reference-system)

---

## 1. File Extraction
<sup>[back to top](#table-of-contents)</sup>

A `.vi` file is a binary RSRC container. pylabview extracts it into multiple files:

| File | Contents |
|------|----------|
| `Name_BDHb.xml` | Block Diagram Heap — all executable logic |
| `Name_FPHb.xml` | Front Panel Heap — UI controls and indicators |
| `Name.xml` | Main metadata — types, SubVI refs, connector pane |
| `Name_BKMK.bin` | Bookmarks (binary) |
| `Name_ICON.png` | VI icon (32x32) |
| `Name_icl4.png` | 4-bit color icon |
| `Name_icl8.png` | 8-bit color icon |
| `Name_HIST.bin` | Edit history (binary) |
| `Name_DSIM*.bin` | Default data (binary) |

The three XML files are the ones that matter for semantic analysis. The BDHb contains the dataflow graph, the FPHb contains the user interface, and the main XML contains type definitions and dependency metadata.

---

## 2. Root Structure
<sup>[back to top](#table-of-contents)</sup>

All three XML files share the same root wrapper:

```xml
<?xml version='1.0' encoding='utf-8'?>
<SL__rootObject class="oHExt" uid="191">
  <root class="diag" uid="109">
    ...contents...
  </root>
  <pBounds>(x1, y1, x2, y2)</pBounds>
  <dBounds>(x1, y1, x2, y2)</dBounds>
  <origin>(x, y)</origin>
  <instrStyle>31</instrStyle>
</SL__rootObject>
```

| Element | Description |
|---------|-------------|
| `SL__rootObject` | Top-level container. `class="oHExt"` is the heap extension class. |
| `root` | The actual content root. `class="diag"` for block diagrams, `class="supC"` for front panels. |
| `pBounds` | Panel bounds in pixels `(left, top, right, bottom)`. |
| `dBounds` | Diagram bounds in pixels. |
| `origin` | Scroll offset `(x, y)`. |
| `instrStyle` | Visual rendering style (31 = standard). |

All coordinate values are pixel positions used for layout. They are irrelevant to semantic analysis.

---

## 3. Block Diagram (`_BDHb.xml`)
<sup>[back to top](#table-of-contents)</sup>

The `root class="diag"` element contains three critical child lists:

```xml
<root class="diag" uid="109">
  <objFlags>...</objFlags>
  <zPlaneList elements="N">
    <!-- Visual ordering: labels, decorations, attachments -->
  </zPlaneList>
  <nodeList elements="N">
    <!-- All executable nodes: SubVIs, primitives, structures -->
    <SL__arrayElement class="iUse" uid="26">...</SL__arrayElement>
    <SL__arrayElement class="prim" uid="42">...</SL__arrayElement>
    <SL__arrayElement class="select" uid="139">...</SL__arrayElement>
  </nodeList>
  <signalList elements="N">
    <!-- All wires connecting terminals -->
    <SL__arrayElement class="signal" uid="120">...</SL__arrayElement>
  </signalList>
  <bgColor>00FFFFFF</bgColor>
  <firstNodeIdx>0</firstNodeIdx>
</root>
```

| Element | Description |
|---------|-------------|
| `zPlaneList` | Z-order stacking of visual elements. Contains labels, decorations, and attachments. Not semantically meaningful for dataflow. |
| `nodeList` | **The core.** Every executable node: VI calls, primitives, structures, constants. Each has a unique `uid`. |
| `signalList` | **Wires.** Each signal connects a source terminal to one or more destination terminals. |
| `bgColor` | Background color in hex RRGGBB (with alpha prefix). |
| `firstNodeIdx` | Index into nodeList for priority execution ordering. |

### Nested Diagrams

Structure nodes (loops, cases, sequences) contain their own `diagramList` with nested `diag` elements. Each nested diagram has its own `nodeList` and `signalList`, creating a tree of diagrams.

---

## 4. Nodes
<sup>[back to top](#table-of-contents)</sup>

Every node in `nodeList` is an `SL__arrayElement` with a `class` attribute that determines its type. The `uid` attribute is the node's unique identifier.

### 4.1 Node Type Registry

| XML Class | LabVIEW Concept | Description |
|-----------|----------------|-------------|
| `iUse` | [SubVI Call](#42-subvi-call-iuse) | Calls another VI |
| `polyIUse` | [Polymorphic SubVI Call](#43-polymorphic-subvi-call-polyiuse) | Calls a polymorphic VI (type-dispatched) |
| `dynIUse` | [Dynamic Dispatch Call](#44-dynamic-dispatch-call-dyniuse) | Calls an overridable class method |
| `prim` | [Primitive](#45-primitive-prim) | Built-in operation (Add, Subtract, etc.) |
| `cpdArith` | [Compound Arithmetic](#46-compound-arithmetic-cpdarith) | Multi-input arithmetic (Add N, Multiply N, AND, OR) |
| `aBuild` | [Build Array](#47-array-operations) | Constructs an array from elements |
| `aDelete` | [Delete From Array](#47-array-operations) | Removes elements from an array |
| `aIndx` | [Index Array](#47-array-operations) | Reads element(s) from an array |
| `concat` | [Concatenate Strings](#48-string-operations) | Joins strings |
| `subset` | [Array Subset](#47-array-operations) | Extracts a subarray |
| `split` | Split Number | Splits a numeric into high/low parts |
| `select` | [Case/Select Structure](#81-case--select-structure-select-casestruct) | Multi-frame conditional execution |
| `caseStruct` | [Case Structure](#81-case--select-structure-select-casestruct) | Alternative form of case structure |
| `whileLoop` | [While Loop](#82-while-loop-whileloop) | Repeats until stop condition |
| `forLoop` | [For Loop](#83-for-loop-forloop) | Repeats N times |
| `flatSequence` | [Flat Sequence](#84-flat-sequence-flatsequence) | Frames execute left-to-right |
| `seq` | [Stacked Sequence](#85-stacked-sequence-seq) | Frames execute top-to-bottom |
| `propNode` | [Property Node](#49-property-node-propnode) | Reads/writes object properties |
| `invokeNode` | [Invoke Node](#410-invoke-node-invokenode) | Calls an object method |
| `nMux` | [Node Multiplexer](#411-node-multiplexer-nmux) | Bundle/unbundle at structure boundaries |
| `printf` | [Format Into String](#48-string-operations) | String formatting |
| `sRN` | [Structure Root Node](#412-structure-root-node-srn) | Owns all FP terminals for the VI |
| `fPTerm` | [Front Panel Terminal](#412-structure-root-node-srn) | Connection point to the front panel |

### 4.2 SubVI Call (`iUse`)

```xml
<SL__arrayElement class="iUse" uid="26">
  <objFlags>512</objFlags>
  <termList elements="3">
    <SL__arrayElement class="term" uid="76">
      <dco class="iUseDCO" uid="74">
        <objFlags>128</objFlags>
        <typeDesc>TypeID(1)</typeDesc>
        <termBounds>(0, 0, 32, 16)</termBounds>
      </dco>
    </SL__arrayElement>
    <!-- more terminals -->
  </termList>
  <label class="label" uid="38">
    <text>"SubVIName.vi"</text>
  </label>
  <bounds>(84, 84, 116, 116)</bounds>
  <shortCount>2</shortCount>
  <connectorTM>TypeID(4)</connectorTM>
</SL__arrayElement>
```

| Field | Description |
|-------|-------------|
| `termList` | Input and output terminals (see [Terminals](#5-terminals-and-dcos)) |
| `label/text` | The SubVI's name |
| `bounds` | Position on the block diagram |
| `shortCount` | Internal execution counter |
| `connectorTM` | TypeID reference to the connector pane type map |

The SubVI's identity (qualified name) is resolved through the main metadata XML's BDHP section, which maps `uid` to qualified name strings.

### 4.3 Polymorphic SubVI Call (`polyIUse`)

Same structure as `iUse`, but the VI can accept multiple data types. The specific variant is selected at edit time or determined by wiring. Polymorphic wrapper metadata (PUPV section) maps the wrapper to its variants.

### 4.4 Dynamic Dispatch Call (`dynIUse`)

```xml
<SL__arrayElement class="dynIUse" uid="42">
  <termList elements="4">...</termList>
  <label class="label" uid="50">
    <text>"ClassName.lvclass:MethodName.vi"</text>
  </label>
  <bounds>...</bounds>
</SL__arrayElement>
```

Like `iUse` but calls an overridable method on a LabVIEW class. The actual method dispatched depends on the runtime class of the input object.

### 4.5 Primitive (`prim`)

```xml
<SL__arrayElement class="prim" uid="1130">
  <termList elements="5">
    <SL__arrayElement class="term" uid="1142">
      <objFlags>32768</objFlags>
      <dco class="parm" uid="1137">
        <objFlags>65536</objFlags>
        <typeDesc>TypeID(36)</typeDesc>
        <termBounds>(24, 0, 32, 8)</termBounds>
        <primIndex>349</primIndex>
      </dco>
    </SL__arrayElement>
    <!-- more terminals -->
  </termList>
  <bounds>(281, 114, 313, 146)</bounds>
  <shortCount>6</shortCount>
  <clumpNum>327683</clumpNum>
  <primResID>8003</primResID>
  <primIndex>349</primIndex>
</SL__arrayElement>
```

| Field | Description |
|-------|-------------|
| `primResID` | **Primary identifier.** Uniquely identifies the operation (e.g., 8003 = Variant To Data). See [Primitive ID Ranges](#primitive-id-ranges). |
| `primIndex` | Sequence number within the primitive table. NOT semantically meaningful for identification — use `primResID`. |
| `clumpNum` | Execution clump assignment (scheduling). |
| `termList/dco class="parm"` | Terminal with `parmIndex` for parameter position. |

#### Primitive ID Ranges

| Range | Category |
|-------|----------|
| 1044–1064 | Array operations |
| 1051–1081 | Numeric/arithmetic |
| 1083–1128 | Comparison, boolean |
| 1140–1170 | Type conversion, variant |
| 1300–1340 | Timing, constants, clusters |
| 1419–1435 | Path operations |
| 1500–1540 | String operations |
| 1600–1610 | Flatten/unflatten |
| 1809–1911 | Array index/sort/delete |
| 1999 | Path constant |
| 2073–2076 | Error handling |
| 2401 | Merge Errors |
| 8003–8083 | File I/O and Variant operations |
| 8100–8101 | VI info |
| 8201–8205 | Variant attributes |
| 9000–9114 | VI Server, references, scripting |

**Important:** Some `primResID` values are shared between different functions that are distinguished by `class` attribute. For example, primResID 1516 is used by both Array Subset (`class="subset"`) and Select (`class="prim"`). Always check `class` first, then `primResID`.

### 4.6 Compound Arithmetic (`cpdArith`)

```xml
<SL__arrayElement class="cpdArith" uid="50">
  <termList elements="3">...</termList>
  <operation>add</operation>
  <bounds>...</bounds>
</SL__arrayElement>
```

| Field | Description |
|-------|-------------|
| `operation` | One of: `add`, `multiply`, `and`, `or`, `xor` |

Multi-input version of basic arithmetic. Has N inputs and 1 output. Inputs are expandable (variable count).

### 4.7 Array Operations

**Build Array (`aBuild`):**
```xml
<SL__arrayElement class="aBuild" uid="60">
  <termList elements="N">...</termList>
</SL__arrayElement>
```
Variable number of input elements, one output array. The number of inputs determines the array size.

**Index Array (`aIndx`):**
```xml
<SL__arrayElement class="aIndx" uid="70">
  <termList elements="3">...</termList>
</SL__arrayElement>
```
Input: array + index. Output: element. Expandable for multi-dimensional indexing.

**Delete From Array (`aDelete`):**
```xml
<SL__arrayElement class="aDelete" uid="80">
  <termList elements="4">...</termList>
</SL__arrayElement>
```
Input: array + index + length. Output: deleted portion + remaining array.

**Array Subset (`subset`):**
```xml
<SL__arrayElement class="subset" uid="90">
  <termList elements="4">...</termList>
</SL__arrayElement>
```
Input: array + index + length. Output: subarray. **Note:** This is the `class="subset"` version. The `class="prim"` with primResID 1516 is the Select function, NOT Array Subset.

### 4.8 String Operations

**Concatenate Strings (`concat`):**
```xml
<SL__arrayElement class="concat" uid="100">
  <termList elements="N">...</termList>
</SL__arrayElement>
```
Variable number of input strings, one concatenated output. Expandable inputs.

**Format Into String (`printf`):**
```xml
<SL__arrayElement class="printf" uid="110">
  <termList elements="N">...</termList>
</SL__arrayElement>
```
Format string + arguments → formatted output string.

### 4.9 Property Node (`propNode`)

```xml
<SL__arrayElement class="propNode" uid="120">
  <termList elements="4">...</termList>
  <label class="label" uid="125">
    <text>"ObjectName"</text>
  </label>
  <props elements="2">
    <SL__arrayElement>
      <prop>PropertyName</prop>
      <accessType>read</accessType>
    </SL__arrayElement>
  </props>
</SL__arrayElement>
```

Property nodes access object properties. Each property in the `props` list is a "drawer" that executes sequentially (NOT in parallel). Terminal pairs (input/output at same index) form pass-through connections for the reference wire.

### 4.10 Invoke Node (`invokeNode`)

```xml
<SL__arrayElement class="invokeNode" uid="130">
  <termList elements="5">...</termList>
  <label class="label" uid="135">
    <text>"ObjectName"</text>
  </label>
  <method>MethodName</method>
  <methodCode>42</methodCode>
</SL__arrayElement>
```

Calls a method on an object reference. The `method` field names the method, `methodCode` is its numeric identifier.

### 4.11 Node Multiplexer (`nMux`)

```xml
<SL__arrayElement class="nMux" uid="140">
  <termList elements="N">...</termList>
</SL__arrayElement>
```

Bundle/unbundle operations at structure boundaries. Used for cluster field access. The DCO elements contain `dco_agg_uid` (aggregate/cluster terminal) and `dco_list_uids` (individual field terminals), with `dco_field_index` mapping DCO UIDs to field positions.

### 4.12 Structure Root Node (`sRN`)

```xml
<SL__arrayElement class="sRN" uid="111">
  <objFlags>16384</objFlags>
  <termList elements="3">
    <SL__arrayElement class="fPTerm" uid="149">...</SL__arrayElement>
    <SL__arrayElement class="fPTerm" uid="150">...</SL__arrayElement>
  </termList>
  <bounds>(0, 0, 0, 0)</bounds>
  <shortCount>1</shortCount>
</SL__arrayElement>
```

The sRN owns all of the VI's front panel terminals. It's NOT an executable node — it's the structural anchor for the VI's inputs and outputs. All `fPTerm` elements inside the sRN are connection points to the front panel controls/indicators.

**Inside structures:** sRN nodes also appear inside loops and case structures as shift register / tunnel boundary nodes. These are identified by `terminal_info.parent_uid` not matching any node in `bd.nodes` — they belong to the enclosing structure, and are matched via `bd.srn_to_structure`.

---

## 5. Terminals and DCOs
<sup>[back to top](#table-of-contents)</sup>

Every node has a `termList` containing its connection points. Each terminal is a `term` element with exactly one `dco` (Data Connector Object) child that carries the terminal's metadata.

### 5.1 Terminal Structure

```xml
<SL__arrayElement class="term" uid="76">
  <objFlags>4227136</objFlags>
  <dco class="iUseDCO" uid="74">
    <objFlags>128</objFlags>
    <termList elements="3">
      <SL__arrayElement uid="72" />
      <SL__arrayElement uid="73" />
      <SL__arrayElement uid="76" />
    </termList>
    <typeDesc>TypeID(1)</typeDesc>
    <termBounds>(0, 0, 32, 16)</termBounds>
  </dco>
</SL__arrayElement>
```

| Field | Description |
|-------|-------------|
| `uid` | Terminal's unique identifier. Wires reference this. |
| `objFlags` | Bit flags encoding direction and properties. |
| `dco` | Data Connector Object — carries type, position, and sub-terminal references. |
| `dco/typeDesc` | Type reference (`TypeID(N)`) resolved against the type map. |
| `dco/termBounds` | Pixel position of the terminal on the node icon. |

### 5.2 DCO Classes

The `dco` element's `class` attribute identifies what kind of terminal this is:

| DCO Class | Context | Description |
|-----------|---------|-------------|
| `iUseDCO` | SubVI call | Terminal on a SubVI call node |
| `parm` | Primitive | Terminal on a primitive operation |
| `fPDCO` | Front panel | Terminal on a front panel control/indicator |
| `caseSel` | Case structure | The selector input terminal |
| `selTun` | Case structure | Data tunnel across case boundary |
| `lSR` | Loop | Left Shift Register (input tunnel) |
| `rSR` | Loop | Right Shift Register (output tunnel) |
| `lpTun` | Loop | Loop Pass-through Tunnel |
| `lMax` | Loop | Left Max / accumulator terminal |
| `flatSeqTun` | Flat sequence | Tunnel between sequence frames |
| `seqTun` | Stacked sequence | Tunnel between sequence frames |
| `dBD_Const` | Constant | Constant value on the diagram |
| `bDConstDCO` | Constant | Alternative constant DCO class |

### 5.3 DCO Sub-Terminal Lists

Many DCO types contain their own inner `termList` with 2-3 UIDs:

```xml
<dco class="selTun" uid="159">
  <termList elements="3">
    <SL__arrayElement uid="163" />   <!-- inner terminal (frame 0) -->
    <SL__arrayElement uid="162" />   <!-- inner terminal (frame 1) -->
    <SL__arrayElement uid="164" />   <!-- outer terminal -->
  </termList>
</dco>
```

For **tunnel DCOs** (`selTun`, `lSR`, `rSR`, `lpTun`, `lMax`, `flatSeqTun`):
- The last UID in the list is the **outer** terminal (outside the structure).
- Earlier UIDs are **inner** terminals (inside the structure, one per frame for case structures).

For **caseSel DCOs**:
```xml
<dco class="caseSel" uid="447">
  <termList elements="3">
    <SL__arrayElement uid="449" />   <!-- inner -->
    <SL__arrayElement uid="450" />   <!-- pass-through -->
    <SL__arrayElement uid="451" />   <!-- outer (the selector input wire) -->
  </termList>
</dco>
```

### 5.4 Terminal Direction

Terminal direction (input vs output) is determined by:

1. **`objFlags` bit analysis:** Certain bit patterns indicate input or output.
2. **Wire connectivity:** If a terminal appears as a wire source → output. If it appears as a wire destination → input.
3. **DCO-specific rules:** Some DCO classes have fixed directions (e.g., `caseSel` is always input).

The parser uses wire connectivity as the authoritative source because `objFlags` encoding varies across LabVIEW versions.

### 5.5 Terminal Index (`parmIndex` / `paramIdx`)

The connector pane position of a terminal. This is the sparse index that identifies which "slot" on the node icon this terminal occupies.

```xml
<dco class="parm" uid="1137">
  <parmIndex>2</parmIndex>
  <!-- OR -->
  <paramIdx>2</paramIdx>
</dco>
```

Both `parmIndex` and `paramIdx` are used (varies by node type). When neither is present, the index must be inferred from:
1. The primitive definition's terminal layout (matched by type and direction)
2. List position within the `termList` (least reliable, used as fallback)

**Critical:** Terminal indices are NOT sequential. A node might have terminals at indices 0, 1, 3, 8, 11 (skipping 2, 4-7, 9-10). This is because the connector pane has a fixed grid layout and not all slots are used.

---

## 6. Wires (Signals)
<sup>[back to top](#table-of-contents)</sup>

Wires are `signal` elements in the `signalList`. Each wire connects one source terminal to one or more destination terminals.

### 6.1 Wire Structure

```xml
<signalList elements="8">
  <SL__arrayElement class="signal" uid="120">
    <objFlags>131072</objFlags>
    <termList elements="2">
      <SL__arrayElement uid="303" />   <!-- source terminal -->
      <SL__arrayElement uid="149" />   <!-- destination terminal -->
    </termList>
    <state>1</state>
    <compressedWireTable>0208</compressedWireTable>
    <lastSignalKind>-31920</lastSignalKind>
  </SL__arrayElement>
</signalList>
```

| Field | Description |
|-------|-------------|
| `termList` | Terminal UIDs. First element is the source, subsequent elements are destinations. |
| `state` | Wire routing state (visual). |
| `compressedWireTable` | Hex-encoded wire routing points for the visual path. |
| `lastSignalKind` | Visual style code (wire thickness, color). |

### 6.2 Fan-Out Wires

A single signal can have multiple destinations (fan-out):

```xml
<SL__arrayElement class="signal" uid="200">
  <termList elements="3">
    <SL__arrayElement uid="100" />   <!-- source -->
    <SL__arrayElement uid="201" />   <!-- destination 1 -->
    <SL__arrayElement uid="202" />   <!-- destination 2 -->
  </termList>
</SL__arrayElement>
```

This represents one output wired to two inputs. The first terminal is always the source.

### 6.3 Cross-Structure Wires

Wires cannot cross structure boundaries directly. Instead, data passes through tunnel terminals. A wire inside a case frame connects to the tunnel's inner terminal, while a wire outside connects to the tunnel's outer terminal. The tunnel DCO's sub-terminal list maps inner to outer.

---

## 7. Type System
<sup>[back to top](#table-of-contents)</sup>

### 7.1 TypeID References

All terminals reference types via `<typeDesc>TypeID(N)</typeDesc>` where N is an index into the type map.

```xml
<dco class="parm" uid="50">
  <typeDesc>TypeID(21)</typeDesc>
</dco>
```

### 7.2 Type Resolution Chain

Types are resolved through a multi-layer chain:

```
TypeID(N) in BDHb XML
    ↓
Heap type table (in-memory during extraction)
    ↓
Consolidated type table
    ↓
FlatTypeID → VCTP section (VI Consolidated Type Pool)
    ↓
Full type definition with fields, enum values, etc.
```

The VCTP section in the main XML contains the detailed type definitions. Each entry describes:
- **Primitive types:** NumInt32, NumFloat64, String, Boolean, Path, etc.
- **Clusters:** Ordered list of named fields with their types
- **Arrays:** Element type + dimensionality
- **Enums:** Underlying integer type + label-to-value mapping
- **Refnums:** Reference type class (VI ref, control ref, file ref, etc.)
- **Typedefs:** Path to `.ctl` file defining the type

### 7.3 Common Type Names

| Type Name | Description |
|-----------|-------------|
| `NumInt8` / `NumInt16` / `NumInt32` / `NumInt64` | Signed integers |
| `NumUInt8` / `NumUInt16` / `NumUInt32` / `NumUInt64` | Unsigned integers |
| `NumFloat32` / `NumFloat64` / `NumFloatExt` | Floating point (single, double, extended) |
| `NumComplex64` / `NumComplex128` / `NumComplexExt` | Complex numbers |
| `String` | String |
| `Boolean` | Boolean |
| `Path` | File system path |
| `LVVariant` | LabVIEW variant (dynamically typed container) |
| `Refnum` | Reference to an object (VI, control, file, etc.) |
| `Cluster` | Ordered collection of named fields (like a struct) |
| `Array` | Ordered collection of elements of one type |
| `Enum` | Named integer values |

### 7.4 Error Cluster

The error cluster is a specific cluster type with three fields:

| Field | Type | Description |
|-------|------|-------------|
| `status` | Boolean | True = error occurred |
| `code` | NumInt32 | Error code number |
| `source` | String | Error source description |

Error clusters are identified by:
1. Typedef name containing "error" (case-insensitive), OR
2. Cluster with exactly three fields matching the status/code/source pattern

---

## 8. Structure Nodes
<sup>[back to top](#table-of-contents)</sup>

Structures are nodes that contain nested diagrams. They have special tunnel terminals for data flow across their boundaries.

### 8.1 Case / Select Structure (`select`, `caseStruct`)

```xml
<SL__arrayElement class="select" uid="139">
  <objFlags>268501632</objFlags>
  <termList elements="7">
    <!-- Selector terminal -->
    <SL__arrayElement class="term" uid="155">
      <dco class="caseSel" uid="146">
        <termList elements="3">
          <SL__arrayElement uid="147" />   <!-- inner ref -->
          <SL__arrayElement uid="153" />   <!-- pass-through -->
          <SL__arrayElement uid="155" />   <!-- outer (selector wire) -->
        </termList>
        <typeDesc>TypeID(21)</typeDesc>
        <termBounds>(575, 0, 587, 8)</termBounds>
        <termBMPs>5</termBMPs>
      </dco>
    </SL__arrayElement>

    <!-- Data tunnel terminals -->
    <SL__arrayElement class="term" uid="164">
      <dco class="selTun" uid="159">
        <objFlags>2049</objFlags>
        <termList elements="3">
          <SL__arrayElement uid="163" />   <!-- frame 0 inner -->
          <SL__arrayElement uid="162" />   <!-- frame 1 inner -->
          <SL__arrayElement uid="164" />   <!-- outer -->
        </termList>
        <typeDesc>TypeID(22)</typeDesc>
      </dco>
    </SL__arrayElement>
    <!-- more tunnels -->
  </termList>

  <bounds>(322, 168, 955, 947)</bounds>
  <contRect>(328, 174, 949, 941)</contRect>

  <tunnelList elements="6">
    <SL__arrayElement uid="159" />   <!-- DCO UIDs for each tunnel -->
    <SL__arrayElement uid="171" />
  </tunnelList>

  <diagramList elements="2">
    <SL__arrayElement class="diag" uid="141">
      <!-- Frame 0: own nodeList and signalList -->
      <nodeList elements="N">...</nodeList>
      <signalList elements="N">...</signalList>
    </SL__arrayElement>
    <SL__arrayElement class="diag" uid="350">
      <!-- Frame 1: own nodeList and signalList -->
      <nodeList elements="N">...</nodeList>
      <signalList elements="N">...</signalList>
    </SL__arrayElement>
  </diagramList>
</SL__arrayElement>
```

#### Selector Terminal (`caseSel`)

The `caseSel` DCO is the case selector input. Its type determines the case behavior:
- `Boolean` → if/else (True/False frames)
- `NumInt*` / `NumUInt*` → match-case on integer values
- `String` → match-case on string values
- `Enum` → match-case on enum values

#### Selector Value Mapping

Case structures include metadata mapping selector values to frame indices:

```xml
<SelectRangeArray32>
  <!-- Maps integer ranges to frame indices -->
</SelectRangeArray32>
<SelectStringArray>
  <!-- Maps string labels to frame indices -->
</SelectStringArray>
<SelectDefaultCase>N</SelectDefaultCase>
```

- `SelectRangeArray32`: For integer selectors — maps value ranges to diagram indices
- `SelectStringArray`: For string selectors — maps string labels to diagram indices
- `SelectDefaultCase`: Index of the default frame

#### Tunnel Terminal Layout (`selTun`)

Each `selTun` DCO's inner `termList` has **N+1 UIDs** where N is the number of frames:

```
termList[0]     → inner terminal for frame 0
termList[1]     → inner terminal for frame 1
...
termList[N-1]   → inner terminal for frame N-1
termList[N]     → OUTER terminal (connects to outside)
```

The outer terminal is always the **last** element. Inner terminals connect to wires inside each frame's diagram.

### 8.2 While Loop (`whileLoop`)

```xml
<SL__arrayElement class="whileLoop" uid="200">
  <termList elements="N">
    <!-- Input tunnel (shift register left) -->
    <SL__arrayElement class="term" uid="205">
      <dco class="lSR" uid="201">
        <termList elements="2">
          <SL__arrayElement uid="204" />   <!-- inner -->
          <SL__arrayElement uid="205" />   <!-- outer -->
        </termList>
        <typeDesc>TypeID(10)</typeDesc>
      </dco>
    </SL__arrayElement>

    <!-- Output tunnel (shift register right) -->
    <SL__arrayElement class="term" uid="208">
      <dco class="rSR" uid="206">
        <termList elements="2">
          <SL__arrayElement uid="207" />   <!-- inner -->
          <SL__arrayElement uid="208" />   <!-- outer -->
        </termList>
      </dco>
    </SL__arrayElement>

    <!-- Pass-through tunnel -->
    <SL__arrayElement class="term" uid="212">
      <dco class="lpTun" uid="209">
        <termList elements="2">
          <SL__arrayElement uid="211" />   <!-- inner -->
          <SL__arrayElement uid="212" />   <!-- outer -->
        </termList>
      </dco>
    </SL__arrayElement>

    <!-- Accumulator (loop count, auto-index) -->
    <SL__arrayElement class="term" uid="216">
      <dco class="lMax" uid="213">
        <termList elements="2">
          <SL__arrayElement uid="215" />   <!-- inner -->
          <SL__arrayElement uid="216" />   <!-- outer -->
        </termList>
      </dco>
    </SL__arrayElement>
  </termList>

  <diagramList elements="1">
    <SL__arrayElement class="diag" uid="220">
      <nodeList elements="N">...</nodeList>
      <signalList elements="N">...</signalList>
    </SL__arrayElement>
  </diagramList>
</SL__arrayElement>
```

#### Loop Tunnel Types

| DCO Class | Name | Direction | Description |
|-----------|------|-----------|-------------|
| `lSR` | Left Shift Register | Input | Data enters loop from outside. Inner terminal provides initial value. |
| `rSR` | Right Shift Register | Output | Data exits loop. Inner terminal receives each iteration's result. |
| `lpTun` | Loop Tunnel | Pass-through | Data passes through without modification. Equivalent to a wire crossing the boundary. |
| `lMax` | Left Max / Accumulator | Output | Accumulates values across iterations. Used for loop count (i) and auto-indexing. |

#### Tunnel termList Layout (Loops)

Loop tunnels have exactly **2 UIDs**:
```
termList[0] → inner terminal (inside the loop body)
termList[1] → outer terminal (outside the loop)
```

#### Shift Register Pairing

Left (`lSR`) and right (`rSR`) shift registers are paired by position. The first `lSR` pairs with the first `rSR`, second with second, etc. The right shift register's inner terminal feeds back to the left shift register's inner terminal on each iteration.

### 8.3 For Loop (`forLoop`)

Identical structure to `whileLoop` but with:
- A count terminal (`N`) that determines iteration count
- No stop condition terminal
- Auto-indexing tunnels that automatically iterate over arrays

### 8.4 Flat Sequence (`flatSequence`)

```xml
<SL__arrayElement class="flatSequence" uid="300">
  <termList elements="N">
    <SL__arrayElement class="term" uid="305">
      <dco class="flatSeqTun" uid="301">
        <termList elements="3">
          <SL__arrayElement uid="303" />   <!-- frame 0 inner -->
          <SL__arrayElement uid="304" />   <!-- frame 1 inner -->
          <SL__arrayElement uid="305" />   <!-- outer -->
        </termList>
      </dco>
    </SL__arrayElement>
  </termList>

  <sequenceList elements="2">
    <SL__arrayElement class="sequenceFrame" uid="310">
      <nodeList elements="N">...</nodeList>
      <signalList elements="N">...</signalList>
    </SL__arrayElement>
    <SL__arrayElement class="sequenceFrame" uid="320">
      <nodeList elements="N">...</nodeList>
      <signalList elements="N">...</signalList>
    </SL__arrayElement>
  </sequenceList>
</SL__arrayElement>
```

Flat sequences use `sequenceList` with `sequenceFrame` children (not `diagramList`). Frames execute left-to-right. Tunnel layout is same as case structures: inner UIDs per frame, outer UID last.

**Important:** All operations in a flat sequence frame execute, even those not connected by wires. This is different from dataflow execution where unwired nodes might not execute.

### 8.5 Stacked Sequence (`seq`)

Similar to flat sequence but uses `diagramList` with `diag` elements. Visually stacked (only one frame visible at a time). Uses `seqTun` DCO class for tunnels.

---

## 9. Front Panel (`_FPHb.xml`)
<sup>[back to top](#table-of-contents)</sup>

The front panel defines the VI's user interface — controls (inputs) and indicators (outputs).

### 9.1 Structure

```xml
<root class="supC" uid="1">
  <objFlags>65536</objFlags>
  <bounds>(0, 0, 317, 417)</bounds>
  <ddoList elements="0" />
  <paneHierarchy class="pane" uid="141">
    <partsList elements="N">
      <SL__arrayElement class="label" uid="143">...</SL__arrayElement>
      <SL__arrayElement class="cosm" uid="144">...</SL__arrayElement>
      <!-- Controls and indicators appear in parts -->
    </partsList>
  </paneHierarchy>
  <conPane class="conPane" uid="5">
    <conId>4800</conId>
    <cons elements="N">
      <!-- Connector pane slot mappings -->
    </cons>
  </conPane>
</root>
```

### 9.2 Control Types

Front panel controls have `fPDCO` elements with specific types:

| Control Class | LabVIEW Control | Python Type |
|---------------|----------------|-------------|
| `stdString` | String Control | `str` |
| `stdBool` | Boolean Control | `bool` |
| `stdNum` | Numeric Control | `int` / `float` |
| `stdPath` | Path Control | `Path` |
| `stdEnum` | Enum Control | `Enum` |
| `stdClust` | Cluster Control | `NamedTuple` / dataclass |
| `stdArray` | Array Control | `list` |
| `stdVariant` | Variant Control | `Any` |
| `stdRef` | Refnum Control | reference type |

### 9.3 Connector Pane

The `conPane` element maps front panel controls to connector pane slots:

```xml
<conPane class="conPane" uid="5">
  <conId>4800</conId>
  <cons elements="4">
    <SL__arrayElement>
      <slotIdx>0</slotIdx>
      <fpDCO uid="10" />   <!-- which FP control -->
    </SL__arrayElement>
    <!-- more slots -->
  </cons>
</conPane>
```

The `conId` identifies the connector pane pattern (layout of slots). Each slot maps to a front panel control/indicator by its DCO UID. The slot index determines the terminal's position in the SubVI call interface.

### 9.4 Control vs Indicator

A control is an input (user provides data). An indicator is an output (VI produces data). Determined by:

1. **Signal analysis:** If the FP terminal is wired as a signal source → indicator (output). If wired as destination → control (input).
2. **`is_indicator` flag** on the fPTerm element.
3. **Terminal direction** on the connector pane slot.

---

## 10. Main Metadata XML
<sup>[back to top](#table-of-contents)</sup>

The main XML file (`Name.xml`) contains the VI's metadata in RSRC format:

```xml
<RSRC FormatVersion="3" Type="LVIN" Encoding="mac_roman">
  <LIBN>...</LIBN>    <!-- Library membership -->
  <LVSR>...</LVSR>    <!-- LabVIEW Save Record -->
  <CONP>...</CONP>    <!-- Connector Pane Type Map -->
  <LIvi>...</LIvi>    <!-- SubVI link references -->
  <STRG>...</STRG>    <!-- VI description string -->
  <CPC2>...</CPC2>    <!-- Connector pane v2 -->
  <BDHP>...</BDHP>    <!-- Block Diagram Heap Pointers -->
  <VCTP>...</VCTP>    <!-- VI Consolidated Type Pool -->
  <ICON>...</ICON>    <!-- Icon data -->
</RSRC>
```

### Key Sections

| Section | Description |
|---------|-------------|
| `LIBN` | Library name if this VI belongs to an `.lvlib` |
| `LVSR` | VI version, execution state, priority, front panel config |
| `CONP` | Type definitions for all `TypeID(N)` references |
| `LIvi` | SubVI dependency list with qualified names |
| `BDHP` | Maps block diagram node UIDs to SubVI qualified names |
| `VCTP` | Full type definitions (cluster fields, enum values, etc.) |
| `CPC2` | Connector pane type map version 2 |
| `VIVI` | SubVI qualified name list |
| `PUPV` | Polymorphic VI wrapper → variant mappings |
| `IUVI` | Resolved polymorphic variant for each call site |

### 10.1 BDHP — UID to SubVI Name Mapping

The BDHP section maps `iUse` node UIDs to their SubVI qualified names:

```
uid_26 → "MyLibrary.lvlib:SubVIName.vi"
uid_42 → "vilib:Error Cluster From Error Code.vi"
```

This is how the parser knows which VI an `iUse` node calls — the block diagram XML only has the UID, not the name.

### 10.2 VIVI — SubVI Qualified Names

Lists all SubVIs referenced by this VI with their full qualified names:

```
"MyLibrary.lvlib:SubVIName.vi"
"OpenG String Library.lvlib:Format Variant Into String__ogtk.vi"
```

Path hints indicate where to find the SubVI:
- `<vilib>` → LabVIEW's built-in VI library
- `<userlib>` → User library directory
- Relative path → relative to this VI's location

### 10.3 PUPV — Polymorphic VI Variants

Maps polymorphic wrapper VIs to their type-specific variants:

```
"Add (Polymorphic).vi" → ["Add (DBL).vi", "Add (I32).vi", "Add (SGL).vi"]
```

### 10.4 IUVI — Resolved Variant Selection

For each `polyIUse` call site, records which variant was selected:

```
uid_50 → "Add (DBL).vi"   (this call uses the Double variant)
```

---

## 11. Constants
<sup>[back to top](#table-of-contents)</sup>

Constants appear as nodes in the `nodeList` with a special DCO class:

```xml
<SL__arrayElement class="const" uid="300">
  <dco class="dBD_Const" uid="298">
    <typeDesc>TypeID(20)</typeDesc>
    <val>00000001</val>
    <termBounds>(0, 0, 32, 16)</termBounds>
  </dco>
</SL__arrayElement>
```

Alternative form:
```xml
<SL__arrayElement uid="500">
  <dco class="bDConstDCO" uid="498">
    <typeDesc>TypeID(15)</typeDesc>
    <val>48656C6C6F</val>
  </dco>
</SL__arrayElement>
```

| Field | Description |
|-------|-------------|
| `dco class` | `dBD_Const` or `bDConstDCO` — both are constants |
| `typeDesc` | Type of the constant value |
| `val` | **Hex-encoded** value. Must be decoded based on type. |

### 11.1 Value Encoding

The `val` field is raw hex bytes. Decoding depends on the type:

| Type | Encoding | Example |
|------|----------|---------|
| Boolean | `00` = False, `01` = True | `01` → True |
| NumInt32 | 4 bytes big-endian | `00000001` → 1 |
| NumFloat64 | 8 bytes IEEE 754 big-endian | `3FF0000000000000` → 1.0 |
| String | ASCII hex | `48656C6C6F` → "Hello" |
| Enum | Integer value (index into enum labels) | `0002` → third enum value |
| Path | Length-prefixed string | varies |

Constants have a single output terminal (they are sources, never sinks).

---

## 12. SubVI References
<sup>[back to top](#table-of-contents)</sup>

### 12.1 Resolution Chain

When the parser encounters an `iUse` node:

1. Get the node's `uid` from the BDHb XML
2. Look up `uid` in the BDHP section → get qualified name
3. If not in BDHP, look up by label text
4. Resolve qualified name to a file path using search paths
5. Recursively parse the referenced VI

### 12.2 Qualified Name Format

SubVI names follow a hierarchical pattern:

```
LibraryName.lvlib:ClassName.lvclass:MethodName.vi
```

Examples:
- `"Format Variant Into String__ogtk.vi"` — standalone VI
- `"VITesterUtilities.lvlib:Get Name Or Data Format As String.vi"` — library member
- `"TestCase.lvclass:failUnlessEqual.vi"` — class method
- `"vilib:dialog/1button.vi"` — vilib built-in

### 12.3 LabVIEW Classes (`.lvclass`)

A `.lvclass` file defines an object-oriented class with:
- Private data cluster (the class's data fields)
- Methods (`.vi` files in the class directory)
- Inheritance hierarchy (parent class reference)
- Dynamic dispatch VIs (overridable methods)

The class XML contains method lists and inheritance info. Method VIs reference their parent class via qualified names.

---

## 13. Object Flags
<sup>[back to top](#table-of-contents)</sup>

The `objFlags` field is a bitmask present on most elements. Key bits:

| Bit Pattern | Context | Meaning |
|-------------|---------|---------|
| `128` (bit 7) | DCO | Input direction hint |
| `1` (bit 0) | DCO | Output direction hint |
| `2048` (bit 11) | DCO | Special tunnel flag |
| `2049` | selTun DCO | Output tunnel |
| `16384` (bit 14) | Node | Structure root node |
| `32768` (bit 15) | Terminal | Input terminal |
| `65536` (bit 16) | Root | Top-level container |
| `131072` (bit 17) | Signal | Standard wire |
| `4227136` | Terminal | Output terminal with full flags |
| `4194368` | Terminal | Output terminal variant |
| `268501632` | Node | Case structure |

**Warning:** Flag meanings are not fully documented by NI and vary across LabVIEW versions. Wire connectivity analysis is more reliable than flag analysis for determining terminal direction.

---

## 14. UID Reference System
<sup>[back to top](#table-of-contents)</sup>

Every element in the XML has a unique `uid` attribute (integer). UIDs create the reference fabric that ties the diagram together:

### 14.1 Reference Chains

```
Node (uid=42)
  └─ termList
       └─ term (uid=76)
            └─ dco (uid=74)
                 └─ termList [uid=72, uid=73, uid=76]

Signal (uid=120)
  └─ termList [uid=303, uid=76]
       ↑ source    ↑ destination
       references node 42's terminal
```

### 14.2 UID Scoping

UIDs are unique **within a single VI's extracted XML files**. They are NOT globally unique across VIs. The graph construction qualifies UIDs with the VI name to create globally unique identifiers:

```
Raw UID: 42
Qualified ID: "TestCase.lvclass:failUnlessEqual.vi::42"
```

### 14.3 Cross-Reference Patterns

| From | To | Via |
|------|----|-----|
| Wire → Terminal | `signal/termList/uid` references `term/@uid` |
| Terminal → Type | `dco/typeDesc` references `TypeID(N)` in type map |
| Tunnel inner → outer | `dco/termList` UIDs reference inner/outer terminal UIDs |
| SubVI call → SubVI def | BDHP maps `iUse/@uid` → qualified VI name |
| Connector pane → FP control | `conPane/cons/fpDCO/@uid` references `fPDCO/@uid` |
| sRN → Structure | `srn_to_structure` lookup maps orphan sRN to enclosing structure |

---

## Appendix A: Complete Element Hierarchy
<sup>[back to top](#table-of-contents)</sup>

```
SL__rootObject (class="oHExt")
├─ root (class="diag")                    # Block diagram
│  ├─ zPlaneList                          # Visual ordering
│  │  └─ SL__arrayElement (class="label"|"cosm"|"attach")
│  ├─ nodeList                            # Executable nodes
│  │  └─ SL__arrayElement (class=<node_type>)
│  │     ├─ objFlags
│  │     ├─ termList                      # Node's terminals
│  │     │  └─ SL__arrayElement (class="term"|"fPTerm")
│  │     │     ├─ objFlags
│  │     │     └─ dco (class=<dco_type>)  # Terminal metadata
│  │     │        ├─ objFlags
│  │     │        ├─ termList             # Inner/outer UIDs
│  │     │        ├─ typeDesc             # TypeID(N)
│  │     │        ├─ termBounds           # Pixel position
│  │     │        ├─ parmIndex|paramIdx   # Connector slot
│  │     │        ├─ primIndex            # Primitive sequence
│  │     │        └─ val                  # Constant value (hex)
│  │     ├─ label (class="label")
│  │     │  └─ text                       # Node name
│  │     ├─ bounds                        # Position/size
│  │     ├─ primResID                     # Primitive ID
│  │     ├─ primIndex                     # Primitive sequence
│  │     ├─ operation                     # cpdArith operation
│  │     ├─ method|methodCode             # invokeNode
│  │     ├─ connectorTM                   # SubVI type map
│  │     ├─ tunnelList                    # Structure tunnel UIDs
│  │     ├─ contRect                      # Content rectangle
│  │     ├─ diagramList                   # Nested frames
│  │     │  └─ SL__arrayElement (class="diag")
│  │     │     ├─ nodeList                # Frame's nodes
│  │     │     └─ signalList              # Frame's wires
│  │     └─ sequenceList                  # Flat sequence frames
│  │        └─ SL__arrayElement (class="sequenceFrame")
│  │           ├─ nodeList
│  │           └─ signalList
│  ├─ signalList                          # Wires
│  │  └─ SL__arrayElement (class="signal")
│  │     ├─ objFlags
│  │     ├─ termList                      # [source, dest1, dest2...]
│  │     ├─ state
│  │     ├─ compressedWireTable
│  │     └─ lastSignalKind
│  ├─ bgColor
│  └─ firstNodeIdx
├─ pBounds
├─ dBounds
├─ origin
└─ instrStyle
```

## Appendix B: DCO Class Quick Reference
<sup>[back to top](#table-of-contents)</sup>

| DCO Class | Parent Context | Direction | Inner termList |
|-----------|---------------|-----------|----------------|
| `iUseDCO` | SubVI call | from wiring | — |
| `parm` | Primitive | from wiring | — |
| `fPDCO` | Front panel | control=in, indicator=out | — |
| `caseSel` | Case structure | input (selector) | [inner, pass, outer] |
| `selTun` | Case structure | from `objFlags` | [frame0, frame1, ..., outer] |
| `lSR` | Loop | input | [inner, outer] |
| `rSR` | Loop | output | [inner, outer] |
| `lpTun` | Loop | pass-through | [inner, outer] |
| `lMax` | Loop | output (accumulator) | [inner, outer] |
| `flatSeqTun` | Flat sequence | from wiring | [frame0, frame1, ..., outer] |
| `seqTun` | Stacked sequence | from wiring | [frame0, frame1, ..., outer] |
| `dBD_Const` | Constant | output (source) | — |
| `bDConstDCO` | Constant | output (source) | — |

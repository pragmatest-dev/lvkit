# vipy Graph Structure Reference

A deep technical reference for vipy's internal graph representation — the in-memory dataflow graph built from LabVIEW VI XML files and consumed by the code generator.

## Table of Contents

1. [Pipeline Overview](#1-pipeline-overview)
2. [Graph Container](#2-graph-container)
3. [Node Types](#3-node-types)
4. [Terminals](#4-terminals)
5. [Wires and Edges](#5-wires-and-edges)
6. [Type System (`LVType`)](#6-type-system-lvtype)
7. [Structure Representation](#7-structure-representation)
8. [Graph Construction](#8-graph-construction)
9. [Terminal Index Resolution](#9-terminal-index-resolution)
10. [Type Resolution and Propagation](#10-type-resolution-and-propagation)
11. [SubVI and Dependency Resolution](#11-subvi-and-dependency-resolution)
12. [Code Generation Layer](#12-code-generation-layer)
13. [Topological Sort and Parallel Tiers](#13-topological-sort-and-parallel-tiers)
14. [Variable Binding and Resolution](#14-variable-binding-and-resolution)

---

## 1. Pipeline Overview
<sup>[back to top](#table-of-contents)</sup>

The conversion pipeline transforms LabVIEW VI files into a unified dataflow graph, then generates Python from that graph:

```
.vi file
  │
  ▼
extract_vi_xml()                      # Binary RSRC → XML files
  │
  ▼
parse_vi()                            # XML → ParsedVI (parser layer)
  │  ├─ BlockDiagram  (nodes, wires, constants, structures)
  │  ├─ FrontPanel    (controls, indicators)
  │  ├─ ConnectorPane (slot → FP terminal mapping)
  │  └─ VIMetadata    (qualified names, type_map, SubVI refs)
  │
  ▼
_load_vi_recursive()                  # Resolve SubVIs, load hierarchy
  │
  ▼
_add_vi_to_graph()                    # ParsedVI → nx.MultiDiGraph nodes & edges
  │
  ▼
InMemoryVIGraph                       # Unified graph with typed nodes & edges
  │
  ▼
_build_vi_context()                   # Graph → VIContext (codegen input)
  │
  ▼
build_module()                        # VIContext + Graph → Python AST → source
```

### Two-Layer Architecture

| Layer | Types | Storage | Purpose |
|-------|-------|---------|---------|
| **Graph layer** (Pydantic) | `GraphNode`, `Terminal`, `Wire`, `WireEnd` | `nx.MultiDiGraph` nodes and edges | Unified dataflow representation |
| **Codegen layer** (dataclasses) | `Operation`, `Constant`, `VIContext` | Passed to code generator | Code generation input |

The graph layer is the source of truth. The codegen layer is built from it.

### Key Files

| File | Purpose |
|------|---------|
| `src/vipy/graph_types.py` | All data structure definitions (both layers) |
| `src/vipy/graph/core.py` | `InMemoryVIGraph` — graph container and queries |
| `src/vipy/graph/construction.py` | XML→graph node and edge building |
| `src/vipy/graph/loading.py` | VI/library recursive loading |
| `src/vipy/graph/queries.py` | Graph analysis queries |
| `src/vipy/parser/vi.py` | XML→ParsedVI entry point |
| `src/vipy/parser/models.py` | Parser-layer data structures |
| `src/vipy/parser/type_mapping.py` | TypeID→LVType resolution |
| `src/vipy/agent/codegen/builder.py` | Code generation orchestration |
| `src/vipy/agent/codegen/context.py` | `CodeGenContext` — graph query wrapper |

---

## 2. Graph Container
<sup>[back to top](#table-of-contents)</sup>

`InMemoryVIGraph` holds the unified dataflow graph and all supporting indices.

### Core State

```python
class InMemoryVIGraph:
    _graph: nx.MultiDiGraph            # Unified dataflow graph
    _vi_nodes: dict[str, set[str]]     # Per-VI node index: vi_name → {node_ids}
    _term_to_node: dict[str, str]      # Terminal ownership: terminal_id → node_id
    _dep_graph: nx.DiGraph             # Dependency graph: VI → SubVIs/types
    _stubs: set[str]                   # Missing dependencies (unresolved VIs)
    _poly_info: dict[str, PolyInfo]    # Polymorphic VI metadata
    _qualified_aliases: dict[str, str] # Name resolution aliases
    _loaded_vis: set[str]              # Prevent re-parsing
    _source_paths: dict[str, Path]     # VI file locations
    _vi_metadata: dict[str, VIMetadata]  # Library membership, qualified names
```

### Index Purposes

| Index | Type | Lookup | Purpose |
|-------|------|--------|---------|
| `_graph` | `nx.MultiDiGraph` | Node/edge traversal | Full dataflow graph with typed nodes and wire edges |
| `_vi_nodes` | `dict[str, set[str]]` | VI name → node IDs | Scope queries: "which nodes belong to this VI?" |
| `_term_to_node` | `dict[str, str]` | Terminal ID → node ID | O(1) parent lookup for any terminal |
| `_dep_graph` | `nx.DiGraph` | VI → SubVIs | Dependency ordering, class/typedef hierarchy |
| `_stubs` | `set[str]` | Membership | Track unresolved dependencies |

### UID Qualification

Parser UIDs are integers unique within a single VI. The graph qualifies them to prevent collisions:

```python
@staticmethod
def _qid(vi_name: str, uid: str) -> str:
    return f"{vi_name}::{uid}"

# Example:
# Parser UID: "42"
# Qualified: "TestCase.lvclass:failUnlessEqual.vi::42"
```

All graph node IDs, terminal IDs, and wire endpoint IDs use qualified format.

### Key Query Methods

| Method | Returns | Purpose |
|--------|---------|---------|
| `incoming_edges(terminal_id)` | `list[WireEnd]` | All sources feeding into a terminal |
| `outgoing_edges(terminal_id)` | `list[WireEnd]` | All destinations a terminal feeds |
| `terminal_is_wired(terminal_id)` | `bool` | Whether a terminal has any connection |
| `set_var_name(terminal_id, name)` | `None` | Bind a Python variable name to a terminal |
| `get_var_name(terminal_id)` | `str | None` | Read bound Python variable name |
| `get_class_fields(classname)` | `list[ClusterField] | None` | Class private data fields |
| `get_type_fields(lv_type)` | `list[ClusterField] | None` | Cluster/typedef field list |

### Edge Storage

Edges are stored on the `nx.MultiDiGraph` with `source` and `dest` attributes:

```python
# Adding an edge:
self._graph.add_edge(
    src_node_id,
    dest_node_id,
    source=WireEnd(terminal_id=src_term, node_id=src_node_id, ...),
    dest=WireEnd(terminal_id=dest_term, node_id=dest_node_id, ...),
)
```

Edge queries filter by terminal ID to find specific connections:

```python
def incoming_edges(self, terminal_id):
    node_id = self._term_to_node[terminal_id]  # O(1) lookup
    results = []
    for _, _, _, d in self._graph.in_edges(node_id, data=True, keys=True):
        if d["dest"].terminal_id == terminal_id:
            results.append(d["source"])
    return results
```

---

## 3. Node Types
<sup>[back to top](#table-of-contents)</sup>

All graph nodes inherit from `GraphNode` (Pydantic BaseModel). Each node carries its own terminals.

### 3.1 Node Hierarchy

```
GraphNode (base)
├── VINode          — VI definition or SubVI call
├── PrimitiveNode   — Built-in operation (Add, Compare, etc.)
├── StructureNode   — Loop, case structure, or sequence
└── ConstantNode    — Literal value on the diagram
```

The discriminated union type:
```python
AnyGraphNode = VINode | PrimitiveNode | StructureNode | ConstantNode
```

### 3.2 GraphNode (base)

Every node in the graph has these fields:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Qualified UID (`"VI::uid"`) |
| `vi` | `str` | Name of the VI this node belongs to |
| `name` | `str | None` | Human-readable name |
| `node_type` | `str | None` | XML class (`"iUse"`, `"prim"`, `"select"`, etc.) |
| `terminals` | `list[Terminal]` | All connection points on this node |
| `description` | `str | None` | Node description (from vilib data) |
| `parent` | `str | None` | Qualified UID of containing structure (if nested) |
| `frame` | `str | int | None` | Frame selector value (if inside a case/sequence) |

### 3.3 VINode

Represents a VI — either the top-level VI definition or a SubVI call site.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["vi"]` | Discriminator |
| `library` | `str | None` | Library name (e.g., `"VITester.lvlib"`) |
| `qualified_name` | `str | None` | Full path (e.g., `"Lib.lvlib:Class.lvclass:Method.vi"`) |
| `poly_variant_name` | `str | None` | Resolved polymorphic variant name |

When a VINode represents the top-level VI, its terminals are `FPTerminal` instances (connector pane slots). When it represents a SubVI call, terminals map to the callee's connector pane.

### 3.4 PrimitiveNode

Represents a built-in LabVIEW operation.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["primitive"]` | Discriminator |
| `prim_id` | `int | None` | Primitive resource ID (e.g., 1051=Subtract) |
| `prim_index` | `int | None` | Sequence number (not semantically meaningful) |
| `operation` | `str | None` | For `cpdArith`: `"add"`, `"multiply"`, `"and"`, `"or"`, `"xor"` |
| `object_name` | `str | None` | Property/invoke node object name |
| `object_method_id` | `str | None` | Method ID string |
| `properties` | `list[PropertyDef]` | Property definitions (for `propNode`) |
| `method_name` | `str | None` | Method name (for `invokeNode`) |
| `method_code` | `int | None` | Method code (for `invokeNode`) |

Covers all XML node types that aren't SubVIs, structures, or constants: `prim`, `cpdArith`, `aBuild`, `aIndx`, `aDelete`, `concat`, `subset`, `split`, `printf`, `propNode`, `invokeNode`, `nMux`.

### 3.5 StructureNode

Represents a loop, case structure, or sequence — any node with nested diagrams.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["structure"]` | Discriminator |
| `loop_type` | `str | None` | `"whileLoop"` or `"forLoop"` (None for cases/sequences) |
| `stop_condition_terminal` | `str | None` | While loop stop condition terminal UID |
| `frames` | `list[FrameInfo]` | Frame metadata (case values, default flag) |
| `selector_terminal` | `str | None` | Case structure selector terminal UID |

Terminals on a StructureNode are `TunnelTerminal` instances — outer and inner endpoints of data tunnels across the structure boundary.

Inner nodes are NOT children of the StructureNode in the graph. Instead, they have `parent = structure_uid` and `frame = selector_value` attributes on their own `GraphNode`.

### 3.6 ConstantNode

Represents a literal value on the block diagram.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["constant"]` | Discriminator |
| `value` | `ScalarValue` | Decoded Python value (`str | int | float | bool | None`) |
| `lv_type` | `LVType | None` | Type information |
| `raw_value` | `str | None` | Hex-encoded value from XML |
| `label` | `str | None` | Constant label text |

Has a single output terminal at index 0.

---

## 4. Terminals
<sup>[back to top](#table-of-contents)</sup>

Terminals are connection points on nodes. Every wire connects a source terminal to a destination terminal. Terminals carry type information and, during code generation, Python variable name bindings.

### 4.1 Terminal Hierarchy

```
Terminal (base)
├── FPTerminal      — Connector pane slot on a VINode
└── TunnelTerminal  — Structure boundary tunnel endpoint
```

### 4.2 Terminal (base)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Qualified terminal UID |
| `index` | `int` | Connector pane slot index (`-1` if unresolved) |
| `direction` | `str` | `"input"` or `"output"` |
| `name` | `str | None` | Terminal name (from primitive/vilib definition) |
| `lv_type` | `LVType | None` | LabVIEW type |
| `var_name` | `str | None` | Python variable name (set during codegen) |
| `nmux_role` | `str | None` | `"agg"` (aggregate) or `"list"` (field) for nMux terminals |
| `nmux_field_index` | `int | None` | Class field index for nMux terminals |
| `wiring_rule` | `int` | `0`=unknown, `1`=required, `2`=recommended, `3`=optional |
| `default_value` | `ScalarValue` | Default value for optional unwired inputs |

Key methods:

| Method | Returns | Description |
|--------|---------|-------------|
| `python_type()` | `str` | Python type string from `lv_type` (e.g., `"int"`, `"list[str]"`) |
| `is_error_cluster` | `bool` | Whether this terminal carries a LabVIEW error cluster |

### 4.3 FPTerminal

Connector pane terminal on a VINode (top-level VI or SubVI call).

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["fp"]` | Discriminator |
| `is_indicator` | `bool` | `True` = output (indicator), `False` = input (control) |
| `is_public` | `bool` | Whether terminal is externally visible |
| `control_type` | `str | None` | FP control class (e.g., `"stdNum"`, `"stdString"`, `"stdClust"`) |
| `enum_values` | `list[str]` | Enum value labels (for enum controls) |

### 4.4 TunnelTerminal

Endpoint of a data tunnel on a StructureNode boundary.

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `Literal["tunnel"]` | Discriminator |
| `tunnel_type` | `str` | DCO class: `"lSR"`, `"rSR"`, `"lpTun"`, `"lMax"`, `"caseSel"`, `"selTun"`, `"flatSeqTun"` |
| `boundary` | `str` | `"outer"` (outside structure) or `"inner"` (inside structure) |
| `paired_id` | `str | None` | Qualified UID of matching terminal on other side of boundary |

Each physical tunnel produces **two** TunnelTerminals — an outer and an inner — connected by an internal graph edge. Shift register pairs (`lSR`/`rSR`) cross-reference via `paired_id`.

### 4.5 Terminal Index Semantics

Terminal indices are **sparse** connector pane slot positions, not sequential list indices. A node might have terminals at indices 0, 1, 3, 8, 11 — with gaps.

- `-1` means the index couldn't be resolved from the XML (missing `parmIndex`/`paramIdx`)
- Resolution is attempted during graph construction (see [Section 9](#9-terminal-index-resolution))
- During codegen, terminals are matched by index to primitive/vilib definitions

---

## 5. Wires and Edges
<sup>[back to top](#table-of-contents)</sup>

Wires are the edges of the dataflow graph. Each wire connects one source terminal to one destination terminal.

### 5.1 WireEnd

One endpoint of a wire. Immutable (frozen).

| Field | Type | Description |
|-------|------|-------------|
| `terminal_id` | `str` | Qualified terminal UID |
| `node_id` | `str` | Qualified parent node UID |
| `index` | `int | None` | Terminal's connector pane slot index |
| `name` | `str | None` | Terminal or node name |
| `labels` | `list[str]` | Node labels (for identification) |

### 5.2 Wire

A single dataflow connection. Immutable (frozen).

| Field | Type | Description |
|-------|------|-------------|
| `source` | `WireEnd` | Source terminal endpoint |
| `dest` | `WireEnd` | Destination terminal endpoint |

Backward-compatible properties for flat access: `from_terminal_id`, `to_terminal_id`, `from_parent_id`, `to_parent_id`, `from_parent_name`, `to_parent_name`, `from_slot_index`, `to_slot_index`.

### 5.3 Fan-Out

LabVIEW fan-out (one output wired to multiple inputs) is represented as **multiple Wire objects** sharing the same source terminal but with different destination terminals. The XML `signal` element's multi-element `termList` is split into individual wires during parsing.

### 5.4 Internal Structure Edges

Tunnel pairs generate internal edges within the graph:

```
outer TunnelTerminal ──edge──▸ inner TunnelTerminal
```

These edges allow `incoming_edges()` / `outgoing_edges()` to trace data flow through structure boundaries without special-casing tunnels.

---

## 6. Type System (`LVType`)
<sup>[back to top](#table-of-contents)</sup>

`LVType` is a unified dataclass representing all LabVIEW types. Every terminal can carry an `LVType`.

### 6.1 LVType Fields

| Field | Type | Description |
|-------|------|-------------|
| `kind` | `str` | `"primitive"`, `"enum"`, `"cluster"`, `"array"`, `"ring"`, `"typedef_ref"` |
| `underlying_type` | `str | None` | Base type name: `"NumInt32"`, `"String"`, `"Boolean"`, `"Refnum"`, etc. |
| `ref_type` | `str | None` | Reference subtype: `"UDClassInst"`, `"Queue"`, etc. |
| `classname` | `str | None` | Class name for Refnum types |
| `values` | `dict[str, EnumValue] | None` | Enum label → value mapping |
| `fields` | `list[ClusterField] | None` | Cluster/typedef field definitions |
| `element_type` | `LVType | None` | Array element type (recursive) |
| `dimensions` | `int | None` | Array dimensionality |
| `typedef_path` | `str | None` | Path to `.ctl` typedef file |
| `typedef_name` | `str | None` | Qualified typedef name |
| `description` | `str | None` | Human-readable description |

### 6.2 Type Kinds

| Kind | Example `underlying_type` | Additional Fields Used |
|------|--------------------------|----------------------|
| `"primitive"` | `"NumInt32"`, `"String"`, `"Boolean"`, `"Path"` | — |
| `"enum"` | `"NumUInt16"` | `values`, `typedef_name` |
| `"cluster"` | — | `fields` (list of `ClusterField`) |
| `"array"` | — | `element_type`, `dimensions` |
| `"ring"` | `"NumUInt16"` | `values` |
| `"typedef_ref"` | — | `typedef_path`, `typedef_name` |

### 6.3 Python Type Mapping

`LVType.to_python()` renders the LabVIEW type as a Python type annotation:

| LabVIEW Type | Python Type |
|--------------|-------------|
| `NumInt8` / `NumInt16` / `NumInt32` / `NumInt64` | `int` |
| `NumUInt8` / `NumUInt16` / `NumUInt32` / `NumUInt64` | `int` |
| `NumFloat32` / `NumFloat64` | `float` |
| `NumComplex64` / `NumComplex128` | `complex` |
| `String` | `str` |
| `Boolean` | `bool` |
| `Path` | `Path` |
| `Variant` / `LVVariant` | `Any` |
| `Refnum` | `Any` |
| Array of T | `list[T]` |
| Cluster | dataclass name or `tuple` |
| Enum | enum class name |
| `Void` | `None` |

### 6.4 Supporting Types

**ClusterField:**

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | Field name |
| `type` | `LVType | None` | Field type (recursive) |

**EnumValue:**

| Field | Type | Description |
|-------|------|-------------|
| `value` | `int` | Numeric value |
| `description` | `str | None` | Optional description |

### 6.5 Error Cluster Detection

A type is an error cluster if:
1. `typedef_name` contains `"error"` (case-insensitive), **OR**
2. Kind is `"cluster"` with exactly 3 fields matching: `Boolean` + `NumInt32` + `String`

Detected by `_is_error_cluster(lv_type)` and exposed as `Terminal.is_error_cluster`.

---

## 7. Structure Representation
<sup>[back to top](#table-of-contents)</sup>

Loops, case structures, and sequences are represented as `StructureNode` objects with tunnel terminals. Their inner nodes are separate graph nodes with `parent`/`frame` attributes.

### 7.1 Containment Model

```
StructureNode (id="VI::139")
  ├── TunnelTerminal (outer, boundary="outer")
  ├── TunnelTerminal (inner, boundary="inner")
  └── [internal edge: outer → inner]

GraphNode (id="VI::200", parent="VI::139", frame="True")   # Inner node, frame 0
GraphNode (id="VI::201", parent="VI::139", frame="True")   # Inner node, frame 0
GraphNode (id="VI::300", parent="VI::139", frame="False")  # Inner node, frame 1
```

There are **no parent→child edges** in the graph. Containment is encoded as attributes on inner nodes and reconstructed by filtering:

```python
frame_nodes = [n for n in all_nodes if n.parent == structure_id and n.frame == selector_value]
```

### 7.2 Tunnel Pairs

Each physical tunnel creates two `TunnelTerminal` objects connected by an internal edge:

```
[outside wire] → outer TunnelTerminal ──edge──▸ inner TunnelTerminal → [inside wire]
```

For case structures with N frames, a tunnel has N inner terminals (one per frame) plus 1 outer terminal. The `paired_id` on each points to its counterpart.

### 7.3 Tunnel Types

| Tunnel Type | Structure | Direction | Semantics |
|-------------|-----------|-----------|-----------|
| `lSR` | Loop | Input | Left shift register — carries initial value into loop |
| `rSR` | Loop | Output | Right shift register — carries updated value out of loop |
| `lpTun` | Loop | Pass-through | Data passes through unchanged |
| `lMax` | Loop | Output | Accumulator / loop count (N terminal) |
| `caseSel` | Case | Input | Selector value distributor |
| `selTun` | Case | From wiring | Data tunnel across case boundary |
| `flatSeqTun` | Flat Sequence | From wiring | Tunnel between sequence frames |
| `seqTun` | Stacked Sequence | From wiring | Tunnel between sequence frames |

### 7.4 Shift Register Pairing

Left (`lSR`) and right (`rSR`) shift registers are paired. The left provides the initial value; the right carries each iteration's result back. Their `paired_terminal_uid` fields cross-reference each other.

### 7.5 FrameInfo

Metadata for each frame in a case structure or sequence:

| Field | Type | Description |
|-------|------|-------------|
| `selector_value` | `str | int` | Case condition value (e.g., `"True"`, `"Default"`, `0`) |
| `is_default` | `bool` | Whether this is the default case frame |

### 7.6 Tunnel Dataclass

The `Tunnel` dataclass (used during construction) captures the raw tunnel mapping:

| Field | Type | Description |
|-------|------|-------------|
| `outer_terminal_uid` | `str` | Outer terminal UID |
| `inner_terminal_uid` | `str` | Inner terminal UID |
| `tunnel_type` | `str` | DCO class name |
| `paired_terminal_uid` | `str | None` | Matching terminal on other boundary side |

---

## 8. Graph Construction
<sup>[back to top](#table-of-contents)</sup>

Graph construction transforms `ParsedVI` objects into typed graph nodes and wire edges on the `nx.MultiDiGraph`.

### 8.1 Construction Pipeline

`_add_vi_to_graph()` executes these steps in order:

```
1. Build VINode with FPTerminals     ← Connector pane slots
2. Add ConstantNodes                  ← Diagram constants
3. Add operation nodes                ← SubVIs, primitives, structures
   ├── Resolve terminal indices       ← Match -1 indices by type/direction
   ├── Dispatch by node type:
   │   ├── iUse/polyIUse/dynIUse → VINode
   │   ├── whileLoop/forLoop     → StructureNode + tunnel terminals
   │   ├── select/caseStruct     → StructureNode + frame metadata
   │   ├── flatSequence/seq      → StructureNode + frame metadata
   │   └── everything else       → PrimitiveNode
   └── Register terminals in _term_to_node
4. Set parent/frame on inner nodes    ← Walk structure contents
5. Add wire edges                     ← Source→dest terminal connections
6. Connect SubVI calls to FP terms    ← Match by terminal index
7. Propagate types & re-match         ← Fill gaps from wire neighbors
```

### 8.2 Parser-Layer Input

The parser produces these structures that feed graph construction:

**BlockDiagram:**

| Field | Type | Description |
|-------|------|-------------|
| `nodes` | `list[Node]` | SubVIs, primitives |
| `constants` | `list[Constant]` | Constant values |
| `wires` | `list[Wire]` | Dataflow connections |
| `fp_terminals` | `list[FPTerminal]` | Connector pane terminals |
| `terminal_info` | `dict[str, TerminalInfo]` | All terminal metadata |
| `loops` | `list[LoopStructure]` | For/while loops with tunnels |
| `case_structures` | `list[CaseStructure]` | Case structures with frames |
| `flat_sequences` | `list[SequenceFrame]` | Sequences with frames |
| `srn_to_structure` | `dict[str, str]` | sRN terminal → containing structure |

**TerminalInfo** (per terminal from XML):

| Field | Type | Description |
|-------|------|-------------|
| `uid` | `str` | XML uid |
| `parent_uid` | `str` | Owning node's uid |
| `index` | `int` | Connector pane slot (`-1` if unknown) |
| `is_output` | `bool` | Direction |
| `parsed_type` | `ParsedType | None` | Type from XML TypeID |
| `name` | `str | None` | Terminal name |

### 8.3 Structure Terminal Building

`_build_structure_terminals()` converts parser tunnels to graph terminals:

1. For each parser tunnel:
   - Look up `TerminalInfo` for outer and inner UIDs
   - Determine direction from `is_output`
   - Create outer `TunnelTerminal` (boundary=`"outer"`, paired_id=inner_uid)
   - Create inner `TunnelTerminal` (boundary=`"inner"`, paired_id=outer_uid)
   - Register both in `_term_to_node`
   - Add internal edge: outer → inner (or inner → outer for output tunnels)

2. For sRN nodes (shift register containers):
   - sRN terminals that aren't in any tunnel list belong to the enclosing structure
   - Matched via `bd.srn_to_structure` lookup

---

## 9. Terminal Index Resolution
<sup>[back to top](#table-of-contents)</sup>

Terminal indices map terminals to connector pane slots. The XML sometimes omits them (`parmIndex` missing → index = -1). The graph construction resolves these by type and direction matching.

### 9.1 The -1 Problem

Sources of missing indices:
- Primitives where the XML lacks `parmIndex`/`paramIdx` elements
- sRN terminals using list position instead of explicit index
- Expandable terminals (e.g., `printf` with variable inputs)

### 9.2 Resolution Algorithm

`_resolve_terminal_indices()`:

```
For each unresolved terminal (index == -1):
  1. Extract type category from lv_type
  2. Find known terminals (from primitive/vilib definition) with:
     - Same direction (input/output)
     - Same type category OR "polymorphic"
     - Not already assigned to another terminal
  3. If exactly ONE match → assign that index
  4. If multiple matches AND one is expandable → assign expandable slot
  5. Otherwise → leave as -1
```

### 9.3 Type Categories

| Category | LabVIEW Types |
|----------|---------------|
| numeric | `NumInt*`, `NumUInt*`, `NumFloat*`, `NumComplex*`, Measurement types |
| string | `String`, `SubString` |
| boolean | `Boolean` |
| path | `Path` |
| array | `Array`, `SubArray` |
| cluster | `Cluster` |
| enum | `Enum`, `Ring` (mapped to numeric) |
| refnum | `Refnum` (checked in `underlying_type`) |
| variant | `Variant` |

### 9.4 Post-Propagation Re-Matching

After all wires are added, types propagate through edges (if one endpoint has a type, the other gets it). Then a second round of index resolution runs on nodes that still have -1 indices, using the newly available type information.

---

## 10. Type Resolution and Propagation
<sup>[back to top](#table-of-contents)</sup>

### 10.1 TypeID Resolution Chain

```
TypeID(N) in BDHb XML
    ↓
parse_type_map_rich(main_xml)
    ↓
Heap type table (comments in XML map HeapTypeID → ConsolidatedID)
    ↓
Consolidated → FlatTypeID (from VCTP TopLevel)
    ↓
VCTP Section TypeDesc → LVType with fields, enum values, etc.
```

`parse_type_map_rich()` returns `dict[int, LVType]` — the full type map for a VI.

### 10.2 Type Enrichment

`_enrich_type()` upgrades parser-layer `ParsedType` to graph-layer `LVType`:

- Converts `ParsedType` fields to `LVType` fields
- Adds enum values from vilib resolver (if available)
- Preserves anonymous cluster field structures

### 10.3 Type Propagation Through Wires

After all edges are added, `_propagate_types_and_rematch()`:

1. For each edge, if source terminal has `lv_type` but dest doesn't → copy to dest
2. For each edge, if dest terminal has `lv_type` but source doesn't → copy to source
3. Re-run terminal index resolution on nodes with remaining -1 indices

---

## 11. SubVI and Dependency Resolution
<sup>[back to top](#table-of-contents)</sup>

### 11.1 Recursive Loading

`_load_vi_recursive()` loads VIs in dependency order:

```
1. parse_vi(vi_path) → ParsedVI
2. Check _loaded_vis to prevent circular loading
3. Extract SubVI references from metadata
4. For each SubVI reference:
   a. _find_subvi() → locate .vi file on disk
   b. Extract XML
   c. Recurse (load SubVI first)
   d. Add edge to _dep_graph
5. Load type dependencies (classes, typedefs)
6. _add_vi_to_graph() → build graph nodes & edges
```

### 11.2 SubVI File Resolution

`_find_subvi()` search order:
1. Caller's directory (if not vilib/userlib)
2. Parent directories up to 3 levels
3. Each search path (recursively)
4. First match wins

### 11.3 SubVI Call Connection

After both caller and callee are in the graph, `_connect_subvi_calls()` creates edges from SubVI call terminals to callee FP terminals:

```
SubVI call node terminal (index=N, direction=input)
    ──edge──▸
Callee VINode FPTerminal (index=N, direction=input)
```

Matching is by terminal index. For unresolved indices (-1), elimination matching is attempted.

### 11.4 Dependency Graph

The `_dep_graph` (separate `nx.DiGraph`) tracks non-dataflow relationships:

| Node Type | Examples |
|-----------|----------|
| `"vi"` | Individual VI names |
| `"library"` | `.lvlib` names |
| `"class"` | `.lvclass` names with `fields`, `parent_class` attributes |
| `"typedef"` | `.ctl` typedef names with `fields` attribute |

Edges represent ownership (library→VI) and calls (VI→SubVI).

---

## 12. Code Generation Layer
<sup>[back to top](#table-of-contents)</sup>

The codegen layer converts graph nodes into `Operation` dataclasses and wraps them in a `VIContext` for the code generator.

### 12.1 VIContext

Complete input for code generation:

| Field | Type | Description |
|-------|------|-------------|
| `name` | `str` | VI name |
| `library` | `str | None` | Library name |
| `qualified_name` | `str | None` | Fully qualified name |
| `inputs` | `list[Terminal]` | Input FPTerminals |
| `outputs` | `list[Terminal]` | Output FPTerminals |
| `constants` | `list[Constant]` | Constants used in VI |
| `operations` | `list[Operation]` | All operations to generate |
| `has_parallel_branches` | `bool` | Whether VI needs parallel execution |

### 12.2 Operation

An operation node for code generation, built from `GraphNode` subclasses:

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Qualified operation ID |
| `name` | `str | None` | Operation name |
| `labels` | `list[str]` | Operation labels |
| `primResID` | `int | None` | Primitive resource ID |
| `terminals` | `list[Terminal]` | Connected terminals |
| `node_type` | `str | None` | Node type string |
| `loop_type` | `str | None` | Loop type (for loops) |
| `tunnels` | `list[Tunnel]` | Tunnels (for structures) |
| `inner_nodes` | `list[Operation]` | Nested operations |
| `stop_condition_terminal` | `str | None` | While loop stop terminal |
| `operation` | `str | None` | cpdArith operation code |
| `object_name` | `str | None` | Property/invoke object name |
| `properties` | `list[PropertyDef]` | Property definitions |
| `method_name` | `str | None` | Invoke method name |
| `method_code` | `int | None` | Invoke method code |
| `case_frames` | `list[CaseFrame]` | Case structure frames |
| `selector_terminal` | `str | None` | Case selector terminal |
| `poly_variant_name` | `str | None` | Polymorphic variant |

### 12.3 CaseFrame

A frame in a case structure or sequence:

| Field | Type | Description |
|-------|------|-------------|
| `selector_value` | `str | int` | Frame selector value |
| `inner_node_uids` | `list[str]` | UIDs of nodes in this frame |
| `operations` | `list[Operation]` | Operations in this frame |
| `is_default` | `bool` | Whether this is the default frame |

### 12.4 Constant (codegen layer)

| Field | Type | Description |
|-------|------|-------------|
| `id` | `str` | Constant ID |
| `value` | `ScalarValue` | Decoded Python value |
| `lv_type` | `LVType | None` | Type information |
| `raw_value` | `str | None` | Raw string value |
| `name` | `str | None` | Constant name |

### 12.5 CodeGenContext

Wraps the graph for code generation, providing variable binding and wire resolution:

| Method | Purpose |
|--------|---------|
| `resolve(terminal_id)` | BFS through incoming edges to find bound variable |
| `bind(terminal_id, var_name)` | Set Python variable name on a terminal |
| `get_source(terminal_id)` | Get first incoming `SourceInfo` |
| `get_destinations(terminal_id)` | Get all outgoing `DestinationInfo` |
| `is_wired(terminal_id)` | Check if terminal has any edge |
| `has_incoming(terminal_id)` | Check if terminal has incoming edge |
| `make_output_var(base, node_id, terminal_id)` | Generate unique Python variable name |
| `generate_body(operations)` | Recursively generate code for a list of operations |
| `merge(bindings)` | Apply terminal→variable bindings to graph |

---

## 13. Topological Sort and Parallel Tiers
<sup>[back to top](#table-of-contents)</sup>

Operations within a frame or top-level VI are sorted into **tiers** by data dependencies. Each tier contains operations that can execute in parallel.

### 13.1 Algorithm: Tiered Kahn's Sort

```
1. Build dependency map:
   For each operation's input terminals:
     Trace incoming wire through graph edges
     Follow through infrastructure nodes (tunnels, shift registers)
     If source is another operation → add dependency

2. Initialize ready queue with operations that have zero dependencies

3. Drain ready operations per iteration:
   tier = all currently ready operations
   tiers.append(tier)
   Remove completed from remaining dependencies
   Newly zero-dependency operations → ready queue

4. Return list of tiers (each tier is a list of operations)
```

### 13.2 Wire Tracing Through Infrastructure

The topological sort traces wires through tunnel and shift register nodes:

```python
# Trace from input terminal to producing operation
visited, current = set(), input_terminal_id
while current not in visited:
    visited.add(current)
    source = ctx.get_source(current)  # Follow wire backward
    if source is None:
        break  # Unwired terminal or constant

    src_term = source.src_terminal
    if src_term in output_to_op:
        # Found producing operation → add dependency
        dependencies[this_op].add(output_to_op[src_term])
        break

    # Source is infrastructure node (tunnel, sRN) → keep tracing
    current = src_term
```

### 13.3 Execution Model

| Tier Size | Execution |
|-----------|-----------|
| 1 operation | Sequential — inline statements |
| N operations | Parallel — `ThreadPoolExecutor` with branch functions |

### 13.4 Parallel Tier Code Pattern

```python
# Multi-operation tier → parallel execution
with concurrent.futures.ThreadPoolExecutor(max_workers=N) as executor:
    def _branch_0():
        result_0 = operation_0(...)
        return result_0

    def _branch_1():
        result_1 = operation_1(...)
        return result_1

    future_0 = executor.submit(_branch_0)
    future_1 = executor.submit(_branch_1)

    result_0 = future_0.result()
    result_1 = future_1.result()
```

### 13.5 Binding Propagation

Even if all operations in a tier produce zero statements (all passthroughs/inlines), their **bindings must still be merged**. This ensures downstream tiers can resolve variables through the graph.

---

## 14. Variable Binding and Resolution
<sup>[back to top](#table-of-contents)</sup>

During code generation, Python variable names are bound to graph terminals. Resolution traces wires backward through the graph to find the bound name.

### 14.1 Binding

```python
ctx.bind(terminal_id, "my_var")
# Calls graph.set_var_name(terminal_id, "my_var")
# Mutates the Terminal.var_name field on the graph node
```

### 14.2 Resolution (BFS)

```python
var = ctx.resolve(terminal_id)
```

Resolution performs a BFS from the target terminal backward through incoming edges:

```
target_terminal
  │
  ▼ check var_name → found? return it
  │
  ▼ walk incoming_edges()
  │
source_terminal
  │
  ▼ check var_name → found? return it
  │
  ▼ walk incoming_edges() (through tunnels, etc.)
  │
  ... continue until var_name found or no more edges
```

This traversal naturally crosses structure boundaries through tunnel internal edges, following the dataflow path.

### 14.3 Variable Name Generation

`ctx.make_output_var()` generates unique Python variable names:

1. **Check tunnel destination**: If the terminal wires to a structure boundary tunnel, derive the name from the outer tunnel's downstream consumer
2. **Use base name**: Convert terminal name to valid Python identifier
3. **Handle collisions**: Append node UID suffix if name already allocated

This ensures consistent naming across case frames — all frames use the same output variable name because it's derived from the shared outer tunnel.

### 14.4 Resolution Data Structures

**SourceInfo** (returned by `ctx.get_source()`):

| Field | Type | Description |
|-------|------|-------------|
| `src_terminal` | `str` | Source terminal ID |
| `src_parent_id` | `str` | Source node ID |
| `src_parent_name` | `str | None` | Source node name |
| `src_parent_labels` | `list[str]` | Source node labels |
| `src_slot_index` | `int | None` | Source terminal slot index |

**DestinationInfo** (returned by `ctx.get_destinations()`):

| Field | Type | Description |
|-------|------|-------------|
| `dest_terminal` | `str` | Destination terminal ID |
| `dest_parent_id` | `str` | Destination node ID |
| `dest_parent_name` | `str | None` | Destination node name |
| `dest_parent_labels` | `list[str]` | Destination node labels |
| `dest_slot_index` | `int | None` | Destination terminal slot index |

---

## Appendix A: Graph Node Storage on NetworkX
<sup>[back to top](#table-of-contents)</sup>

Nodes and edges are stored on `nx.MultiDiGraph` with dictionary attributes:

```python
# Node storage:
graph.nodes[node_id] = {
    "node": AnyGraphNode,   # VINode | PrimitiveNode | StructureNode | ConstantNode
}

# Edge storage:
graph.edges[src_node, dest_node, key] = {
    "source": WireEnd(...),
    "dest": WireEnd(...),
}
```

The `_term_to_node` index provides O(1) lookup from any terminal to its parent node, avoiding linear scans.

## Appendix B: Codegen Node Dispatch
<sup>[back to top](#table-of-contents)</sup>

Each node type dispatches to a specialized code generator:

| Node Type | Codegen File | Handler |
|-----------|-------------|---------|
| SubVI call (`iUse`, `polyIUse`, `dynIUse`) | `nodes/subvi.py` | `SubVICodeGen` |
| Primitive (`prim`, `cpdArith`, etc.) | `nodes/primitive.py` | `PrimitiveCodeGen` |
| Case structure | `nodes/case.py` | `CaseCodeGen` |
| Loop (`whileLoop`, `forLoop`) | `nodes/loop.py` | `LoopCodeGen` |
| Flat sequence | `nodes/sequence.py` | `SequenceCodeGen` |
| Property node | `nodes/property.py` | `PropertyCodeGen` |
| Invoke node | `nodes/invoke.py` | `InvokeCodeGen` |

Each handler receives an `Operation` and `CodeGenContext`, queries the graph through the context, and returns a `CodeFragment` containing AST statements and terminal bindings.

## Appendix C: Graph Query Patterns
<sup>[back to top](#table-of-contents)</sup>

Common graph access patterns used throughout code generation:

| Pattern | Code | Purpose |
|---------|------|---------|
| Terminal → Variable | `ctx.resolve(term.id)` | Find Python var bound to a terminal |
| Terminal → Wired? | `ctx.is_wired(term.id)` | Skip unwired optional terminals |
| Terminal → Source | `ctx.get_source(term.id)` | Find upstream operation |
| Terminal → Consumers | `ctx.get_destinations(term.id)` | Find downstream operations |
| Terminal → Type | `term.lv_type` | Get LabVIEW type for codegen decisions |
| Operation → Dependencies | Topological sort wire tracing | Build execution tiers |
| Structure → Inner nodes | Filter by `parent`/`frame` | Find nodes inside a structure frame |
| Tunnel → Paired terminal | `tunnel_term.paired_id` | Cross structure boundary |

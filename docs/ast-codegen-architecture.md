# AST-Based Code Generation Architecture

This document describes the pipeline for converting LabVIEW VIs to Python using AST-based deterministic code generation.

## Pipeline Overview

```
VI File → pylabview → XML → Parser → Memory Graph → CodeGen Context → Python AST → Source Code
```

## 1. Structural Parsing (XML to Graph)

### Input: pylabview XML Output

pylabview extracts two XML files from each VI:
- `vi.xml` - Front panel (controls, indicators, types)
- `vi_BDHb.xml` - Block diagram (nodes, wires, structures)

### Parser: `src/lvpy/parser/`

The parser package extracts structured data from XML into dataclasses:

**Key files:**
- `structures.py` - LoopStructure, TunnelInfo, CaseFrame dataclasses
- `block_diagram.py` - BlockDiagram with nodes, wires, constants
- `front_panel.py` - Front panel terminals (inputs/outputs)

**Key concepts:**

1. **Nodes** - Operations on the diagram:
   - `prim` - Primitive function (identified by `primResID`)
   - `iUse`/`polyIUse` - SubVI call
   - `whileLoop`/`forLoop` - Loop structures
   - `caseStruct` - Case/Select structure

2. **Terminals** - Connection points on nodes:
   - Each terminal has a `uid`, `parmIndex`, `direction`
   - `parmIndex` maps to primitive terminal definitions

3. **Wires (Signals)** - Data flow connections:
   - Multi-element termList: first is source, rest are destinations
   - Example: `[704, 646, 231, 223]` = 704→646, 704→231, 704→223

4. **Loop Tunnels** - Data crossing loop boundaries:
   - `lSR`/`rSR` - Left/Right Shift Register (iteration state)
   - `lpTun` - Loop Tunnel (simple pass-through or auto-index)
   - `lMax` - Loop count (N terminal on for loops)

### Example: While Loop Structure
```
whileLoop uid=502
├── termList (outer terminals: 162-168)
│   ├── term 168 → rSR (right shift register out)
│   ├── term 167 → lSR (left shift register in)
│   └── term 162 → lpTun (loop tunnel)
├── diagramList (inner diagram)
│   ├── prim 193 (primResID=8082, File Info)
│   ├── polyIUse 620 (Strip Path SubVI)
│   └── ...
├── tunnelList [uid references]
└── srDCOList [shift register DCO references]
```

## 2. Memory Graph Construction

### InMemoryVIGraph: `src/lvpy/memory_graph.py`

Builds a NetworkX DiGraph representing data flow:

**Nodes in Graph:**
- FP terminals (inputs/outputs)
- Constants
- Operations (primitives, subvis, loops)
- Internal terminals (for edge connections)

**Edges in Graph:**
- Data flow connections (wire → edge)
- Tunnel connections (outer↔inner terminals)

**Key methods:**

```python
def load_vi(path, search_paths):
    """Parse VI and add to graph. Recursively loads dependencies."""

def get_vi_context(vi_name) -> dict:
    """Get complete context for code generation."""
    return {
        "inputs": [...],      # FP input terminals
        "outputs": [...],     # FP output terminals
        "constants": [...],   # Diagram constants
        "operations": [...],  # Nodes with inner_nodes for loops
        "data_flow": [...],   # Edges as wire list
        "terminals": [...],   # All terminals
    }
```

**Loop Handling in Graph:**

For each loop, the parser:
1. Creates edges: outer_terminal → inner_terminal (for lSR, lpTun)
2. Adds tunnels list to operation dict
3. Recursively parses inner diagram as `inner_nodes`

## 3. Code Generation

### CodeGenContext: `src/lvpy/agent/codegen/context.py`

Manages variable bindings and data flow resolution:

```python
@dataclass
class CodeGenContext:
    bindings: dict[str, str]      # terminal_id → variable_name
    data_flow: list[dict]         # Wire info from vi_context
    _flow_map: dict[str, dict]    # dest_terminal → source info

    def bind(terminal_id, var_name):
        """Register variable for terminal."""

    def resolve(terminal_id) -> str | None:
        """Trace back through data flow to find variable name."""
```

**Resolution algorithm:**
1. Check if terminal has direct binding
2. If not, look up in _flow_map to find source terminal
3. Recursively resolve source terminal
4. Handle tunnel traversal automatically

### Node Code Generators: `src/lvpy/agent/codegen/nodes/`

Each node type has a specialized generator:

**primitive.py** - Primitive operations:
```python
def generate(node, ctx) -> list[ast.stmt]:
    # 1. Resolve primitive definition from primResID
    # 2. Map wired terminals to Python variable names
    # 3. Substitute into python hint template
    # 4. Generate assignment statements
```

**subvi.py** - SubVI calls:
```python
def generate(node, ctx) -> list[ast.stmt]:
    # 1. Resolve input terminal values
    # 2. Build function call with keyword arguments
    # 3. Unpack result tuple to output variables
```

**loop.py** - While/For loops:
```python
def generate(node, ctx) -> list[ast.stmt]:
    # 1. Initialize shift register variables before loop
    # 2. Set up inner context with tunnel bindings
    # 3. Generate loop body from inner_nodes
    # 4. Handle auto-indexing for lpTun
    # 5. Update shift register variables at end of body
```

### Primitive Resolution: `src/lvpy/data/primitives.json`

Maps primResID to Python code hints:

```json
{
  "8082": {
    "name": "File/Directory Info",
    "terminals": [
      {"index": 0, "direction": "out", "name": "size"},
      {"index": 3, "direction": "out", "name": "path"},
      {"index": 11, "direction": "in", "name": "path"}
    ],
    "python": {
      "_body": "_stat = Path(path).stat() if path... else None",
      "size": "_stat.st_size if _stat else 0",
      "path": "path"
    }
  }
}
```

**Terminal index mapping:**
- `parmIndex` in XML terminal → `index` in primitive definition
- Matches terminal to name (e.g., idx=11 → "path")
- Code gen substitutes resolved variables into python hints

## 4. Data Flow Example

### VI: Get Settings Path

```
[Get System Directory] → [Build Path] → [Strip Path] → [Create Dir] → output
     directory_type=7     + "Settings.ini"   .parent
```

### Data Flow Graph Edges

```
422 (Directory Path input) → 167 (while loop lSR outer)
167 → 280 (lSR inner terminal)
280 → 719 (File Info path input)
704 (File Info path out) → 646 (Strip Path input)
...
```

### Generated Python

```python
def get_settings_path():
    result = get_system_directory(directory_type=7)
    appended = result.system_directory_path / Path('JKI/VI...')
    stripped = appended.parent
    create_dir_if_non_existant__ogtk(directory_path=stripped)
    return GetSettingsPathResult(config_path=appended)
```

## 5. Current Issues

### Primitive Terminal Mapping
Some primitives have incorrect terminal index→name mappings. Evidence shows:
- File Info (8082): idx=11 is path input, idx=3 is path output
- Create Folder (8055): idx=11 can be path input (variant)

### Unbundle By Name (1112)
Generated code attempts `.path` on Path objects. The primitive definition
doesn't correctly handle cluster field access.

### Loop Data Flow
Shift registers correctly share variables across iterations. The bindings:
1. `shift_var_X = input` (before loop)
2. Use `shift_var_X` in loop body
3. `shift_var_X = new_value` (update at end of iteration)

## 6. File Locations

```
src/lvpy/
├── parser/           # XML → dataclasses
├── memory_graph.py   # Graph construction
└── agent/
    └── codegen/
        ├── context.py    # Variable bindings
        ├── builder.py    # Main orchestration
        └── nodes/
            ├── primitive.py
            ├── subvi.py
            └── loop.py

data/
├── primitives.json  # Primitive → Python mapping
└── vilib-vis.json           # vilib VI definitions

scripts/
└── ast_only.py      # Test script for AST codegen
```

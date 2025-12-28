# /convert - Convert LabVIEW VI to Python

Convert a LabVIEW VI file to Python code using the graph-based pipeline.

## Usage
```
/convert path/to/file.vi [--output dir] [--model model-name]
```

## Pipeline Steps

### 1. Extract VI to XML
```bash
cd /path/to/vi/directory
python -m pylabview.readRSRC -i file.vi -x
```
This creates `file_BDHb.xml` (block diagram), `file_FPHb.xml` (front panel), `file.xml` (main).

### 2. Load into Neo4j Graph
```bash
vipy graph path/to/file.vi --clear
```
This parses the XML, generates Cypher, and loads into Neo4j. SubVIs are expanded recursively.

### 3. Get Conversion Order
Query the graph for dependency order (leaves first):
```python
from vipy.graph import VIGraph
with VIGraph() as graph:
    order = graph.get_conversion_order()
    # Returns: ['leaf_subvi.vi', 'mid_subvi.vi', 'main.vi']
```

### 4. Convert Each VI in Order
For each VI in dependency order:

1. **Query graph for context:**
   ```python
   context = graph.get_vi_context(vi_name)
   # Returns: inputs, outputs, operations, data_flow, constants
   ```

2. **Get converted SubVI signatures** (from previous conversions):
   ```python
   subvi_sigs = state.get_converted_signatures(vi_name)
   ```

3. **Build LLM prompt** with context + signatures + primitives

4. **Generate code** via Ollama

5. **Validate** (syntax, imports, types)

6. **Save** to output directory, update state

### 5. Generate Package
Create `__init__.py` exporting all converted functions.

## Example Session

```
User: /convert samples/OpenG/file.llb/Build Path__ogtk.vi -o output/

Agent: Loading VI into graph...
  Found 3 VIs to convert

Converting in dependency order:
  [1/3] Strip Path__ogtk.vi
    → Querying graph for context
    → Generating code
    → Validation passed
    → Saved to output/strip_path__ogtk.py

  [2/3] Valid Path__ogtk.vi
    → Querying graph for context
    → Generating code
    → Validation passed
    → Saved to output/valid_path__ogtk.py

  [3/3] Build Path__ogtk.vi
    → Querying graph for context
    → SubVI imports: strip_path__ogtk, valid_path__ogtk
    → Generating code
    → Validation passed
    → Saved to output/build_path__ogtk.py

Complete: 3/3 converted
Output: output/
```

## Graph Queries Used

- `graph.get_conversion_order()` - dependency-ordered VI list
- `graph.get_vi_context(name)` - full VI context (inputs, outputs, ops, flow)
- `graph.get_vi_inputs(name)` - input terminals
- `graph.get_vi_outputs(name)` - output terminals
- `graph.get_subvi_calls(name)` - SubVIs called by this VI
- `graph.is_stub_vi(name)` - check if VI is missing (stub)

## State Tracking

The agent maintains conversion state:
- `state.mark_converted(vi_name, path)` - record successful conversion
- `state.is_converted(vi_name)` - check if already done
- `state.get_import_statement(vi_name)` - get import for SubVI

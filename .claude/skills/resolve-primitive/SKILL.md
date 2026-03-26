---
name: resolve-primitive
description: Resolve a single unknown LabVIEW primitive by following a strict verification process against documentation and graph context. Called when TerminalResolutionNeeded fires during vipy generate.
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# Resolve Unknown Primitive

When `TerminalResolutionNeeded` fires for a primitive during `vipy generate`, this skill resolves it by identifying the function from documentation and graph context. Follow ALL steps IN ORDER. Do NOT skip. Do NOT guess.

## Input

The `TerminalResolutionNeeded` exception provides:
- `prim_id` (primResID)
- Terminal indices, directions, and types
- VI name where it was encountered

## Step 1: Record the diagnostic

Write down the EXACT diagnostic output:
- primResID
- Every terminal: index, direction, type
- The VI name

## Step 2: Get more instances

Search for ALL instances of this primResID across our VIs to see terminal variations:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from pathlib import Path
from vipy.parser.vi import parse_vi
from vipy.parser.node_types import PrimitiveNode
count = 0
for vi_path in Path('samples').rglob('*.vi'):
    try:
        parsed = parse_vi(str(vi_path))
    except:
        continue
    for node in parsed.block_diagram.nodes:
        if isinstance(node, PrimitiveNode) and node.prim_res_id == PRIM_ID:
            terms = [(uid, ti) for uid, ti in parsed.block_diagram.terminal_info.items() if ti.parent_uid == node.uid]
            types = [f'{\"out\" if ti.is_output else \"in\"}:idx={ti.index}:{ti.parsed_type.type_name if ti.parsed_type else \"?\"}' for _, ti in sorted(terms, key=lambda x: x[1].index)]
            print(f'{vi_path.name}: primIdx={node.prim_index} {types}')
            count += 1
            if count >= 10:
                break
    if count >= 10:
        break
print(f'Total: {count}')
" 2>/dev/null
```

## Step 3: Examine graph context

For each instance, check what operations feed into and consume from this primitive:
```bash
python3 -c "
import sys; sys.path.insert(0, 'src')
from pathlib import Path
from vipy.parser.vi import parse_vi
from vipy.parser.node_types import PrimitiveNode

for vi_path in Path('samples').rglob('VI_NAME'):
    try:
        parsed = parse_vi(str(vi_path))
    except:
        continue
    for node in parsed.block_diagram.nodes:
        if isinstance(node, PrimitiveNode) and node.prim_res_id == PRIM_ID:
            my_terms = {uid for uid, ti in parsed.block_diagram.terminal_info.items() if ti.parent_uid == node.uid}
            for w in parsed.block_diagram.wires:
                if w.from_term in my_terms or w.to_term in my_terms:
                    other_uid = w.to_term if w.from_term in my_terms else w.from_term
                    other_ti = parsed.block_diagram.terminal_info.get(other_uid)
                    if other_ti:
                        other_node = next((n for n in parsed.block_diagram.nodes if n.uid == other_ti.parent_uid), None)
                        direction = 'output →' if w.from_term in my_terms else 'input ←'
                        name = other_node.name if other_node else other_ti.parent_uid
                        print(f'  {direction} {name}')
            break
    break
" 2>/dev/null
```

The connected operations, their names, and the data types flowing through reveal what this primitive does.

## Step 4: Cross-check primResID range

Related LabVIEW primitives share primResID ranges:
- 1044-1064: Array operations
- 1061-1081: Numeric/arithmetic
- 1083-1128: Path/comparison/boolean
- 1140-1170: Type conversion, variant, data manipulation
- 1300-1340: Timing, constants, clusters
- 1419-1435: Path operations
- 1500-1540: String operations
- 1600-1610: Flatten/unflatten
- 1809-1911: Array index/sort/delete
- 1999: Path constant
- 2073-2076: Error handling
- 2401: Merge Errors
- 8003-8083: File I/O
- 8100-8101: VI info
- 8201-8205: Variant operations
- 9000-9114: VI Server, references, scripting

Does the terminal signature (types, count, directions) fit the range?

## Step 5: Search the LabVIEW documentation

The full text is at `docs/labview_ref_manual.txt`. Search for candidate functions matching:
- The terminal COUNT (number of inputs and outputs)
- The terminal TYPES (matching the actual types from Step 1)
- The CATEGORY (matching the range from Step 4)
- The CONTEXT (matching the connected operations from Step 3)

Read the FULL Inputs/Outputs section. Confirm EVERY terminal name, direction, and type matches the actual data.

```bash
grep -n "CANDIDATE FUNCTION NAME" docs/labview_ref_manual.txt | head -5
```

Then read the relevant section to get the complete terminal layout.

## Step 6: Add the JSON entry

Only after completing steps 1-5. Add to `data/primitives-codegen.json` under `primitives`:

```json
"PRIM_ID": {
    "name": "Confirmed Function Name",
    "terminals": [
        {"index": N, "direction": "in", "name": "descriptive_python_name", "type": "actual_type"},
        {"index": N, "direction": "out", "name": "descriptive_python_name", "type": "actual_type"}
    ],
    "python_code": {"output_name": "in_N op in_M"},
    "inline": true,
    "verified": true,
    "pdf_page": PAGE_NUMBER
}
```

Rules:
- Terminal **indices** MUST be confirmed from observed wiring (Steps 2-3), NEVER assumed from documentation order. The documentation lists terminals in a different order than the connector pane indices. Example: Split 1D Array docs list "array, index" but the connector pane has index=2 for the numeric index and index=3 for the array.
- Terminal **directions** MUST be confirmed from observed `is_output` flags in the parser data, not from documentation
- Terminal names MUST be valid Python identifiers (no `x=y?`, no `NaN/Path/Refnum?`)
- Template expressions MUST use `in_N` index references matching the OBSERVED connector pane indices
- Include ALL terminals that appear in the actual VI data, including error clusters
- `python_code` dict keys match output terminal names
- Mark `"verified": true` only if indices confirmed from observed connections
- The parser reports **element types** for array terminals (e.g., NumUInt8 for Array of UInt8). Don't confuse element types with scalar types — check the output terminal types and wiring context

## Step 7: Re-run generation

```bash
vipy generate "path/to/vi" -o outputs --search-path samples/OpenG/extracted
```

If the same primitive fails again, the terminal matching is wrong — go back to Step 1.
If a NEW primitive fails, start this process again for that one.

## NEVER do these things

- NEVER guess a function name from terminal types alone
- NEVER say "polymorphic" to explain away type mismatches — ask the user
- NEVER copy a name from another entry because "it looks similar"
- NEVER fill python_code without confirming semantics from the documentation
- NEVER assume terminal indices from documentation listing order — always confirm from observed wiring in Steps 2-3
- NEVER assume a primResID maps to a different function based on terminal types — primitive polymorphism must be observed in data or confirmed by the user
- NEVER skip the context check (Step 3) — it reveals what the function actually does
- NEVER batch-fill entries — one at a time, fully verified

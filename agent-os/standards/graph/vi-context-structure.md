# vi_context Structure

`get_vi_context()` returns the dict consumed by code generators.

## Structure

```python
{
    "name": "MyVI.vi",
    "library": "MyLib.lvlib",  # or None
    "qualified_name": "MyLib.lvlib:MyVI.vi",
    "inputs": [FPTerminalNode(...)],
    "outputs": [FPTerminalNode(...)],
    "constants": [Constant(...)],
    "operations": [Operation(...)],
    "data_flow": [Wire(...)],
    "poly_variants": ["Variant1.vi", ...],  # for polymorphic VIs
    "has_parallel_branches": bool,
}
```

## Key Points

- Values are **dataclasses** from `graph_types.py`, not raw dicts
- `operations` is already in execution order (topologically sorted)
- Inner loop/case operations are nested in `Operation.inner_nodes`, not at top level
- `has_parallel_branches` enables held error model in codegen

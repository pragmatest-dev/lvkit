# Generation Order

`get_generation_order()` returns VIs in dependency-safe order.

## API

```python
for vi_group in graph.get_generation_order():
    for vi_name in vi_group:
        ctx = graph.get_vi_context(vi_name)
        # Generate code for vi_name
```

## Grouping for Recursive VIs

Uses Strongly Connected Component (SCC) detection. Each group is either:
- **Single VI**: No recursion, safe to generate
- **Multiple VIs**: Mutually recursive, must generate together

Recursive groups need forward declarations or stubs.

## Why Groups

LabVIEW supports recursive VIs. Python needs functions defined before calling. Groups let you handle mutual recursion by generating stubs first.
